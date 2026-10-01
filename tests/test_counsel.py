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
from typing import Any, cast

import jsonschema

from helpers import FIXTURES, commit_all, make_tenant

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"
COUNSEL_SCHEMA = json.loads((ROOT / "schema" / "records" / "counsel.schema.json").read_text())


def run_attavo(root: Path, *args: str, env: dict[str, Any] | None = None) -> subprocess.CompletedProcess[str]:
    import os
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run([sys.executable, str(ATTAVO), *args], cwd=str(root),
                          capture_output=True, text=True, check=False, env=full_env)


def write_probe_adapter(root: Path, name: str, script: str) -> None:
    """A tenant-local adapter override under attavo/adapters/<name>/run
    (resolve_adapter's own tenant-first search, same pattern
    tests/test_verify.py's "silent"/"loud" adapters use) -- for a probe
    that inspects the evidence files counsel hands it. adapters/fixture/run
    is a shared fixture this lane's own files list does not include, so a
    probe that needs to see ATTAVO_COUNSEL_CHECKS or ATTAVO_COUNSEL_GRAPH
    lives here instead of there."""
    adapter_dir = root / "attavo" / "adapters" / name
    adapter_dir.mkdir(parents=True)
    adapter_run = adapter_dir / "run"
    adapter_run.write_text(script)
    adapter_run.chmod(0o755)


def use_counsel_adapter(root: Path, name: str) -> None:
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text().replace(
        '[executor]\nbuild = "fixture"', f'[executor]\nbuild = "fixture"\ncounsel = "{name}"'))
    commit_all(root, f"use the {name} counsel adapter")


def configure_forge(root: Path, checks_command: str, check_log_command: str | None = None) -> None:
    text = (root / "attavo.toml").read_text() + f'\n[forge]\nchecks_command = "{checks_command}"\n'
    if check_log_command is not None:
        text += f'check_log_command = "{check_log_command}"\n'
    (root / "attavo.toml").write_text(text)
    commit_all(root, "configure the forge read")


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


def write_counsel_response(tmp_path: Path, response: dict[str, Any]) -> Path:
    path = tmp_path / "counsel_response.json"
    path.write_text(json.dumps(response))
    return path


def canned(verdict: str, **kwargs: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "model": "fixture", "model_version": None, "adapter_version": "fixture-1",
        "tokens": {"input": 1, "output": 1}, "cost_usd": 0.0,
        "class": "unclassed", "verdict": verdict,
        "citations": [], "proposal": {"ruling_text": None, "patch": None, "question": None},
    }
    base.update(kwargs)
    return base


def test_counsel_refuses_without_a_non_pass_record(tmp_path: Path) -> None:
    """Acceptance row 2 (half). A fresh item with no build, verify, or
    spec-gate record at all -- refused by name, nothing written."""
    root = make_tenant(tmp_path, "build_valid")
    configure_counsel_adapter(root)

    run = run_attavo(root, "counsel", "1")
    assert run.returncode == 1
    assert "no non-pass build, verify, or spec-gate record" in run.stdout
    assert not records_dir(root).exists() or list(records_dir(root).glob("counsel-*.json")) == []


def test_counsel_refuses_when_the_latest_record_is_a_pass(tmp_path: Path) -> None:
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


def test_counsel_answers_a_refused_spec_gate_record_when_no_build_or_verify_exists(tmp_path: Path) -> None:
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


def test_counsel_writes_a_valid_record_for_a_halted_item(tmp_path: Path) -> None:
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


def test_counsel_commits_nothing_and_writes_only_under_records(tmp_path: Path) -> None:
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


def test_counsel_person_verdict_carries_a_one_line_question(tmp_path: Path) -> None:
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


def test_counsel_answer_verdict_carries_ruling_text_and_three_citations(tmp_path: Path) -> None:
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


def test_counsel_patch_verdict_carries_file_symbol_rejection_test_and_diff(tmp_path: Path) -> None:
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


def test_counsel_refuses_without_an_executor_counsel_or_verify_adapter(tmp_path: Path) -> None:
    """No [executor].counsel and no [executor].verify -- refused by name,
    nothing written; build_valid declares [executor].build only."""
    root = make_tenant(tmp_path, "build_valid")
    approve_and_halt(root)

    run = run_attavo(root, "counsel", "1")
    assert run.returncode == 1
    assert "executor" in run.stdout
    assert not (records_dir(root) / "counsel-01.json").exists()


