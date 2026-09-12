"""attavo drive: the mechanical transitions between approval and
ready-to-merge (issue #84) -- one step per invocation, and only that step,
until a state that belongs to a person. Every test uses the fixture
adapter and a fake `[forge].checks_command`; every record commit is
`git commit --only` and pushed to a real (bare) fixture remote.

Regression tests, written and run red first: on `main` at 85f6f3b there
was no `drive` subcommand, so every one of these failed with "unknown
subcommand".

Run: python3 -m pytest tests -q
"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

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
FAST_POLL_ENV = {"ATTAVO_DRIVE_POLL_BOUND_S": "0.9", "ATTAVO_DRIVE_POLL_INTERVAL_S": "0.5"}


def run_attavo(root: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    full_env = dict(os.environ)
    full_env.update(FAST_POLL_ENV)
    if env:
        full_env.update(env)
    return subprocess.run([sys.executable, str(ATTAVO), *args], cwd=str(root),
                          capture_output=True, text=True, check=False, env=full_env)


def attavo_module():
    """bin/attavo imported in-process, for the default-constant assertion
    below that a CLI invocation cannot provoke without actually waiting
    out the real clock."""
    loader = importlib.machinery.SourceFileLoader("attavo_drive_under_test", str(ATTAVO))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


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


def latest_record(root: Path, stage: str, item: str = "1") -> dict:
    files = record_files(root, stage, item)
    assert files, f"no {stage} record for item {item}"
    return json.loads(files[-1].read_text())


def head_of(root: Path) -> str:
    return git(root, "rev-parse", "HEAD").strip()


def last_commit_paths(root: Path) -> list[str]:
    return git(root, "diff", "--name-only", "HEAD^", "HEAD").splitlines()


def remote_head(bare: Path, branch: str = "main") -> str:
    return subprocess.run(["git", "-C", str(bare), "rev-parse", branch],
                          capture_output=True, text=True, check=True).stdout.strip()


def test_drive_refuses_without_a_valid_approval(tmp_path):
    """Acceptance row 6. No approval at all: refused by name, nothing run
    -- no spec-gate, no build, no records of any kind written."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    add_bare_remote(root, tmp_path)

    run = run_attavo(root, "drive", "1")
    assert run.returncode != 0
    assert "approval" in run.stdout
    assert "absent" in run.stdout

    assert not records_dir(root).exists() or list(records_dir(root).glob("*.json")) == []


def test_drive_refuses_with_an_expired_approval(tmp_path):
    """Acceptance row 6, the other half: an approval bound to a spec sha
    that has since moved is expired, not absent -- still refused by name,
    nothing run."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    add_bare_remote(root, tmp_path)
    approve_item(root)

    (root / "specs" / "1" / "spec.md").write_text(
        (root / "specs" / "1" / "spec.md").read_text() + "\n")
    commit_all(root, "touch the spec after approval")

    run = run_attavo(root, "drive", "1")
    assert run.returncode != 0
    assert "expired" in run.stdout
    assert record_files(root, "build") == []


def test_drive_builds_from_an_approved_spec_with_no_build(tmp_path):
    """Acceptance row 1. One drive run produces a passing build record,
    commits only the record and its log (`git commit --only`), and pushes
    the branch; the fixture remote carries the record commit."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    bare = add_bare_remote(root, tmp_path)
    approve_item(root)
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()

    run = run_attavo(root, "drive", "1")
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


def test_drive_verifies_from_a_passed_build_with_the_forge_green(tmp_path):
    """Acceptance row 2. From a passed build with the fake forge green,
    one run produces a verify record, commits only the record and its log,
    and pushes."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    bare = add_bare_remote(root, tmp_path)
    approve_item(root)
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()

    assert run_attavo(root, "drive", "1").returncode == 0  # builds

    run = run_attavo(root, "drive", "1")
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


def test_drive_prints_ready_to_merge_from_a_passed_verify(tmp_path):
    """Acceptance row 3. From a passed verify with the forge green, one
    run prints ready-to-merge, exits 0, writes nothing, enqueues nothing
    -- and, per row 8, a further run repeats exactly the same thing."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    add_bare_remote(root, tmp_path)
    approve_item(root)

    assert run_attavo(root, "drive", "1").returncode == 0  # builds
    assert run_attavo(root, "drive", "1").returncode == 0  # verifies

    pre_head = head_of(root)
    run = run_attavo(root, "drive", "1")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "ready to merge" in run.stdout
    assert head_of(root) == pre_head
    assert record_files(root, "verify") and len(record_files(root, "verify")) == 1

    # Acceptance row 8: idempotence -- run it again, same result, nothing
    # new written or pushed.
    run2 = run_attavo(root, "drive", "1")
    assert run2.returncode == 0
    assert "ready to merge" in run2.stdout
    assert head_of(root) == pre_head
    assert len(record_files(root, "verify")) == 1
    assert len(record_files(root, "build")) == 1


