from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

from attavo.approval import approval_status
from attavo.build import cmd_build, driver_identity
from attavo.common import MANIFEST, ShallowCheckoutError, derive_item_id, git, read_manifest
from attavo.counsel import cmd_counsel
from attavo.doctor import (
    _parse_json_line,
    invocation_alive,
    invocations_without_record,
    stall_bound_s,
    write_invocation_marker,
)
from attavo.filing import cmd_file_stops
from attavo.records import (
    latest_attempt_path,
    latest_record,
    next_attempt_number,
    stage_schema,
    write_record,
)
from attavo.verify import cmd_verify, poll_forge_checks

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


def _drive_run_stage(root: Path, records_dir: Path, branch: str, stage: str,
                      item_id: str) -> tuple[int, bool]:
    """Runs build or verify in-process -- the same command `attavo build`
    or `attavo verify` would run, printing the same reason on a refusal,
    failure, or halt -- and returns (rc, done): done is False only when
    the record just written carries status "pass" (a stage command's own
    exit code says nothing about that: cmd_verify exits 0 on a "returned"
    record too, since returning is not an operational error). A non-pass
    record is counseled (cmd_counsel, issue #100) and filed (cmd_file_stops,
    issue #89) before it is transported,
    so a stop's own counsel record and .filed marker -- the filing
    command's stdout, the issue file-stops just opened -- exist on disk
    in time to be committed and pushed in the very same commit as the
    stage record and its log; a marker written only after the push would
    never reach the ledger at all, and a second worktree or a fresh clone
    would refile every stop it finds with no marker beside it. The stage
    record and the counsel record (when one was written) are then
    transported together: committed with commit_record and pushed with
    push_record_commit, whatever the stage record's status -- the record
    pushes stay the same pushes, made by drive in the run that produced
    the record (issue #84's rule 42 row)."""
    before = latest_attempt_path(records_dir, stage)

    # The invocation's marker, committed and pushed before the stage runs:
    # one commit per attempt, so that an invocation which dies or hangs
    # is a fact on the branch (proof five) and not a silence.
    nn = next_attempt_number(records_dir, stage)
    marker = write_invocation_marker(root, records_dir, stage, nn)
    try:
        commit_record(root, [marker], f"attavo: {stage} invocation {nn:02d} started for item {item_id}")
    except RuntimeError as exc:
        print(f"attavo drive: stopped: could not commit the {stage} invocation marker: {exc}")
        return 1  # type: ignore[return-value]  # defect (#189): an int, not the (rc, done) its callers unpack
    ok, err = push_record_commit(root, branch)
    if not ok:
        print(f"attavo drive: stopped: could not push the {stage} invocation marker: {err}")
        return 1  # type: ignore[return-value]  # defect (#189): an int, not the (rc, done) its callers unpack

    rc = cmd_build([item_id]) if stage == "build" else cmd_verify([item_id])
    after = latest_attempt_path(records_dir, stage)

    if after is None or after == before:
        # Refused before writing anything at all (e.g. no adapter
        # configured) -- the stage command already printed why.
        return (rc if rc != 0 else 1), True

    # An attempt path exists, so the record read from the same files does too.
    record = cast(dict[str, Any], latest_record(records_dir, stage))
    status_out = record.get("status")
    to_commit = [after]
    if status_out != "pass":
        counsel_before = latest_attempt_path(records_dir, "counsel")
        cmd_counsel([item_id])
        counsel_after = latest_attempt_path(records_dir, "counsel")
        if counsel_after is not None and counsel_after != counsel_before:
            to_commit.append(counsel_after)
        # file-stops is its own idempotent read of the item's records, so
        # calling it here on every run that wrote a non-pass record is
        # safe to call again on a later run: an already-filed stop's own
        # marker is what stops it from being filed twice.
        cmd_file_stops([item_id])

    message = f"attavo: {stage} record for item {item_id} ({after.stem})"
    try:
        commit_record(root, to_commit, message)
    except RuntimeError as exc:
        print(f"attavo drive: stopped: could not commit the {stage} record: {exc}")
        return 1  # type: ignore[return-value]  # defect (#189): an int, not the (rc, done) its callers unpack

    ok, err = push_record_commit(root, branch)
    if not ok:
        print(f"attavo drive: stopped: could not push the {stage} record: {err}")
        return 1  # type: ignore[return-value]  # defect (#189): an int, not the (rc, done) its callers unpack

    if status_out == "halted":
        question = (record.get("halted") or {}).get("question")
        print(f"attavo drive: stopped: {stage} halted: {question} ({after.name}); "
              f"pushed to {branch}")
    elif status_out != "pass":
        reason = record.get("refusal") or status_out
        print(f"attavo drive: stopped: {stage} {status_out}: {reason} ({after.name}); "
              f"pushed to {branch}")
    else:
        print(f"attavo drive: pushed {after.name} to {branch}")
    return rc, status_out != "pass"


