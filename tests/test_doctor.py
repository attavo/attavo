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


def test_doctor_proof_two_counts_three_of_five_stages_still_red(tmp_path):
    """Acceptance 8. A work item carrying spec, approval, and build records
    -- the three stages this lane can produce -- is three of the five
    STAGES proof two names; verify and attestation don't exist yet, so the
    proof stays red and names exactly those two as lacking.
    """
    root = tmp_path / "three_stages"
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    init_repo(root, "install")

    records = root / "records" / "1"
    records.mkdir(parents=True)
    (records / "spec-01.json").write_text(json.dumps({"status": "pass"}))
    (records / "approval-01.json").write_text(json.dumps({"approved_by": "alice"}))
    (records / "build-01.json").write_text(json.dumps({"status": "pass"}))
    commit_all(root, "spec, approval, and build records")

    p = run_doctor(root)["one-item-every-stage"]
    assert not p["holds"]
    assert "verify" in p["reason"] and "attestation" in p["reason"]
    assert "build" not in p["reason"]


def test_doctor_proof_two_holds_on_a_fixture_carrying_all_five_stages(tmp_path):
    """Acceptance 5, issue #54: proof two is already generic over STAGES --
    what was missing was a record type named attestation ever existing.
    A fixture with one record per stage, attestation included, is enough
    to prove the mechanism; it goes green on tenant zero in the lane that
    follows this one."""
    root = tmp_path / "five_stages"
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    init_repo(root, "install")

    records = root / "records" / "1"
    records.mkdir(parents=True)
    (records / "spec-01.json").write_text(json.dumps({"status": "pass"}))
    (records / "approval-01.json").write_text(json.dumps({"approved_by": "alice"}))
    (records / "build-01.json").write_text(json.dumps({"status": "pass"}))
    (records / "verify-01.json").write_text(json.dumps({"status": "pass"}))
    (records / "attestation-01.json").write_text(json.dumps({"item": "1"}))
    commit_all(root, "spec, approval, build, verify, and attestation records")

    p = run_doctor(root)["one-item-every-stage"]
    assert p["holds"], p["reason"]


def test_no_commit_lacks_a_record_via_attestation_merge_sha(tmp_path):
    """Acceptance 6, issue #54: a merge commit is a real change, not a
    records-only one, so it must be named explicitly. An attestation
    record's merge_sha does that -- naming it only by merge_sha, not sha
    or commits, isolates the new code path from the two that already
    existed."""
    root = tmp_path / "merge_sha"
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    init_repo(root, "install")

    (root / "src.txt").write_text("code\n")
    commit_all(root, "a real change, not yet named by any record")
    merge_sha = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                               capture_output=True, text=True, check=True).stdout.strip()

    p = run_doctor(root)["no-commit-lacks-record"]
    assert not p["holds"]

    records = root / "records" / "1"
    records.mkdir(parents=True)
    (records / "attestation-01.json").write_text(json.dumps({"merge_sha": merge_sha}))
    commit_all(root, "attestation record naming the merge commit")

    p = run_doctor(root)["no-commit-lacks-record"]
    assert p["holds"], p["reason"]


def test_doctor_proof_one_refuses_a_gate_with_empty_retires(tmp_path):
    """Residual, issue #54 -- proposed, not yet ruled on: a gate that
    retires nothing only adds. The manifest schema has required
    [gates.<name>].retires to be non-empty since Phase 1, but read_manifest
    never validates against that schema, so nothing enforced it at
    runtime until this proof read it directly."""
    root = tmp_path / "empty_retires"
    root.mkdir()
    (root / "attavo.toml").write_text(
        '[paths]\nrejections = "rejections"\n\n'
        '[gates.spec]\ncommand = "false"\nretires = []\n')
    (root / "rejections" / "spec").mkdir(parents=True)
    init_repo(root, "install")

    p = run_doctor(root)["every-gate-rejects"]
    assert not p["holds"]
    assert "retires" in p["reason"] and "spec" in p["reason"]


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