def test_drive_stops_when_the_forge_is_red(tmp_path):
    """Acceptance row 4. A forge read where the one check has already
    concluded and failed is not polled at all -- there is nothing left to
    wait on, so drive stops on the first read, naming the check, with no
    sleep in between. Writes nothing -- no verify record at all, no new
    commit."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, RED_FORGE)
    add_bare_remote(root, tmp_path)
    approve_item(root)

    assert run_attavo(root, "drive", "1").returncode == 0  # builds
    pre_head = head_of(root)

    started = time.monotonic()
    run = run_attavo(root, "drive", "1")
    elapsed = time.monotonic() - started
    assert run.returncode != 0
    assert "ci" in run.stdout and "failure" in run.stdout
    assert elapsed < 0.4, (
        f"a concluded, red forge check should stop on the first read with no "
        f"poll interval slept -- took {elapsed:.2f}s")

    assert record_files(root, "verify") == []
    assert head_of(root) == pre_head

    # Idempotence (row 8): running again in the same red state stops the
    # same way, still nothing written.
    run2 = run_attavo(root, "drive", "1")
    assert run2.returncode != 0
    assert record_files(root, "verify") == []
    assert head_of(root) == pre_head


def test_drive_polls_the_bound_while_the_forge_is_still_running(tmp_path):
    """Acceptance row 4, the unconcluded case: a check still carrying a
    status (in_progress) rather than a conclusion is not yet red -- drive
    keeps polling it for the full bound before giving up, unlike a
    concluded red check, which it does not poll at all. Writes nothing."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, UNCONCLUDED_FORGE)
    add_bare_remote(root, tmp_path)
    approve_item(root)

    assert run_attavo(root, "drive", "1").returncode == 0  # builds

    started = time.monotonic()
    run = run_attavo(root, "drive", "1")
    elapsed = time.monotonic() - started
    assert run.returncode != 0
    assert "ci" in run.stdout and "in_progress" in run.stdout
    assert elapsed >= 0.4, (
        f"a check still in_progress should be polled for the bound, not "
        f"stopped on the first read -- took only {elapsed:.2f}s")

    assert record_files(root, "verify") == []