def _drive_step(root: Path, m: dict[str, Any], item_id: str) -> tuple[int, bool]:
    """One mechanical step, exactly as a single `attavo drive` call always
    has -- and whether repetition under --until-ready should stop here:
    True for every refusal or stop (no valid approval, no
    [forge].checks_command, the forge not green, or a stage that wrote a
    non-pass record) and for the ready-to-merge terminal state; False
    only when a stage just passed, so drive_state may have a further
    stage to pursue on the next call."""
    try:
        stage, extra, records_dir = drive_state(root, m, item_id)
    except ShallowCheckoutError as exc:
        print(f"attavo drive: refused: {exc}", file=sys.stderr)
        return 2, True

    if stage == "no-approval":
        print(f"attavo drive: refused: approval is {extra}")
        return 1, True

    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()

    if stage == "build":
        return _drive_run_stage(root, records_dir, branch, "build", item_id)

    checks_command = (m.get("forge") or {}).get("checks_command")
    if not checks_command:
        print("attavo drive: refused: no [forge].checks_command in the manifest")
        return 1, True
    head_sha = git(root, "rev-parse", "HEAD").strip()
    forge_checks, ok = poll_forge_checks(root, checks_command, head_sha)
    if not ok:
        conclusions = (", ".join(f"{c['name']} {c['conclusion']}"
                                 for c in forge_checks["conclusions"])
                      or "no checks reported")
        print(f"attavo drive: stopped: forge checks not green on {head_sha[:12]}: {conclusions}")
        return 1, True

    if stage == "verify":
        return _drive_run_stage(root, records_dir, branch, "verify", item_id)

    print(f"attavo drive: item {item_id} is ready to merge")
    return 0, True


def cmd_drive(argv: list[str]) -> int:
    # Issue #145: Python block-buffers stdout when it is not a tty (a
    # pipe, e.g. a screen reading this process live), so every stage
    # line -- attavo build:, attavo drive: pushed, attavo verify:, attavo
    # drive: item N is ready to merge -- would otherwise sit unflushed
    # until this process exits. Line-buffering makes a pipe reader see
    # each line the moment it is printed, the same as a terminal already
    # does; no other output path changes.
    cast(io.TextIOWrapper, sys.stdout).reconfigure(line_buffering=True)
    until_ready = "--until-ready" in argv
    recover = "--recover" in argv
    positional = [a for a in argv if a not in ("--until-ready", "--recover")]
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

    if recover:
        return _drive_recover(root, m, item_id)

    # Without --until-ready this is exactly one call to _drive_step,
    # letter for letter what cmd_drive always did: `not until_ready`
    # returns on the very first iteration regardless of `done`. With it,
    # a step that just passed (done False) repeats -- step, wait (the
    # forge poll already inside _drive_step), step, wait -- until the
    # item is ready to merge or a step stops on its own account.
    while True:
        rc, done = _drive_step(root, m, item_id)
        if done or not until_ready:
            return rc


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
        stage, nn_text = marker.stem.rsplit("-", 1)
        nn = int(nn_text)
        events = _events_path_for(root, stage, nn)
        log, last_at = _events_tail(events)
        age = _seconds_since(last_at or info.get("at"))
        pid, host = info.get("pid"), info.get("host")
        if invocation_alive(info):
            if age is None or age <= bound:
                print(f"attavo drive: {marker.stem} is running (pid {pid}, last event "
                      f"{'unknown' if age is None else int(age)} s ago, bound {bound} s); "
                      f"nothing to recover")
                continue
            cls = "hung"
            try:
                os.kill(cast(int, pid), signal.SIGTERM)  # invocation_alive: pid is an int
                ended = "terminated"
            except OSError as exc:
                ended = f"not terminated ({exc})"
            detail = (f"pid {pid} on {host} alive with no event for {int(age)} s "
                      f"(bound {bound} s); {ended}")
        else:
            cls = "died"
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
