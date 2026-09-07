"""Acceptance 10: the claude-code adapter is not exercised by the suite --
judgment is measured, not tested -- so its argument construction is checked
through ATTAVO_ADAPTER_DRY_RUN=1, which prints the `claude` command line as
a JSON array instead of running it, and its response handling is checked
through ATTAVO_ADAPTER_RESPONSE_FILE, which feeds it a canned
--output-format json response instead of invoking `claude`.

The response-handling tests are a regression for a real response shape:
`claude --bare -p ... --output-format json` carries no top-level "model"
field at all -- the model id is the key of "modelUsage", its per-model
cost/usage breakdown (confirmed against `claude --version 2.1.263` on
2026-09-07). Before this fix the adapter read the absent "model" field,
so every real build record carried `model: ""` -- which the build
contract itself refuses. A future response-shape change on the
executor's side that drops or renames "modelUsage" would fail these
tests before it ever reached a record.

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
    env.pop("ATTAVO_MODEL", None)
    env.update({
        "ATTAVO_SPEC_DIR": str(spec_dir),
        "ATTAVO_RECORDS_DIR": str(tmp_path / "records"),
        "ATTAVO_RULINGS": str(tmp_path / "rulings.md"),
        "ATTAVO_BRANCH": "attavo/1-example",
        "ATTAVO_ITERATION": "1",
        "ATTAVO_FEEDBACK": str(tmp_path / "feedback.txt"),
    })
    return env


def dry_run(tmp_path: Path, extra_env: dict | None = None) -> list[str]:
    env = base_env(tmp_path)
    env["ATTAVO_ADAPTER_DRY_RUN"] = "1"
    if extra_env:
        env.update(extra_env)

    run = subprocess.run([str(CLAUDE_CODE_RUN)], capture_output=True, text=True,
                         check=False, env=env)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


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


def test_claude_code_adapter_command_line(tmp_path):
    cmd = dry_run(tmp_path)

    assert "--bare" in cmd
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert "--json-schema" in cmd
    assert "--model" not in cmd


def test_claude_code_adapter_passes_the_model_when_set(tmp_path):
    cmd = dry_run(tmp_path, {"ATTAVO_MODEL": "claude-sonnet-5"})

    assert cmd[cmd.index("--model") + 1] == "claude-sonnet-5"


def test_claude_code_adapter_derives_model_from_usage_breakdown(tmp_path):
    """The real response shape: no top-level "model", one modelUsage key."""
    response = {
        "usage": {"input_tokens": 5, "output_tokens": 9},
        "total_cost_usd": 0.01,
        "modelUsage": {
            "claude-sonnet-5": {"inputTokens": 5, "outputTokens": 9},
        },
    }
    run = run_with_response(tmp_path, response)
    assert run.returncode == 0, run.stderr
    line = json.loads(run.stdout)
    assert line["model"] == "claude-sonnet-5"


def test_claude_code_adapter_derives_model_from_dominant_usage_key(tmp_path):
    """Several modelUsage keys (a lightweight helper model alongside the
    primary one): the key with the most total tokens wins."""
    response = {
        "usage": {"input_tokens": 907, "output_tokens": 21},
        "total_cost_usd": 0.05,
        "modelUsage": {
            "claude-haiku-4-5-20251001": {"inputTokens": 897, "outputTokens": 12},
            "claude-sonnet-5": {"inputTokens": 10, "outputTokens": 9},
        },
    }
    run = run_with_response(tmp_path, response)
    assert run.returncode == 0, run.stderr
    line = json.loads(run.stdout)
    assert line["model"] == "claude-haiku-4-5-20251001"


def test_claude_code_adapter_fails_without_a_model_id(tmp_path):
    """No top-level "model", empty modelUsage, and ATTAVO_MODEL unset: the
    adapter must fail loudly rather than emit an empty model that would
    let a "pass" record through with no identity behind it."""
    response = {"usage": {"input_tokens": 0, "output_tokens": 0}, "modelUsage": {}}
    run = run_with_response(tmp_path, response)
    assert run.returncode == 1
    assert "model" in run.stderr
    assert run.stdout == ""
