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

from helpers import extract_step_run

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "workflows" / "attavo-attest.yml"
SCHEMA = json.loads((ROOT / "schema" / "records" / "attestation.schema.json").read_text())
STEP_NAME = "Record the signing outcome"


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
