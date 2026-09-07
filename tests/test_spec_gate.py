"""The spec contract: attavo spec-gate N.

Each test is a rejection test, written and run red before the capability
existed: on `main` at commit 22ca1e1 there was no `spec-gate` subcommand, so
every one of these failed with "unknown subcommand".

Run: python3 -m pytest tests -q
"""
import json
import subprocess
import sys
from pathlib import Path

from helpers import commit_all, make_tenant

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"


def run_attavo(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ATTAVO), *args], cwd=str(root),
                          capture_output=True, text=True, check=False)


def latest_record(root: Path, item: str, stage: str) -> dict:
    records = sorted((root / "records" / item).glob(f"{stage}-*.json"))
    assert records, f"no {stage} record for item {item}"
    return json.loads(records[-1].read_text())


def test_spec_gate_refuses_prose_criterion(tmp_path):
    root = make_tenant(tmp_path, "prose_criterion")
    run = run_attavo(root, "spec-gate", "1")
    assert run.returncode == 1
    assert "missing.sh" in run.stdout
    rec = latest_record(root, "1", "spec")
    assert rec["status"] == "refused"
    assert "missing.sh" in rec["refusal"]


def test_spec_gate_refuses_criterion_passing_on_tree(tmp_path):
    root = make_tenant(tmp_path, "passes_on_tree")
    run = run_attavo(root, "spec-gate", "1")
    assert run.returncode == 1
    assert "checks.sh" in run.stdout
    rec = latest_record(root, "1", "spec")
    assert rec["status"] == "refused"
    assert "checks.sh" in rec["refusal"]


def test_spec_gate_refuses_criterion_failing_on_stub(tmp_path):
    root = make_tenant(tmp_path, "fails_on_stub")
    run = run_attavo(root, "spec-gate", "1")
    assert run.returncode == 1
    assert "checks.sh" in run.stdout
    rec = latest_record(root, "1", "spec")
    assert rec["status"] == "refused"
    assert "checks.sh" in rec["refusal"]


def test_spec_gate_refuses_wrong_headings(tmp_path):
    root = make_tenant(tmp_path, "wrong_headings")
    run = run_attavo(root, "spec-gate", "1")
    assert run.returncode == 1
    assert "Design" in run.stdout
    rec = latest_record(root, "1", "spec")
    assert rec["status"] == "refused"
    assert "Design" in rec["refusal"]


def test_spec_gate_passes_and_writes_record(tmp_path):
    root = make_tenant(tmp_path, "valid")
    run = run_attavo(root, "spec-gate", "1")
    assert run.returncode == 0

    first = root / "records" / "1" / "spec-01.json"
    assert first.is_file()
    rec = json.loads(first.read_text())
    assert rec["status"] == "pass"
    assert rec["spec_sha"]
    assert rec["criteria"] == [{"name": "checks_marker.sh", "on_tree": 1, "on_stub": 0}]
    assert rec["refusal"] is None

    run2 = run_attavo(root, "spec-gate", "1")
    assert run2.returncode == 0
    assert (root / "records" / "1" / "spec-02.json").is_file()


def amend_problem(root: Path) -> None:
    spec_md = root / "specs" / "1" / "spec.md"
    spec_md.write_text(spec_md.read_text().replace(
        "does not emit", "used to not emit"))
    commit_all(root, "amend problem prose")


def approve_valid(root: Path) -> None:
    assert run_attavo(root, "spec-gate", "1").returncode == 0
    assert run_attavo(root, "approve", "1", "--by", "alice").returncode == 0


def complete_the_build(root: Path) -> None:
    """The marker.txt the criterion checks for, committed with no touch to
    spec.md: the same commit shape a real `attavo build` leaves behind --
    spec_sha, and so the approval, is unchanged."""
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "marker.txt").write_text("built\n")
    commit_all(root, "build completed")


def test_spec_gate_reports_approved_when_approval_is_valid(tmp_path):
    """Issue #21, acceptance 1. Once a build has made the criterion pass on
    the tree, the gate must not re-run it and refuse the very success
    approval exists to bind: it reports the approval instead. Red today:
    the unmodified gate refuses this by name ("criterion passes against
    the tree: checks_marker.sh").
    """
    root = make_tenant(tmp_path, "valid")
    approve_valid(root)
    complete_the_build(root)

    run = run_attavo(root, "spec-gate", "1")
    assert run.returncode == 0

    rec = latest_record(root, "1", "spec")
    assert rec["status"] == "approved"
    assert rec["criteria"] == []
    assert rec["refusal"] is None
    assert rec["spec_sha"]
    approval = json.loads(sorted((root / "records" / "1").glob("approval-*.json"))[-1].read_text())
    assert rec["approval_sha"] == approval["sha"]


def test_spec_gate_runs_when_approval_expired(tmp_path):
    """Issue #21, acceptance 2. The same fixture, after an edit above the
    line expires the approval: the gate must fall through to running the
    criteria for real, not keep reporting approved past the approval it
    no longer holds.
    """
    root = make_tenant(tmp_path, "valid")
    approve_valid(root)
    complete_the_build(root)
    assert run_attavo(root, "spec-gate", "1").returncode == 0  # approved, short-circuited

    amend_problem(root)

    run = run_attavo(root, "spec-gate", "1")
    assert run.returncode == 1
    rec = latest_record(root, "1", "spec")
    assert rec["status"] == "refused"
    assert "checks_marker.sh" in rec["refusal"]