def test_counsel_falls_back_to_executor_verify_when_counsel_is_undeclared(tmp_path: Path) -> None:
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


def test_counsel_chooses_the_most_recent_non_pass_record_across_stages(tmp_path: Path) -> None:
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


# --- issue #175: the forge and the graph in the evidence set, a fourth
# proposal shape, and the operator's own question ---

def test_counsel_forge_is_not_read_without_any_forge_section(tmp_path: Path) -> None:
    """Acceptance row 2 (half). No [forge] table at all: counsel's own
    record says the forge was not read, naming the missing key -- never a
    record that reads as if the forge were green."""
    root = make_tenant(tmp_path, "build_valid")
    configure_counsel_adapter(root)
    approve_and_halt(root)

    run = run_attavo(root, "counsel", "1")
    assert run.returncode == 0, run.stdout + run.stderr

    record = json.loads((records_dir(root) / "counsel-01.json").read_text())
    jsonschema.validate(record, COUNSEL_SCHEMA)
    assert record["forge"]["read"] is False
    assert "checks_command" in record["forge"]["reason"]
    assert record["forge"]["checks"] == []


def test_counsel_forge_is_not_read_without_a_check_log_command(tmp_path: Path) -> None:
    """Acceptance row 2 (rest, the rejection test itself, red before this
    lane). [forge].checks_command is configured but [forge].check_log_command
    is not: still not read -- absent is not silently the same as green."""
    root = make_tenant(tmp_path, "build_valid")
    configure_counsel_adapter(root)
    configure_forge(root, "printf 'ci failure\\n'")
    approve_and_halt(root)

    run = run_attavo(root, "counsel", "1")
    assert run.returncode == 0, run.stdout + run.stderr

    record = json.loads((records_dir(root) / "counsel-01.json").read_text())
    jsonschema.validate(record, COUNSEL_SCHEMA)
    assert record["forge"]["read"] is False
    assert "check_log_command" in record["forge"]["reason"]
    assert record["forge"]["checks"] == []


PROBE_CHECKS_AND_GRAPH = """#!/usr/bin/env python3
import json
import os

checks_path = os.environ.get("ATTAVO_COUNSEL_CHECKS")
checks = json.loads(open(checks_path).read()) if checks_path else {}
print("--- CHECKS ---")
print(json.dumps(checks))
for check in checks.get("checks", []):
    if check.get("log"):
        print(f"--- LOG {check['name']} ---")
        print(open(check["log"]).read())
        print(f"--- END LOG {check['name']} ---")

graph_path = os.environ.get("ATTAVO_COUNSEL_GRAPH")
if graph_path:
    print("--- GRAPH ---")
    print(open(graph_path).read())
    print("--- END GRAPH ---")

print(json.dumps({
    "model": "probe", "model_version": None, "adapter_version": "probe-1",
    "tokens": {"input": 1, "output": 1}, "cost_usd": 0.0,
    "class": "unclassed", "verdict": "person",
    "citations": [],
    "proposal": {"ruling_text": None, "patch": None, "question": "noted", "issue": None},
}))
"""


