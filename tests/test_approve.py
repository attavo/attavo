"""Approval bound to a commit: attest approve N --by LOGIN, attest approval-status N.

Rejection tests, written and run red before the capability existed: on
`main` at commit 22ca1e1 there was no `approve` or `approval-status`
subcommand, so every one of these failed with "unknown subcommand".

Run: python3 -m pytest tests -q
"""
import subprocess
import sys
from pathlib import Path

from helpers import clone_shallow, commit_all, make_tenant

ROOT = Path(__file__).resolve().parent.parent
ATTEST = ROOT / "bin" / "attest"


def run_attest(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ATTEST), *args], cwd=str(root),
                          capture_output=True, text=True, check=False)


def approvals(root: Path, item: str = "1") -> list[Path]:
    return sorted((root / "records" / item).glob("approval-*.json"))


def amend_problem(root: Path) -> None:
    spec_md = root / "specs" / "1" / "spec.md"
    spec_md.write_text(spec_md.read_text().replace(
        "does not emit", "used to not emit"))
    commit_all(root, "amend problem prose")


def test_approve_refuses_without_current_pass(tmp_path):
    root = make_tenant(tmp_path, "valid")

    # no spec-gate has run at all yet
    run = run_attest(root, "approve", "1", "--by", "alice")
    assert run.returncode == 1
    assert approvals(root) == []

    # a pass exists, but the spec has since moved without a fresh gate run
    assert run_attest(root, "spec-gate", "1").returncode == 0
    amend_problem(root)

    stale = run_attest(root, "approve", "1", "--by", "alice")
    assert stale.returncode == 1
    assert "stale" in stale.stdout
    assert approvals(root) == []


def test_approve_refuses_unlisted_login(tmp_path):
    root = make_tenant(tmp_path, "valid")
    assert run_attest(root, "spec-gate", "1").returncode == 0

    run = run_attest(root, "approve", "1", "--by", "mallory")
    assert run.returncode == 1
    assert "mallory" in run.stdout
    assert approvals(root) == []


def test_amendment_above_line_costs_one_act(tmp_path):
    root = make_tenant(tmp_path, "valid")

    assert run_attest(root, "spec-gate", "1").returncode == 0
    assert run_attest(root, "approve", "1", "--by", "alice").returncode == 0
    assert run_attest(root, "approval-status", "1").stdout.strip() == "valid"

    amend_problem(root)
    expired = run_attest(root, "approval-status", "1")
    assert expired.returncode == 1
    assert expired.stdout.strip() == "expired"

    # the next gate run CI performs on every push
    assert run_attest(root, "spec-gate", "1").returncode == 0
    assert run_attest(root, "approve", "1", "--by", "alice").returncode == 0

    valid_again = run_attest(root, "approval-status", "1")
    assert valid_again.returncode == 0
    assert valid_again.stdout.strip() == "valid"

    assert [p.name for p in approvals(root)] == ["approval-01.json", "approval-02.json"]


def test_approve_refuses_shallow_checkout(tmp_path):
    """On a --depth 1 clone, `git log -1 -- <path>` returns HEAD for every
    path (there is no history to prove otherwise), so a shallow checkout
    would compute a spec_sha that always matches HEAD and pass approval
    for a spec that may have moved. attest approve must refuse instead of
    trusting that number: exit 2, naming the cause, not "stale".
    """
    root = make_tenant(tmp_path, "valid")
    assert run_attest(root, "spec-gate", "1").returncode == 0
    commit_all(root, "spec-gate record")

    shallow = tmp_path / "shallow"
    clone_shallow(root, shallow)

    run = run_attest(shallow, "approve", "1", "--by", "alice")
    assert run.returncode == 2
    assert "shallow checkout" in run.stderr
    assert approvals(shallow) == []


def test_approval_status_refuses_shallow_checkout(tmp_path):
    root = make_tenant(tmp_path, "valid")
    assert run_attest(root, "spec-gate", "1").returncode == 0
    assert run_attest(root, "approve", "1", "--by", "alice").returncode == 0
    commit_all(root, "spec-gate and approval records")

    shallow = tmp_path / "shallow"
    clone_shallow(root, shallow)

    run = run_attest(shallow, "approval-status", "1")
    assert run.returncode == 2
    assert "shallow checkout" in run.stderr
