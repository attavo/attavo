"""Acceptance 7: spec-NN.json, approval-NN.json, and build-NN.json validate
against schema/records/*.schema.json, added by this lane. One test over all
fixture records; extend it, don't duplicate it, in Lanes 3 and 4 (verify,
attestation).

Run: python3 -m pytest tests -q
"""
import json
import subprocess
import sys
from pathlib import Path

import jsonschema

from helpers import make_tenant

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"
SCHEMAS_DIR = ROOT / "schema" / "records"


def run_attavo(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ATTAVO), *args], cwd=str(root),
                          capture_output=True, text=True, check=False)


def load_schema(stage: str) -> dict:
    return json.loads((SCHEMAS_DIR / f"{stage}.schema.json").read_text())


def test_records_match_their_shapes(tmp_path):
    root = make_tenant(tmp_path, "build_valid")
    assert run_attavo(root, "spec-gate", "1").returncode == 0
    assert run_attavo(root, "approve", "1", "--by", "alice").returncode == 0
    assert run_attavo(root, "build", "1").returncode == 0

    # the build didn't touch spec.md, so the approval is still valid: this
    # spec-gate run writes the new spec-02.json with status "approved".
    approved = run_attavo(root, "spec-gate", "1")
    assert approved.returncode == 0
    assert json.loads((root / "records" / "1" / "spec-02.json").read_text())["status"] == "approved"

    schemas = {"spec": load_schema("spec"), "approval": load_schema("approval"),
              "build": load_schema("build")}

    records = sorted((root / "records" / "1").glob("*.json"))
    assert {p.stem.split("-")[0] for p in records} == {"spec", "approval", "build"}
    for path in records:
        stage = path.stem.split("-")[0]
        jsonschema.validate(json.loads(path.read_text()), schemas[stage])
