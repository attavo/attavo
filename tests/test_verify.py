"""attavo verify: refuse without a passing build record for the item, read
the forge's own check conclusions on the build record's commit before any
adapter is called, and write verify-NN.json. Issue #40, section 2.

Regression tests, written and run red first: on `main` at 719d39f there
was no `verify` subcommand, so every one of these failed with "unknown
subcommand".

Run: python3 -m pytest tests -q
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from helpers import commit_all, git, make_tenant

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"

# A stand-in forge read: ignores the sha argv build passes it and prints
# one fixed "<name> <conclusion>" line, green or red.
GREEN_FORGE = "printf 'ci success\\n'"
RED_FORGE = "printf 'ci failure\\n'"


def run_attavo(root: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run([sys.executable, str(ATTAVO), *args], cwd=str(root),
                          capture_output=True, text=True, check=False, env=full_env)


def add_forge(root: Path, checks_command: str) -> None:
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text() + f'\n[forge]\nchecks_command = "{checks_command}"\n')
    commit_all(root, "configure the forge read")


def approve_item(root: Path, item: str = "1") -> None:
    assert run_attavo(root, "spec-gate", item).returncode == 0
    assert run_attavo(root, "approve", item, "--by", "alice").returncode == 0


def latest_verify(root: Path, item: str = "1") -> dict:
    files = sorted((root / "records" / item).glob("verify-*.json"))
    assert files, f"no verify record for item {item}"
    return json.loads(files[-1].read_text())


def latest_verify_log(root: Path, item: str = "1") -> str:
    files = sorted((root / "records" / item).glob("verify-*.json"))
    assert files, f"no verify record for item {item}"
    return files[-1].with_suffix(".log").read_text()


def diff_block(log_text: str) -> str:
    """The text the fixture adapter echoed back from ATTAVO_DIFF, between
    the markers adapters/fixture/run prints for ATTAVO_STAGE=verify."""
    start = log_text.index("--- ATTAVO_DIFF ---\n") + len("--- ATTAVO_DIFF ---\n")
    end = log_text.index("--- end diff ---")
    return log_text[start:end]


def test_verify_refuses_without_a_build_record(tmp_path):
    """Acceptance 4a. No build record at all for the item: refused,
    naming the cause, no verify record written."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    approve_item(root)

    run = run_attavo(root, "verify", "1")
    assert run.returncode == 1
    assert "build" in run.stdout
    assert list((root / "records" / "1").glob("verify-*.json")) == []


def test_verify_refuses_when_latest_build_is_not_pass(tmp_path):
    """Acceptance 4b. A build record exists but its status is not pass:
    refused, naming the cause, no verify record written."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text() + '\n[checks]\ncommand = "exit 7"\n')
    commit_all(root, "configure a check the build can never pass")
    approve_item(root)

    build = run_attavo(root, "build", "1")
    assert build.returncode == 1  # issue #30: the tenant's checks are part of done

    run = run_attavo(root, "verify", "1")
    assert run.returncode == 1
    assert "not pass" in run.stdout
    assert list((root / "records" / "1").glob("verify-*.json")) == []


def test_verify_pass_record_carries_no_stop(tmp_path):
    """Issue #89, rejection test 2 (half). A pass record carries stop:
    null."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    approve_item(root)
    assert run_attavo(root, "build", "1").returncode == 0

    run = run_attavo(root, "verify", "1")
    assert run.returncode == 0

    rec = latest_verify(root)
    assert rec["status"] == "pass"
    assert rec["stop"] is None


def test_verify_returns_when_the_forge_is_red(tmp_path):
    """Acceptance 5. The forge's own check conclusions on the build
    record's commit are read before any judge is called: a failing
    conclusion writes status "returned" and calls no adapter at all --
    not even a transcript log exists to show it ran.
    """
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, RED_FORGE)
    approve_item(root)
    assert run_attavo(root, "build", "1").returncode == 0

    run = run_attavo(root, "verify", "1")
    assert run.returncode == 0

    rec = latest_verify(root)
    assert rec["status"] == "returned"
    assert rec["adapter"] is None
    assert rec["model"] is None
    assert rec["findings"] is None
    assert rec["forge_checks"]["conclusions"] == [{"name": "ci", "conclusion": "failure"}]
    # no adapter was ever invoked -- no transcript log for this attempt
    assert not (root / "records" / "1" / "verify-01.log").exists()
    # Issue #89, rejection test 3: a returned verify is forge-unconcluded/tenant.
    assert rec["stop"] == {"class": "forge-unconcluded", "provenance": "tenant", "detail": None}


