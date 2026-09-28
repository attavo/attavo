"""attavo drive: the mechanical transitions between approval and
ready-to-merge (issue #84) -- under R2, one pass per invocation: read the
report, act at most once (recover, start the build, start the review) in
a detached worker, return. A level that belongs to a person is printed
and left. Every test uses the fixture
adapter and a fake `[forge].checks_command`; every record commit is
`git commit --only` and pushed to a real (bare) fixture remote.

Regression tests, written and run red first: on `main` at 85f6f3b there
was no `drive` subcommand, so every one of these failed with "unknown
subcommand".

Run: python3 -m pytest tests -q
"""
import importlib
import json
import os
import socket
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import IO, Any, cast

from helpers import commit_all, git, make_tenant

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"

GREEN_FORGE = "printf 'ci success\\n'"
# A concluded conclusion -- checked and failing, not something still
# running. Distinct from UNCONCLUDED_FORGE below (issue #84): drive polls
# the one still running for the bound, but stops on the first read of a
# check that has already concluded, whether or not it passed.
RED_FORGE = "printf 'ci failure\\n'"
UNCONCLUDED_FORGE = "printf 'ci in_progress\\n'"

# The real defaults (ATTAVO_DRIVE_POLL_BOUND_S=600, _INTERVAL_S=20) are a
# real forge's clock, not a test's -- tenant zero's own checks conclude
# about five minutes after a push. Every `attavo drive` call here runs
# against this much shorter clock instead, read at call time from the
# same environment variables; test_drive_forge_poll_defaults_are_a_real_
# forges_clock is the one test that leaves them unset, to assert the
# defaults themselves. The interval is kept well above typical subprocess
# startup overhead so a zero-sleep stop and a polled one are easy to tell
# apart by wall clock alone.
FAST_POLL_ENV = {"ATTAVO_DRIVE_POLL_BOUND_S": "0.9", "ATTAVO_DRIVE_POLL_INTERVAL_S": "0.5",
                 "ATTAVO_HEARTBEAT_S": "0.2"}


def run_attavo(root: Path, *args: str, env: dict[str, Any] | None = None) -> subprocess.CompletedProcess[str]:
    full_env = dict(os.environ)
    full_env.update(FAST_POLL_ENV)
    if env:
        full_env.update(env)
    return subprocess.run([sys.executable, str(ATTAVO), *args], cwd=str(root),
                          capture_output=True, text=True, check=False, env=full_env)


def drive(root: Path, *args: str, env: dict[str, Any] | None = None) -> subprocess.CompletedProcess[str]:
    """One pass, then wait for the worker it may have started (R2): the pass
    returns as soon as the marker is pushed and the stage runs detached, so
    a test that reads the record after "drive" waits for the report to stop
    saying running. The pass's own output is returned, with the worker's
    log appended, so assertions on the stage's lines still read them."""
    run = run_attavo(root, "drive", *args, env=env)
    import re
    pids = [int(x) for x in re.findall(r"worker pid (\d+)", run.stdout)]
    deadline = time.monotonic() + 90
    while pids and time.monotonic() < deadline:
        try:
            os.kill(pids[-1], 0)
        except ProcessLookupError:
            break
        time.sleep(0.2)
    logs = sorted(Path(git(root, "rev-parse", "--absolute-git-dir").strip(), "attavo").glob("*.worker.log"))
    tail = "".join(p.read_text() for p in logs)
    return subprocess.CompletedProcess(run.args, run.returncode, run.stdout + tail, run.stderr)


def attavo_module() -> Any:
    """attavo.verify imported in-process, for the default-constant assertion
    below that a CLI invocation cannot provoke without actually waiting
    out the real clock."""
    return importlib.import_module("attavo.verify")


def add_forge(root: Path, checks_command: str) -> None:
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text() + f'\n[forge]\nchecks_command = "{checks_command}"\n')
    commit_all(root, "configure the forge read")


def approve_item(root: Path, item: str = "1") -> None:
    assert run_attavo(root, "spec-gate", item).returncode == 0
    assert run_attavo(root, "approve", item, "--by", "alice").returncode == 0


def add_bare_remote(root: Path, tmp_path: Path) -> Path:
    """A real (bare) remote named origin, seeded with the tenant's current
    state -- drive's own push target, distinct from the fake
    refs/remotes/origin/* helpers.init_repo sets up purely for verify's
    diff base."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--quiet", "--bare", "-b", "main", str(bare)], check=True)
    git(root, "remote", "add", "origin", str(bare))
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    git(root, "push", "origin", f"HEAD:{branch}")
    return bare


def records_dir(root: Path, item: str = "1") -> Path:
    return root / "records" / item


def record_files(root: Path, stage: str, item: str = "1") -> list[Path]:
    return sorted(records_dir(root, item).glob(f"{stage}-*.json"))


def latest_record(root: Path, stage: str, item: str = "1") -> dict[str, Any]:
    files = record_files(root, stage, item)
    assert files, f"no {stage} record for item {item}"
    return cast(dict[str, Any], json.loads(files[-1].read_text()))


def head_of(root: Path) -> str:
    return git(root, "rev-parse", "HEAD").strip()


def last_commit_paths(root: Path) -> list[str]:
    return git(root, "diff", "--name-only", "HEAD^", "HEAD").splitlines()


def remote_head(bare: Path, branch: str = "main") -> str:
    return subprocess.run(["git", "-C", str(bare), "rev-parse", branch],
                          capture_output=True, text=True, check=True).stdout.strip()


def add_drive_stall_bound(root: Path, seconds: int = 60) -> None:
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text() + f'\n[drive]\nstall_after_s = {seconds}\n')
    commit_all(root, "declare the stall bound")


def _dead_pid() -> int:
    """A pid guaranteed not to be running: a real child, spawned and
    reaped, on this host."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def write_dead_marker(root: Path, stage: str, nn: int, item: str = "1") -> Path:
    """A <stage>-NN.started marker as a dead invocation would have left
    it, committed the way `attavo drive` itself commits one -- but never
    followed by its own record."""
    d = records_dir(root, item)
    d.mkdir(parents=True, exist_ok=True)
    marker = d / f"{stage}-{nn:02d}.started"
    marker.write_text(json.dumps({
        "at": "2026-09-17T13:16:08Z", "host": socket.gethostname(), "pid": _dead_pid(),
        "sha": head_of(root), "product_version": "fixture"}))
    git(root, "add", str(marker.relative_to(root)))
    git(root, "commit", "-q", "-m", f"invocation marker for item {item}")
    return marker



