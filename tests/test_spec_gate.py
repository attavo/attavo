"""The spec contract: attest spec-gate N.

Each test is a rejection test, written and run red before the capability
existed: on `main` at commit 22ca1e1 there was no `spec-gate` subcommand, so
every one of these failed with "unknown subcommand".

Run: python3 -m pytest tests -q
"""
import json
import subprocess
import sys
from pathlib import Path

from helpers import make_tenant

ROOT = Path(__file__).resolve().parent.parent
ATTEST = ROOT / "bin" / "attest"


def run_attest(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ATTEST), *args], cwd=str(root),
                          capture_output=True, text=True, check=False)


def latest_record(root: Path, item: str, stage: str) -> dict:
    records = sorted((root / "records" / item).glob(f"{stage}-*.json"))
    assert records, f"no {stage} record for item {item}"
    return json.loads(records[-1].read_text())


def test_spec_gate_refuses_prose_criterion(tmp_path):
    root = make_tenant(tmp_path, "prose_criterion")
    run = run_attest(root, "spec-gate", "1")
    assert run.returncode == 1
    assert "missing.sh" in run.stdout
    rec = latest_record(root, "1", "spec")
    assert rec["status"] == "refused"
    assert "missing.sh" in rec["refusal"]


def test_spec_gate_refuses_criterion_passing_on_tree(tmp_path):
    root = make_tenant(tmp_path, "passes_on_tree")
    run = run_attest(root, "spec-gate", "1")
    assert run.returncode == 1
    assert "checks.sh" in run.stdout
    rec = latest_record(root, "1", "spec")
    assert rec["status"] == "refused"
    assert "checks.sh" in rec["refusal"]


def test_spec_gate_refuses_criterion_failing_on_stub(tmp_path):
    root = make_tenant(tmp_path, "fails_on_stub")
    run = run_attest(root, "spec-gate", "1")
    assert run.returncode == 1
    assert "checks.sh" in run.stdout
    rec = latest_record(root, "1", "spec")
    assert rec["status"] == "refused"
    assert "checks.sh" in rec["refusal"]


def test_spec_gate_refuses_wrong_headings(tmp_path):
    root = make_tenant(tmp_path, "wrong_headings")
    run = run_attest(root, "spec-gate", "1")
    assert run.returncode == 1
    assert "Design" in run.stdout
    rec = latest_record(root, "1", "spec")
    assert rec["status"] == "refused"
    assert "Design" in rec["refusal"]


def test_spec_gate_passes_and_writes_record(tmp_path):
    root = make_tenant(tmp_path, "valid")
    run = run_attest(root, "spec-gate", "1")
    assert run.returncode == 0

    first = root / "records" / "1" / "spec-01.json"
    assert first.is_file()
    rec = json.loads(first.read_text())
    assert rec["status"] == "pass"
    assert rec["spec_sha"]
    assert rec["criteria"] == [{"name": "checks_marker.sh", "on_tree": 1, "on_stub": 0}]
    assert rec["refusal"] is None

    run2 = run_attest(root, "spec-gate", "1")
    assert run2.returncode == 0
    assert (root / "records" / "1" / "spec-02.json").is_file()
