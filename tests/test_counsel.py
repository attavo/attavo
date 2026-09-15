"""attavo counsel: a stop is read inside the product first, before a
person (issue #100). A read-only invocation of the tenant's
[executor].counsel adapter (defaulting to [executor].verify) on the
item's latest non-pass build or verify record -- it commits nothing and
writes nothing outside paths.records/<N>/. Every test here uses the
fixture adapter, with a canned counsel response supplied through
ATTAVO_FIXTURE_COUNSEL_RESPONSE where the verdict shape matters.

Rejection tests, written and run red first: on `main` before this issue
there was no `counsel` subcommand, so every one of these failed with
"unknown subcommand".

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
COUNSEL_SCHEMA = json.loads((ROOT / "schema" / "records" / "counsel.schema.json").read_text())


def run_attavo(root: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    import os
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run([sys.executable, str(ATTAVO), *args], cwd=str(root),
                          capture_output=True, text=True, check=False, env=full_env)


def configure_counsel_adapter(root: Path) -> None:
    """[executor].counsel = "fixture" -- build_valid's own manifest
    declares [executor].build = "fixture" only; counsel needs its own
    adapter named, per the lane's own default ([executor].verify, absent
    here, so this is the only way build_valid gets a counsel adapter at
    all)."""
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text().replace(
        '[executor]\nbuild = "fixture"', '[executor]\nbuild = "fixture"\ncounsel = "fixture"'))
    commit_all(root, "configure the counsel adapter")


def records_dir(root: Path, item: str = "1") -> Path:
    return root / "records" / item


def approve_and_halt(root: Path, question: str = "which overlay wins?") -> None:
    assert run_attavo(root, "spec-gate", "1").returncode == 0
    assert run_attavo(root, "approve", "1", "--by", "alice").returncode == 0
    build = run_attavo(root, "build", "1", env={"ATTAVO_FIXTURE_QUESTION": question})
    assert build.returncode == 3, build.stdout + build.stderr


def write_counsel_response(tmp_path: Path, response: dict) -> Path:
    path = tmp_path / "counsel_response.json"
    path.write_text(json.dumps(response))
    return path


def canned(verdict: str, **kwargs) -> dict:
    base = {
        "model": "fixture", "model_version": None, "adapter_version": "fixture-1",
        "tokens": {"input": 1, "output": 1}, "cost_usd": 0.0,
        "class": "unclassed", "verdict": verdict,
        "citations": [], "proposal": {"ruling_text": None, "patch": None, "question": None},
    }
    base.update(kwargs)
    return base


def test_counsel_refuses_without_a_non_pass_record(tmp_path):
    """Acceptance row 2 (half). A fresh item with no build, verify, or
    spec-gate record at all -- refused by name, nothing written."""
    root = make_tenant(tmp_path, "build_valid")
    configure_counsel_adapter(root)

    run = run_attavo(root, "counsel", "1")
    assert run.returncode == 1
    assert "no non-pass build, verify, or spec-gate record" in run.stdout
    assert not records_dir(root).exists() or list(records_dir(root).glob("counsel-*.json")) == []


def test_counsel_refuses_when_the_latest_record_is_a_pass(tmp_path):
    """Acceptance row 2 (rest). A passing build -- and the passing spec-01
    it rests on -- carries nothing to counsel -- refused the same as an
    item with no record at all: the spec-gate fallback only ever reads a
    refused spec record, never a passing one."""
    root = make_tenant(tmp_path, "build_valid")
    configure_counsel_adapter(root)
    assert run_attavo(root, "spec-gate", "1").returncode == 0
    assert run_attavo(root, "approve", "1", "--by", "alice").returncode == 0
    assert run_attavo(root, "build", "1").returncode == 0

    run = run_attavo(root, "counsel", "1")
    assert run.returncode == 1
    assert "no non-pass build, verify, or spec-gate record" in run.stdout


def test_counsel_answers_a_refused_spec_gate_record_when_no_build_or_verify_exists(tmp_path):
    """Acceptance row 1. No build or verify record exists at all -- the
    item never got approved -- but a refused spec-01.json does (issue
    #133's own specimen, tenant zero #683: a criterion the gate cannot
    express). counsel reads it as the earliest stop, citing the refusal
    and the criterion by name."""
    root = make_tenant(tmp_path, "passes_on_tree")
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text().replace(
        '[paths]\nspecs = "specs"\nrecords = "records"',
        '[paths]\nspecs = "specs"\nrecords = "records"\n'
        'rulings = "rulings.md"\nrejections = "rejections"'
    ) + '\n[executor]\ncounsel = "fixture"\n')
    commit_all(root, "configure the counsel adapter")

    gate = run_attavo(root, "spec-gate", "1")
    assert gate.returncode == 1
    assert "checks.sh" in gate.stdout

    response = write_counsel_response(tmp_path, canned(
        "answer",
        citations=[
            {"kind": "instruction", "path": "specs/1/spec.md", "symbol": None, "ruling_id": None},
            {"kind": "mechanism", "path": "specs/1/criteria/checks.sh", "symbol": None,
             "ruling_id": None},
        ],
        proposal={"ruling_text": "checks.sh always exits zero and never fails on the tree",
                  "patch": None, "question": None}))

    run = run_attavo(root, "counsel", "1",
                     env={"ATTAVO_FIXTURE_COUNSEL_RESPONSE": str(response)})
    assert run.returncode == 0, run.stdout + run.stderr

    record = json.loads((records_dir(root) / "counsel-01.json").read_text())
    jsonschema.validate(record, COUNSEL_SCHEMA)
    assert record["record_stage"] == "spec"
    assert record["record"] == "spec-01"
    assert record["status"] == "done"
    assert record["verdict"] == "answer"
    assert "checks.sh" in record["proposal"]["ruling_text"]


def test_counsel_writes_a_valid_record_for_a_halted_item(tmp_path):
    """Acceptance row 1. counsel writes counsel-NN.json (and a log) that
    validates against schema/records/counsel.schema.json, with class,
    verdict, citations, and proposal."""
    root = make_tenant(tmp_path, "build_valid")
    configure_counsel_adapter(root)
    approve_and_halt(root)

    response = write_counsel_response(tmp_path, canned(
        "answer",
        citations=[
            {"kind": "instruction", "path": "specs/1/spec.md", "symbol": None, "ruling_id": None},
            {"kind": "mechanism", "path": "bin/attavo", "symbol": "cmd_build", "ruling_id": None},
            {"kind": "guard", "path": None, "symbol": None, "ruling_id": "overlay-2026-09-01"},
        ],
        proposal={"ruling_text": "use the base tree's overlay", "patch": None, "question": None}))

    run = run_attavo(root, "counsel", "1",
                     env={"ATTAVO_FIXTURE_COUNSEL_RESPONSE": str(response)})
    assert run.returncode == 0, run.stdout + run.stderr
    assert "done" in run.stdout

    record_path = records_dir(root) / "counsel-01.json"
    assert record_path.is_file()
    assert record_path.with_suffix(".log").is_file()

    record = json.loads(record_path.read_text())
    jsonschema.validate(record, COUNSEL_SCHEMA)
    assert record["record_stage"] == "build"
    assert record["record"] == "build-01"
    assert record["status"] == "done"
    assert record["class"] == "unclassed"
    assert record["verdict"] == "answer"
    assert len(record["citations"]) == 3
    assert record["proposal"]["ruling_text"] == "use the base tree's overlay"


def test_counsel_commits_nothing_and_writes_only_under_records(tmp_path):
    """Acceptance row 2. counsel writes nothing outside
    paths.records/<N>/ and commits nothing -- the caller commits, as with
    every stage."""
    root = make_tenant(tmp_path, "build_valid")
    configure_counsel_adapter(root)
    approve_and_halt(root)
    pre_head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()

    run = run_attavo(root, "counsel", "1")
    assert run.returncode == 0, run.stdout + run.stderr

    post_head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                               capture_output=True, text=True, check=True).stdout.strip()
    assert post_head == pre_head, "counsel must not create a commit"

    status = subprocess.run(["git", "-C", str(root), "status", "--porcelain",
                             "--untracked-files=all"],
                            capture_output=True, text=True, check=True).stdout
    changed = [line[3:] for line in status.splitlines()]
    assert changed, "counsel should have written something"
    assert all(p.startswith("records/1/") for p in changed), changed


def test_counsel_person_verdict_carries_a_one_line_question(tmp_path):
    """Acceptance row 5 (person half). A person verdict carries the
    one-line question; ruling_text and patch stay null."""
    root = make_tenant(tmp_path, "build_valid")
    configure_counsel_adapter(root)
    approve_and_halt(root)

    response = write_counsel_response(tmp_path, canned(
        "person",
        proposal={"ruling_text": None, "patch": None,
                  "question": "should the overlay win, or the base tree?"}))

    run = run_attavo(root, "counsel", "1",
                     env={"ATTAVO_FIXTURE_COUNSEL_RESPONSE": str(response)})
    assert run.returncode == 0, run.stdout + run.stderr

    record = json.loads((records_dir(root) / "counsel-01.json").read_text())
    jsonschema.validate(record, COUNSEL_SCHEMA)
    assert record["verdict"] == "person"
    assert record["proposal"]["question"] == "should the overlay win, or the base tree?"
    assert record["proposal"]["ruling_text"] is None
    assert record["proposal"]["patch"] is None


def test_counsel_answer_verdict_carries_ruling_text_and_three_citations(tmp_path):
    """Acceptance row 5 (answer half)."""
    root = make_tenant(tmp_path, "build_valid")
    configure_counsel_adapter(root)
    approve_and_halt(root)

    response = write_counsel_response(tmp_path, canned(
        "answer",
        citations=[
            {"kind": "instruction", "path": "specs/1/spec.md", "symbol": None, "ruling_id": None},
            {"kind": "mechanism", "path": "adapters/fixture/run", "symbol": None, "ruling_id": None},
            {"kind": "guard", "path": "bin/attavo", "symbol": "cmd_spec_gate", "ruling_id": None},
        ],
        proposal={"ruling_text": "the overlay wins", "patch": None, "question": None}))

    run = run_attavo(root, "counsel", "1",
                     env={"ATTAVO_FIXTURE_COUNSEL_RESPONSE": str(response)})
    assert run.returncode == 0, run.stdout + run.stderr

    record = json.loads((records_dir(root) / "counsel-01.json").read_text())
    jsonschema.validate(record, COUNSEL_SCHEMA)
    assert record["verdict"] == "answer"
    assert record["proposal"]["ruling_text"] == "the overlay wins"
    assert len(record["citations"]) == 3
    assert {c["kind"] for c in record["citations"]} == {"instruction", "mechanism", "guard"}


def test_counsel_patch_verdict_carries_file_symbol_rejection_test_and_diff(tmp_path):
    """Acceptance row 5 (patch half)."""
    root = make_tenant(tmp_path, "build_valid")
    configure_counsel_adapter(root)
    approve_and_halt(root)

    patch = {
        "file": "bin/attavo",
        "symbol": "cmd_spec_gate",
        "rejection_test": "tests/test_spec_gate.py::test_a_halted_builds_tree_is_still_accepted",
        "diff": "--- a/bin/attavo\n+++ b/bin/attavo\n@@\n-built = False\n+built = True\n",
    }
    response = write_counsel_response(tmp_path, canned(
        "patch", proposal={"ruling_text": None, "patch": patch, "question": None}))

    run = run_attavo(root, "counsel", "1",
                     env={"ATTAVO_FIXTURE_COUNSEL_RESPONSE": str(response)})
    assert run.returncode == 0, run.stdout + run.stderr

    record = json.loads((records_dir(root) / "counsel-01.json").read_text())
    jsonschema.validate(record, COUNSEL_SCHEMA)
    assert record["verdict"] == "patch"
    assert record["proposal"]["patch"] == patch


def test_counsel_refuses_without_an_executor_counsel_or_verify_adapter(tmp_path):
    """No [executor].counsel and no [executor].verify -- refused by name,
    nothing written; build_valid declares [executor].build only."""
    root = make_tenant(tmp_path, "build_valid")
    approve_and_halt(root)

    run = run_attavo(root, "counsel", "1")
    assert run.returncode == 1
    assert "executor" in run.stdout
    assert not (records_dir(root) / "counsel-01.json").exists()


def test_counsel_falls_back_to_executor_verify_when_counsel_is_undeclared(tmp_path):
    """"defaulting to [executor].verify" -- no [executor].counsel, but
    [executor].verify is fixture: counsel runs."""
    root = make_tenant(tmp_path, "build_valid")
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text().replace(
        '[executor]\nbuild = "fixture"', '[executor]\nbuild = "fixture"\nverify = "fixture"'))
    commit_all(root, "configure the verify adapter")
    approve_and_halt(root)

    run = run_attavo(root, "counsel", "1")
    assert run.returncode == 0, run.stdout + run.stderr
    record = json.loads((records_dir(root) / "counsel-01.json").read_text())
    assert record["adapter"] == "fixture"


def test_counsel_chooses_the_most_recent_non_pass_record_across_stages(tmp_path):
    """A passed build with a non-pass verify: counsel reads verify, not
    build, since verify's own record is the more recent stop."""
    root = make_tenant(tmp_path, "build_valid")
    configure_counsel_adapter(root)
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text() + """\n[forge]\nchecks_command = "printf 'ci success\\n'"\n""")
    commit_all(root, "configure the forge read")

    assert run_attavo(root, "spec-gate", "1").returncode == 0
    assert run_attavo(root, "approve", "1", "--by", "alice").returncode == 0
    assert run_attavo(root, "build", "1").returncode == 0
    verify = run_attavo(root, "verify", "1",
                        env={"ATTAVO_FIXTURE_OMIT_FINDINGS": "1"})
    assert verify.returncode != 0

    run = run_attavo(root, "counsel", "1")
    assert run.returncode == 0, run.stdout + run.stderr
    record = json.loads((records_dir(root) / "counsel-01.json").read_text())
    assert record["record_stage"] == "verify"
    assert record["record"] == "verify-01"