def test_drive_pass_waits_on_an_absent_approval(tmp_path: Path) -> None:
    """Acceptance row 6, as a level (R2): with no approval the pass says
    the item is waiting on the operator, exits 0 because a level is not a
    refusal, and runs nothing -- no spec-gate, no build, no records."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    add_bare_remote(root, tmp_path)

    run = drive(root, "1")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "approve the spec" in run.stdout
    assert "worker pid" not in run.stdout

    assert not records_dir(root).exists() or list(records_dir(root).glob("*.json")) == []



def test_drive_pass_waits_on_an_expired_approval(tmp_path: Path) -> None:
    """Acceptance row 6, the other half: an approval bound to a spec sha
    that has since moved is expired, not absent -- the pass says so, by
    that word, exits 0, and starts nothing."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    add_bare_remote(root, tmp_path)
    approve_item(root)

    (root / "specs" / "1" / "spec.md").write_text(
        (root / "specs" / "1" / "spec.md").read_text() + "\n")
    commit_all(root, "touch the spec after approval")

    run = drive(root, "1")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "expired" in run.stdout
    assert "worker pid" not in run.stdout
    assert record_files(root, "build") == []


def test_drive_builds_from_an_approved_spec_with_no_build(tmp_path: Path) -> None:
    """Acceptance row 1. One drive run produces a passing build record,
    commits only the record and its log (`git commit --only`), and pushes
    the branch; the fixture remote carries the record commit."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    bare = add_bare_remote(root, tmp_path)
    approve_item(root)
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()

    run = drive(root, "1")
    assert run.returncode == 0, run.stdout + run.stderr

    rec = latest_record(root, "build")
    assert rec["status"] == "pass"

    record_path = record_files(root, "build")[-1]
    log_path = record_path.with_suffix(".log")
    assert log_path.exists()

    touched = set(last_commit_paths(root))
    assert touched == {
        str(record_path.relative_to(root)),
        str(log_path.relative_to(root)),
    }

    assert remote_head(bare, branch) == head_of(root)


def test_drive_leaves_the_events_file_out_of_the_record_commit(tmp_path: Path) -> None:
    """Issue #26, acceptance row 2, strengthened by issue #140: attavo
    build always writes a stage's events file now (test_build.py's own
    row-1 tests), under the checkout's own git directory rather than
    the working tree -- so it is not merely excluded from the commit's
    file list, it is not even a path git tracks anything under. The
    commit's file list still names only the record and its log.
    """
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    add_bare_remote(root, tmp_path)
    approve_item(root)

    run = drive(root, "1")
    assert run.returncode == 0, run.stdout + run.stderr

    record_path = record_files(root, "build")[-1]
    git_dir = git(root, "rev-parse", "--absolute-git-dir").strip()
    events_path = Path(git_dir) / "attavo" / "build-01.events.jsonl"
    assert events_path.is_file()

    touched = set(last_commit_paths(root))
    assert touched == {
        str(record_path.relative_to(root)),
        str(record_path.with_suffix(".log").relative_to(root)),
    }


def test_drive_commits_the_record_as_the_driver_not_the_tenants_own_identity(tmp_path: Path) -> None:
    """The driver's identity (driver_identity, beside executor_identity):
    set by the product, never the tenant's git config, so a driven item's
    record commits are never indistinguishable from an operator's own.
    The fixture tenant's user.name is set to another value here, so a
    commit carrying it would prove commit_record fell back to the
    tenant's git config instead."""
    root = make_tenant(tmp_path, "build_valid")
    git(root, "config", "user.name", "Some Operator")
    git(root, "config", "user.email", "operator@example.com")
    add_forge(root, GREEN_FORGE)
    add_bare_remote(root, tmp_path)
    approve_item(root)

    run = drive(root, "1")
    assert run.returncode == 0, run.stdout + run.stderr

    author_name = git(root, "log", "-1", "--format=%an").strip()
    author_email = git(root, "log", "-1", "--format=%ae").strip()
    committer_name = git(root, "log", "-1", "--format=%cn").strip()
    committer_email = git(root, "log", "-1", "--format=%ce").strip()

    assert author_name == "attavo driver"
    assert author_email == "driver@attavo.invalid"
    assert committer_name == "attavo driver"
    assert committer_email == "driver@attavo.invalid"


def test_drive_verifies_from_a_passed_build_with_the_forge_green(tmp_path: Path) -> None:
    """Acceptance row 2. From a passed build with the fake forge green,
    one run produces a verify record, commits only the record and its log,
    and pushes."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    bare = add_bare_remote(root, tmp_path)
    approve_item(root)
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()

    assert drive(root, "1").returncode == 0  # builds

    run = drive(root, "1")
    assert run.returncode == 0, run.stdout + run.stderr

    assert record_files(root, "build") and record_files(root, "verify")
    rec = latest_record(root, "verify")
    assert rec["status"] == "pass"

    record_path = record_files(root, "verify")[-1]
    log_path = record_path.with_suffix(".log")
    assert log_path.exists()

    touched = set(last_commit_paths(root))
    assert touched == {
        str(record_path.relative_to(root)),
        str(log_path.relative_to(root)),
    }

    assert remote_head(bare, branch) == head_of(root)


def test_drive_verifies_again_when_the_latest_verify_is_superseded_by_a_later_build(tmp_path: Path) -> None:
    """Acceptance row 4, issue #109. A verify record can only name a build
    that existed when it was written, so a later build makes the latest
    verify superseded, never a mismatch -- drive derives the next stage
    as verify, not build, and never refuses.

    Regression: on main before this lane, drive_state's own inline
    staleness check already treats this as 'verify', so this test does
    not fail against current code -- it is here to lock the behavior
    check_attest_chain's fix must not disturb (the acceptance table's
    'never refuses' row)."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    add_bare_remote(root, tmp_path)
    approve_item(root)

    assert drive(root, "1").returncode == 0  # builds
    assert drive(root, "1").returncode == 0  # verifies

    # A second build record, minted by hand -- a real re-run of the
    # fixture adapter is not idempotent against its own tree -- naming a
    # sha the existing verify-01 does not: verify-01 is now superseded.
    build_01 = json.loads(record_files(root, "build")[-1].read_text())
    build_01["sha"] = "f" * 40
    (records_dir(root) / "build-02.json").write_text(json.dumps(build_01))
    commit_all(root, "attavo: second build record for #1, minted by hand")

    run = drive(root, "1")
    assert run.returncode == 0, run.stdout + run.stderr
    assert len(record_files(root, "build")) == 2
    assert len(record_files(root, "verify")) == 2
    rec = latest_record(root, "verify")
    assert rec["status"] == "pass"
    assert rec["build_sha"] == build_01["sha"]