def test_counsel_reads_the_forge_and_hands_the_adapter_a_checks_file(tmp_path: Path) -> None:
    """Acceptance row 1 (mechanism half). [forge].checks_command and
    [forge].check_log_command both configured: counsel's own record
    carries what the forge reported, and the file handed to the adapter
    (ATTAVO_COUNSEL_CHECKS) names, for the check that did not succeed,
    the path of a log this lane's own check_log_command fetched -- read
    here from the adapter's own transcript (the log file itself is
    ephemeral, gone once counsel returns)."""
    root = make_tenant(tmp_path, "build_valid")
    approve_and_halt(root)

    check_log_script = root / "check_log.py"
    check_log_script.write_text(
        "import sys\n"
        "logs = {'doctor': 'proof 5 failed on the marker commit\\n'}\n"
        "name = sys.argv[2]\n"
        "sys.stdout.write(logs[name]) if name in logs else sys.exit(1)\n")
    configure_forge(root, "printf 'doctor failure\\ntest success\\n'",
                    f"python3 {check_log_script.name}")

    write_probe_adapter(root, "probe", PROBE_CHECKS_AND_GRAPH)
    use_counsel_adapter(root, "probe")

    run = run_attavo(root, "counsel", "1")
    assert run.returncode == 0, run.stdout + run.stderr

    record = json.loads((records_dir(root) / "counsel-01.json").read_text())
    jsonschema.validate(record, COUNSEL_SCHEMA)
    assert record["forge"]["read"] is True
    assert {(c["name"], c["conclusion"]) for c in record["forge"]["checks"]} == {
        ("doctor", "failure"), ("test", "success")}

    log_text = (records_dir(root) / "counsel-01.log").read_text()
    checks_json = json.loads(log_text.split("--- CHECKS ---\n", 1)[1].split("\n--- LOG", 1)[0])
    assert checks_json["read"] is True
    by_name = {c["name"]: c for c in checks_json["checks"]}
    assert by_name["test"]["log"] is None
    assert by_name["doctor"]["log"] is not None
    assert "--- LOG doctor ---" in log_text
    assert "proof 5 failed on the marker commit" in log_text
    assert "--- LOG test ---" not in log_text


def test_counsel_hands_the_adapter_a_graph_file(tmp_path: Path) -> None:
    """Acceptance row 1 (mechanism half). ATTAVO_COUNSEL_GRAPH names a
    file describing the default branch since this item's branch point and
    this branch's own commits."""
    root = make_tenant(tmp_path, "build_valid")
    approve_and_halt(root)

    write_probe_adapter(root, "probe", PROBE_CHECKS_AND_GRAPH)
    use_counsel_adapter(root, "probe")

    run = run_attavo(root, "counsel", "1")
    assert run.returncode == 0, run.stdout + run.stderr

    log_text = (records_dir(root) / "counsel-01.log").read_text()
    graph_text = log_text.split("--- GRAPH ---\n", 1)[1].split("--- END GRAPH ---")[0]
    assert "the default branch" in graph_text
    assert "this branch's own commits" in graph_text


def test_counsel_proposal_issue_validates_against_schema(tmp_path: Path) -> None:
    """Acceptance row 3. proposal.issue -- a title and a body in this
    repository's own issue format -- validates against
    schema/records/counsel.schema.json, for a "patch" verdict whose
    remedy is better filed as an item than shipped as a diff."""
    root = make_tenant(tmp_path, "build_valid")
    configure_counsel_adapter(root)
    approve_and_halt(root)

    body = (
        "Severity: blocking for the overlay's own claim.\n\n"
        "## Problem\n\nthe overlay and the base tree disagree.\n\n"
        "## Retires\n\nthe operator's own manual read of the overlay.\n\n"
        "## Change\n\n1. prefer the overlay's own tree.\n\n"
        "## Acceptance\n\n| Assertion | How it is checked |\n|---|---|\n"
        "| the overlay wins | a test |\n")
    response = write_counsel_response(tmp_path, canned(
        "patch",
        proposal={"ruling_text": None, "patch": None, "question": None,
                  "issue": {"title": "the overlay should win over the base tree", "body": body}}))

    run = run_attavo(root, "counsel", "1",
                     env={"ATTAVO_FIXTURE_COUNSEL_RESPONSE": str(response)})
    assert run.returncode == 0, run.stdout + run.stderr

    record = json.loads((records_dir(root) / "counsel-01.json").read_text())
    jsonschema.validate(record, COUNSEL_SCHEMA)
    assert record["verdict"] == "patch"
    assert record["proposal"]["patch"] is None
    assert record["proposal"]["issue"]["title"] == "the overlay should win over the base tree"
    assert "## Acceptance" in record["proposal"]["issue"]["body"]


def test_counsel_ask_requires_nonempty_text(tmp_path: Path) -> None:
    """Acceptance row 4 (guard half)."""
    root = make_tenant(tmp_path, "build_valid")
    configure_counsel_adapter(root)
    approve_and_halt(root)

    run = run_attavo(root, "counsel", "1", "--ask", "")
    assert run.returncode == 2
    assert "--ask" in run.stderr
    assert not (records_dir(root) / "counsel-01.json").exists()


