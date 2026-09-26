from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, cast

from attavo.approval import approval_status
from attavo.build import cmd_build, driver_identity
from attavo.common import MANIFEST, PRODUCT_ROOT, derive_item_id, git, read_manifest
from attavo.counsel import cmd_counsel
from attavo.doctor import (
    _parse_json_line,
    attempts_budget,
    ceiling_s,
    invocation_alive,
    invocations_without_record,
    retry_after_s,
    stall_bound_s,
    write_invocation_marker,
)
from attavo.stops import RETRYABLE_CLASSES, signature_of
from attavo.events import stage_events_dir
from attavo.filing import cmd_file_stops
from attavo.records import (
    latest_attempt_path,
    latest_record,
    next_attempt_number,
    stage_schema,
    write_record,
)
from attavo.verify import cmd_verify, read_forge_checks

if TYPE_CHECKING:
    from attavo.types import DriveReport

# --- drive: the mechanical transitions between approval and ready-to-merge
# (issue #84), under the reconciler's R2 (rulings A, B, C's recovery half,
# F, G, H): `attavo drive N` is one short pass. It reads the report
# (attavo.report), acts at most once -- recovers a dead or hung
# invocation, or starts the next stage in a detached worker -- and
# returns within seconds; the stage itself runs in `attavo worker`, a
# child of the pass's own session, which outlives it. Writes no record of
# its own: the stage records already on disk, and the invocation markers
# and heartbeats beside them, are the only state a pass or a worker
# reads. ---


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
    commit just wrote already names. This is the record push's own retry
    -- the worker's, once it has the lease -- distinct from push_lease
    below, which never retries."""
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


_RACE_MARKERS = ("non-fast-forward", "fetch first", "stale info", "cannot lock ref",
                 "failed to update ref")


def push_lease(root: Path, branch: str) -> tuple[str, str | None]:
    """Push HEAD to branch on origin as a plain fast-forward -- no merge,
    no retry: this is the lease the marker commit takes on the attempt it
    names (ruling A). ("pushed", None) on success.

    ("lost-race", stderr) when git's own ref check is what rejected it --
    the branch moved since this pass fast-forwarded to it (a sequential
    "[rejected] ... (fetch first)"), or the remote's own ref transaction
    lost a genuine simultaneous push ("[remote rejected] ... (failed to
    update ref)", "cannot lock ref"): either way, another pass's own
    marker (or record) landed there first. The pass that reads this is
    the loser: it says so, resets its own marker commit, and starts
    nothing.

    ("refused", stderr) for every other rejection -- a remote that
    refuses the push outright (a hook, a permission, an unreachable
    remote). This is not a race for the lease; it is a refusal, and ends
    the pass with exit 1."""
    run = subprocess.run(["git", "-C", str(root), "push", "origin", f"HEAD:{branch}"],
                         capture_output=True, text=True, check=False)
    if run.returncode == 0:
        return "pushed", None
    stderr = run.stderr
    if any(marker in stderr for marker in _RACE_MARKERS):
        return "lost-race", stderr.strip()
    return "refused", stderr.strip()


def has_origin(root: Path) -> bool:
    """Whether this checkout carries an origin remote at all -- a fixture
    or a scratch checkout with none is not a refusal; a pass commits
    locally and skips the push, the way recovery always has."""
    try:
        git(root, "remote", "get-url", "origin")
        return True
    except RuntimeError:
        return False


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


# --- the heartbeat, the worker log, and the forge-checks handoff: local
# facts under the checkout's own git directory (issue #140's precedent),
# never committed and never among the paths commit_record ever names. ---

def heartbeat_s() -> float:
    return float(os.environ.get("ATTAVO_HEARTBEAT_S", 15))


def heartbeat_path(root: Path, stage: str, nn: int) -> Path:
    return stage_events_dir(root) / f"{stage}-{nn:02d}.heartbeat"


def worker_log_path(root: Path, stage: str, nn: int) -> Path:
    return stage_events_dir(root) / f"{stage}-{nn:02d}.worker.log"


def forge_checks_path(root: Path, stage: str, nn: int) -> Path:
    return stage_events_dir(root) / f"{stage}-{nn:02d}.forge-checks.json"


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
    bound: str | None = None  # R3: "stall" or "ceiling" when cls is hung


def read_invocation(root: Path, bound: int | None, marker: Path, info: dict[str, Any]) -> InvocationReading:
    """Whether an invocation that began and wrote no record is running,
    hung, or died -- read from its own heartbeat first (R2): a heartbeat
    file fresh within three beats of ATTAVO_HEARTBEAT_S is alive; a stale
    one is died, whether or not the pid still lives -- a worker stuck
    somewhere that never reaches its own heartbeat loop again is exactly
    what this is for. Only with no heartbeat file at all -- an invocation
    from before this release, or one that died before touching its
    first beat -- does this fall back to the pid: alive and, with a
    stall bound declared, past it is hung; alive with no bound, or
    within it, is running for as long as it lives; not alive is died.
    The stall bound decides hung, never died."""
    stage, nn_text = marker.stem.rsplit("-", 1)
    nn = int(nn_text)
    log, last_at = _events_tail(_events_path_for(root, stage, nn))
    # R3: the stage ceiling, wall clock from the marker, judged before any
    # liveness signal -- alive past it is hung by the ceiling.
    ceiling = ceiling_s(_manifest_of(root), stage)
    since_start = _seconds_since(info.get("at"))
    if ceiling is not None and since_start is not None and since_start > ceiling:
        return InvocationReading(stage, nn, log, last_at, since_start, "hung", "ceiling")
    if not _streams_events(root, _manifest_of(root), stage):
        bound = None
    beat = heartbeat_path(root, stage, nn)
    if beat.is_file():
        try:
            beat_age = time.time() - beat.stat().st_mtime
        except OSError:
            beat_age = None
        cls = "running" if beat_age is not None and beat_age <= heartbeat_s() * 3 else "died"
        return InvocationReading(stage, nn, log, last_at, beat_age, cls)
    age = _seconds_since(last_at or info.get("at"))
    if not invocation_alive(info):
        return InvocationReading(stage, nn, log, last_at, age, "died")
    if bound is None or age is None or age <= bound:
        return InvocationReading(stage, nn, log, last_at, age, "running")
    return InvocationReading(stage, nn, log, last_at, age, "hung", "stall")


def _manifest_of(root: Path) -> dict[str, Any]:
    return read_manifest(root) or {}


def _streams_events(root: Path, m: dict[str, Any], stage: str) -> bool:
    """R3: whether the stage's adapter declares it streams events
    (conformance.json, streams_events); one that does not is judged by
    its ceiling alone, never by event age."""
    name = (m.get("executor") or {}).get(stage) or (m.get("executor") or {}).get("build")
    if not name:
        return True
    for base in (root / "attavo" / "adapters", PRODUCT_ROOT / "adapters"):
        conf = base / name / "conformance.json"
        if conf.is_file():
            try:
                return bool(json.loads(conf.read_text()).get("streams_events", True))
            except (OSError, ValueError):
                return True
    return True


def uncommitted_record(root: Path, records_dir: Path, stage: str) -> Path | None:
    """The latest <stage>-NN.json this working tree carries that git has
    no commit for yet -- the moment between a worker's write and its own
    commit. None when the latest record is already committed, or there
    is none at all."""
    path = latest_attempt_path(records_dir, stage)
    if path is None or not path.is_file():
        return None
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--", str(path.relative_to(root))],
        capture_output=True, text=True, check=False)
    return path if status.stdout.strip() else None


def worker_holds_copy(root: Path, records_dir: Path) -> bool:
    """True when this checkout's own working tree cannot safely be moved
    -- an invocation marker with no record yet (a worker, here or
    elsewhere, still holds this attempt), or a build or verify record the
    working tree carries that git has not committed yet. Scoped to the
    stage records a pass or a worker itself ever transports -- an
    operator's own spec-gate or approve, routinely uncommitted between
    their own act and the next pass, is not this. A pass never
    fast-forwards while this holds (never while a worker holds the
    copy)."""
    if invocations_without_record(records_dir):
        return True
    return (uncommitted_record(root, records_dir, "build") is not None
           or uncommitted_record(root, records_dir, "verify") is not None)


def fast_forward_to_origin(root: Path, m: dict[str, Any], item_id: str) -> None:
    """The pass's own read of origin's branch, taken before it reads
    anything else -- a commit landed there from elsewhere (an approval
    from the screen, a ruling, another pass's own record) is not a race
    for the lease, it is exactly what the lease is for. Never while a
    worker holds this copy (worker_holds_copy): resetting a working tree
    a detached child is still writing into would corrupt its own commits.
    A plain fast-forward when origin is simply ahead; when this checkout
    also carries its own commits origin has not seen yet (an operator's
    approval, not yet pushed), those are replayed on top of origin's own
    tip instead -- no merge commit, and the marker this pass is about to
    write still lands as a fast-forward when it pushes. Silent about
    everything short of that -- a real conflict, or an absent origin, is
    left for the ordinary read (and the marker push, for the lease) to
    sort out."""
    records_dir = root / m["paths"]["records"] / item_id
    if worker_holds_copy(root, records_dir) or not has_origin(root):
        return
    try:
        branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
        git(root, "fetch", "origin", branch)
    except RuntimeError:
        return
    try:
        git(root, "merge", "--ff-only", "FETCH_HEAD")
        return
    except RuntimeError:
        pass
    try:
        git(root, "rebase", "FETCH_HEAD")
    except RuntimeError:
        subprocess.run(["git", "-C", str(root), "rebase", "--abort"],
                       capture_output=True, text=True, check=False)


# --- the worker: a detached child of the pass's own session, running the
# stage exactly as the pass used to inline, touching its own heartbeat
# while it does (R2). ---

def _stage_body(item_id: str, stage: str, nn: int) -> int:
    """Runs build or verify in-process -- the same command `attavo build`
    or `attavo verify` would run -- while touching heartbeat_path every
    heartbeat_s() seconds, then transports whatever it wrote: counsel and
    file-stops on a non-pass record (issue #100, #89), one `git commit
    --only`, one push with the record push's own merge retry. The
    marker is the pass's, already committed and pushed before this
    starts; this writes and transports only the stage's own record (and,
    on a stop, its counsel record)."""
    root = Path(".").resolve()
    m = read_manifest(root)
    if m is None:
        return 2
    records_dir = root / m["paths"]["records"] / item_id
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    before = latest_attempt_path(records_dir, stage)

    beat = heartbeat_path(root, stage, nn)
    beat.parent.mkdir(parents=True, exist_ok=True)
    beat.touch()
    stop_beat = threading.Event()

    def _tick() -> None:
        while not stop_beat.wait(heartbeat_s()):
            beat.touch()

    beat_thread = threading.Thread(target=_tick, daemon=True)
    beat_thread.start()
    try:
        rc = cmd_build([item_id]) if stage == "build" else cmd_verify([item_id])
    finally:
        stop_beat.set()
        beat_thread.join()

    after = latest_attempt_path(records_dir, stage)
    if after is None or after == before:
        # Refused before writing anything at all (e.g. no adapter
        # configured) -- the stage command already printed why.
        return rc if rc != 0 else 1

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
        return 1

    if has_origin(root):
        ok, err = push_record_commit(root, branch)
        if not ok:
            print(f"attavo drive: stopped: could not push the {stage} record: {err}")
            return 1
    else:
        print(f"attavo drive: note: no origin remote; {after.name} is committed, not pushed")

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
    return rc


def cmd_worker(argv: list[str]) -> int:
    """`attavo worker N <build|verify> NN`: the detached child a pass
    starts once its marker is pushed -- the lease held, the attempt is
    this worker's to run. Its own session (start_new_session, set by the
    pass that spawns it); its stdout and stderr are the pass's own
    redirect, to <git-dir>/attavo/<stage>-NN.worker.log."""
    cast(io.TextIOWrapper, sys.stdout).reconfigure(line_buffering=True)
    if len(argv) != 3:
        print("attavo worker: usage: attavo worker N <build|verify> NN", file=sys.stderr)
        return 2
    item_id, stage, nn_text = argv
    if stage not in ("build", "verify"):
        print(f"attavo worker: {stage!r} is not build or verify", file=sys.stderr)
        return 2
    try:
        nn = int(nn_text)
    except ValueError:
        print(f"attavo worker: {nn_text!r} is not an attempt number", file=sys.stderr)
        return 2
    return _stage_body(item_id, stage, nn)


# --- the pass's own two acts: start a stage (the lease), and recover
# exactly the one invocation the report names owed one. ---

def _start_stage(root: Path, m: dict[str, Any], item_id: str, stage: str,
                 forge_checks: dict[str, Any] | None) -> int:
    """The lease (ruling A): the marker, committed and pushed as a plain
    fast-forward with no merge and no retry (push_lease). Lost to another
    pass, or refused outright by the remote, both undo the marker commit
    here (git reset --hard) so this checkout carries no half-taken
    attempt either way; only a pushed lease starts the worker. On a
    review, forge_checks is the checks this same pass already read on
    the pre-marker head (ruling B) -- written to
    <git-dir>/attavo/verify-NN.forge-checks.json and named in
    ATTAVO_FORGE_CHECKS, so the worker's own `attavo verify` reads it
    instead of polling."""
    records_dir = root / m["paths"]["records"] / item_id
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    nn = next_attempt_number(records_dir, stage)
    marker = write_invocation_marker(root, records_dir, stage, nn)
    try:
        commit_record(root, [marker], f"attavo: {stage} invocation {nn:02d} started for item {item_id}")
    except RuntimeError as exc:
        print(f"attavo drive: stopped: could not commit the {stage} invocation marker: {exc}")
        return 1

    if has_origin(root):
        outcome, err = push_lease(root, branch)
        if outcome != "pushed":
            git(root, "reset", "--hard", "HEAD^")
            if outcome == "lost-race":
                print(f"attavo drive: another pass holds this attempt ({marker.name}); nothing started")
                return 0
            print(f"attavo drive: stopped: could not push the {stage} invocation marker: {err}")
            return 1

    env = dict(os.environ)
    if forge_checks is not None:
        checks_file = forge_checks_path(root, stage, nn)
        checks_file.write_text(json.dumps(forge_checks))
        env["ATTAVO_FORGE_CHECKS"] = str(checks_file)
    log = worker_log_path(root, stage, nn)
    with log.open("ab") as fh, open(os.devnull, "rb") as devnull:
        proc = subprocess.Popen(
            [sys.executable, str(PRODUCT_ROOT / "bin" / "attavo"), "worker", item_id, stage, f"{nn:02d}"],
            cwd=str(root), env=env, stdin=devnull, stdout=fh, stderr=subprocess.STDOUT,
            start_new_session=True)
    word = "Build" if stage == "build" else "Review"
    print(f"attavo drive: {word} started (attempt {nn:02d}, worker pid {proc.pid})")
    return 0


def _recover_one(root: Path, m: dict[str, Any], item_id: str, records_dir: Path) -> int:
    """The pass's own recovery act (what `--recover` did): exactly the one
    invocation the report just read as owing one -- died or hung -- ends
    here. class died when the heartbeat is stale (or absent and the pid
    is gone), class hung when there is no heartbeat, the pid lives, and a
    declared [drive].stall_after_s has passed with no newer event, and
    terminated. Provenance product; the events tail as the log. Committed
    and pushed the way any stage record is (push_record_commit, with its
    own merge retry) -- this is not a race for a lease, since no new
    attempt marker is written."""
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    bound = stall_bound_s(m)
    pending = invocations_without_record(records_dir)
    readings = [(marker, info, read_invocation(root, bound, marker, info)) for marker, info in pending]
    candidate = next(((mk, i, rd) for mk, i, rd in readings if rd.cls != "running"), None)
    if candidate is None:
        # The report read this a moment ago; nothing owed recovery by the
        # time this pass got here (another pass recovered it, or it
        # finished on its own).
        return 0
    marker, info, reading = candidate
    stage, nn, log, last_at, age, cls, crossed = reading
    pid, host = info.get("pid"), info.get("host")

    if next_attempt_number(records_dir, stage) != nn:
        print(f"attavo drive: stopped: {marker.name} names attempt {nn} but the next "
              f"{stage} record would be {next_attempt_number(records_dir, stage):02d}; "
              f"a later record already exists, read the records before recovering")
        return 1

    if cls == "hung":
        try:
            os.kill(cast(int, pid), signal.SIGTERM)
            ended = "terminated"
        except OSError as exc:
            ended = f"not terminated ({exc})"
        if crossed == "ceiling":
            detail = (f"pid {pid} on {host} alive {int(cast(float, age))} s after its marker, past the "
                      f"{stage} ceiling of {ceiling_s(m, stage)} s; {ended}")
        else:
            detail = (f"pid {pid} on {host} alive with no event for {int(cast(float, age))} s "
                      f"(bound {bound} s); {ended}")
    else:
        detail = (f"pid {pid} on {host} is not running and wrote no {marker.stem}.json; "
                  f"started {info.get('at')}, last event {last_at or 'none'}")

    schema = stage_schema(stage)
    record: dict[str, Any] = {k: None for k in schema.get("required", [])}
    approval = latest_record(records_dir, "approval")
    record.update({"sha": info.get("sha"), "start_sha": info.get("sha"),
                   "approval_sha": approval.get("sha") if approval else None,
                   "at": info.get("at"),
                   "adapter": (m.get("executor") or {}).get(stage),
                   "status": "failed", "refusal": None, "halted": None,
                   "retry_of": None, "steer": None,
                   "stop": {"class": cls, "provenance": "product", "detail": detail,
                            "signature": signature_of(log.strip().splitlines()[-1] if log.strip() else detail),
                            "bound": crossed}})
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
    if has_origin(root):
        ok, err = push_record_commit(root, branch)
        if not ok:
            print(f"attavo drive: stopped: could not push the {cls} record: {err}")
            return 1
    else:
        print(f"attavo drive: note: no origin remote; {path.name} is committed, not pushed")
    print(f"attavo drive: recovered {marker.stem} as {cls}: {detail}")
    return 0


def _advance_again(root: Path, m: dict[str, Any], item_id: str, stage: str, head: str | None) -> int:
    """`--again`'s own act on a stopped stage: exactly what next-build or
    next-review would do, bypassing the level a plain pass leaves alone
    for the operator. A review reads the forge fresh here -- once, since
    the report's own stopped reading never touched it -- rather than
    reusing a read that was never taken."""
    if stage == "build":
        return _start_stage(root, m, item_id, "build", None)
    checks_command = (m.get("forge") or {}).get("checks_command")
    if not checks_command:
        print("attavo drive: refused: no [forge].checks_command in the manifest")
        return 1
    forge_checks, ok = read_forge_checks(root, checks_command, cast(str, head))
    if not ok:
        conclusions = forge_checks["conclusions"]
        named = ", ".join(f"{c['name']} {c['conclusion']}" for c in conclusions) or "no checks reported"
        print(f"attavo drive: forge checks not green on {cast(str, head)[:12]}: {named}")
        return 0
    return _start_stage(root, m, item_id, "verify", forge_checks)


def _retry_due(root: Path, m: dict[str, Any], item_id: str, r: "DriveReport") -> bool:
    """R3 (stub): a stopped stage whose class is retryable, whose budget is
    not spent, whose last two signatures differ, and -- for transient --
    whose record is older than retry_after_s, is retried by this pass."""
    if r["stop_class"] not in RETRYABLE_CLASSES:
        return False
    records_dir = root / m["paths"]["records"] / item_id
    stage = "build" if r["stop_class"] == "returned" else cast(str, r["stage"])
    spent = next_attempt_number(records_dir, stage) - 1
    if spent >= attempts_budget(m):
        return False
    from attavo.records import parse_attempts
    attempts = parse_attempts(records_dir, cast(str, r["stage"]))
    if len(attempts) >= 2:
        last, prev = attempts[max(attempts)], attempts[sorted(attempts)[-2]]
        sig_last = ((last.get("stop") or {}).get("signature"))
        sig_prev = ((prev.get("stop") or {}).get("signature"))
        if sig_last is not None and sig_last == sig_prev:
            return False
    if r["stop_class"] == "transient":
        rec = attempts[max(attempts)] if attempts else {}
        age = _seconds_since(rec.get("at"))
        if age is not None and age < retry_after_s(m):
            return False
    return True


def _pass_once(root: Path, m: dict[str, Any], item_id: str, again: bool) -> tuple[int, "DriveReport"]:
    """One pass: fast-forward to origin (never while a worker holds the
    copy), read the report, act at most once, return. The three acts,
    one per report state that is the product's move: owes-recovery
    recovers; next-build and next-review start their stage, handing a
    review the checks the report already read once on the pre-marker
    head. --again starts the next attempt of a stopped stage. Every
    other level is printed in the report's own words and left."""
    from attavo.report import drive_report
    fast_forward_to_origin(root, m, item_id)
    r = drive_report(root, m, item_id)

    if r["state"] == "owes-recovery":
        records_dir = root / m["paths"]["records"] / item_id
        return _recover_one(root, m, item_id, records_dir), r
    if r["state"] == "next-build":
        return _start_stage(root, m, item_id, "build", None), r
    if r["state"] == "next-review":
        checks_command = (m.get("forge") or {}).get("checks_command")
        forge_checks = {"command": checks_command, "sha": r["head"], "conclusions": r["checks"] or []}
        return _start_stage(root, m, item_id, "verify", forge_checks), r
    if r["state"] == "stopped" and r["stage"] and _retry_due(root, m, item_id, r):
        stage = "build" if r["stop_class"] == "returned" else r["stage"]
        return _advance_again(root, m, item_id, stage, r["head"]), r
    if r["state"] == "blocked-on-checks" and r["stage"] == "verify":
        # R3: a concluded red check on a passed build's head is a record,
        # not a level -- the review returns it, and the next build is
        # handed the evidence.
        checks_command = (m.get("forge") or {}).get("checks_command")
        forge_checks = {"command": checks_command, "sha": r["head"], "conclusions": r["checks"] or []}
        return _start_stage(root, m, item_id, "verify", forge_checks), r
    if again and r["state"] == "stopped" and r["stage"]:
        return _advance_again(root, m, item_id, r["stage"], r["head"]), r

    print(f"attavo drive: {r['says']}")
    return (1 if r["state"] == "refused" else 0), r


_TERMINAL_STATES = ("waiting-on-approval", "stopped", "ready-to-merge", "merged")


def _until_ready(root: Path, m: dict[str, Any], item_id: str, again: bool) -> int:
    """--until-ready: the pass, repeated in one process, sleeping
    ATTAVO_DRIVE_POLL_INTERVAL_S between passes, until the level is the
    operator's or done (exit 0), a concluded check is red (exit 1), or
    an unconcluded one has not concluded within ATTAVO_DRIVE_POLL_BOUND_S
    (exit 1). A stage running in its own worker is not bounded by this --
    only time spent waiting on the forge is."""
    from attavo.verify import drive_forge_poll_bound_s, drive_forge_poll_interval_s
    bound = drive_forge_poll_bound_s()
    interval = drive_forge_poll_interval_s()
    waiting_since: float | None = None
    while True:
        rc, r = _pass_once(root, m, item_id, again)
        state = r["state"]

        if state == "waiting-on-checks":
            if waiting_since is None:
                waiting_since = time.monotonic()
            elif time.monotonic() - waiting_since >= bound:
                print(f"attavo drive: stopped: the checks did not conclude within {bound:.0f}s")
                return 1
        else:
            waiting_since = None

        if rc != 0:
            return rc
        if state in _TERMINAL_STATES:
            return 0
        if state == "blocked-on-checks":
            return 1
        time.sleep(interval)


def _drive_all() -> int:
    """--all: one pass, in its own process, in every ../attavo-worktree-N
    beside this checkout -- each one derives its own item from its own
    attavo/N-* branch. Every line either prints is prefixed with the
    worktree's own directory name, so an operator reading them together
    can tell which item each line is about."""
    root = Path(".").resolve()
    siblings = sorted(p for p in root.parent.glob("attavo-worktree-*") if p.is_dir())
    if not siblings:
        print("attavo drive: --all found no ../attavo-worktree-N beside this checkout")
        return 0
    worst = 0
    for copy in siblings:
        run = subprocess.run([sys.executable, str(PRODUCT_ROOT / "bin" / "attavo"), "drive"],
                             cwd=str(copy), capture_output=True, text=True, check=False)
        for line in (run.stdout + run.stderr).splitlines():
            print(f"{copy.name}: {line}")
        worst = max(worst, run.returncode)
    return worst


def cmd_drive(argv: list[str]) -> int:
    # Issue #145: Python block-buffers stdout when it is not a tty (a
    # pipe, e.g. a screen reading this process live), so every line this
    # prints would otherwise sit unflushed until the process exits.
    # Line-buffering makes a pipe reader see each line the moment it is
    # printed, the same as a terminal already does; `attavo worker`
    # reconfigures its own stdout the same way.
    cast(io.TextIOWrapper, sys.stdout).reconfigure(line_buffering=True)

    if "--recover" in argv:
        print("attavo drive: --recover is gone; a pass recovers a dead or hung invocation "
              "itself, as soon as the report owes one", file=sys.stderr)
        return 2

    until_ready = "--until-ready" in argv
    report = "--report" in argv
    again = "--again" in argv
    all_flag = "--all" in argv
    positional = [a for a in argv if not a.startswith("--")]

    if all_flag:
        if until_ready or report or again or positional:
            print("attavo drive: --all runs one pass in every worktree beside this checkout; "
                  "drop N and every other flag", file=sys.stderr)
            return 2
        return _drive_all()

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

    if report:
        if until_ready or again:
            print("attavo drive: --report reads and does nothing else; drop --until-ready "
                  "and --again", file=sys.stderr)
            return 2
        fast_forward_to_origin(root, m, item_id)
        from attavo.report import drive_report
        print(json.dumps(drive_report(root, m, item_id), indent=2))
        return 0

    if until_ready:
        return _until_ready(root, m, item_id, again)
    rc, _ = _pass_once(root, m, item_id, again)
    return rc