def test_drive_prints_ready_to_merge_from_a_passed_verify(tmp_path: Path) -> None:
    """Acceptance row 3. From a passed verify with the forge green, one
    pass prints ready to merge, exits 0, writes nothing, enqueues nothing
    -- and, per row 8, a further pass repeats exactly the same thing."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    add_bare_remote(root, tmp_path)
    approve_item(root)

    assert drive(root, "1").returncode == 0  # builds
    assert drive(root, "1").returncode == 0  # verifies

    pre_head = head_of(root)
    run = drive(root, "1")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "ready to merge" in run.stdout.lower()
    assert head_of(root) == pre_head
    assert record_files(root, "verify") and len(record_files(root, "verify")) == 1

    # Acceptance row 8: idempotence -- run it again, same result, nothing
    # new written or pushed.
    run2 = drive(root, "1")
    assert run2.returncode == 0
    assert "ready to merge" in run2.stdout.lower()
    assert head_of(root) == pre_head
    assert len(record_files(root, "verify")) == 1
    assert len(record_files(root, "build")) == 1



def test_drive_until_ready_runs_build_then_verify_to_ready_in_one_call(tmp_path: Path) -> None:
    """Issue #100's own followups, kept under R2: --until-ready repeats
    the pass in one process until the level is the operator's or done.
    One call, from an approved item with the forge green, starts the
    build, waits it out, starts the review, waits it out, and prints
    ready to merge -- both records written and pushed."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    bare = add_bare_remote(root, tmp_path)
    approve_item(root)
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()

    run = run_attavo(root, "drive", "1", "--until-ready")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "ready to merge" in run.stdout.lower()

    assert len(record_files(root, "build")) == 1
    assert len(record_files(root, "verify")) == 1
    assert latest_record(root, "build")["status"] == "pass"
    assert latest_record(root, "verify")["status"] == "pass"
    assert remote_head(bare, branch) == head_of(root)

    # Idempotence, same as the single-pass form: a further --until-ready
    # call repeats exactly the same ready-to-merge result, writing nothing.
    run2 = run_attavo(root, "drive", "1", "--until-ready")
    assert run2.returncode == 0
    assert "ready to merge" in run2.stdout.lower()
    assert len(record_files(root, "build")) == 1
    assert len(record_files(root, "verify")) == 1


def test_drive_without_the_flag_still_takes_one_step_at_a_time(tmp_path: Path) -> None:
    """Regression: --until-ready is additive. With no flag, an approved
    item with the forge green still needs two separate calls -- build,
    then verify -- exactly as before this issue."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    add_bare_remote(root, tmp_path)
    approve_item(root)

    run = drive(root, "1")
    assert run.returncode == 0
    assert record_files(root, "build") and not record_files(root, "verify")

    run2 = drive(root, "1")
    assert run2.returncode == 0
    assert record_files(root, "verify")



def test_drive_until_ready_stops_on_a_halt(tmp_path: Path) -> None:
    """A stage that stops on its own account -- here, a halted build --
    ends the --until-ready loop at a level that is the operator's: the
    loop prints the stopped line the plain pass would and exits 0; it
    does not start the next attempt on its own."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    bare = add_bare_remote(root, tmp_path)
    approve_item(root)
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()

    run = run_attavo(root, "drive", "1", "--until-ready",
                     env={"ATTAVO_FIXTURE_QUESTION": "which overlay wins?"})
    assert run.returncode == 0, run.stdout + run.stderr
    assert "Needs you" in run.stdout and "stopped" in run.stdout

    assert len(record_files(root, "build")) == 1
    assert latest_record(root, "build")["status"] == "halted"
    assert record_files(root, "verify") == []
    assert remote_head(bare, branch) == head_of(root)



def retry_friendly_adapter(root: Path) -> None:
    """R3: a build the fixture adapter already passed once carries
    built.txt already; a plain retry of that same build must still have
    something to commit, so this tenant-side wrapper rewrites built.txt
    with a fresh timestamp before delegating to the product's own
    fixture adapter."""
    d = root / "attavo" / "adapters" / "fixture"
    d.mkdir(parents=True, exist_ok=True)
    (d / "run").write_text(
        "#!/bin/sh\n"
        'if [ "${ATTAVO_STAGE:-build}" = build ] && [ -f built.txt ]; then\n'
        '  echo "built again $(date +%s%N)" > built.txt\n'
        "  git add built.txt\n"
        "  git commit -q -m 'fixture: write built.txt again'\n"
        f'  echo \'{{"status":"done","question":null,"model":"fixture","model_version":null,'
        '"adapter_version":"fixture-1","tokens":{"input":1,"output":1},"cost_usd":0.0}\'\n'
        "  exit 0\n"
        "fi\n"
        f"exec sh {ROOT / 'adapters' / 'fixture' / 'run'}\n")
    (d / "run").chmod(0o755)
    commit_all(root, "a retry-friendly tenant adapter")


