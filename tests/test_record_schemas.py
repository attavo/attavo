"""spec-NN.json, approval-NN.json, and build-NN.json validate against
schema/records/*.schema.json. One test over all fixture records; extend it,
don't duplicate it, as new record shapes land (issue #24's adapter_config
among them).

Run: python3 -m pytest tests -q
"""
import json
import subprocess
import sys
from pathlib import Path

import jsonschema

from helpers import commit_all, make_tenant

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
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text() + "\n[executor.fixture]\ny = 2\n")
    commit_all(root, "configure the fixture adapter")

    assert run_attavo(root, "spec-gate", "1").returncode == 0
    assert run_attavo(root, "approve", "1", "--by", "alice").returncode == 0
    assert run_attavo(root, "build", "1").returncode == 0

    build_rec = json.loads((root / "records" / "1" / "build-01.json").read_text())
    assert build_rec["adapter_config"] == {"y": 2}

    # the build didn't touch spec.md, so the approval is still valid: this
    # spec-gate run writes the new spec-02.json with status "approved".
    approved = run_attavo(root, "spec-gate", "1")
    assert approved.returncode == 0
    assert json.loads((root / "records" / "1" / "spec-02.json").read_text())["status"] == "approved"

    # issue #28: an amendment above the line expires the approval, but a
    # build already ran after it -- status "built", on_stub null.
    spec_md = root / "specs" / "1" / "spec.md"
    spec_md.write_text(spec_md.read_text().replace("does not emit", "used to not emit"))
    commit_all(root, "amend problem prose")
    built = run_attavo(root, "spec-gate", "1")
    assert built.returncode == 0
    spec_03 = json.loads((root / "records" / "1" / "spec-03.json").read_text())
    assert spec_03["status"] == "built"
    assert spec_03["criteria"][0]["on_stub"] is None

    schemas = {"spec": load_schema("spec"), "approval": load_schema("approval"),
              "build": load_schema("build")}

    records = sorted((root / "records" / "1").glob("*.json"))
    assert {p.stem.split("-")[0] for p in records} == {"spec", "approval", "build"}
    for path in records:
        stage = path.stem.split("-")[0]
        jsonschema.validate(json.loads(path.read_text()), schemas[stage])


def test_manifest_schema_accepts_open_executor_config():
    """Issue #24, acceptance 5 (manifest half): [executor.<adapter-name>]
    is an open table -- the schema permits any keys under it, none
    required, alongside the existing build/verify string keys."""
    schema = json.loads((ROOT / "schema" / "manifest.schema.json").read_text())
    manifest = {
        "schema_version": 1,
        "attavo": {"version": "v0.3.1"},
        "executor": {
            "build": "claude-code",
            "claude-code": {"auth": "subscription", "some_future_key": True},
        },
        "spec": {"boundary": {"paths": ["src/**"]}},
        "gates": {},
        "paths": {"specs": "specs", "records": "records",
                 "rulings": "rulings.md", "rejections": "rejections"},
        "operators": {"approvers": ["alice"]},
    }
    jsonschema.validate(manifest, schema)
