"""templates/workflows/attavo-attest.yml: the signing step is
refusal-tolerant, not failure-tolerant (issue #54, acceptance 7). It
carries continue-on-error, and the step after it patches the just-written
attestation record's signed and signature fields from steps.sign.outcome
-- which continue-on-error does not touch, unlike .conclusion -- before
the record is committed. A platform that refuses to sign is reported on
the record, never routed around by failing the run.

This test extracts the literal python out of the "Record the signing
outcome" step (tests/helpers.py:extract_step_run) and runs it against a
real attestation record, so it tracks the template's actual patch logic
instead of a paraphrase of it.

Rejection test, written and run red before the step existed: the
template's step name didn't exist at all, so extract_step_run raised
StopIteration.

Run: python3 -m pytest tests -q
"""
import json
import os
import subprocess
from pathlib import Path

import jsonschema

from helpers import commit_all, extract_step_run, git, make_tenant, step_block

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "workflows" / "attavo-attest.yml"
SCHEMA = json.loads((ROOT / "schema" / "records" / "attestation.schema.json").read_text())
STEP_NAME = "Record the signing outcome"
DOWNSTREAM_STEPS = [
    "Compute the merge commit's subject digest",
    "Sign the attestation record",
    "Record the signing outcome",
    "Commit and push the attestation record",
]


def base_record() -> dict:
    return {
        "sha": "a" * 40, "merge_sha": "a" * 40, "item": "1",
        "spec": "spec-01", "spec_sha": "b" * 40,
        "approval": "approval-01", "approval_sha": "c" * 40,
        "build": "build-01", "build_sha": "d" * 40,
        "verify": "verify-01", "verify_sha": "e" * 40,
        "at": "2026-09-09T00:00:00Z",
        "interventions": [], "acts": [],
        "tokens_by_stage": {"build": {"input": 1, "output": 1}, "verify": {"input": 1, "output": 1}},
        "cost_by_stage_usd": {"build": None, "verify": None},
        "wall_clock_by_stage_s": {"build": 1.0, "verify": 1.0},
        "ci_runs": 0,
        "signed": False, "signature": None,
        "product_version": "v0.7.0", "product_pin": "v0.6.0",
    }


def run_patch_step(record_path: Path, sign_outcome: str, bundle_url: str = "") -> subprocess.CompletedProcess:
    script = extract_step_run(TEMPLATE.read_text(), STEP_NAME)
    env = dict(os.environ, RECORD_PATH=str(record_path), SIGN_OUTCOME=sign_outcome,
              BUNDLE_URL=bundle_url, RUN_URL="https://example.invalid/actions/runs/1")
    return subprocess.run(["bash", "-eo", "pipefail", "-c", script],
                          env=env, capture_output=True, text=True, check=False)


def test_patch_step_writes_signed_false_and_a_reason_when_signing_is_unavailable(tmp_path):
    record_path = tmp_path / "attestation-01.json"
    record_path.write_text(json.dumps(base_record()))

    run = run_patch_step(record_path, sign_outcome="failure")
    assert run.returncode == 0, run.stderr

    rec = json.loads(record_path.read_text())
    jsonschema.validate(rec, SCHEMA)
    assert rec["signed"] is False
    assert rec["signature"]["bundle"] is None
    assert rec["signature"]["reason"]


def test_patch_step_writes_signed_true_and_the_bundle_url_when_signing_succeeds(tmp_path):
    record_path = tmp_path / "attestation-01.json"
    record_path.write_text(json.dumps(base_record()))

    run = run_patch_step(record_path, sign_outcome="success",
                         bundle_url="https://github.example/owner/repo/attestations/1")
    assert run.returncode == 0, run.stderr

    rec = json.loads(record_path.read_text())
    jsonschema.validate(rec, SCHEMA)
    assert rec["signed"] is True
    assert rec["signature"]["bundle"] == "https://github.example/owner/repo/attestations/1"
    assert rec["signature"]["reason"] is None


def test_predicate_type_is_the_ruled_url():
    text = TEMPLATE.read_text()
    assert "predicate-type: https://attavo.dev/attestation/v1" in text


def zero_item_merge_repo(tmp_path: Path) -> Path:
    """A merge to main naming no item at all -- the shape of tenant
    zero's own PR #626, installing this very template."""
    root = make_tenant(tmp_path, "build_valid")
    git(root, "checkout", "-b", "chore/unrelated")
    (root / "README-CHORE.md").write_text("nothing to do with any item\n")
    commit_all(root, "chore: unrelated change")
    git(root, "checkout", "main")
    git(root, "merge", "--no-ff", "-m", "Merge branch 'chore/unrelated' into main",
        "chore/unrelated")
    return root


def run_attest_step(root: Path) -> tuple[subprocess.CompletedProcess, str]:
    """Runs the template's "attavo attest" step exactly as GitHub Actions
    would -- bash -e -o pipefail, a real GITHUB_OUTPUT file, `attavo` on
    PATH -- and returns (result, record_output)."""
    script = extract_step_run(TEMPLATE.read_text(), "attavo attest")
    output_file = root / "_github_output.txt"
    output_file.write_text("")
    env = dict(os.environ, GITHUB_OUTPUT=str(output_file),
              PATH=f"{ROOT / 'bin'}:{os.environ.get('PATH', '')}")
    run = subprocess.run(["bash", "-eo", "pipefail", "-c", script], cwd=str(root),
                         env=env, capture_output=True, text=True, check=False)
    record = ""
    for line in output_file.read_text().splitlines():
        if line.startswith("record="):
            record = line[len("record="):]
    return run, record


def test_attest_step_survives_a_zero_item_merge_with_no_record(tmp_path):
    """Issue #58's second half: attavo attest itself exits 0 and writes no
    record on a zero-item merge (fixed already), but the template's own
    "attavo attest" step runs as `bash -e -o pipefail`, and the grep that
    looks for the record it just wrote exits 1 on no match -- exactly the
    expected outcome here -- which -e turns into a failed step. Red
    before this lane's second fix: the step itself exits non-zero."""
    root = zero_item_merge_repo(tmp_path)

    run, record = run_attest_step(root)
    assert run.returncode == 0, run.stderr
    assert record == ""


def test_steps_after_attest_are_guarded_on_a_non_empty_record():
    """The four steps after "attavo attest" -- computing the subject
    digest, signing, recording the signing outcome, and committing and
    pushing -- have nothing to sign or commit when attest wrote no
    record, and must not run then, the same guard shape already used for
    the record-only skip."""
    text = TEMPLATE.read_text()
    for name in DOWNSTREAM_STEPS:
        block = step_block(text, f"- name: {name}")
        if_line = next(l for l in block.splitlines() if l.strip().startswith("if:"))
        assert "steps.attest.outputs.record" in if_line, name
        assert "!= ''" in if_line, name