def test_drive_until_ready_stops_after_two_matching_returned_reviews(tmp_path: Path) -> None:
    """R3 (ruling C amended): a build that passes, then a forge that
    concludes red on its head every time, is no longer a dead end the
    loop gives up on -- the pass converts it into a returned record and
    retries the build on its own, inside the budget. Two returned
    reviews with the identical signature then stop the stage: the loop
    reads that as done (a person's to look at, not a forge it never
    finished reading), and exits 0."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, RED_FORGE)
    retry_friendly_adapter(root)
    add_bare_remote(root, tmp_path)
    approve_item(root)

    run = run_attavo(root, "drive", "1", "--until-ready")
    assert run.returncode == 0, run.stdout + run.stderr

    assert len(record_files(root, "build")) == 2
    assert [r["status"] for r in (latest_record(root, "build"),)] == ["pass"]
    assert len(record_files(root, "verify")) == 2
    for rec in (json.loads(p.read_text()) for p in record_files(root, "verify")):
        assert rec["status"] == "returned" and rec["stop"]["class"] == "returned"
    out = json.loads(run_attavo(root, "drive", "1", "--report").stdout)
    assert out["state"] == "stopped" and out["signatures_match"] is True, out



def test_drive_line_buffers_stdout_so_a_pipe_reader_sees_the_start_line_before_the_stage_ends(tmp_path: Path) -> None:
    """Issue #145, kept under R2. A pipe reader of `attavo drive` saw
    every line arrive as one burst when the process ended -- Python
    block-buffers stdout when it is not a tty. With --until-ready the
    process now outlives a slow stage; a pipe reader with a timeout well
    under the stage's own sleep must already see the "started" line the
    pass printed before that sleep ends -- unreachable if stdout is
    still block-buffered. The worker reconfigures its own stdout the same
    way, so its log is readable while it runs.
    """
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    add_bare_remote(root, tmp_path)
    approve_item(root)

    sleep_s = 3.0
    adapter_dir = root / "attavo" / "adapters" / "slow"
    adapter_dir.mkdir(parents=True)
    adapter_run = adapter_dir / "run"
    adapter_run.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        'if [ "${ATTAVO_STAGE:-build}" = "verify" ]; then\n'
        '  printf \'{"model":"slow","model_version":null,"adapter_version":"slow-1",'
        '"tokens":{"input":1,"output":1},"cost_usd":0.0,"findings":[],"tests_named":[]}\\n\'\n'
        "  exit 0\n"
        "fi\n"
        f"sleep {sleep_s}\n"
        "echo built > built.txt\n"
        "git add built.txt\n"
        "git commit -q -m 'slow: write built.txt'\n"
        'echo \'{"status":"done","question":null,"model":"slow","model_version":null,'
        '"adapter_version":"slow-1","tokens":{"input":1,"output":1},"cost_usd":0.0}\'\n'
    )
    adapter_run.chmod(0o755)
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text().replace('build = "fixture"', 'build = "slow"'))
    commit_all(root, "use a slow adapter for build")

    full_env = dict(os.environ)
    full_env.update(FAST_POLL_ENV)
    proc = subprocess.Popen(
        [sys.executable, str(ATTAVO), "drive", "1", "--until-ready"],
        cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, env=full_env)

    lines: "queue.Queue[str | None]" = queue.Queue()

    def reader() -> None:
        for raw in cast(IO[str], proc.stdout):
            lines.put(raw)
        lines.put(None)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()

    try:
        first_line = lines.get(timeout=sleep_s / 2)
    except queue.Empty:
        proc.kill()
        raise
    assert first_line is not None and "Build started" in first_line, first_line

    collected = [first_line]
    while True:
        line = lines.get(timeout=sleep_s + 30)
        if line is None:
            break
        collected.append(line)
    thread.join(timeout=5)
    proc.wait(timeout=5)

    output = "".join(collected)
    assert proc.returncode == 0, output
    assert "ready to merge" in output.lower()



def test_drive_pass_turns_a_red_forge_on_a_passed_build_into_a_returned_record(tmp_path: Path) -> None:
    """R3 (ruling C amended, #206's own build-01): a forge read where the
    one required check has already concluded and failed is a record now,
    not a level with nothing behind it -- the pass starts the review,
    which writes verify-01 status returned, class returned, naming the
    check in its signature. Inside the budget, the very next pass
    retries the build on its own -- no operator act, no counsel run."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, RED_FORGE)
    retry_friendly_adapter(root)
    add_bare_remote(root, tmp_path)
    approve_item(root)

    assert drive(root, "1").returncode == 0  # builds
    pre_head = head_of(root)

    run = drive(root, "1")
    assert run.returncode == 0, run.stdout + run.stderr
    assert head_of(root) != pre_head

    assert len(record_files(root, "verify")) == 1
    review = latest_record(root, "verify")
    assert review["status"] == "returned", review
    assert review["stop"]["class"] == "returned" and "ci" in review["stop"]["signature"], review["stop"]

    run2 = drive(root, "1")
    assert run2.returncode == 0, run2.stdout + run2.stderr
    assert len(record_files(root, "build")) == 2
    assert latest_record(root, "build")["retry_of"] == "build-01"
    assert record_files(root, "counsel") == []



def test_drive_until_ready_waits_out_the_bound_on_a_forge_still_running(tmp_path: Path) -> None:
    """Acceptance row 4, the unconcluded case, under R2: a check still
    carrying a status (in_progress) is not red. The pass reads it once
    and returns at once saying Waiting on the checks; the waiting is
    --until-ready's, which repeats the pass for the forge bound and then
    gives up with exit 1, naming the check. Nothing is written either
    way."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, UNCONCLUDED_FORGE)
    add_bare_remote(root, tmp_path)
    approve_item(root)

    assert drive(root, "1").returncode == 0  # builds

    started = time.monotonic()
    run = drive(root, "1")
    elapsed = time.monotonic() - started
    assert run.returncode == 0, run.stdout + run.stderr
    assert "Waiting on the checks" in run.stdout and "in_progress" in run.stdout
    assert elapsed < 0.4, f"a pass reads the forge once and returns -- took {elapsed:.2f}s"

    started = time.monotonic()
    run2 = run_attavo(root, "drive", "1", "--until-ready")
    elapsed = time.monotonic() - started
    assert run2.returncode == 1, run2.stdout + run2.stderr
    assert "in_progress" in run2.stdout and "did not conclude" in run2.stdout
    assert elapsed >= 0.9, (
        f"--until-ready should repeat the pass for the bound before giving up "
        f"-- took only {elapsed:.2f}s")

    assert record_files(root, "verify") == []



def test_drive_halts_on_a_question_and_again_resumes_at_build_after_a_ruling(tmp_path: Path) -> None:
    """Acceptance row 5, under R2. The fixture adapter halts with a
    question: the worker writes the halted record naming it and pushes
    it, same as any other record. After a ruling is appended, a plain
    pass leaves the stopped stage alone (the level is the operator's
    until rulings are records, R4); `--again` starts the next attempt,
    which this time passes."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    bare = add_bare_remote(root, tmp_path)
    approve_item(root)
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()

    run = drive(root, "1", env={"ATTAVO_FIXTURE_QUESTION": "which overlay wins?"})
    assert run.returncode == 0, run.stdout + run.stderr

    rec = latest_record(root, "build")
    assert rec["status"] == "halted"
    assert rec["halted"] == {"question": "which overlay wins?"}
    record_path = record_files(root, "build")[-1]
    assert f"halted ({record_path.name})" in run.stdout

    # The halted record was still committed and pushed.
    assert remote_head(bare, branch) == head_of(root)

    rulings = root / "rulings.md"
    rulings.write_text(rulings.read_text() + "\n- overlay: use the base tree\n"
                       if rulings.exists() else "- overlay: use the base tree\n")
    commit_all(root, "rule on the overlay question")
    git(root, "push", "origin", f"HEAD:{branch}")

    left = drive(root, "1")
    assert left.returncode == 0, left.stdout + left.stderr
    assert "Needs you" in left.stdout
    assert len(record_files(root, "build")) == 1

    run2 = drive(root, "1", "--again", "--by", "alice")
    assert run2.returncode == 0, run2.stdout + run2.stderr

    assert len(record_files(root, "build")) == 2
    rec2 = latest_record(root, "build")
    assert rec2["status"] == "pass"
    assert remote_head(bare, branch) == head_of(root)


def configure_counsel(root: Path) -> None:
    """[executor].counsel = "fixture" -- build_valid's own manifest
    declares [executor].build = "fixture" only; counsel needs its own
    adapter named (issue #100)."""
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text().replace(
        '[executor]\nbuild = "fixture"', '[executor]\nbuild = "fixture"\ncounsel = "fixture"'))
    commit_all(root, "configure the counsel adapter")


