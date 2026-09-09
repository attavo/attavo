"""spec-NN.json, approval-NN.json, build-NN.json, and verify-NN.json
validate against schema/records/*.schema.json. One test over all fixture
records; extend it, don't duplicate it, as new record shapes land (issue
#24's adapter_config, issue #40's verify, among them).

Run: python3 -m pytest tests -q
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

from helpers import commit_all, make_tenant

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"
SCHEMAS_DIR = ROOT / "schema" / "records"


def run_attavo(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ATTAVO), *args], cwd=str(root),
                          capture_output=True, text=True, check=False)


def load_schema(stage: str) -> dict:
    return json.loads((SCHEMAS_DIR / f"{stage}.schema.json").read_text())


def attavo_module():
    """bin/attavo imported in-process, for the write_record/load_records
    unit tests below that a CLI invocation cannot provoke (there is no
    command-line path to a record carrying a key its schema forbids)."""
    loader = importlib.machinery.SourceFileLoader("attavo_under_test", str(ATTAVO))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_records_match_their_shapes(tmp_path):
    root = make_tenant(tmp_path, "build_valid")
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text()
                    + '\n[executor.fixture]\ny = 2\n'
                    + '\n[checks]\ncommand = "true"\n'
                    + """\n[forge]\nchecks_command = "printf 'ci success\\n'"\n""")
    commit_all(root, "configure the fixture adapter, the tenant's checks, and the forge read")

    assert run_attavo(root, "spec-gate", "1").returncode == 0
    assert run_attavo(root, "approve", "1", "--by", "alice").returncode == 0
    assert run_attavo(root, "build", "1").returncode == 0

    build_rec = json.loads((root / "records" / "1" / "build-01.json").read_text())
    assert build_rec["adapter_config"] == {"y": 2}
    assert build_rec["checks"] == {"command": "true", "exit": 0}

    verify = run_attavo(root, "verify", "1")
    assert verify.returncode == 0
    verify_rec = json.loads((root / "records" / "1" / "verify-01.json").read_text())
    assert verify_rec["status"] == "pass"
    assert verify_rec["findings"]

    # the build didn't touch spec.md, so the approval is still valid: this
    # spec-gate run writes the new spec-02.json with status "approved".
    approved = run_attavo(root, "spec-gate", "1")
    assert approved.returncode == 0
    assert json.loads((root / "records" / "1" / "spec-02.json").read_text())["status"] == "approved"

    # issue #28: an amendment above the line expires the approval, but a
    # build already ran after it -- status "built". Issue #36: built now
    # judges each criterion on both on_tree and on_stub, like a pass
    # record; this fixture's stub also satisfies checks_built.sh, so
    # on_stub is 0, not null.
    spec_md = root / "specs" / "1" / "spec.md"
    spec_md.write_text(spec_md.read_text().replace("does not emit", "used to not emit"))
    commit_all(root, "amend problem prose")
    built = run_attavo(root, "spec-gate", "1")
    assert built.returncode == 0
    spec_03 = json.loads((root / "records" / "1" / "spec-03.json").read_text())
    assert spec_03["status"] == "built"
    assert spec_03["criteria"][0]["on_stub"] == 0

    # issue #54: an explicit item id bypasses attest's merge-commit
    # derivation -- the chain this fixture already built (build-01 against
    # approval-01, verify-01 against build-01) is exactly what attest
    # checks, so no merge commit is needed to prove the record's shape.
    assert run_attavo(root, "attest", "1").returncode == 0

    schemas = {"spec": load_schema("spec"), "approval": load_schema("approval"),
              "build": load_schema("build"), "verify": load_schema("verify"),
              "attestation": load_schema("attestation")}

    records = sorted((root / "records" / "1").glob("*.json"))
    assert ({p.stem.split("-")[0] for p in records}
           == {"spec", "approval", "build", "verify", "attestation"})
    for path in records:
        stage = path.stem.split("-")[0]
        jsonschema.validate(json.loads(path.read_text()), schemas[stage])

    # issue #49: product_version and product_pin are not optional extras --
    # every record shape requires them, the same as any other field.
    for stage, schema in schemas.items():
        assert "product_version" in schema["required"], stage
        assert "product_pin" in schema["required"], stage

    # issue #60, acceptance 1: every record write_record writes carries
    # record_version -- equal to that stage schema's own $id version, the
    # single place the version lives -- and item, matching the directory
    # it was written under.
    for path in records:
        stage = path.stem.split("-")[0]
        rec = json.loads(path.read_text())
        assert rec["record_version"] == int(schemas[stage]["$id"].rsplit("/", 1)[-1]), stage
        assert rec["item"] == "1", stage


def test_records_carry_product_version_distinct_from_pin(tmp_path):
    """Issue #49: a record names both what ran (product_version, derived
    from the running checkout via git describe) and what the tenant
    declared (product_pin, copied verbatim from the manifest). The
    build_valid fixture pins v0.2.1; this checkout is not on that tag, so
    every record type written against it must carry both fields and they
    must differ -- the mismatch is a recorded fact, not an error nothing
    here refuses on."""
    root = make_tenant(tmp_path, "build_valid")
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text()
                    + '\n[executor.fixture]\ny = 2\n'
                    + '\n[checks]\ncommand = "true"\n'
                    + """\n[forge]\nchecks_command = "printf 'ci success\\n'"\n""")
    commit_all(root, "configure the fixture adapter, the tenant's checks, and the forge read")

    assert run_attavo(root, "spec-gate", "1").returncode == 0
    assert run_attavo(root, "approve", "1", "--by", "alice").returncode == 0
    assert run_attavo(root, "build", "1").returncode == 0
    assert run_attavo(root, "verify", "1").returncode == 0

    running_version = subprocess.run(
        ["git", "describe", "--tags", "--always", "--dirty"],
        cwd=str(ROOT), capture_output=True, text=True, check=True).stdout.strip()

    records = sorted((root / "records" / "1").glob("*.json"))
    assert {p.stem.split("-")[0] for p in records} == {"spec", "approval", "build", "verify"}
    for path in records:
        rec = json.loads(path.read_text())
        assert rec["product_pin"] == "v0.2.1"
        assert rec["product_version"] == running_version
        assert rec["product_version"] != rec["product_pin"]


def test_product_version_unknown_when_product_root_is_not_a_git_repo(tmp_path):
    """Issue #49 follow-up: GitHub Actions extracts an action into
    _actions/ from a tarball, not a git checkout -- PRODUCT_ROOT there has
    no .git, so `git describe` fails and, unguarded, raises inside
    write_record: the one function that must never fail, since it runs
    after the work it records already happened. A copy of bin/attavo
    placed under a fresh, non-git tmp_path reproduces PRODUCT_ROOT with no
    .git without needing an actual GitHub Actions run. Red today: the
    KeyError this reproduces is a RuntimeError from git(), uncaught, and
    no record is written at all. schema/ is copied alongside bin/, same as
    the real action extraction: write_record reads schema/records/<stage>.schema.json
    from PRODUCT_ROOT (issue #60), which the action ships beside bin/."""
    product_bin = tmp_path / "product" / "bin"
    product_bin.mkdir(parents=True)
    shutil.copy(ATTAVO, product_bin / "attavo")
    shutil.copytree(ROOT / "schema", tmp_path / "product" / "schema")

    root = make_tenant(tmp_path, "build_valid", name="tenant")

    env = dict(os.environ)
    env.pop("ATTAVO_PRODUCT_VERSION", None)
    run = subprocess.run([sys.executable, str(product_bin / "attavo"), "spec-gate", "1"],
                        cwd=str(root), env=env, capture_output=True, text=True, check=False)
    assert run.returncode == 0, run.stderr

    rec = json.loads((root / "records" / "1" / "spec-01.json").read_text())
    assert rec["product_version"] == "unknown"


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


def base_manifest() -> dict:
    return {
        "schema_version": 1,
        "attavo": {"version": "v0.3.3"},
        "executor": {"build": "fixture"},
        "spec": {"boundary": {"paths": ["src/**"]}},
        "gates": {},
        "paths": {"specs": "specs", "records": "records",
                 "rulings": "rulings.md", "rejections": "rejections"},
        "operators": {"approvers": ["alice"]},
    }


def test_manifest_schema_describes_checks():
    """Issue #30, acceptance 4 (manifest half): [checks] is optional, with
    command required inside it -- a manifest with no [checks] table at
    all is still valid (unlike [tests], which build itself requires)."""
    schema = json.loads((ROOT / "schema" / "manifest.schema.json").read_text())

    without_checks = base_manifest()
    jsonschema.validate(without_checks, schema)

    with_checks = base_manifest()
    with_checks["checks"] = {"command": "python3 bin/factory-lane check --base origin/main"}
    jsonschema.validate(with_checks, schema)


# --- issue #60: records declare their own record_version and item ---

def test_write_record_refuses_a_key_its_schema_does_not_declare(tmp_path):
    """Acceptance 2: write_record enforces the declared key set at write
    time -- a record carrying a key its stage's schema does not list under
    "properties" is refused, and nothing is written. Enforced with the
    standard library (set difference against the schema's own properties),
    not a jsonschema import into bin/attavo -- it is a single script a
    tenant pins, and a runtime dependency changes installation."""
    attavo = attavo_module()
    schema = load_schema("spec")
    record = {k: None for k in schema["properties"]
             if k not in ("record_version", "item", "product_version", "product_pin")}
    record["unexpected_key"] = "not in the schema"

    records_dir = tmp_path / "records" / "1"
    with pytest.raises(ValueError, match="unexpected_key"):
        attavo.write_record(records_dir, "spec", record, {"attavo": {}}, "1")
    assert list(records_dir.glob("spec-*.json")) == []


def test_write_record_stamps_record_version_from_the_schemas_id(tmp_path):
    """Acceptance/correction point 2: record_version is read from the
    stage schema's own $id at call time rather than carried separately in
    bin/attavo, so it cannot drift from what the schema declares. Point
    write_record at a copy of the real build schema whose $id is bumped
    past what ships today, and confirm the written record follows it."""
    attavo = attavo_module()
    schema_dir = tmp_path / "schema"
    (schema_dir / "records").mkdir(parents=True)
    build_schema = load_schema("build")
    bumped = json.loads(json.dumps(build_schema))
    stem, _, tail = bumped["$id"].rpartition("/")
    bumped["$id"] = f"{stem}/{int(tail) + 1}"
    (schema_dir / "records" / "build.schema.json").write_text(json.dumps(bumped))

    real_schema_dir = attavo.SCHEMA_RECORDS_DIR
    attavo.SCHEMA_RECORDS_DIR = schema_dir / "records"
    try:
        record = {k: None for k in build_schema["properties"]
                 if k not in ("record_version", "item", "product_version", "product_pin")}
        path = attavo.write_record(tmp_path / "records" / "1", "build", record,
                                   {"attavo": {}}, "1")
    finally:
        attavo.SCHEMA_RECORDS_DIR = real_schema_dir

    assert json.loads(path.read_text())["record_version"] == int(tail) + 1


def _schema_shape(schema: dict) -> str:
    props = {k: v for k, v in schema.get("properties", {}).items() if k != "record_version"}
    required = sorted(k for k in schema.get("required", []) if k != "record_version")
    return json.dumps({"properties": props, "required": required}, sort_keys=True)


def schema_version_drift(old: dict, new: dict) -> str | None:
    """None when the shape is unchanged, or changed alongside a bumped
    $id. A message when the shape changed but $id's trailing integer
    didn't -- a version nobody bumps is worse than none, because readers
    trust it (issue #60, acceptance 3)."""
    old_version = old["$id"].rsplit("/", 1)[-1]
    new_version = new["$id"].rsplit("/", 1)[-1]
    if _schema_shape(old) != _schema_shape(new) and old_version == new_version:
        return f"schema/records shape changed but $id version is still {new_version}"
    return None


def test_schema_shape_change_without_a_version_bump_is_refused():
    """Acceptance 3, run against a mutated schema fixture: drop-in a new
    required field on the real, committed build schema without touching
    its $id -- refused. The identical mutation with $id bumped alongside
    it is not."""
    base = load_schema("build")
    mutated = json.loads(json.dumps(base))
    mutated["properties"]["a_new_field"] = {"type": "string", "minLength": 1}
    mutated["required"] = mutated["required"] + ["a_new_field"]
    assert schema_version_drift(base, mutated) is not None

    bumped = json.loads(json.dumps(mutated))
    stem, _, tail = bumped["$id"].rpartition("/")
    bumped["$id"] = f"{stem}/{int(tail) + 1}"
    assert schema_version_drift(base, bumped) is None

    # a change carrying no shape change at all -- e.g. a description edit
    # -- never needs a version bump.
    reworded = json.loads(json.dumps(base))
    reworded["title"] = reworded["title"] + " (reworded)"
    assert schema_version_drift(base, reworded) is None


def test_load_records_defaults_a_version_1_record_and_fills_its_item(tmp_path):
    """Acceptance 4: a record written before record_version and item
    shipped -- tenant zero's real shape at 3648830, common keys across 49
    records: ['at', 'sha'] and nothing else -- loads with record_version 1
    and item filled from the directory it sits under. The disk copy is
    never touched: version 1 is never migrated (issue #60, operator
    ruling 2026-09-09: record_version is a label, never a trigger for
    reading or upgrading an older record)."""
    attavo = attavo_module()
    records_dir = tmp_path / "records" / "1"
    records_dir.mkdir(parents=True)
    tenant_zero_shape = {"sha": "a" * 40, "at": "2026-01-01T00:00:00Z"}
    (records_dir / "build-01.json").write_text(json.dumps(tenant_zero_shape))

    items = attavo.load_records(tmp_path / "records")
    rec = items["1"]["build"]
    assert rec["record_version"] == 1
    assert rec["item"] == "1"

    on_disk = json.loads((records_dir / "build-01.json").read_text())
    assert on_disk == tenant_zero_shape


def test_load_records_refuses_an_item_that_disagrees_with_its_directory(tmp_path):
    """Acceptance 5: a record whose item field names a different item
    than the directory it was found in -- a misfiled or copied record --
    is refused rather than trusted."""
    attavo = attavo_module()
    records_dir = tmp_path / "records" / "1"
    records_dir.mkdir(parents=True)
    (records_dir / "build-01.json").write_text(json.dumps(
        {"sha": "a" * 40, "at": "2026-01-01T00:00:00Z", "item": "2"}))

    with pytest.raises(attavo.RecordItemMismatch):
        attavo.load_records(tmp_path / "records")


def test_only_load_record_parses_record_file_content():
    """Acceptance 6: _load_record is the one place in this repository
    that turns a <stage>-NN.json record's bytes into a dict; parse_attempts
    and load_records both read through it rather than calling json.loads
    themselves. `git grep -n "json.loads(" bin/attavo` still finds three
    other sites -- a schema file, an adapter's conformance.json, and an
    adapter's own stdout report -- none of them a record."""
    text = ATTAVO.read_text()
    assert text.count("json.loads(") == 4, (
        "a new json.loads( site was added; review it against issue #60 acceptance 6 "
        "and update this count")
    assert "data = json.loads(path.read_text())" in text

    for fn in ("parse_attempts", "load_records"):
        start = text.index(f"\ndef {fn}(")
        end = text.index("\ndef ", start + 1)
        body = text[start:end]
        assert "json.loads(" not in body, f"{fn} should read through _load_record, not parse directly"
        assert "_load_record(" in body
