from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, NamedTuple, cast

from attavo.approval import approval_status
from attavo.build import cmd_build, driver_identity
from attavo.common import MANIFEST, PRODUCT_ROOT, ShallowCheckoutError, derive_item_id, git, read_manifest
from attavo.counsel import cmd_counsel
from attavo.doctor import (
    _parse_json_line,
    invocation_alive,
    invocations_without_record,
    stall_bound_s,
    write_invocation_marker,
)
from attavo.events import stage_events_dir
from attavo.filing import cmd_file_stops
from attavo.records import (
    latest_attempt_path,
    latest_record,
    next_attempt_number,
    stage_schema,
    write_record,
)
from attavo.verify import cmd_verify

# --- drive: the mechanical transitions between approval and ready-to-merge
# (issue #84) -- one step per invocation, and only that step, until a
# state that belongs to a person. Writes no record of its own; the stage
# records already on disk are the only state it reads. ---


def commit_record(root: Path, record_paths: Path | list[Path], message: str) -> None:
    """`git commit --only` on every record path given, and each one's .log
    and .filed, each if one exists -- never the whole index. A transport
    commit that sweeps the index is exactly the regression issue #46
    already guards build and verify against on the adapter side; drive
    must not reintroduce it on the transport side. The .filed marker
    (issue #89) is committed alongside the record it names, in the same
    commit and the same push, so a second worktree or a fresh clone reads
    the same filed fact rather than refiling every stop it finds with no
    marker of its own. A single Path is accepted for the common one-record
    case; issue #100 passes a list so the counsel record lands in the same
    commit as the stage record it counsels. Authored and committed as
    driver_identity(), set by the product and never the tenant's git
    config, so a driven item's record commits are never indistinguishable
    from an operator's own."""
    if isinstance(record_paths, Path):
        record_paths = [record_paths]
    paths: list[str] = []
    for record_path in record_paths:
        paths.append(str(record_path.relative_to(root)))
        log_path = record_path.with_suffix(".log")
        if log_path.is_file():
            paths.append(str(log_path.relative_to(root)))
        marker_path = record_path.with_suffix(".filed")
        if marker_path.is_file():
            paths.append(str(marker_path.relative_to(root)))
    identity = driver_identity()
    env = dict(os.environ)
    env["GIT_AUTHOR_NAME"] = identity["name"]
    env["GIT_AUTHOR_EMAIL"] = identity["email"]
    env["GIT_COMMITTER_NAME"] = identity["name"]
    env["GIT_COMMITTER_EMAIL"] = identity["email"]
    git(root, "add", *paths)
    git(root, "commit", "--only", "-m", message, *paths, env=env)


def push_record_commit(root: Path, branch: str) -> tuple[bool, str | None]:
    """Push HEAD to branch on origin. A push a divergent remote rejects is
    retried once after `git pull --no-rebase --no-edit origin branch` -- a
    merge, never a rebase, because a rebase would change the sha the record
    commit just wrote already names."""
    def attempt() -> tuple[bool, str]:
        run = subprocess.run(["git", "-C", str(root), "push", "origin", f"HEAD:{branch}"],
                             capture_output=True, text=True, check=False)
        return run.returncode == 0, run.stderr.strip()

    ok, err = attempt()
    if ok:
        return True, None
    pull = subprocess.run(["git", "-C", str(root), "pull", "--no-rebase", "--no-edit",
                           "origin", branch], capture_output=True, text=True, check=False)
    if pull.returncode != 0:
        return False, f"push rejected and the retry merge failed: {pull.stderr.strip() or err}"
    ok, err = attempt()
    if not ok:
        return False, f"push rejected again after merging {branch}: {err}"
    return True, None