def test_drive_counsels_a_stop_before_filing_it_and_commits_both_records_together(tmp_path: Path) -> None:
    """Issue #100, acceptance row 3. On a stop, drive calls counsel
    before file-stops, and commits the counsel record with the stage
    record in one commit."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    bare = add_bare_remote(root, tmp_path)
    configure_counsel(root)
    approve_item(root)
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    filed = tmp_path / "filed.jsonl"
    configure_filing(root, filed)

    run = drive(root, "1", env={"ATTAVO_FIXTURE_QUESTION": "which overlay wins?"})
    assert run.returncode == 0, run.stdout + run.stderr

    assert record_files(root, "counsel")
    counsel_rec = latest_record(root, "counsel")
    assert counsel_rec["record_stage"] == "build"
    assert counsel_rec["record"] == "build-01"
    assert counsel_rec["verdict"] == "person"

    committed = set(last_commit_paths(root))
    assert committed == {
        "records/1/build-01.json", "records/1/build-01.log",
        "records/1/counsel-01.json", "records/1/counsel-01.log",
        "records/1/build-01.filed",
    }
    assert remote_head(bare, branch) == head_of(root)

    filed_bodies = [json.loads(l) for l in filed.read_text().splitlines() if l.strip()]
    assert len(filed_bodies) == 1
    assert filed_bodies[0]["counsel_verdict"] == "person"
    assert filed_bodies[0]["counsel_proposal"]["question"]


def test_drive_still_files_a_stop_with_no_counsel_adapter_configured(tmp_path: Path) -> None:
    """counsel refuses gracefully (no [executor].counsel or
    [executor].verify) -- drive still files and transports the stage
    record; nothing new for counsel to commit."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    bare = add_bare_remote(root, tmp_path)
    approve_item(root)
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    filed = tmp_path / "filed.jsonl"
    configure_filing(root, filed)

    run = drive(root, "1", env={"ATTAVO_FIXTURE_QUESTION": "which overlay wins?"})
    assert run.returncode == 0, run.stdout + run.stderr

    assert record_files(root, "counsel") == []
    committed = set(last_commit_paths(root))
    assert committed == {"records/1/build-01.json", "records/1/build-01.log",
                         "records/1/build-01.filed"}
    assert remote_head(bare, branch) == head_of(root)


def configure_filing(root: Path, filed_path: Path) -> None:
    """Appended under the [forge] table add_forge already opened -- a
    second [forge] header in the same file is a TOML redeclaration
    error. Prints a fake issue URL on stdout, numbered by filed_path's
    own line count so far -- the fact a real file_issue_command's stdout
    would carry, and what the .filed marker beside each stop record is
    meant to hold (issue #89)."""
    toml = root / "attavo.toml"
    command = (f"n=$(wc -l < {filed_path} 2>/dev/null || echo 0); "
              f"cat >> {filed_path}; "
              f"echo https://github.com/tenant/repo/issues/$((n + 1))")
    toml.write_text(toml.read_text() + f"\nfile_issue_command = '{command}'\n")
    commit_all(root, "configure issue filing")



