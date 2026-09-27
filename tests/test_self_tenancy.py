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

import re
from typing import Any

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


def load_manifest() -> dict[str, Any]:
    return tomllib.loads(MANIFEST.read_text())


def test_manifest_validates_against_schema() -> None:
    schema = __import__("json").loads(MANIFEST_SCHEMA.read_text())
    jsonschema.validate(load_manifest(), schema)


def test_manifest_declares_every_key_issue_103_lists() -> None:
    m = load_manifest()
    assert re.fullmatch(r"v\d+\.\d+\.\d+", m["attavo"]["version"]), m["attavo"]["version"]
    # No [doctor].from: proof_no_commit_lacks_record only accepts a
    # declared from equal to, or on the first-parent line strictly
    # after, the install commit (schema: "at or after the install
    # commit") -- never a commit before it, which is what "the last
    # ungoverned commit" would have to name. Left absent, proof three's
    # window starts at the install commit itself (the schema's own
    # default), which already holds: the one first-parent commit after
    # it is this issue's own merge, an ordinary merge touching no
    # boundary path. Verified by simulating the merge locally.
    # Item 227 added [doctor].built_since, the window for proof seven; [doctor].from
    # stays absent for the reason above.
    assert set(m.get("doctor") or {}) == {"built_since"}, m.get("doctor")
    assert m["forge"]["checks_command"]
    assert m["forge"]["file_issue_command"]
    paths = m["spec"]["boundary"]["paths"]
    assert "bin/**" in paths and "attavo/**" in paths, paths
    assert "spec" in m["gates"]
    assert m["gates"]["spec"]["retires"]
    assert m["tests"]["select"]
    assert m["tests"]["run"]
    assert m["operators"]["approvers"]
    assert m["checks"]["command"]


def test_tests_run_and_the_test_job_exclude_the_own_records_tests_by_marker() -> None:
    """The tests that read this repository's own records -- the doctor's
    proofs among them -- must never run inside an item's gate or the
    test job: a proof no item has satisfied yet, or one that reads the
    running build's own marker, fails a build for nothing (#229
    build-04: the gate ran the whole suite and
    test_no_invocation_lacks_record read build-04's own .started marker
    as a dead invocation). They are told apart by @pytest.mark.own_records
    and by nothing else: [tests].run and ci.yml's test job carry
    -m 'not own_records', the doctor job and the inherit path carry
    -m own_records, and no --deselect names a test anywhere. Three
    hand-copied lists did this before and one was missing a test."""
    m = load_manifest()
    assert "-m 'not own_records'" in m["tests"]["run"], m["tests"]["run"]
    assert "--deselect" not in m["tests"]["run"], m["tests"]["run"]
    assert "--deselect" not in m["checks"]["command"], m["checks"]["command"]
    if "pytest" in m["checks"]["command"]:
        assert "-m 'not own_records'" in m["checks"]["command"], m["checks"]["command"]
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "--deselect" not in ci
    assert "python3 -m pytest tests -q --durations=5 -m 'not own_records'" in ci
    assert ci.count("python3 -m pytest tests -q -m own_records") == 2, "the inherit path and the doctor job"


def run_select_tests(stdin_text: str) -> list[str]:
    run = subprocess.run([sys.executable, str(SELECT_TESTS)], input=stdin_text,
                         capture_output=True, text=True, check=True)
    return [line for line in run.stdout.splitlines() if line]


def test_select_tests_maps_bin_attavo_to_the_whole_suite() -> None:
    assert run_select_tests("bin/attavo\n") == ["tests"]


def test_select_tests_maps_an_adapter_path_to_the_adapter_tests() -> None:
    selected = run_select_tests("adapters/example/run\n")
    assert "tests/test_adapters.py" in selected and "tests/test_executor_conformance.py" in selected
    assert "tests" not in selected, "an adapter path selected the whole suite"


def test_select_tests_maps_a_schema_path_to_the_record_and_attest_tests() -> None:
    selected = run_select_tests("schema/records/build.schema.json\n")
    assert "tests/test_record_schemas.py" in selected and "tests/test_attest.py" in selected
    assert "tests" not in selected, "a schema path selected the whole suite"


def test_select_tests_maps_an_unrelated_path_to_nothing() -> None:
    assert run_select_tests("README.md\n") == []


def test_copied_workflows_pin_the_release_the_manifest_declares() -> None:
    pin = load_manifest()["attavo"]["version"]
    for path in COPIED_WORKFLOWS:
        assert f"uses: attavo/attavo@{pin}" in path.read_text(), (path.name, pin)


def test_schema_version_job_reports_on_merge_group() -> None:
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


def test_doctor_job_reports_on_pull_request_and_merge_group() -> None:
    """Issue #113: the doctor job's #106 condition, main-only, hid it from
    pull requests and merge groups -- the events where a refusal is
    cheap. It must name both, and never reduce to the main-only shape a
    later edit could reintroduce."""
    condition = job_if_condition(CI_YML.read_text(), "doctor")
    assert condition is not None
    assert "github.event_name == 'pull_request'" in condition
    assert "github.event_name == 'merge_group'" in condition
    assert condition != "github.ref == 'refs/heads/main'"


# The product's own pre-existing, generic vocabulary that a blind grep for
# TENANCY_STRINGS would otherwise flag: "attavo.toml" is the manifest
# filename every tenant uses, named by the schema itself, not a fact
# about this repository's own installation (adapters/README.md's
# ATTAVO_MANIFEST doc line, bin/attavo's own MANIFEST constant, the
# schema's own title, and the two template workflows' own tomllib read of
# whatever tenant copies them in -- theirs, never this repository's).
# Each entry is
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
}


def test_rejection_no_product_file_names_a_tenancy_path() -> None:
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
