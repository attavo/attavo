"""Issue #103: this repository installs Attavo on itself, pinned at
v0.17.1 -- the product's second tenant, and the last product change to
land by the lane rather than the product it builds. Nothing under
bin/, adapters/, schema/, templates/, or action.yml changes for this
issue; every assertion here is about the tenancy files added beside
them: attavo.toml, .attavo/bin/*, the three copied workflows, and
ci.yml's schema-version job.

Run: python3 -m pytest tests -q
"""
from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import jsonschema

from helpers import job_if_condition

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_SCHEMA = ROOT / "schema" / "manifest.schema.json"
MANIFEST = ROOT / "attavo.toml"
SELECT_TESTS = ROOT / ".attavo" / "bin" / "select-tests"
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"
COPIED_WORKFLOWS = [
    ROOT / ".github" / "workflows" / "attavo-spec-gate.yml",
    ROOT / ".github" / "workflows" / "attavo-approve.yml",
    ROOT / ".github" / "workflows" / "attavo-coherence.yml",
]
BOUNDARY_DIRS = ["bin", "adapters", "schema", "templates"]
BOUNDARY_FILES = [ROOT / "action.yml"]
TENANCY_STRINGS = (".attavo/", "attavo.toml", "attest/records")


def load_manifest() -> dict:
    return tomllib.loads(MANIFEST.read_text())


def test_manifest_validates_against_schema():
    schema = __import__("json").loads(MANIFEST_SCHEMA.read_text())
    jsonschema.validate(load_manifest(), schema)


def test_manifest_declares_every_key_issue_103_lists():
    m = load_manifest()
    assert m["attavo"]["version"] == "v0.17.1"
    # No [doctor].from: proof_no_commit_lacks_record only accepts a
    # declared from equal to, or on the first-parent line strictly
    # after, the install commit (schema: "at or after the install
    # commit") -- never a commit before it, which is what "the last
    # ungoverned commit" would have to name. Left absent, proof three's
    # window starts at the install commit itself (the schema's own
    # default), which already holds: the one first-parent commit after
    # it is this issue's own merge, an ordinary merge touching no
    # boundary path. Verified by simulating the merge locally.
    assert "doctor" not in m
    assert m["forge"]["checks_command"]
    assert m["forge"]["file_issue_command"]
    assert m["executor"]["build"] == "claude-code"
    assert m["executor"]["verify"] == "codex"
    assert m["executor"]["claude-code"] == {"auth": "subscription", "model": "claude-sonnet-5"}
    assert m["executor"]["codex"] == {"model": "gpt-5.6-sol"}
    assert m["spec"]["boundary"]["paths"] == [
        "bin/**", "adapters/**", "schema/**", "templates/**", "action.yml", "scripts/**",
    ]
    assert "spec" in m["gates"]
    assert m["gates"]["spec"]["command"] == "attavo spec-gate"
    assert m["gates"]["spec"]["retires"]
    assert m["tests"]["select"]
    assert m["tests"]["run"]
    assert m["paths"] == {
        "specs": "attest/specs", "records": "attest/records",
        "rulings": "attest/rulings.md", "rejections": "attest/rejections",
    }
    assert m["operators"]["approvers"] == ["dbean10"]
    assert m["checks"]["command"]


DESELECT_FLAGS = (
    "--deselect tests/test_doctor.py::test_every_gate_rejects",
    "--deselect tests/test_doctor.py::test_one_item_traversed_every_stage",
    "--deselect tests/test_doctor.py::test_no_commit_lacks_a_record",
    "--deselect tests/test_doctor.py::test_no_stop_lacks_cause",
)


def test_checks_and_tests_run_deselect_the_four_dogfood_doctor_tests():
    """[checks].command and [tests].run must never run the four doctor
    tests that fail until an item has traversed every stage: the first
    item's own build would fail its own checks on a doctor proof no
    item has satisfied yet. [checks].command is ci.yml's test job
    command verbatim, deselects included; [tests].run carries the same
    four flags after `python3 -m pytest -q`. The doctor job in ci.yml is
    the only place the four run without them. Written and run red once
    by dropping one flag from [checks].command, confirmed it failed,
    then restored."""
    m = load_manifest()
    for flag in DESELECT_FLAGS:
        assert flag in m["checks"]["command"], m["checks"]["command"]
        assert flag in m["tests"]["run"], m["tests"]["run"]