def drive_state(root: Path, m: dict[str, Any], item_id: str) -> tuple[str, str | None, Path]:
    """("no-approval" | "build" | "verify" | "ready", detail, records_dir):
    the next mechanical step for item_id, read from its latest records
    alone. A build only counts as current when it is a pass bound to the
    valid approval's own sha; a verify only counts as current when it is a
    pass bound to that build's own sha -- the same staleness a re-approval
    or a re-build must force, attest's own chain check already enforces
    (check_attest_chain), read here rather than re-typed."""
    records_rel = m["paths"]["records"]
    records_dir = root / records_rel / item_id

    status = approval_status(root, m, item_id)
    if status != "valid":
        return "no-approval", status, records_dir

    latest_approval = latest_record(records_dir, "approval")
    latest_build = latest_record(records_dir, "build")
    build_current = (latest_build is not None and latest_build.get("status") == "pass"
                     and latest_build.get("approval_sha")
                     == (latest_approval.get("sha") if latest_approval else None))
    if latest_build is None or not build_current:
        return "build", None, records_dir

    latest_verify = latest_record(records_dir, "verify")
    verify_current = (latest_verify is not None and latest_verify.get("status") == "pass"
                      and latest_verify.get("build_sha") == latest_build.get("sha"))
    if not verify_current:
        return "verify", None, records_dir

    return "ready", None, records_dir


def _stage_body(item_id: str, stage: str) -> int:
    """Stub: what the pass's detached child runs -- the stage in-process,
    counsel and file-stops on a non-pass record, one commit, one push.
    The marker is the pass's, committed and pushed before this starts."""
    root = Path(".").resolve()
    m = read_manifest(root)
    if m is None:
        return 2
    records_dir = root / m["paths"]["records"] / item_id
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    before = latest_attempt_path(records_dir, stage)
    rc = cmd_build([item_id]) if stage == "build" else cmd_verify([item_id])
    after = latest_attempt_path(records_dir, stage)
    if after is None or after == before:
        return (rc if rc != 0 else 1)
    record = cast(dict[str, Any], latest_record(records_dir, stage))
    to_commit = [after]
    if record.get("status") != "pass":
        counsel_before = latest_attempt_path(records_dir, "counsel")
        cmd_counsel([item_id])
        counsel_after = latest_attempt_path(records_dir, "counsel")
        if counsel_after is not None and counsel_after != counsel_before:
            to_commit.append(counsel_after)
        cmd_file_stops([item_id])
    try:
        commit_record(root, to_commit, f"attavo: {stage} record for item {item_id} ({after.stem})")
    except RuntimeError as exc:
        print(f"attavo drive: stopped: could not commit the {stage} record: {exc}")
        return 1
    ok, err = push_record_commit(root, branch)
    if not ok:
        print(f"attavo drive: stopped: could not push the {stage} record: {err}")
        return 1
    print(f"attavo drive: pushed {after.name} to {branch}")
    return rc


