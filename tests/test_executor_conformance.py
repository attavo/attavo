"""The executor conformance suite (issue #40): the entrance exam a second
adapter passes before it is pointed at a tenant. Every situation here is a
claude-code specimen from the last week of Lane 2, stated as contract
against the adapter's own two test hooks -- ATTAVO_ADAPTER_DRY_RUN and
ATTAVO_ADAPTER_RESPONSE_FILE -- rather than against claude-code's own
module path, so a second adapter inherits it by declaring a
conformance.json, not by copying a test file.

Each adapter under adapters/ (except NOT_AN_EXECUTOR) declares its own
conformance.json: {"dry_run_env": "<VAR>", "response_env": "<VAR>",
"tool_binary": "<name>", "situations": {"<name>": <canned response>}}.
dry_run_env and response_env are the two hooks tests/test_adapters.py
already exercised for claude-code (issues #24, #30, #34); tool_binary is
new here -- the name of the executable the adapter shells out to when
neither hook is set, needed to fake a non-zero exit from it (situation 5)
without a model in the loop.

Situation 8 (a commit the adapter makes carries the identity attavo build
exported) has no mechanism yet -- issue #33. It is written as a real,
currently-failing assertion and deselected in CI by name, exactly as
tests/test_doctor.py's proofs two and three are.

Run: python3 -m pytest tests/test_executor_conformance.py -q
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

from helpers import FIXTURES

ROOT = Path(__file__).resolve().parent.parent
ADAPTERS_DIR = ROOT / "adapters"

# Not an executor: adapters/fixture/run exists for the tests and the
# doctor, has no canned-response hook, and is never pointed at a tenant.
NOT_AN_EXECUTOR = {"fixture"}

MIN_QUESTION_CHARS = 40


def find_adapters_without_conformance(adapters_dir: Path) -> list[str]:
    """Every directory under adapters_dir, other than NOT_AN_EXECUTOR,
    that carries no conformance.json -- named, so a second adapter cannot
    join by declaring nothing."""
    missing = []
    if not adapters_dir.is_dir():
        return missing
    for d in sorted(p for p in adapters_dir.iterdir() if p.is_dir()):
        if d.name in NOT_AN_EXECUTOR:
            continue
        if not (d / "conformance.json").is_file():
            missing.append(d.name)
    return missing


def load_conformance() -> dict[str, dict]:
    manifests = {}
    for name in sorted(p.name for p in ADAPTERS_DIR.iterdir() if p.is_dir()):
        if name in NOT_AN_EXECUTOR:
            continue
        manifest_path = ADAPTERS_DIR / name / "conformance.json"
        if manifest_path.is_file():
            manifests[name] = json.loads(manifest_path.read_text())
    return manifests


CONFORMANCE = load_conformance()
ADAPTER_NAMES = sorted(CONFORMANCE)


def base_env(tmp_path: Path) -> dict:
    spec_dir = FIXTURES / "build_valid" / "specs" / "1"
    env = dict(os.environ)
    env.pop("ATTAVO_CHECKS_COMMAND", None)
    env.update({
        "ATTAVO_SPEC_DIR": str(spec_dir),
        "ATTAVO_RECORDS_DIR": str(tmp_path / "records"),
        "ATTAVO_RULINGS": str(tmp_path / "rulings.md"),
        "ATTAVO_BRANCH": "attavo/1-example",
        "ATTAVO_ITERATION": "1",
        "ATTAVO_FEEDBACK": str(tmp_path / "feedback.txt"),
    })
    return env


def run_adapter(adapter_name: str, env: dict) -> subprocess.CompletedProcess:
    run_path = ADAPTERS_DIR / adapter_name / "run"
    return subprocess.run([str(run_path)], capture_output=True, text=True,
                          check=False, env=env)


def dry_run(adapter_name: str, tmp_path: Path, extra_env: dict | None = None) -> list[str]:
    manifest = CONFORMANCE[adapter_name]
    env = base_env(tmp_path)
    env[manifest["dry_run_env"]] = "1"
    if extra_env:
        env.update(extra_env)
    run = run_adapter(adapter_name, env)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def run_situation(adapter_name: str, situation: str, tmp_path: Path,
                  extra_env: dict | None = None) -> subprocess.CompletedProcess:
    manifest = CONFORMANCE[adapter_name]
    response = manifest["situations"][situation]
    response_file = tmp_path / "response.json"
    response_file.write_text(json.dumps(response))
    env = base_env(tmp_path)
    env[manifest["response_env"]] = str(response_file)
    if extra_env:
        env.update(extra_env)
    return run_adapter(adapter_name, env)


def test_every_executor_adapter_declares_conformance():
    """Acceptance 3 (real check, against this repository's own adapters/)."""
    missing = find_adapters_without_conformance(ADAPTERS_DIR)
    assert not missing, f"adapter(s) with no conformance.json: {', '.join(missing)}"


def test_find_adapters_without_conformance_names_the_directory(tmp_path):
    """Acceptance 3 (fixture): a directory with no conformance.json is
    named, without touching the real adapters/ tree."""
    adapters_dir = tmp_path / "adapters"
    declared = adapters_dir / "declared"
    declared.mkdir(parents=True)
    (declared / "run").write_text("#!/bin/sh\n")
    (declared / "conformance.json").write_text("{}")

    undeclared = adapters_dir / "undeclared"
    undeclared.mkdir(parents=True)
    (undeclared / "run").write_text("#!/bin/sh\n")

    assert find_adapters_without_conformance(adapters_dir) == ["undeclared"]


@pytest.mark.parametrize("adapter_name", ADAPTER_NAMES)
def test_situation_1_report_line_is_last_and_has_a_model(adapter_name, tmp_path):
    run = run_situation(adapter_name, "done", tmp_path)
    assert run.returncode == 0, run.stderr
    lines = [l for l in run.stdout.splitlines() if l.strip()]
    report = json.loads(lines[-1])
    assert report.get("model")


@pytest.mark.parametrize("adapter_name", ADAPTER_NAMES)
def test_situation_2_tool_error_fails_and_prints_the_result(adapter_name, tmp_path):
    run = run_situation(adapter_name, "is_error", tmp_path)
    assert run.returncode != 0
    assert run.stdout.strip(), "the raw result must still reach stdout"


@pytest.mark.parametrize("adapter_name", ADAPTER_NAMES)
def test_situation_3_no_structured_output_fails(adapter_name, tmp_path):
    run = run_situation(adapter_name, "no_structured_output", tmp_path)
    assert run.returncode != 0


@pytest.mark.parametrize("adapter_name", ADAPTER_NAMES)
def test_situation_4_question_quality_gates_the_halt(adapter_name, tmp_path):
    for situation in ("question_null", "question_empty", "question_short"):
        run = run_situation(adapter_name, situation, tmp_path)
        assert run.returncode != 0, f"{situation} should have failed, not halted"

    run = run_situation(adapter_name, "question_long", tmp_path)
    assert run.returncode == 0, run.stderr
    report = json.loads(run.stdout.splitlines()[-1])
    assert report["status"] == "question"


@pytest.mark.parametrize("adapter_name", ADAPTER_NAMES)
def test_situation_5_underlying_tool_nonzero_exit_passes_through(adapter_name, tmp_path):
    """A fake <tool_binary> on PATH that writes to both of its streams and
    exits 3: the adapter exits 3, and its own stdout -- the only stream
    build-NN.log is built from -- carries both of the tool's streams.
    """
    manifest = CONFORMANCE[adapter_name]
    tool_binary = manifest["tool_binary"]

    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    fake_tool = fake_bin / tool_binary
    fake_tool.write_text(
        "#!/bin/sh\n"
        "echo 'tool stdout: ran out of budget'\n"
        "echo 'tool stderr: overloaded_error' >&2\n"
        "exit 3\n")
    fake_tool.chmod(0o755)

    env = base_env(tmp_path)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    run = run_adapter(adapter_name, env)
    assert run.returncode == 3
    assert "tool stdout: ran out of budget" in run.stdout
    assert "tool stderr: overloaded_error" in run.stdout


@pytest.mark.parametrize("adapter_name", ADAPTER_NAMES)
def test_situation_6_transcript_non_empty_on_every_path(adapter_name, tmp_path):
    for situation in ("done", "is_error", "no_structured_output", "question_short"):
        run = run_situation(adapter_name, situation, tmp_path)
        assert run.stdout.strip(), f"{situation} produced no transcript at all"


@pytest.mark.parametrize("adapter_name", ADAPTER_NAMES)
def test_situation_7_checks_command_in_the_invocation(adapter_name, tmp_path):
    command = "python3 bin/factory-lane check --base origin/main"
    with_checks = dry_run(adapter_name, tmp_path, extra_env={"ATTAVO_CHECKS_COMMAND": command})
    assert command in json.dumps(with_checks)

    without_checks = dry_run(adapter_name, tmp_path)
    assert command not in json.dumps(without_checks)


@pytest.mark.parametrize("adapter_name", ADAPTER_NAMES)
def test_situation_8_commits_carry_the_attavo_build_identity(adapter_name, tmp_path):
    """No mechanism yet exports a commit identity for the adapter to use
    (issue #33). Red by design; deselected in CI by name, like
    tests/test_doctor.py's proofs two and three.
    """
    pytest.fail("no mechanism yet exports a commit identity for the adapter to use (issue #33)")


@pytest.mark.parametrize("adapter_name", ADAPTER_NAMES)
def test_situation_9_verify_stage_uses_a_findings_schema(adapter_name, tmp_path):
    """Issue #46, rejection test 3. With ATTAVO_STAGE=verify, the
    invocation names a response shape carrying findings and tests_named
    -- not build's status/question. Red at 699f56d for claude-code,
    where ATTAVO_STAGE was read nowhere and every invocation got the
    build schema.
    """
    cmd = dry_run(adapter_name, tmp_path, extra_env={"ATTAVO_STAGE": "verify"})
    schema_index = cmd.index("--json-schema") + 1
    schema = json.loads(cmd[schema_index])
    assert "findings" in schema.get("properties", {})
    assert "tests_named" in schema.get("properties", {})


@pytest.mark.parametrize("adapter_name", ADAPTER_NAMES)
def test_situation_10_verify_names_approval_commit_divergence_as_reportable(adapter_name, tmp_path):
    """Issue #48, rejection test 1. With ATTAVO_STAGE=verify, the
    invocation's instruction names a spec whose approval commit does
    not describe the working tree as itself a reportable finding --
    issue #4's absorption, specified in issue #40's section 2, lost
    between issue #40 and issue #46. Red at d04b484, where
    verify_instruction() never said this.
    """
    cmd = dry_run(adapter_name, tmp_path, extra_env={"ATTAVO_STAGE": "verify"})
    instruction = cmd[cmd.index("-p") + 1]
    assert "approval commit" in instruction
    assert "reportable" in instruction


@pytest.mark.parametrize("adapter_name", ADAPTER_NAMES)
def test_situation_11_verify_defines_severity_as_derived(adapter_name, tmp_path):
    """Issue #48, rejection test 2. With ATTAVO_STAGE=verify, the
    invocation's instruction defines severity as derived rather than
    judged. Red at d04b484, where severity was named once in the
    finding fields and defined nowhere.
    """
    cmd = dry_run(adapter_name, tmp_path, extra_env={"ATTAVO_STAGE": "verify"})
    instruction = cmd[cmd.index("-p") + 1]
    assert "severity" in instruction
    assert "derived rather than judged" in instruction
