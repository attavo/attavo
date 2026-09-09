"""adapters/claude-code/run: its argument construction is checked through
ATTAVO_ADAPTER_DRY_RUN=1, which prints the `claude` command line as a JSON
array instead of running it, and its response handling is checked through
ATTAVO_ADAPTER_RESPONSE_FILE, which feeds it a canned --output-format json
response instead of invoking `claude`. Judgment itself is measured, not
tested, so no real model is ever in this loop.

Issue #24: [executor.<adapter>] is an open configuration table, passed
through as ATTAVO_ADAPTER_CONFIG and copied verbatim into build-NN.json as
adapter_config. claude-code reads auth, model, allowed_tools,
setting_sources, and permission_mode from it; ATTAVO_MODEL is retired.

Issue #30: the tenant's checks are part of done. build exports
ATTAVO_CHECKS_COMMAND when the manifest declares [checks]; the
instruction names it as part of done when set, and says nothing about
checks when it is not. Retires the dead test-run-command environment
variable this adapter used to read (dead: bin/attavo never set it).

Issue #34: the adapter keeps the evidence it is handed. The whole result
prints to stdout before the adapter's own final line, so build-NN.log
carries it. is_error, an absent structured_output, or a "question" under
40 characters all exit 1 (build's own "failed", not a halt to rule on).
Every canned response below now carries is_error and structured_output
for this reason -- a response without them now fails before ever
reaching model derivation, which is the point.

The response-handling tests are a separate regression, for a real response
shape: `claude --bare -p ... --output-format json` carries no top-level
"model" field at all -- the model id is the key of "modelUsage", its
per-model cost/usage breakdown (confirmed against `claude --version
2.1.263` on 2026-09-07). A future response-shape change on the executor's
side that drops or renames "modelUsage" would fail these tests before it
ever reached a record.

Issue #38: a non-zero `claude` exit used to reach this adapter's own
stderr only -- never stdout -- so build-NN.log, which reads only stdout,
recorded nothing about the failure. The adapter now prints claude's
stdout and stderr as transcript before exiting with claude's own code.
Specimen: attavo/code-factory build-11.json, a "failed" record with a
0-byte log after a 14-minute run.

Issue #40: this file is claude-code-specific by construction (every test
invokes CLAUDE_CODE_RUN directly), so it is not where a general executor
contract belongs -- a second adapter inherits none of it. What used to
be here and is genuinely general (the raw result reaching stdout,
is_error and question-quality gating a halt, a non-zero tool exit
passing through) moved, not copied, to
tests/test_executor_conformance.py, which runs the same situations
against any adapter that declares a conformance.json. What stays here
is what is genuinely Claude Code's: --bare, --setting-sources,
--strict-mcp-config, and modelUsage parsing.
test_claude_code_names_the_tenants_checks keeps only its
English-specific half (the instruction must not tell the executor to
run the check) -- the command's mere presence in the instruction is now
the conformance suite's situation 7.

Run: python3 -m pytest tests -q
"""
import json
import os
import subprocess
from pathlib import Path

from helpers import FIXTURES

ROOT = Path(__file__).resolve().parent.parent
CLAUDE_CODE_RUN = ROOT / "adapters" / "claude-code" / "run"


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


def dry_run(tmp_path: Path, config: dict | None = None,
           extra_env: dict | None = None) -> list[str]:
    env = base_env(tmp_path)
    env["ATTAVO_ADAPTER_DRY_RUN"] = "1"
    env["ATTAVO_ADAPTER_CONFIG"] = json.dumps(config or {})
    if extra_env:
        env.update(extra_env)

    run = subprocess.run([str(CLAUDE_CODE_RUN)], capture_output=True, text=True,
                         check=False, env=env)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)["argv"]


def run_with_response(tmp_path: Path, response: dict,
                      extra_env: dict | None = None) -> subprocess.CompletedProcess:
    response_file = tmp_path / "response.json"
    response_file.write_text(json.dumps(response))
    env = base_env(tmp_path)
    env["ATTAVO_ADAPTER_RESPONSE_FILE"] = str(response_file)
    if extra_env:
        env.update(extra_env)
    return subprocess.run([str(CLAUDE_CODE_RUN)], capture_output=True, text=True,
                          check=False, env=env)


def test_claude_code_api_mode_is_default(tmp_path):
    """Acceptance 3. With no [executor.claude-code] table at all, the
    command line is unchanged from v0.3.0."""
    cmd = dry_run(tmp_path)

    assert "--bare" in cmd
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert "--json-schema" in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "acceptEdits"
    assert cmd[cmd.index("--allowedTools") + 1] == "Read,Edit,Write,Bash(git *),Bash(python3 *)"
    assert "--model" not in cmd
    assert "--setting-sources" not in cmd
    assert "--strict-mcp-config" not in cmd