def _start_stage(root: Path, m: dict[str, Any], item_id: str, stage: str,
                 forge_checks: dict[str, Any] | None) -> int:
    """Stub: marker, commit, push; then the stage body in a child of its
    own session, and return."""
    records_dir = root / m["paths"]["records"] / item_id
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    nn = next_attempt_number(records_dir, stage)
    marker = write_invocation_marker(root, records_dir, stage, nn)
    try:
        commit_record(root, [marker], f"attavo: {stage} invocation {nn:02d} started for item {item_id}")
    except RuntimeError as exc:
        print(f"attavo drive: stopped: could not commit the {stage} invocation marker: {exc}")
        return 1
    ok, err = push_record_commit(root, branch)
    if not ok:
        print(f"attavo drive: stopped: could not push the {stage} invocation marker: {err}")
        return 1
    env = dict(os.environ)
    if forge_checks is not None:
        checks_file = stage_events_dir(root) / f"{stage}-{nn:02d}.forge-checks.json"
        checks_file.parent.mkdir(parents=True, exist_ok=True)
        checks_file.write_text(json.dumps(forge_checks))
        env["ATTAVO_FORGE_CHECKS"] = str(checks_file)
    log = stage_events_dir(root) / f"{stage}-{nn:02d}.worker.log"
    code = (f"import sys; sys.path.insert(0, {str(PRODUCT_ROOT)!r}); "
            f"from attavo.drive import _stage_body; sys.exit(_stage_body({item_id!r}, {stage!r}))")
    with log.open("ab") as fh, open(os.devnull, "rb") as devnull:
        proc = subprocess.Popen([sys.executable, "-c", code], cwd=str(root), env=env, stdin=devnull,
                                stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
    print(f"attavo drive: {'Build' if stage == 'build' else 'Review'} started (attempt {nn:02d}, worker pid {proc.pid})")
    return 0


def cmd_drive(argv: list[str]) -> int:
    cast(io.TextIOWrapper, sys.stdout).reconfigure(line_buffering=True)
    if "--recover" in argv:
        print("attavo drive: --recover is gone; a pass recovers a dead or hung invocation itself",
              file=sys.stderr)
        return 2
    report = "--report" in argv
    positional = [a for a in argv if not a.startswith("--")]
    root = Path(".").resolve()
    item_id = positional[0] if positional else derive_item_id(root)
    if not item_id:
        print("attavo drive: cannot derive item id; pass N or run on an attavo/N-* branch",
              file=sys.stderr)
        return 2
    m = read_manifest(root)
    if m is None:
        print(f"attavo drive: no {MANIFEST} at {root}", file=sys.stderr)
        return 2
    from attavo.report import drive_report
    r = drive_report(root, m, item_id)
    if report:
        print(json.dumps(r, indent=2))
        return 0
    if r["state"] == "owes-recovery":
        return _drive_recover(root, m, item_id)
    if r["state"] == "next-build":
        return _start_stage(root, m, item_id, "build", None)
    if r["state"] == "next-review":
        return _start_stage(root, m, item_id, "verify", {
            "command": (m.get("forge") or {}).get("checks_command"), "sha": r["head"],
            "conclusions": r["checks"] or []})
    print(f"attavo drive: {r['says']}")
    return 1 if r["state"] == "refused" else 0


def _events_path_for(root: Path, stage: str, nn: int) -> Path | None:
    """The adapter's own events file for this invocation, under the
    checkout's git dir (a worktree's own dir when the checkout is one)."""
    try:
        gitdir = Path(git(root, "rev-parse", "--git-dir").strip())
    except RuntimeError:
        return None
    if not gitdir.is_absolute():
        gitdir = root / gitdir
    path = gitdir / "attavo" / f"{stage}-{nn:02d}.events.jsonl"
    return path if path.is_file() else None


def _events_tail(path: Path | None, lines: int = 20) -> tuple[str, str | None]:
    """(the last lines as a log, the last event's own at)."""
    if path is None:
        return "no events file for this invocation\n", None
    try:
        text = path.read_text()
    except OSError:
        return "events file unreadable\n", None
    tail = text.splitlines()[-lines:]
    last_at = None
    for line in reversed(tail):
        parsed = _parse_json_line(line)
        if not isinstance(parsed, dict):
            continue
        at = parsed.get("at")
        if isinstance(at, str):
            last_at = at
            break
    return "\n".join(tail) + "\n", last_at


def _seconds_since(at: str | None) -> float | None:
    from datetime import datetime, timezone
    if not at:
        return None
    try:
        then = datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - then).total_seconds()


class InvocationReading(NamedTuple):
    """What one marker with no record reads as, from here and now."""
    stage: str
    nn: int
    log: str
    last_at: str | None
    age: float | None
    cls: str  # "running" | "hung" | "died"


def read_invocation(root: Path, bound: int, marker: Path, info: dict[str, Any]) -> InvocationReading:
    """Whether an invocation that began and wrote no record is running
    (a live pid, not past bound seconds since its last event or, with no
    event, its own start), hung (alive past the bound), or died (its pid
    is gone, or on another host). Reads only: `attavo drive --recover`
    ends what this names hung, and `attavo drive --report` prints it."""
    stage, nn_text = marker.stem.rsplit("-", 1)
    nn = int(nn_text)
    log, last_at = _events_tail(_events_path_for(root, stage, nn))
    age = _seconds_since(last_at or info.get("at"))
    if not invocation_alive(info):
        cls = "died"
    elif age is None or age <= bound:
        cls = "running"
    else:
        cls = "hung"
    return InvocationReading(stage, nn, log, last_at, age, cls)


