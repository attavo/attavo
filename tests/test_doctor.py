"""The doctor's three proofs, as tests written before the capability.

Each test asserts that one proof holds against this repository. All three
fail at the first commit and must fail: that is the definition of done,
stated before anything exists to satisfy it. A test here turns green only
when the capability it names has landed and been used for real.

Run: python3 -m pytest tests -q
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

from helpers import FIXTURES, commit_all, init_repo

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"


def run_doctor(root: Path) -> dict:
    out = subprocess.run([sys.executable, str(ATTAVO), "doctor", str(root), "--json"],
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


def test_record_loader_uses_highest_attempt_number(tmp_path):
    """Acceptance 2a. <stage>-NN.json files are one stage's attempts; the
    doctor treats the highest NN as latest, and STAGES is unchanged.

    spec-01.json names a commit that isn't on the branch at all; spec-02.json
    names the real code commit. Proof 3 holds only if the loader reads
    spec-02, not spec-01 -- i.e. only if it picks the highest attempt.
    """
    root = tmp_path / "attempts"
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    init_repo(root, "install")

    (root / "src.txt").write_text("code\n")
    commit_all(root, "code change")
    code_sha = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()

    records = root / "records" / "1"
    records.mkdir(parents=True)
    (records / "spec-01.json").write_text(json.dumps({"sha": "0" * 40}))
    (records / "spec-02.json").write_text(json.dumps({"sha": code_sha}))
    commit_all(root, "spec records")

    p = run_doctor(root)["no-commit-lacks-record"]
    assert p["holds"], p["reason"]


def test_doctor_proof_one_runs_the_gate_without_an_item_argument(tmp_path):
    """Issue #7, and its regression test. [gates.spec].command is a bare
    `attavo spec-gate`, no item argument, and its rejections/spec/ fixture
    carries no attavo/N-* branch at all (init_repo leaves it on `main`) --
    so before the doctor exported the fixture's item as ATTAVO_ITEM, the
    gate command would exit 2 (cannot derive item id) without ever writing
    a record, which still counts as "rejected" but proves nothing about the
    prose criterion this fixture means to test.

    Red at the merge base: no ATTAVO_ITEM export existed, so no
    spec-01.json was ever written and reading it raised FileNotFoundError.
    Green once the doctor exports it: the record exists and names the
    prose criterion by name, which is the actual thing proof one asserts.
    """
    outer = tmp_path / "gate_rejection"
    shutil.copytree(FIXTURES / "gate_rejection", outer)
    cmd = f"{sys.executable} {ATTAVO}"
    toml = outer / "attavo.toml"
    toml.write_text(toml.read_text().replace("ATTAVO_CMD", cmd))
    init_repo(outer / "rejections" / "spec")

    p = run_doctor(outer)["every-gate-rejects"]
    assert p["holds"], p["reason"]

    record = json.loads(
        (outer / "rejections" / "spec" / "records" / "1" / "spec-01.json").read_text())
    assert record["refusal"] == "criterion is prose: missing.sh"
