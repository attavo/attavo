"""Approval bound to a commit: attavo approve N --by LOGIN, attavo approval-status N.

Rejection tests, written and run red before the capability existed: on
`main` at commit 22ca1e1 there was no `approve` or `approval-status`
subcommand, so every one of these failed with "unknown subcommand".

Run: python3 -m pytest tests -q
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from helpers import clone_shallow, commit_all, commit_approval, make_tenant

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"


def run_attavo(root: Path, *args: str, env: dict[str, Any] | None = None) -> subprocess.CompletedProcess[str]:
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run([sys.executable, str(ATTAVO), *args], cwd=str(root),
                          capture_output=True, text=True, check=False, env=full_env)


def approvals(root: Path, item: str = "1") -> list[Path]:
    return sorted((root / "records" / item).glob("approval-*.json"))


def amend_problem(root: Path) -> None:
    spec_md = root / "specs" / "1" / "spec.md"
    spec_md.write_text(spec_md.read_text().replace(
        "does not emit", "used to not emit"))
    commit_all(root, "amend problem prose")


def test_approve_refuses_without_current_pass(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "valid")

    # no spec-gate has run at all yet
    run = run_attavo(root, "approve", "1", "--by", "alice")
    assert run.returncode == 1
    assert approvals(root) == []

    # a pass exists, but the spec has since moved without a fresh gate run
    assert run_attavo(root, "spec-gate", "1").returncode == 0
    amend_problem(root)

    stale = run_attavo(root, "approve", "1", "--by", "alice")
    assert stale.returncode == 1
    assert "stale" in stale.stdout
    assert approvals(root) == []


def test_approve_refuses_unlisted_login(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "valid")
    assert run_attavo(root, "spec-gate", "1").returncode == 0

    run = run_attavo(root, "approve", "1", "--by", "mallory")
    assert run.returncode == 1
    assert "mallory" in run.stdout
    assert approvals(root) == []


def test_amendment_above_line_costs_one_act(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "valid")

    assert run_attavo(root, "spec-gate", "1").returncode == 0
    assert run_attavo(root, "approve", "1", "--by", "alice").returncode == 0
    commit_approval(root)
    assert run_attavo(root, "approval-status", "1").stdout.strip() == "valid"

    amend_problem(root)
    expired = run_attavo(root, "approval-status", "1")
    assert expired.returncode == 1
    assert expired.stdout.strip() == "expired"

    # the next gate run CI performs on every push
    assert run_attavo(root, "spec-gate", "1").returncode == 0
    assert run_attavo(root, "approve", "1", "--by", "alice").returncode == 0
    commit_approval(root)

    valid_again = run_attavo(root, "approval-status", "1")
    assert valid_again.returncode == 0
    assert valid_again.stdout.strip() == "valid"

    assert [p.name for p in approvals(root)] == ["approval-01.json", "approval-02.json"]


def test_approve_accepts_built(tmp_path: Path) -> None:
    """Issue #28, acceptance 4. approve succeeds on a latest spec-gate
    record of status built -- the re-approval path after an amendment
    that landed above the line after a real build.
    """
    root = make_tenant(tmp_path, "valid")
    assert run_attavo(root, "spec-gate", "1").returncode == 0
    assert run_attavo(root, "approve", "1", "--by", "alice").returncode == 0
    commit_approval(root)

    build = run_attavo(root, "build", "1", env={"ATTAVO_FIXTURE_WRITE": "src/marker.txt"})
    assert build.returncode == 0

    amend_problem(root)
    built = run_attavo(root, "spec-gate", "1")
    assert built.returncode == 0
    latest_spec = sorted((root / "records" / "1").glob("spec-*.json"))[-1]
    assert json.loads(latest_spec.read_text())["status"] == "built"

    run = run_attavo(root, "approve", "1", "--by", "alice")
    assert run.returncode == 0
    assert [p.name for p in approvals(root)] == ["approval-01.json", "approval-02.json"]


def test_approve_refuses_shallow_checkout(tmp_path: Path) -> None:
    """On a --depth 1 clone, `git log -1 -- <path>` returns HEAD for every
    path (there is no history to prove otherwise), so a shallow checkout
    would compute a spec_sha that always matches HEAD and pass approval
    for a spec that may have moved. attavo approve must refuse instead of
    trusting that number: exit 2, naming the cause, not "stale".
    """
    root = make_tenant(tmp_path, "valid")
    assert run_attavo(root, "spec-gate", "1").returncode == 0
    commit_all(root, "spec-gate record")

    shallow = tmp_path / "shallow"
    clone_shallow(root, shallow)

    run = run_attavo(shallow, "approve", "1", "--by", "alice")
    assert run.returncode == 2
    assert "shallow checkout" in run.stderr
    assert approvals(shallow) == []


def test_approval_status_refuses_shallow_checkout(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "valid")
    assert run_attavo(root, "spec-gate", "1").returncode == 0
    assert run_attavo(root, "approve", "1", "--by", "alice").returncode == 0
    commit_all(root, "spec-gate and approval records")

    shallow = tmp_path / "shallow"
    clone_shallow(root, shallow)

    run = run_attavo(shallow, "approval-status", "1")
    assert run.returncode == 2
    assert "shallow checkout" in run.stderr


# --- an approval that is only in the working tree is nobody's act (item 224:
# build and verify ran on an approval record that had never been committed,
# and the merge queue's attest refused it as absent). Written red first
# against v0.29.0, where approval-status read the working-tree record as
# valid. ---


def test_approval_status_reads_uncommitted_until_the_record_is_in_head(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "valid")
    assert run_attavo(root, "spec-gate", "1").returncode == 0
    assert run_attavo(root, "approve", "1", "--by", "alice").returncode == 0
    before = run_attavo(root, "approval-status", "1")
    assert (before.returncode, before.stdout.strip()) == (1, "uncommitted"), before.stdout + before.stderr
    commit_approval(root)
    after = run_attavo(root, "approval-status", "1")
    assert (after.returncode, after.stdout.strip()) == (0, "valid"), after.stdout + after.stderr
    record = approvals(root)[-1]
    record.write_text(record.read_text().replace("alice", "mallory"))
    edited = run_attavo(root, "approval-status", "1")
    assert edited.stdout.strip() == "uncommitted", "a committed record edited in the tree read as valid"


def test_build_refuses_an_uncommitted_approval_and_writes_no_record(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "build_valid")
    assert run_attavo(root, "spec-gate", "1").returncode == 0
    assert run_attavo(root, "approve", "1", "--by", "alice").returncode == 0
    build = run_attavo(root, "build", "1")
    assert build.returncode == 1, build.stdout + build.stderr
    assert "attavo build: refused: approval is uncommitted" in build.stdout, build.stdout
    assert not list((root / "records" / "1").glob("build-*")), "a build record was written"
    commit_approval(root)
    assert run_attavo(root, "build", "1").returncode == 0


def test_approve_says_the_record_must_be_committed(tmp_path: Path) -> None:
    """Red on the stub by design: the sentence is the build's. The command
    that writes the one record no script transports says so as it writes."""
    root = make_tenant(tmp_path, "valid")
    assert run_attavo(root, "spec-gate", "1").returncode == 0
    run = run_attavo(root, "approve", "1", "--by", "alice")
    assert run.returncode == 0
    assert "commit records/1/approval-01.json" in run.stdout, run.stdout
    assert "nothing runs on it until then" in run.stdout, run.stdout