def _drive_recover(root: Path, m: dict[str, Any], item_id: str) -> int:
    """`attavo drive --recover [N]`: every invocation of item N that began
    and wrote no record ends in one here -- class died when its pid is
    gone or on another host, class hung when it is alive past
    [drive].stall_after_s with no newer event (and terminated); provenance
    product; the events tail as the log. A live invocation inside the
    bound is running and left alone. The record is committed and pushed
    the way every stage record is; the ordinary relaunch then reads a
    non-pass record and does what it does."""
    import os
    import signal
    records_rel = m["paths"]["records"]
    records_dir = root / records_rel / item_id
    bound = stall_bound_s(m)
    if bound is None:
        print("attavo drive: refused: no [drive].stall_after_s in the manifest; a hung "
              "invocation cannot be told from a running one")
        return 1
    pending = invocations_without_record(records_dir)
    if not pending:
        print(f"attavo drive: item {item_id}: every invocation has its record; nothing to recover")
        return 0
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    try:
        git(root, "remote", "get-url", "origin")
        has_origin = True
    except RuntimeError:
        has_origin = False
    recovered = 0
    for marker, info in pending:
        stage, nn, log, last_at, age, cls = read_invocation(root, bound, marker, info)
        pid, host = info.get("pid"), info.get("host")
        if cls == "running":
            print(f"attavo drive: {marker.stem} is running (pid {pid}, last event "
                  f"{'unknown' if age is None else int(age)} s ago, bound {bound} s); "
                  f"nothing to recover")
            continue
        if cls == "hung":
            try:
                os.kill(cast(int, pid), signal.SIGTERM)  # invocation_alive: pid is an int
                ended = "terminated"
            except OSError as exc:
                ended = f"not terminated ({exc})"
            detail = (f"pid {pid} on {host} alive with no event for {int(cast(float, age))} s "
                      f"(bound {bound} s); {ended}")
        else:
            detail = (f"pid {pid} on {host} is not running and wrote no {marker.stem}.json; "
                      f"started {info.get('at')}, last event {last_at or 'none'}")
        if next_attempt_number(records_dir, stage) != nn:
            print(f"attavo drive: stopped: {marker.name} names attempt {nn} but the next "
                  f"{stage} record would be {next_attempt_number(records_dir, stage):02d}; "
                  f"a later record already exists, read the records before recovering")
            return 1
        schema = stage_schema(stage)
        record: dict[str, Any] = {k: None for k in schema.get("required", [])}
        approval = latest_record(records_dir, "approval")
        record.update({"sha": info.get("sha"), "start_sha": info.get("sha"),
                       "approval_sha": approval.get("sha") if approval else None,
                       "at": info.get("at"),
                       "adapter": (m.get("executor") or {}).get(stage),
                       "status": "failed", "refusal": None, "halted": None,
                       "stop": {"class": cls, "provenance": "product", "detail": detail}})
        if "wall_clock_s" in record:
            record["wall_clock_s"] = None if age is None else int(age)
        record = {k: v for k, v in record.items() if k in schema.get("properties", {})}
        try:
            path = write_record(records_dir, stage, record, m, item_id, log=log)
        except ValueError as exc:
            print(f"attavo drive: stopped: could not write the {cls} record for {marker.stem}: {exc}")
            return 1
        try:
            commit_record(root, [path], f"attavo: {stage} record for item {item_id} "
                                        f"({path.stem}), recovered as {cls}")
        except RuntimeError as exc:
            print(f"attavo drive: stopped: could not commit the {cls} record: {exc}")
            return 1
        if has_origin:
            ok, err = push_record_commit(root, branch)
            if not ok:
                print(f"attavo drive: stopped: could not push the {cls} record: {err}")
                return 1
        else:
            print(f"attavo drive: note: no origin remote; {path.name} is committed, not pushed")
        print(f"attavo drive: recovered {marker.stem} as {cls}: {detail}")
        recovered += 1
    print(f"attavo drive: item {item_id}: {recovered} invocation(s) recovered")
    return 0