def test_drive_halts_on_a_question_and_resumes_at_build_after_a_ruling(tmp_path):
    """Acceptance row 5. The fixture adapter halts with a question: drive
    stops naming the question and the record (and still pushes that
    halted record, same as any other record build writes). After a
    ruling is appended, a second run resumes at build and this time
    passes."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    bare = add_bare_remote(root, tmp_path)
    approve_item(root)
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()

    run = run_attavo(root, "drive", "1",
                     env={"ATTAVO_FIXTURE_QUESTION": "which overlay wins?"})
    assert run.returncode == 3
    assert "which overlay wins?" in run.stdout

    rec = latest_record(root, "build")
    assert rec["status"] == "halted"
    assert rec["halted"] == {"question": "which overlay wins?"}
    record_path = record_files(root, "build")[-1]
    assert record_path.name in run.stdout

    # The halted record was still committed and pushed.
    assert remote_head(bare, branch) == head_of(root)

    rulings = root / "rulings.md"
    rulings.write_text(rulings.read_text() + "\n- overlay: use the base tree\n"
                       if rulings.exists() else "- overlay: use the base tree\n")
    commit_all(root, "rule on the overlay question")
    git(root, "push", "origin", f"HEAD:{branch}")

    run2 = run_attavo(root, "drive", "1")
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


def test_drive_counsels_a_stop_before_filing_it_and_commits_both_records_together(tmp_path):
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

    run = run_attavo(root, "drive", "1",
                     env={"ATTAVO_FIXTURE_QUESTION": "which overlay wins?"})
    assert run.returncode == 3

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


def test_drive_still_files_a_stop_with_no_counsel_adapter_configured(tmp_path):
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

    run = run_attavo(root, "drive", "1",
                     env={"ATTAVO_FIXTURE_QUESTION": "which overlay wins?"})
    assert run.returncode == 3

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


def test_drive_files_a_stop_it_reports_exactly_once(tmp_path):
    """Issue #89, acceptance row 5. A stop drive reports -- here, the
    fixture adapter's halt -- calls file-stops once: one drive run files
    exactly one issue for the attempt it just wrote, not zero and not
    more than one."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    add_bare_remote(root, tmp_path)
    approve_item(root)
    filed = tmp_path / "filed.jsonl"
    configure_filing(root, filed)

    run = run_attavo(root, "drive", "1",
                     env={"ATTAVO_FIXTURE_QUESTION": "which overlay wins?"})
    assert run.returncode == 3

    filed_bodies = [json.loads(l) for l in filed.read_text().splitlines() if l.strip()]
    assert len(filed_bodies) == 1
    assert filed_bodies[0]["record"] == "records/1/build-01.json"

    # A second drive run halts again -- a new attempt, build-02 -- and
    # files exactly one more issue for that new record; file-stops' own
    # .filed marker means build-01's issue is never filed twice.
    run2 = run_attavo(root, "drive", "1",
                      env={"ATTAVO_FIXTURE_QUESTION": "which overlay wins?"})
    assert run2.returncode == 3
    assert len(record_files(root, "build")) == 2

    filed_bodies_after = [json.loads(l) for l in filed.read_text().splitlines() if l.strip()]
    assert len(filed_bodies_after) == 2
    assert filed_bodies_after[0] == filed_bodies[0]
    assert filed_bodies_after[1]["record"] == "records/1/build-02.json"


def test_drive_marker_holds_the_filing_commands_stdout_and_is_committed_with_the_record(tmp_path):
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

    run = run_attavo(root, "drive", "1",
                     env={"ATTAVO_FIXTURE_QUESTION": "which overlay wins?"})
    assert run.returncode == 3

    marker_path = records_dir(root) / "build-01.filed"
    assert marker_path.is_file()
    assert marker_path.read_text().strip() == "https://github.com/tenant/repo/issues/1"

    committed = set(last_commit_paths(root))
    assert committed == {"records/1/build-01.json", "records/1/build-01.log",
                         "records/1/build-01.filed"}
    assert remote_head(bare) == head_of(root)


def test_drive_a_second_run_in_the_same_state_files_nothing(tmp_path):
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

    run = run_attavo(root, "drive", "1",
                     env={"ATTAVO_FIXTURE_QUESTION": "which overlay wins?"})
    assert run.returncode == 3
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


def test_drive_retries_a_rejected_push_with_a_merge_never_a_rebase(tmp_path):
    """Acceptance row 7. A rejected push with a divergent remote is
    retried after `git pull --no-rebase --no-edit`, never a rebase; the
    build record's own sha is unchanged (still reachable, still what the
    record names), and the retry lands a merge commit, not a rewritten
    one."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    bare = add_bare_remote(root, tmp_path)
    approve_item(root)
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()

    # Diverge the bare remote: another clone pushes an unrelated commit to
    # the same branch before drive's own push happens.
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

    run = run_attavo(root, "drive", "1")
    assert run.returncode == 0, run.stdout + run.stderr

    rec = latest_record(root, "build")
    assert rec["status"] == "pass"
    build_sha = rec["sha"]

    # The build's own commit sha is still reachable -- a merge, not a
    # rebase, which would have rewritten it under a new sha.
    assert build_sha in git(root, "rev-list", "HEAD").split()

    merge_parents = git(root, "log", "-1", "--format=%P", "HEAD").split()
    assert len(merge_parents) == 2, "the retry should have landed a merge commit"

    assert remote_head(bare, branch) == head_of(root)
    # the concurrent commit from the other clone is present too
    assert "concurrent.txt" in git(root, "ls-tree", "-r", "--name-only", "HEAD")


def test_drive_forge_poll_defaults_are_a_real_forges_clock():
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