def test_verify_treats_an_empty_forge_read_as_not_green(tmp_path):
    """Issue #44, rejection test 1. A [forge].checks_command that prints
    nothing and exits 0 is not green -- a commit the forge has no record
    of is unread, not passing (all() over an empty conclusions list is
    True, which is why this has to be spelled out). Red at 0b2b869,
    where an empty read wrote status: "pass" and called the adapter.
    """
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, "true")  # prints nothing, exits 0
    approve_item(root)
    assert run_attavo(root, "build", "1").returncode == 0

    run = run_attavo(root, "verify", "1")
    assert run.returncode == 0

    rec = latest_verify(root)
    assert rec["status"] == "returned"
    assert rec["adapter"] is None
    assert rec["forge_checks"]["conclusions"] == []
    # no adapter was ever invoked -- no transcript log for this attempt
    assert not (root / "records" / "1" / "verify-01.log").exists()


def test_verify_reads_the_forge_on_the_branch_head_not_the_build_sha(tmp_path):
    """Issue #44, rejection test 2. The forge attaches checks to a pull
    request's head, not to the executor's own commit -- that commit
    stops being the head the moment a later commit lands on top of it.
    verify must read the forge on the branch head; build_sha and
    forge_checks["sha"] are kept as two separate fields rather than one
    sha serving two purposes. Red at 0b2b869, where they were equal by
    construction (both the build record's own sha).
    """
    root = make_tenant(tmp_path, "build_valid")
    # echoes the sha it is given back as a (bogus) conclusion, so the
    # test can see exactly which sha verify queried the forge on.
    add_forge(root, "echo ci")
    approve_item(root)
    assert run_attavo(root, "build", "1").returncode == 0

    build_rec = json.loads(sorted((root / "records" / "1").glob("build-*.json"))[-1].read_text())
    build_sha = build_rec["sha"]

    # a later commit lands on top of the build's own commit before
    # verify runs -- the shape a record push (or any later push) leaves.
    (root / "after-build.txt").write_text("later\n")
    commit_all(root, "a later commit, as a record push would leave behind")
    head_sha = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    assert head_sha != build_sha

    run = run_attavo(root, "verify", "1")
    assert run.returncode == 0

    rec = latest_verify(root)
    assert rec["build_sha"] == build_sha
    assert rec["forge_checks"]["sha"] == head_sha
    assert rec["forge_checks"]["sha"] != rec["build_sha"]


def test_verify_merges_stage_config(tmp_path):
    """Acceptance 6. [executor.<adapter>.verify] shadows
    [executor.<adapter>] for verify alone; build sees the parent,
    verify sees the merged result.
    """
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text()
                    + '\n[executor.fixture]\nx = "parent"\n'
                    + '\n[executor.fixture.verify]\nx = "override"\n')
    commit_all(root, "configure per-stage adapter config")
    approve_item(root)

    assert run_attavo(root, "build", "1").returncode == 0
    build_rec = json.loads(sorted((root / "records" / "1").glob("build-*.json"))[-1].read_text())
    assert build_rec["adapter_config"] == {"x": "parent"}

    run = run_attavo(root, "verify", "1")
    assert run.returncode == 0
    verify_rec = latest_verify(root)
    assert verify_rec["status"] == "pass"
    assert verify_rec["adapter_config"] == {"x": "override"}


def head_of(root: Path) -> str:
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


def test_verify_fails_when_findings_key_is_absent(tmp_path):
    """Issue #46, rejection test 1. A verify report line carrying no
    findings key at all is failed, not pass -- the absence of a channel
    to report a finding is not a clean bill of health, and is not the
    same thing as an explicit empty list. Red at 0b2b869, where
    payload.get("findings") or [] collapsed an absent key and an
    explicit empty list into the same "pass".
    """
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    approve_item(root)
    assert run_attavo(root, "build", "1").returncode == 0

    run = run_attavo(root, "verify", "1", env={"ATTAVO_FIXTURE_OMIT_FINDINGS": "1"})
    assert run.returncode == 1

    rec = latest_verify(root)
    assert rec["status"] == "failed"
    assert rec["findings"] is None
    # Issue #89: no rule of its own names this shape -- it falls to
    # unclassed/product, with the log's last line as its detail.
    assert rec["stop"]["class"] == "unclassed"
    assert rec["stop"]["provenance"] == "product"
    assert rec["stop"]["detail"]


def test_verify_fails_when_the_adapter_commits(tmp_path):
    """Issue #46, rejection test 2. Verify is a report, not a commit: an
    adapter that moves the branch head during the invocation fails the
    attempt, and the record names the commit it should not have made.
    Red at 0b2b869, where the commit was silently kept on the branch and
    the record reported success with no trace of it.
    """
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    approve_item(root)
    assert run_attavo(root, "build", "1").returncode == 0

    pre_head = head_of(root)

    run = run_attavo(root, "verify", "1", env={"ATTAVO_FIXTURE_COMMIT_DURING_VERIFY": "1"})
    assert run.returncode == 1

    post_head = head_of(root)
    assert post_head != pre_head, "the fixture's own commit should have landed"

    rec = latest_verify(root)
    assert rec["status"] == "failed"
    assert rec["sha"] == pre_head  # what was actually being verified
    assert rec["commits"] == [post_head]  # named, not silently kept