def test_claude_code_subscription_mode(tmp_path):
    """Acceptance 2. auth = "subscription": no --bare, and both
    --setting-sources user and --strict-mcp-config so the tenant's own
    project hooks and MCP servers do not load -- CLAUDE.md still does.
    Regression: detects the tenant's hooks (tenant zero's editor guard,
    which #576 deletes) creeping back into a subscription build.
    """
    cmd = dry_run(tmp_path, config={"auth": "subscription"})

    assert "--bare" not in cmd
    assert cmd[cmd.index("--setting-sources") + 1] == "user"
    assert "--strict-mcp-config" in cmd


def test_claude_code_model_from_config(tmp_path):
    """Acceptance 4. model in the table appears as --model; the retired
    ATTAVO_MODEL, even if still set in the environment, is ignored."""
    cmd = dry_run(tmp_path, config={"model": "claude-sonnet-5"},
                 extra_env={"ATTAVO_MODEL": "should-be-ignored"})

    assert cmd.count("--model") == 1
    assert cmd[cmd.index("--model") + 1] == "claude-sonnet-5"
    assert "should-be-ignored" not in cmd


def test_claude_code_names_the_tenants_checks(tmp_path):
    """Issue #30, acceptance 5, reworded by issue #36. The instruction
    must not tell the executor to run the check itself. Regression:
    build-10 on tenant zero, where "must exit 0 before you commit" made
    the executor the runner (146 and 123 turns, 72 of 83 executor
    minutes waiting on tools) of a check attavo build already runs after
    every commit.

    The command's mere presence in the instruction (with it, and its
    absence without it) is now tests/test_executor_conformance.py's
    situation 7 -- general across any adapter. What stays here is the
    part that is an assertion about English and does not transfer.
    """
    with_checks = dry_run(tmp_path, extra_env={
        "ATTAVO_CHECKS_COMMAND": "python3 bin/factory-lane check --base origin/main"})
    instruction = with_checks[with_checks.index("-p") + 1]
    assert "are run for you" in instruction
    assert "before you commit" not in instruction

    without_checks = dry_run(tmp_path)
    instruction_without = without_checks[without_checks.index("-p") + 1]
    assert "are run for you" not in instruction_without


def test_claude_code_adapter_derives_model_from_usage_breakdown(tmp_path):
    """The real response shape: no top-level "model", one modelUsage key."""
    response = {
        "is_error": False,
        "structured_output": {"status": "done", "question": None},
        "usage": {"input_tokens": 5, "output_tokens": 9},
        "total_cost_usd": 0.01,
        "modelUsage": {
            "claude-sonnet-5": {"inputTokens": 5, "outputTokens": 9},
        },
    }
    run = run_with_response(tmp_path, response)
    assert run.returncode == 0, run.stderr
    line = json.loads(run.stdout.splitlines()[-1])
    assert line["model"] == "claude-sonnet-5"


def test_claude_code_adapter_derives_model_from_dominant_usage_key(tmp_path):
    """Several modelUsage keys (a lightweight helper model alongside the
    primary one): the key with the most total tokens wins."""
    response = {
        "is_error": False,
        "structured_output": {"status": "done", "question": None},
        "usage": {"input_tokens": 907, "output_tokens": 21},
        "total_cost_usd": 0.05,
        "modelUsage": {
            "claude-haiku-4-5-20251001": {"inputTokens": 897, "outputTokens": 12},
            "claude-sonnet-5": {"inputTokens": 10, "outputTokens": 9},
        },
    }
    run = run_with_response(tmp_path, response)
    assert run.returncode == 0, run.stderr
    line = json.loads(run.stdout.splitlines()[-1])
    assert line["model"] == "claude-haiku-4-5-20251001"


def test_claude_code_adapter_fails_without_a_model_id(tmp_path):
    """No top-level "model" and empty modelUsage: the adapter must fail
    loudly rather than emit an empty model that would let a "pass" record
    through with no identity behind it. The raw result still reaches
    stdout (issue #34) -- only the final report line is withheld."""
    response = {"is_error": False, "structured_output": {"status": "done", "question": None},
               "usage": {"input_tokens": 0, "output_tokens": 0}, "modelUsage": {}}
    run = run_with_response(tmp_path, response)
    assert run.returncode == 1
    assert "model" in run.stderr
    assert run.stdout == json.dumps(response) + "\n"