def test_drive_files_a_stop_it_reports_exactly_once(tmp_path: Path) -> None:
    """Issue #89, acceptance row 5. A stop the worker reports -- here,
    the fixture adapter's halt -- calls file-stops once: one attempt
    files exactly one issue for the record it just wrote, not zero and
    not more than one."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    add_bare_remote(root, tmp_path)
    approve_item(root)
    filed = tmp_path / "filed.jsonl"
    configure_filing(root, filed)

    run = drive(root, "1", env={"ATTAVO_FIXTURE_QUESTION": "which overlay wins?"})
    assert run.returncode == 0, run.stdout + run.stderr

    filed_bodies = [json.loads(l) for l in filed.read_text().splitlines() if l.strip()]
    assert len(filed_bodies) == 1
    assert filed_bodies[0]["record"] == "records/1/build-01.json"

    # The next attempt (`--again`) halts again -- build-02 -- and files
    # exactly one more issue for that new record; file-stops' own .filed
    # marker means build-01's issue is never filed twice.
    run2 = drive(root, "1", "--again", "--by", "alice", env={"ATTAVO_FIXTURE_QUESTION": "which overlay wins?"})
    assert run2.returncode == 0, run2.stdout + run2.stderr
    assert len(record_files(root, "build")) == 2

    filed_bodies_after = [json.loads(l) for l in filed.read_text().splitlines() if l.strip()]
    assert len(filed_bodies_after) == 2
    assert filed_bodies_after[0] == filed_bodies[0]
    assert filed_bodies_after[1]["record"] == "records/1/build-02.json"


def test_drive_marker_holds_the_filing_commands_stdout_and_is_committed_with_the_record(tmp_path: Path) -> None:
    """A stop's own .filed marker is written before the record is
    transported, so it carries the filing command's stdout (the issue it
    opened) and rides in the very same commit as the record and its log
    -- one commit, one push, as before this fix. Red before it: the
    marker was written empty, after commit_record and push_record_commit
    had already run, so it was never part of any commit at all.
    """
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    bare = add_bare_remote(root, tmp_path)
    approve_item(root)
    filed = tmp_path / "filed.jsonl"
    configure_filing(root, filed)

    run = drive(root, "1", env={"ATTAVO_FIXTURE_QUESTION": "which overlay wins?"})
    assert run.returncode == 0, run.stdout + run.stderr

    marker_path = records_dir(root) / "build-01.filed"
    assert marker_path.is_file()
    assert marker_path.read_text().strip() == "https://github.com/tenant/repo/issues/1"

    committed = set(last_commit_paths(root))
    assert committed == {"records/1/build-01.json", "records/1/build-01.log",
                         "records/1/build-01.filed"}
    assert remote_head(bare) == head_of(root)


def test_drive_a_second_run_in_the_same_state_files_nothing(tmp_path: Path) -> None:
    """Issue #89. The marker committed and pushed above is the fact a
    second worktree or a fresh clone reads too -- a second drive run
    against the same records, from a fresh clone of the remote drive
    already pushed to, files nothing for the stop that clone already
    carries a marker for. Red before this fix: the marker never reached
    the remote at all, so every clone refiled every stop it found.
    """
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    bare = add_bare_remote(root, tmp_path)
    approve_item(root)
    filed = tmp_path / "filed.jsonl"
    configure_filing(root, filed)

    run = drive(root, "1", env={"ATTAVO_FIXTURE_QUESTION": "which overlay wins?"})
    assert run.returncode == 0, run.stdout + run.stderr
    filed_after_first = filed.read_text()
    assert filed_after_first.strip() != ""

    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    clone = tmp_path / "fresh-clone"
    subprocess.run(["git", "clone", "--quiet", "--branch", branch, str(bare), str(clone)],
                   check=True)

    run2 = run_attavo(clone, "file-stops", "1")
    assert run2.returncode == 0
    assert "no stops to file" in run2.stdout
    assert filed.read_text() == filed_after_first  # nothing new was filed



def slow_build_adapter(root: Path, seconds: float) -> None:
    """A tenant-side build adapter: the product's own fixture adapter,
    after `seconds` of sleep on the build stage -- long enough for a test
    to act between the pass's marker push and the worker's record push."""
    d = root / "attavo" / "adapters" / "fixture"
    d.mkdir(parents=True, exist_ok=True)
    run = d / "run"
    run.write_text(f'#!/bin/sh\nif [ "${{ATTAVO_STAGE:-build}}" = build ]; then sleep {seconds}; fi\n'
                   f'exec sh {ROOT / "adapters" / "fixture" / "run"}\n')
    run.chmod(0o755)
    commit_all(root, "a slow tenant-side adapter")


def test_drive_pass_fast_forwards_to_a_branch_that_moved_before_taking_the_lease(tmp_path: Path) -> None:
    """R2. A commit landed on the branch from elsewhere before the pass
    -- an approval from the screen, a ruling, another pass's record -- is
    not a race for the lease: the pass fast-forwards to origin first, so
    its report and its marker sit on the branch as the forge holds it,
    and the marker push is a plain fast-forward."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    bare = add_bare_remote(root, tmp_path)
    approve_item(root)
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()

    other = tmp_path / "other-clone"
    subprocess.run(["git", "clone", "--quiet", str(bare), str(other)], check=True)
    subprocess.run(["git", "-C", str(other), "checkout", "-q", branch], check=True)
    subprocess.run(["git", "-C", str(other), "config", "user.email", "other@example.com"],
                   check=True)
    subprocess.run(["git", "-C", str(other), "config", "user.name", "Other"], check=True)
    (other / "concurrent.txt").write_text("from elsewhere\n")
    subprocess.run(["git", "-C", str(other), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(other), "commit", "-q", "-m", "a concurrent commit"],
                   check=True)
    subprocess.run(["git", "-C", str(other), "push", "origin", f"HEAD:{branch}"], check=True)

    run = drive(root, "1")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "another pass holds" not in run.stdout
    assert "worker pid" in run.stdout

    rec = latest_record(root, "build")
    assert rec["status"] == "pass"
    assert git(root, "log", "--merges", "--format=%H", "HEAD").split() == []
    assert remote_head(bare, branch) == head_of(root)
    assert "concurrent.txt" in git(root, "ls-tree", "-r", "--name-only", "HEAD")


def test_drive_worker_retries_a_rejected_record_push_with_a_merge_never_a_rebase(tmp_path: Path) -> None:
    """Acceptance row 7, under R2. The branch moves while the worker
    runs -- after the pass's marker push, before the worker's record
    push: the record push is rejected, retried after `git pull
    --no-rebase --no-edit`, never a rebase; the build record's own sha is
    unchanged (still reachable, still what the record names), and the
    retry lands a merge commit, not a rewritten one."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    bare = add_bare_remote(root, tmp_path)
    slow_build_adapter(root, 3)
    approve_item(root)
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()

    run = run_attavo(root, "drive", "1")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "worker pid" in run.stdout
    assert "invocation 01 started" in subprocess.run(
        ["git", "-C", str(bare), "log", "-1", "--format=%s", branch],
        capture_output=True, text=True).stdout

    other = tmp_path / "other-clone"
    subprocess.run(["git", "clone", "--quiet", str(bare), str(other)], check=True)
    subprocess.run(["git", "-C", str(other), "checkout", "-q", branch], check=True)
    subprocess.run(["git", "-C", str(other), "config", "user.email", "other@example.com"],
                   check=True)
    subprocess.run(["git", "-C", str(other), "config", "user.name", "Other"], check=True)
    (other / "concurrent.txt").write_text("from elsewhere\n")
    subprocess.run(["git", "-C", str(other), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(other), "commit", "-q", "-m", "a concurrent commit"],
                   check=True)
    subprocess.run(["git", "-C", str(other), "push", "origin", f"HEAD:{branch}"], check=True)

    waiting = drive(root, "1")  # a pass while the worker holds the copy: reads, touches nothing
    assert waiting.returncode == 0, waiting.stdout + waiting.stderr
    assert "running" in waiting.stdout
    assert "concurrent.txt" not in git(root, "ls-tree", "-r", "--name-only", "HEAD")

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not (
            record_files(root, "build") and remote_head(bare, branch) == head_of(root)):
        time.sleep(0.3)

    rec = latest_record(root, "build")
    assert rec["status"] == "pass"
    build_sha = rec["sha"]
    assert build_sha in git(root, "rev-list", "HEAD").split()

    merges = git(root, "log", "--merges", "--format=%H", "HEAD").split()
    assert merges, "the retry should have landed a merge commit"

    assert remote_head(bare, branch) == head_of(root)
    assert "concurrent.txt" in git(root, "ls-tree", "-r", "--name-only", "HEAD")



def test_drive_a_refused_marker_push_ends_the_pass_with_exit_1(tmp_path: Path) -> None:
    """A remote whose pre-receive hook refuses every push is a refusal,
    not a lease held by another pass: the pass says why, exits 1, raises
    nothing, starts no worker, and with --until-ready does not pass
    again. Distinct from a non-fast-forward rejection, which is the
    lease (test_pass)."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    bare = add_bare_remote(root, tmp_path)
    approve_item(root)
    hook = bare / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'refused by the fixture remote' >&2\nexit 1\n")
    hook.chmod(0o755)

    run = run_attavo(root, "drive", "1", "--until-ready")
    assert run.returncode == 1, run.stdout + run.stderr
    assert "Traceback" not in run.stderr, run.stderr
    assert "could not push the build invocation marker" in run.stdout, run.stdout
    assert "another pass holds" not in run.stdout
    assert "worker pid" not in run.stdout
    assert record_files(root, "build") == []
    assert not (records_dir(root) / "build-01.started").exists()