def test_verify_records_a_silently_failing_adapter_without_being_refused(tmp_path):
    """Issue #53, rejection test 4. Same guarantee as build's: an adapter
    invoked during verify that exits non-zero with nothing on stdout or
    stderr must not leave a failed record with an empty log and no trail
    -- cmd_verify puts the adapter's exit status into the log before the
    write, so the write is not refused.
    """
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    approve_item(root)
    assert run_attavo(root, "build", "1").returncode == 0

    adapter_dir = root / "attavo" / "adapters" / "silent"
    adapter_dir.mkdir(parents=True)
    adapter_run = adapter_dir / "run"
    adapter_run.write_text("#!/bin/sh\nexit 1\n")
    adapter_run.chmod(0o755)
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text().replace(
        '[executor]\nbuild = "fixture"', '[executor]\nbuild = "fixture"\nverify = "silent"'))
    commit_all(root, "use a silently failing verify adapter")

    run = run_attavo(root, "verify", "1")
    assert run.returncode == 1

    rec = latest_verify(root)
    assert rec["status"] == "failed"

    log = (root / "records" / "1" / "verify-01.log").read_text()
    assert log.strip() != ""
    assert "1" in log  # the exit status, so the record explains itself


def test_verify_ceiling_error_line_carries_ceiling_stop(tmp_path):
    """Issue #89, rejection test 3: an adapter exit with a tool error line
    matching a declared ceiling is ceiling/product -- issue #86's own
    specimen, codex's "input_too_large" on a diff too large to judge."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    approve_item(root)
    assert run_attavo(root, "build", "1").returncode == 0

    run = run_attavo(root, "verify", "1", env={"ATTAVO_FIXTURE_CEILING_ERROR": "1"})
    assert run.returncode == 1

    rec = latest_verify(root)
    assert rec["status"] == "failed"
    assert rec["stop"] == {"class": "ceiling", "provenance": "product", "detail": None}
    log = latest_verify_log(root)
    assert "input_too_large" in log


def test_verify_records_the_tools_own_error_line_on_a_nonzero_exit(tmp_path):
    """Issue #86, rejection test 3. Specimen: tenant zero
    attest/records/659/verify-01.log -- two lines, thread.started and
    "[adapter exited 1]", though the codex adapter printed codex's own
    "Input exceeds..." error line to its stdout, which issue #53's fix
    was supposed to guarantee reaches the log. The mechanism:
    split_adapter_stdout takes the adapter's last non-empty stdout line
    as an attempted JSON payload and treats only what comes before it as
    transcript; on a non-zero exit, a tool's own error line, printed
    last, is that attempted payload -- it fails to parse as JSON and is
    dropped from the transcript instead of kept in it. An adapter that
    prints an event line, then an error line, then exits 1: the log
    must still carry the error line.
    """
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    approve_item(root)
    assert run_attavo(root, "build", "1").returncode == 0

    adapter_dir = root / "attavo" / "adapters" / "loud"
    adapter_dir.mkdir(parents=True)
    adapter_run = adapter_dir / "run"
    adapter_run.write_text(
        "#!/bin/sh\n"
        "echo '{\"type\":\"thread.started\"}'\n"
        "echo 'Error: turn/start failed: input_too_large'\n"
        "exit 1\n")
    adapter_run.chmod(0o755)
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text().replace(
        '[executor]\nbuild = "fixture"', '[executor]\nbuild = "fixture"\nverify = "loud"'))
    commit_all(root, "use a verify adapter that errors loudly on stdout")

    run = run_attavo(root, "verify", "1")
    assert run.returncode == 1

    log = (root / "records" / "1" / "verify-01.log").read_text()
    assert "input_too_large" in log


# --- issue #52: the diff handed to the adapter is the item's own work,
# not everything merged onto the branch since approval ---
#
# The issue's own proposed fix -- a three-dot diff against approval_sha,
# i.e. `git diff $(git merge-base approval_sha HEAD) HEAD` -- turns out
# not to change anything: approval_sha is already an ancestor of HEAD in
# the normal flow (build's commits are children of it, and merging main
# into the item branch keeps it reachable via the first-parent chain), so
# merge-base(approval_sha, HEAD) is approval_sha again and A...B reduces
# to the same A..B already computed today. Confirmed with two raw `git
# diff` runs before writing test 1 below; posted to issue #52 as a
# comment. The fix actually applied here diffs from the merge-base of
# HEAD and refs/remotes/origin/HEAD instead -- the default branch's own
# tip is not, in general, an ancestor of HEAD once the item branch has
# taken merges from it, so that merge-base does move, and content already
# identical on both sides of it (whatever main contributed) drops out of
# the diff.


def test_verify_diff_excludes_content_merged_from_the_default_branch(tmp_path):
    """Issue #52, rejection test 1. A commit lands on the fixture's main
    and is merged into the item branch; then one change is made on the
    item branch itself (the build). The diff at ATTAVO_DIFF names only
    that second change. Red before this fix: it names both, since the
    two-dot diff against approval_sha cannot tell main's own content
    apart from the item's.
    """
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    approve_item(root)  # approval_sha == the tenant's initial commit

    fork = git(root, "rev-parse", "HEAD").strip()
    git(root, "checkout", "-q", "-b", "sim-main", fork)
    (root / "mainfile.txt").write_text("from main\n")
    commit_all(root, "a commit on the fixture's main")
    main_tip = git(root, "rev-parse", "HEAD").strip()
    git(root, "update-ref", "refs/remotes/origin/main", main_tip)
    git(root, "checkout", "-q", "main")
    git(root, "merge", "-q", "--no-ff", "sim-main", "-m", "merge main")

    assert run_attavo(root, "build", "1").returncode == 0  # the item's own change

    run = run_attavo(root, "verify", "1")
    assert run.returncode == 0

    diff = diff_block(latest_verify_log(root))
    assert "built.txt" in diff
    assert "mainfile.txt" not in diff


def test_verify_diff_matches_the_old_two_dot_diff_without_a_merge(tmp_path):
    """Issue #52, rejection test 2. With no merge from the default
    branch, the new diff must not regress to something narrower or
    broader than before: it comes out byte-identical to the old
    ``git diff approval_sha HEAD``.

    The branch's first commit after the base (refs/remotes/origin/main,
    pinned here to the tenant's state right before approval) is an empty
    marker standing in for "approved, nothing built yet" -- its tree is
    therefore identical to the base's, so the new diff's base
    (merge-base(origin/HEAD, HEAD)) and the old diff's base
    (approval_sha) name trees with the same content, and the two diffs,
    though computed from different commits, come out identical byte for
    byte.
    """
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)

    base = git(root, "rev-parse", "HEAD").strip()
    git(root, "update-ref", "refs/remotes/origin/main", base)

    git(root, "commit", "--allow-empty", "-q", "-m", "approved: item 1")
    approve_item(root)
    assert run_attavo(root, "build", "1").returncode == 0

    build_rec = json.loads(sorted((root / "records" / "1").glob("build-*.json"))[-1].read_text())
    approval_sha = build_rec["approval_sha"]
    head = git(root, "rev-parse", "HEAD").strip()
    old_diff = git(root, "diff", approval_sha, head)

    run = run_attavo(root, "verify", "1")
    assert run.returncode == 0
    assert diff_block(latest_verify_log(root)) == old_diff


def test_verify_refuses_without_an_origin_head(tmp_path):
    """Issue #52, rejection test 3. With no refs/remotes/origin/HEAD to
    diff against, cmd_verify refuses by name -- telling the operator to
    run `git remote set-head origin -a` -- rather than guessing, and
    writes no verify record at all. Red before this fix: there was
    nothing to refuse on, since refs/remotes/origin/HEAD was never
    consulted.
    """
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    approve_item(root)
    assert run_attavo(root, "build", "1").returncode == 0

    # helpers.init_repo fakes refs/remotes/origin/HEAD for every other
    # test in this file; undo that here to exercise a real checkout that
    # was never told which branch origin considers default.
    subprocess.run(["git", "-C", str(root), "symbolic-ref", "-d", "refs/remotes/origin/HEAD"],
                   capture_output=True, text=True, check=True)

    run = run_attavo(root, "verify", "1")
    assert run.returncode == 1
    assert "origin/HEAD" in run.stdout
    assert list((root / "records" / "1").glob("verify-*.json")) == []


def test_verify_diff_is_empty_without_an_approval_sha(tmp_path):
    """Issue #52 rejection table row 4 (unchanged from the original
    issue): a build record with no approval_sha at all gets an empty
    diff, and no crash. refs/remotes/origin/HEAD is dropped here too, to
    show it is never consulted when there is no approval_sha to diff
    from in the first place.
    """
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    approve_item(root)
    assert run_attavo(root, "build", "1").returncode == 0

    build_path = sorted((root / "records" / "1").glob("build-*.json"))[-1]
    build_rec = json.loads(build_path.read_text())
    build_rec["approval_sha"] = None
    build_path.write_text(json.dumps(build_rec))

    subprocess.run(["git", "-C", str(root), "symbolic-ref", "-d", "refs/remotes/origin/HEAD"],
                   capture_output=True, text=True, check=True)

    run = run_attavo(root, "verify", "1")
    assert run.returncode == 0
    assert diff_block(latest_verify_log(root)) == ""
