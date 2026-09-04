"""The doctor's three proofs, as tests written before the capability.

Each test asserts that one proof holds against this repository. All three
fail at the first commit and must fail: that is the definition of done,
stated before anything exists to satisfy it. A test here turns green only
when the capability it names has landed and been used for real.

Run: python3 -m pytest tests -q
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ATTEST = ROOT / "bin" / "attest"


def run_doctor(root: Path) -> dict:
    out = subprocess.run([sys.executable, str(ATTEST), "doctor", str(root), "--json"],
                         capture_output=True, text=True, check=False)
    report = json.loads(out.stdout)
    assert out.returncode == (0 if report["done"] else 1)
    return {p["proof"]: p for p in report["proofs"]}


def test_every_gate_rejects():
    """Proof 1. Every gate in the manifest rejects its rejection fixture.

    Holds when: a manifest exists, [gates] names at least one gate, each gate
    has a fixture under paths.rejections, and each gate exits non-zero on it.
    """
    p = run_doctor(ROOT)["every-gate-rejects"]
    assert p["holds"], p["reason"]


def test_one_item_traversed_every_stage():
    """Proof 2. One work item carries a record for spec, approval, build,
    verify, and attestation, every one written by a script."""
    p = run_doctor(ROOT)["one-item-every-stage"]
    assert p["holds"], p["reason"]


def test_no_commit_lacks_a_record():
    """Proof 3. Every commit on the default branch after the manifest was
    introduced is named by a record's sha or commits list."""
    p = run_doctor(ROOT)["no-commit-lacks-record"]
    assert p["holds"], p["reason"]