def test_drive_commits_and_pushes_an_invocation_marker_before_the_stage_runs(tmp_path: Path) -> None:
    """Issue #151: the invocation's own marker is a fact on the branch
    before the stage runs -- its own commit, authored by the driver
    identity, landing and pushed ahead of the stage record's commit."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    bare = add_bare_remote(root, tmp_path)
    approve_item(root)
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()

    run = drive(root, "1")
    assert run.returncode == 0, run.stdout + run.stderr

    marker = records_dir(root) / "build-01.started"
    assert marker.is_file()
    info = json.loads(marker.read_text())
    assert info["host"] == socket.gethostname()
    assert "pid" not in info, info

    marker_commit = git(root, "log", "--format=%H", "--", str(marker.relative_to(root))).split()[-1]
    build_commit = head_of(root)
    assert marker_commit != build_commit
    # the marker's own commit is an ancestor of the build record's commit
    assert git(root, "merge-base", "--is-ancestor", marker_commit, build_commit) == ""

    assert git(root, "log", "-1", "--format=%an", marker_commit).strip() == "attavo driver"
    assert remote_head(bare, branch) == head_of(root)



def test_drive_pass_writes_a_died_record_for_a_dead_invocation(tmp_path: Path) -> None:
    """Issue #151, under R2: the pass itself retires a build-01.started
    marker naming a dead pid with a failed record, class died,
    provenance product -- committed by the driver identity, pushed to
    the fixture remote. Recovery is the pass's one act that pass; it
    starts nothing else."""
    root = make_tenant(tmp_path, "build_valid")
    add_drive_stall_bound(root)
    bare = add_bare_remote(root, tmp_path)
    approve_item(root)
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()

    marker = write_dead_marker(root, "build", 1)
    git(root, "push", "origin", f"HEAD:{branch}")

    run = drive(root, "1")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "recovered build-01 as died" in run.stdout
    assert "worker pid" not in run.stdout

    rec = latest_record(root, "build")
    assert rec["status"] == "failed"
    assert rec["stop"]["class"] == "died"
    assert rec["stop"]["provenance"] == "product"
    assert "wrote no build-01.json" in rec["stop"]["detail"]

    record_path = record_files(root, "build")[-1]
    assert record_path.with_suffix(".log").exists()
    assert git(root, "log", "-1", "--format=%an").strip() == "attavo driver"
    assert git(root, "status", "--porcelain", "--", str(record_path),
              str(record_path.with_suffix(".log")), str(marker)).strip() == ""
    assert remote_head(bare, branch) == head_of(root)
    assert marker.is_file()  # the marker itself is left in place, retired by the record



def test_drive_pass_recovers_a_dead_invocation_without_a_stall_bound(tmp_path: Path) -> None:
    """R2: the stall bound decides hung, not died. With no
    [drive].stall_after_s a dead invocation (its heartbeat stale, or no
    heartbeat and its pid gone) is still recovered as died; what the
    missing bound forgoes is only the hung class -- alive is running for
    as long as it lives (test_pass covers the stale heartbeat)."""
    root = make_tenant(tmp_path, "build_valid")
    add_bare_remote(root, tmp_path)
    approve_item(root)

    write_dead_marker(root, "build", 1)

    run = drive(root, "1")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "recovered build-01 as died" in run.stdout
    assert latest_record(root, "build")["stop"]["class"] == "died"


def test_drive_recover_flag_is_gone(tmp_path: Path) -> None:
    """R2 retires `attavo drive --recover`: recovery is what a pass does
    when the report owes one. The flag is refused with exit 2 and a line
    saying so; nothing is read or written."""
    root = make_tenant(tmp_path, "build_valid")
    add_drive_stall_bound(root)
    add_forge(root, GREEN_FORGE)
    add_bare_remote(root, tmp_path)
    approve_item(root)
    assert drive(root, "1").returncode == 0

    run = run_attavo(root, "drive", "--recover", "1")
    assert run.returncode == 2, run.stdout + run.stderr
    assert "--recover is gone" in run.stderr
    assert len(record_files(root, "build")) == 1


def test_drive_forge_poll_defaults_are_a_real_forges_clock() -> None:
    """The bound and interval every other test overrides with
    FAST_POLL_ENV default, unset, to at least five minutes and ten
    seconds -- tenant zero's own checks conclude about five minutes after
    a push, and a bound shorter than that would make every drive run
    after a record push stop on 'forge checks not green', with a person
    running it again after CI: exactly the transport act this lane
    retires. Row 4 (a forge that never goes green) is unchanged in
    meaning by this -- it still stops, just after waiting out a real
    forge's own clock instead of a test's fake one.
    """
    module = attavo_module()
    assert module.DRIVE_FORGE_POLL_BOUND_DEFAULT_S >= 300
    assert module.DRIVE_FORGE_POLL_INTERVAL_DEFAULT_S >= 10


# --- the holder writes the fact of holding: the worker's own pid is the
# heartbeat file's content, and every reader of liveness reads that file
# (root cause 4, issue #245). ---

LIVE_BEAT = {"ATTAVO_HEARTBEAT_S": "15"}


def heartbeat_file(root: Path, stage: str = "build", nn: int = 1) -> Path:
    return Path(git(root, "rev-parse", "--absolute-git-dir").strip()) / "attavo" / f"{stage}-{nn:02d}.heartbeat"


def write_held_marker(root: Path, stage: str, nn: int, holder_pid: str, *, marker_pid: int | None = None,
                      event_age_s: int = 600, beat_age_s: float = 0) -> Path:
    """A marker with no record whose heartbeat is `beat_age_s` old and
    holds `holder_pid` as its content (empty for none), the adapter's
    last event `event_age_s` old, committed and pushed."""
    from datetime import datetime, timedelta, timezone

    def stamp(ago: int) -> str:
        return (datetime.now(timezone.utc) - timedelta(seconds=ago)).strftime("%Y-%m-%dT%H:%M:%SZ")

    d = records_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    marker = d / f"{stage}-{nn:02d}.started"
    info: dict[str, Any] = {"at": stamp(event_age_s), "host": socket.gethostname(),
                            "sha": head_of(root), "product_version": "fixture"}
    if marker_pid is not None:
        info["pid"] = marker_pid
    marker.write_text(json.dumps(info))
    git(root, "add", str(marker.relative_to(root)))
    git(root, "commit", "-q", "-m", "invocation marker for item 1")
    beat = heartbeat_file(root, stage, nn)
    beat.parent.mkdir(parents=True, exist_ok=True)
    beat.write_text(holder_pid)
    os.utime(beat, (time.time() - beat_age_s, time.time() - beat_age_s))
    (beat.parent / f"{stage}-{nn:02d}.events.jsonl").write_text(
        json.dumps({"at": stamp(event_age_s), "kind": "tool_call", "text": "fixture-tool"}) + "\n")
    return marker


def test_the_worker_writes_its_own_pid_as_the_heartbeat_content(tmp_path: Path) -> None:
    """The driver returns seconds after it starts the worker; the worker
    is the process that holds the stage, so its pid, not the driver's, is
    what the heartbeat file says -- one line, unchanged by later beats."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    add_bare_remote(root, tmp_path)
    slow_build_adapter(root, 3)
    approve_item(root)

    run = run_attavo(root, "drive", "1")
    assert run.returncode == 0, run.stdout + run.stderr
    import re
    worker_pid = int(re.search(r"worker pid (\d+)", run.stdout).group(1))  # type: ignore[union-attr]
    beat = heartbeat_file(root)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not (beat.exists() and beat.read_text().strip()):
        time.sleep(0.1)
    assert beat.read_text() == f"{worker_pid}\n"
    first = beat.stat().st_mtime
    time.sleep(0.6)
    assert beat.stat().st_mtime > first, "the heartbeat did not move"
    assert beat.read_text() == f"{worker_pid}\n"
    marker = json.loads((records_dir(root) / "build-01.started").read_text())
    assert "pid" not in marker and marker["host"] == socket.gethostname()
    while time.monotonic() < deadline:
        try:
            os.kill(worker_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.2)