def test_counsel_question_is_null_without_ask(tmp_path: Path) -> None:
    """Regression: the ordinary stop path still writes question: null."""
    root = make_tenant(tmp_path, "build_valid")
    configure_counsel_adapter(root)
    approve_and_halt(root)

    run = run_attavo(root, "counsel", "1")
    assert run.returncode == 0, run.stdout + run.stderr
    record = json.loads((records_dir(root) / "counsel-01.json").read_text())
    jsonschema.validate(record, COUNSEL_SCHEMA)
    assert record["question"] is None


# --- issue #175, acceptance row 1: #158's own verify-02 (forge-unconcluded),
# with its checks and the doctor job's own log as fixtures -- a conformance
# case in both adapters' conformance.json, exercised here through each real
# adapter binary (not the shell fixture) with a canned response. ---

def write_158_specimen(root: Path) -> None:
    """#158's real verify-02.json (tests/fixtures/counsel_158/), item
    corrected to this tenant's own item id -- the mechanism read
    correctly (forge-unconcluded) but the remedy wrong (counsel-04's own
    "retry"), which this lane retires."""
    rec = json.loads((FIXTURES / "counsel_158" / "verify-02.json").read_text())
    rec["item"] = "1"
    records_dir(root).mkdir(parents=True, exist_ok=True)
    (records_dir(root) / "verify-02.json").write_text(json.dumps(rec))


def configure_158_forge(root: Path) -> None:
    """[forge].checks_command reports doctor and test both failure on the
    specimen's own sha; [forge].check_log_command hands back the real
    doctor job's own failing-step log for "doctor" (the fixture gh run
    view --log-failed excerpt naming proof 5) and a short pytest tail for
    "test"."""
    doctor_log = (FIXTURES / "counsel_158" / "doctor_job.log").read_text()
    check_log_script = root / "check_log.py"
    check_log_script.write_text(
        "import sys\n"
        f"DOCTOR_LOG = {doctor_log!r}\n"
        "logs = {'doctor': DOCTOR_LOG,\n"
        "        'test': 'FAILED tests/test_doctor.py::"
        "test_no_invocation_lacks_record\\n'}\n"
        "name = sys.argv[2]\n"
        "sys.stdout.write(logs[name]) if name in logs else sys.exit(1)\n")
    configure_forge(root, "printf 'doctor failure\\ntest failure\\n'",
                    f"python3 {check_log_script.name}")


def counsel_158_situation(adapter_name: str) -> dict[str, Any]:
    manifest = json.loads((ROOT / "adapters" / adapter_name / "conformance.json").read_text())
    return cast(dict[str, Any], manifest["situations"]["counsel_158_forge_evidence"])


def assert_158_citation_and_no_retry(record: dict[str, Any]) -> None:
    jsonschema.validate(record, COUNSEL_SCHEMA)
    assert record["forge"]["read"] is True
    assert {(c["name"], c["conclusion"]) for c in record["forge"]["checks"]} == {
        ("doctor", "failure"), ("test", "failure")}
    symbols = {c.get("symbol") for c in record["citations"]}
    assert symbols & {"proof_no_invocation_lacks_record", "invocation_alive"}, record["citations"]
    assert "retry" not in (record["proposal"]["ruling_text"] or "").lower()


def test_counsel_158_specimen_cites_the_marker_not_retry_with_claude_code(tmp_path: Path) -> None:
    """Acceptance row 1. counsel-04's own answer, before this lane, was
    "retry only after the forge exposes concluded, green checks for that
    exact sha" -- the mechanism read correctly, the remedy wrong, because
    the doctor job's own log on that sha shows proof 5
    no-invocation-lacks-record failing on the marker commit, and no
    retry could ever be green. With the checks and the log now in the
    evidence set, counsel's citations name the real cause instead."""
    root = make_tenant(tmp_path, "build_valid")
    write_158_specimen(root)
    configure_158_forge(root)
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text().replace(
        '[executor]\nbuild = "fixture"',
        '[executor]\nbuild = "fixture"\ncounsel = "claude-code"'))
    commit_all(root, "use the real claude-code counsel adapter")

    response_path = tmp_path / "claude_code_response.json"
    response_path.write_text(json.dumps(counsel_158_situation("claude-code")))

    run = run_attavo(root, "counsel", "1",
                     env={"ATTAVO_ADAPTER_RESPONSE_FILE": str(response_path)})
    assert run.returncode == 0, run.stdout + run.stderr

    record = json.loads((records_dir(root) / "counsel-01.json").read_text())
    assert_158_citation_and_no_retry(record)


