"""The executor conformance suite (issue #40): the entrance exam a second
adapter passes before it is pointed at a tenant. Every situation here is a
claude-code specimen from the last week of Lane 2, stated as contract
against the adapter's own two test hooks -- ATTAVO_ADAPTER_DRY_RUN and
ATTAVO_ADAPTER_RESPONSE_FILE -- rather than against claude-code's own
module path, so a second adapter inherits it by declaring a
conformance.json, not by copying a test file.

Each adapter under adapters/ (except NOT_AN_EXECUTOR) declares its own
conformance.json: {"dry_run_env": "<VAR>", "response_env": "<VAR>",
"tool_binary": "<name>", "success_script": "<shell text>",
"reports": ["<field>", ...], "situations": {"<name>": <canned response>}}.
dry_run_env and response_env are the two hooks tests/test_adapters.py
already exercised for claude-code (issues #24, #30, #34); tool_binary is
the name of the executable the adapter shells out to when neither hook
is set, needed to fake a non-zero exit from it (situation 5) without a
model in the loop. success_script and reports are issue #41's, once a
second adapter (codex) proved the first two claude-code-shaped: a
canned "done" response's shape and a fake tool's own successful output
are each adapter's private contract, not this file's assumption.

dry_run()'s own envelope -- {"argv", "instruction", "schema"} -- is
issue #41's too. argv alone was claude-code's shape: its instruction
lives in argv's own "-p" value, but codex delivers its instruction on
stdin, where argv carries none at all, so the suite reads "instruction"
and "schema" directly rather than parsing either out of argv.

Situation 8: a commit the adapter makes carries the identity attavo build
exports (issue #33) -- checked by faking the adapter's own tool_binary on
PATH, the same technique situation 5 uses, so the fake tool can commit
for real inside a throwaway repo without a model in the loop. Its own
canned "success_script" (above) is what lets this generalize past
claude-code's single-JSON-blob response shape.

Situation 12: vendor independence (2026-09-09 ruling) -- an adapter's
declared "reports" fields must actually be non-null on a "done" run, so
a silent measurement gap fails the suite instead of writing one.

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
    # codex's own auth guard (issue #41) refuses "subscription" (the
    # default) when this is set -- absent here regardless of what the
    # ambient shell running these tests happens to carry, so the suite
    # never depends on the operator's own environment.
    env.pop("OPENAI_API_KEY", None)
    env.update({
        "ATTAVO_SPEC_DIR": str(spec_dir),
        "ATTAVO_RECORDS_DIR": str(tmp_path / "records"),
        "ATTAVO_RULINGS": str(tmp_path / "rulings.md"),
        "ATTAVO_BRANCH": "attavo/1-example",
        "ATTAVO_ITERATION": "1",
        "ATTAVO_FEEDBACK": str(tmp_path / "feedback.txt"),
        # codex requires [executor.codex].model (issue #41); harmless
        # for claude-code, which treats every key here as optional.
        "ATTAVO_ADAPTER_CONFIG": json.dumps({"model": "test-model"}),
    })
    return env


def run_adapter(adapter_name: str, env: dict, cwd: Path | None = None) -> subprocess.CompletedProcess:
    run_path = ADAPTERS_DIR / adapter_name / "run"
    return subprocess.run([str(run_path)], capture_output=True, text=True,
                          check=False, env=env, cwd=str(cwd) if cwd else None)


def dry_run(adapter_name: str, tmp_path: Path, extra_env: dict | None = None) -> dict:
    """{"argv", "instruction", "schema"}: the instruction and the schema
    named separately from argv, because a second adapter (issue #41)
    delivers the instruction on stdin, where argv itself carries none."""
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
    assert command in with_checks["instruction"]

    without_checks = dry_run(adapter_name, tmp_path)
    assert command not in without_checks["instruction"]


@pytest.mark.parametrize("adapter_name", ADAPTER_NAMES)
def test_situation_8_commits_carry_the_attavo_build_identity(adapter_name, tmp_path):
    """Issue #33. attavo build exports the executor identity -- set by the
    product, never the tenant's git config -- as GIT_AUTHOR_NAME/EMAIL and
    GIT_COMMITTER_NAME/EMAIL before invoking the adapter; the adapter must
    pass it through rather than setting its own explicit environment for
    the underlying tool. The fake tool_binary here commits for real, so
    this checks the adapter's own subprocess call, not just the harness.
    Regression: the specimen is attavo/code-factory@e22fa85, an executor
    commit indistinguishable in git log from the operator's own, because
    no identity was exported for it to carry.
    """
    manifest = CONFORMANCE[adapter_name]
    tool_binary = manifest["tool_binary"]

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "a tenant operator"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "operator@example.com"], cwd=str(repo), check=True)
    (repo / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=str(repo), check=True)

    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    fake_tool = fake_bin / tool_binary
    # A successful invocation's own shape is per-adapter (claude-code's
    # single JSON blob vs. codex's JSONL-plus-output-file), so each
    # adapter declares its own script rather than this file assuming
    # one -- assuming claude-code's shape here was itself the
    # claude-code-ism issue #41 found building a second adapter.
    fake_tool.write_text(manifest["success_script"])
    fake_tool.chmod(0o755)

    identity_name = f"attavo executor ({adapter_name})"
    identity_email = f"executor+{adapter_name}@attavo.invalid"
    env = base_env(tmp_path)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["GIT_AUTHOR_NAME"] = identity_name
    env["GIT_AUTHOR_EMAIL"] = identity_email
    env["GIT_COMMITTER_NAME"] = identity_name
    env["GIT_COMMITTER_EMAIL"] = identity_email

    run = run_adapter(adapter_name, env, cwd=repo)
    assert run.returncode == 0, run.stderr

    log = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%an/%ae"],
                         capture_output=True, text=True, check=True).stdout.strip()
    assert log == f"{identity_name}/{identity_email}"


@pytest.mark.parametrize("adapter_name", ADAPTER_NAMES)
def test_situation_9_verify_stage_uses_a_findings_schema(adapter_name, tmp_path):
    """Issue #46, rejection test 3. With ATTAVO_STAGE=verify, the
    invocation names a response shape carrying findings and tests_named
    -- not build's status/question. Red at 699f56d for claude-code,
    where ATTAVO_STAGE was read nowhere and every invocation got the
    build schema.
    """
    schema = dry_run(adapter_name, tmp_path, extra_env={"ATTAVO_STAGE": "verify"})["schema"]
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
    instruction = dry_run(adapter_name, tmp_path, extra_env={"ATTAVO_STAGE": "verify"})["instruction"]
    assert "approval commit" in instruction
    assert "reportable" in instruction


@pytest.mark.parametrize("adapter_name", ADAPTER_NAMES)
def test_situation_11_verify_defines_severity_as_derived(adapter_name, tmp_path):
    """Issue #48, rejection test 2. With ATTAVO_STAGE=verify, the
    invocation's instruction defines severity as derived rather than
    judged. Red at d04b484, where severity was named once in the
    finding fields and defined nowhere.
    """
    instruction = dry_run(adapter_name, tmp_path, extra_env={"ATTAVO_STAGE": "verify"})["instruction"]
    assert "severity" in instruction
    assert "derived rather than judged" in instruction


@pytest.mark.parametrize("adapter_name", ADAPTER_NAMES)
def test_situation_12_adapter_supplies_every_field_it_declares(adapter_name, tmp_path):
    """Vendor independence (2026-09-09 ruling). conformance.json's
    "reports" names the fields this adapter can supply with a real
    value -- an adapter claiming a field and returning null for it is a
    silent measurement gap, not a legitimate absence, and this fails
    the suite instead of writing one. Dollars are never summed across
    executors; absent is null, never zero or blank -- this situation is
    what makes an adapter's null trustworthy as "not reported by this
    executor" rather than "forgot to report it".
    """
    manifest = CONFORMANCE[adapter_name]
    reports = manifest.get("reports", [])
    run = run_situation(adapter_name, "done", tmp_path)
    assert run.returncode == 0, run.stderr
    report = json.loads(run.stdout.splitlines()[-1])
    for field in reports:
        assert report.get(field) is not None, (
            f"{adapter_name} declares {field!r} in its reports but a done "
            f"run's own report line carries none")


@pytest.mark.parametrize("adapter_name", ADAPTER_NAMES)
def test_situation_13_subagent_guard_is_declared(adapter_name, tmp_path):
    """Issue #88, rejection test (class B, general across any adapter).
    Every adapter's conformance.json names subagent_guard.supported: an
    adapter naming neither a working check nor an admitted inability is
    an undeclared blind spot, not a legitimate absence (mirrors
    situation 12's vendor-independence ruling). A supported adapter's
    named situation must refuse the run -- naming the tool's own counts
    -- rather than reporting done on work that can outlive it; an
    unsupported adapter must say why it cannot check at all.
    """
    manifest = CONFORMANCE[adapter_name]
    guard = manifest.get("subagent_guard")
    assert guard is not None, f"{adapter_name} declares no subagent_guard"
    assert isinstance(guard.get("supported"), bool), (
        f"{adapter_name}'s subagent_guard.supported must be true or false")

    if guard["supported"]:
        run = run_situation(adapter_name, guard["situation"], tmp_path)
        assert run.returncode != 0, (
            f"{adapter_name} accepted a run with spawned background work "
            "as done")
        transcript = run.stdout + run.stderr
        assert "subagent" in transcript.lower()
    else:
        assert guard.get("reason"), (
            f"{adapter_name} declares subagent_guard.supported: false "
            "with no reason")