def run_select_tests(stdin_text: str) -> list[str]:
    run = subprocess.run([sys.executable, str(SELECT_TESTS)], input=stdin_text,
                         capture_output=True, text=True, check=True)
    return [line for line in run.stdout.splitlines() if line]


def test_select_tests_maps_bin_attavo_to_the_whole_suite():
    assert run_select_tests("bin/attavo\n") == ["tests"]


def test_select_tests_maps_an_adapter_path_to_the_adapter_tests():
    assert run_select_tests("adapters/claude-code/run\n") == [
        "tests/test_adapters.py", "tests/test_executor_conformance.py",
    ]


def test_select_tests_maps_a_schema_path_to_the_record_and_attest_tests():
    assert run_select_tests("schema/records/build.schema.json\n") == [
        "tests/test_record_schemas.py", "tests/test_attest.py",
    ]


def test_select_tests_maps_an_unrelated_path_to_nothing():
    assert run_select_tests("README.md\n") == []


def test_copied_workflows_pin_the_release():
    for path in COPIED_WORKFLOWS:
        assert "uses: attavo/attavo@v0.17.1" in path.read_text(), path.name


def test_schema_version_job_reports_on_merge_group():
    text = CI_YML.read_text()
    lines = text.splitlines()
    on_idx = next(i for i, l in enumerate(lines) if l.strip() == "on:")
    jobs_idx = next(i for i, l in enumerate(lines) if l.strip() == "jobs:")
    on_body = "\n".join(lines[on_idx + 1:jobs_idx])
    assert "merge_group:" in on_body

    condition = job_if_condition(text, "schema-version")
    assert condition is not None
    assert "github.event_name == 'merge_group'" in condition
    assert "github.event_name == 'pull_request'" in condition


# The product's own pre-existing, generic vocabulary that a blind grep for
# TENANCY_STRINGS would otherwise flag: "attavo.toml" is the manifest
# filename every tenant uses, named by the schema itself, not a fact
# about this repository's own installation (adapters/README.md's
# ATTAVO_MANIFEST doc line, bin/attavo's own MANIFEST constant, the
# schema's own title, and the two template workflows' own tomllib read of
# whatever tenant copies them in -- theirs, never this repository's);
# the two bin/attavo docstring lines citing tenant zero's real
# attest/records/659/verify-01.log are a specimen from a different
# repository's history, not a path this product resolves. Each entry is
# (path relative to ROOT, the exact line's stripped text) so a genuinely
# new occurrence -- anywhere else, or a changed line here -- still fails
# the test.
BASELINE = {
    ("adapters/README.md",
     "- `ATTAVO_MANIFEST` — counsel only: the path to the tenant's own `attavo.toml`."),
    ("bin/attavo", 'MANIFEST = "attavo.toml"'),
    ("schema/manifest.schema.json", '"title": "Attavo tenant manifest (attavo.toml)",'),
    ("templates/workflows/attavo-approve.yml",
     'records_rel="$(python3 -c "import tomllib; print(tomllib.load(open(\'attavo.toml\',\'rb\'))[\'paths\'][\'records\'])")"'),
    ("templates/workflows/attavo-spec-gate.yml",
     'records_rel="$(python3 -c "import tomllib; print(tomllib.load(open(\'attavo.toml\',\'rb\'))[\'paths\'][\'records\'])")"'),
    ("bin/attavo", "tenant zero's attest/records/659/verify-01.log: codex's"),
    ("bin/attavo",
     'zero attest/records/659/verify-01.log, codex\'s own "Input exceeds..."'),
}


def test_rejection_no_product_file_names_a_tenancy_path():
    """The product does not know it is a tenant: no file under bin/,
    adapters/, schema/, templates/, or action.yml mentions .attavo/,
    attavo.toml, or attest/records as a path, beyond the pre-existing
    generic mentions in BASELINE. Written and run red once by planting
    "attest/records" in a scratch line under bin/ during development;
    removed once this test caught it."""
    offenders = []
    paths = list(BOUNDARY_FILES)
    for d in BOUNDARY_DIRS:
        paths.extend(p for p in (ROOT / d).rglob("*") if p.is_file())
    for path in paths:
        try:
            lines = path.read_text().splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        relpath = str(path.relative_to(ROOT))
        for line in lines:
            stripped = line.strip()
            if (relpath, stripped) in BASELINE:
                continue
            for needle in TENANCY_STRINGS:
                if needle in line:
                    offenders.append(f"{relpath}: {needle!r} in {stripped!r}")
    assert not offenders, offenders