def test_hung_recovery_signals_the_pid_the_heartbeat_names(tmp_path: Path) -> None:
    """The hung recovery used to signal the marker's pid -- the driver's,
    long gone -- and report "terminated" over a live hung worker. It
    signals the holder: the pid in the heartbeat file."""
    root = make_tenant(tmp_path, "build_valid")
    add_drive_stall_bound(root, 60)
    bare = add_bare_remote(root, tmp_path)
    approve_item(root)
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    holder = subprocess.Popen(["sleep", "60"])
    try:
        write_held_marker(root, "build", 1, f"{holder.pid}\n", marker_pid=_dead_pid())
        git(root, "push", "origin", f"HEAD:{branch}")

        run = drive(root, "1", env=LIVE_BEAT)
        assert run.returncode == 0, run.stdout + run.stderr
        assert holder.wait(timeout=10) == -15, "the holder was not sent SIGTERM"
        assert "recovered build-01 as hung" in run.stdout
        stop = latest_record(root, "build")["stop"]
        assert stop["class"] == "hung" and stop["bound"] == "stall", stop
        assert f"pid {holder.pid}" in stop["detail"] and "terminated" in stop["detail"], stop
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait()


def test_hung_recovery_says_there_is_no_holder_to_signal_when_the_heartbeat_names_none(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "build_valid")
    add_drive_stall_bound(root, 60)
    add_bare_remote(root, tmp_path)
    approve_item(root)
    bystander = subprocess.Popen(["sleep", "60"])
    try:
        # the marker's stale pid field must not be read: this process lives and must be left alone
        write_held_marker(root, "build", 1, "", marker_pid=bystander.pid)
        git(root, "push", "origin", f"HEAD:{git(root, 'rev-parse', '--abbrev-ref', 'HEAD').strip()}")

        run = drive(root, "1", env=LIVE_BEAT)
        assert run.returncode == 0, run.stdout + run.stderr
        assert "recovered build-01 as hung" in run.stdout
        assert "no holder to signal" in run.stdout, run.stdout
        assert bystander.poll() is None, "a pid nobody holds the stage with was signalled"
        assert latest_record(root, "build")["stop"]["class"] == "hung"
    finally:
        bystander.kill()
        bystander.wait()


def test_a_marker_with_no_heartbeat_on_this_host_reads_died_though_its_pid_lives(tmp_path: Path) -> None:
    """read_invocation has no pid fallback: this test's own process lives
    and the marker names it (a marker from before this release), yet no
    heartbeat file means no holder."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    add_drive_stall_bound(root, 60)
    approve_item(root)
    write_held_marker(root, "build", 1, "", marker_pid=os.getpid(), event_age_s=1)
    heartbeat_file(root).unlink()

    run = run_attavo(root, "drive", "1", "--report", env=LIVE_BEAT)
    assert run.returncode == 0, run.stdout + run.stderr
    out = json.loads(run.stdout)
    assert (out["state"], out["stop_class"]) == ("owes-recovery", "died"), out


def test_again_without_by_refuses_even_inside_the_budget(tmp_path: Path) -> None:
    """--again names who pressed it, as `attavo approve` names its
    approver: refused before anything is read, whether or not it would
    have done anything."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    approve_item(root)

    run = run_attavo(root, "drive", "1", "--again")
    assert run.returncode == 2, run.stdout + run.stderr
    assert "attavo drive: --again requires --by LOGIN" in run.stderr
    assert not (records_dir(root) / "build-01.started").exists()

    empty = run_attavo(root, "drive", "1", "--again", "--by")
    assert empty.returncode == 2 and "--by LOGIN" in empty.stderr, empty.stderr


def test_by_without_again_refuses(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "build_valid")
    approve_item(root)
    run = run_attavo(root, "drive", "1", "--by", "alice")
    assert run.returncode == 2 and "--by is only for --again" in run.stderr, run.stderr
    assert not (records_dir(root) / "build-01.started").exists()