def test_counsel_158_specimen_cites_the_marker_not_retry_with_codex(tmp_path: Path) -> None:
    """Acceptance row 1, codex half -- COUNSEL_SCHEMA and
    counsel_instruction are identical between the two adapters by
    design; this proves the mechanism through codex's own real binary
    too, not just claude-code's."""
    root = make_tenant(tmp_path, "build_valid")
    write_158_specimen(root)
    configure_158_forge(root)
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text().replace(
        '[executor]\nbuild = "fixture"',
        '[executor]\nbuild = "fixture"\ncounsel = "codex"\n\n'
        '[executor.codex]\nmodel = "test-model"'))
    commit_all(root, "use the real codex counsel adapter")

    response_path = tmp_path / "codex_response.json"
    response_path.write_text(json.dumps(counsel_158_situation("codex")))

    run = run_attavo(root, "counsel", "1",
                     env={"ATTAVO_ADAPTER_RESPONSE_FILE": str(response_path)})
    assert run.returncode == 0, run.stdout + run.stderr

    record = json.loads((records_dir(root) / "counsel-01.json").read_text())
    assert_158_citation_and_no_retry(record)


# --- counsel as a conversation with a record behind it (rulings of
# 2026-09-25): a follow-up is handed counsel's earlier readings first and
# names the one it answers; the stage's last events stand in for the
# whole log unless the log is asked about; the tenant's corpus under
# docs/ is handed and a missing path is refused. Specimen: #219's
# counsel-02, 73 s and three turns re-deriving what counsel-01 already
# said, searching the checkout for briefs that lived elsewhere. Written
# red first against v0.29.0. ---


MANIFEST_SCHEMA = json.loads((Path(__file__).resolve().parent.parent / "schema" / "manifest.schema.json").read_text())


def counsel_log_text(root: Path, name: str) -> str:
    return (records_dir(root) / f"{name}.log").read_text()


def section(text: str, marker: str) -> str:
    start = text.index(f"--- {marker} ---") + len(f"--- {marker} ---")
    end = text.index("--- end ", start)
    return text[start:end]


def test_the_stop_path_is_handed_the_events_tail_and_the_log(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "build_valid")
    configure_counsel_adapter(root)
    approve_and_halt(root)
    response = write_counsel_response(tmp_path, canned("answer"))
    env = {"ATTAVO_FIXTURE_COUNSEL_RESPONSE": str(response)}
    assert run_attavo(root, "counsel", "1", env=env).returncode == 0
    stop_path = counsel_log_text(root, "counsel-01")
    assert "log handed: yes" in stop_path, stop_path
    events = section(stop_path, "ATTAVO_COUNSEL_EVENTS").strip().splitlines()
    assert events and all(json.loads(line)["kind"] for line in events), "the stage's events were not handed"


def test_counsel_reads_the_tenants_corpus_and_refuses_a_missing_path(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "build_valid")
    configure_counsel_adapter(root)
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text() + '\n[counsel]\nreads = ["docs/rules.md", "docs/brief.md"]\n')
    approve_and_halt(root)
    response = write_counsel_response(tmp_path, canned("answer"))
    env = {"ATTAVO_FIXTURE_COUNSEL_RESPONSE": str(response)}
    refused = run_attavo(root, "counsel", "1", env=env)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "refused: [counsel].reads names docs/rules.md, docs/brief.md, which does not exist" in refused.stdout, refused.stdout
    assert not list(records_dir(root).glob("counsel-*")), "a refused counsel wrote a record"
    (root / "docs").mkdir()
    (root / "docs" / "rules.md").write_text("# rules\n")
    (root / "docs" / "brief.md").write_text("# brief\n")
    assert run_attavo(root, "counsel", "1", env=env).returncode == 0
    reads = section(counsel_log_text(root, "counsel-01"), "ATTAVO_COUNSEL_READS").split()
    assert reads == [str(root / "docs" / "rules.md"), str(root / "docs" / "brief.md")], reads
    jsonschema.validate({"reads": ["docs/rules.md"]}, MANIFEST_SCHEMA["properties"]["counsel"])
