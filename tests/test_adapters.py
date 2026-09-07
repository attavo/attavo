"""Acceptance 10: the claude-code adapter is not exercised by the suite --
judgment is measured, not tested -- so its argument construction is checked
through ATTAVO_ADAPTER_DRY_RUN=1, which prints the `claude` command line as
a JSON array instead of running it.

Run: python3 -m pytest tests -q
"""
import json
import os
import subprocess
from pathlib import Path

from helpers import FIXTURES

ROOT = Path(__file__).resolve().parent.parent
CLAUDE_CODE_RUN = ROOT / "adapters" / "claude-code" / "run"


def dry_run(tmp_path: Path, extra_env: dict | None = None) -> list[str]:
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
        "ATTAVO_ADAPTER_DRY_RUN": "1",
    })
    if extra_env:
        env.update(extra_env)

    run = subprocess.run([str(CLAUDE_CODE_RUN)], capture_output=True, text=True,
                         check=False, env=env)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_claude_code_adapter_command_line(tmp_path):
    cmd = dry_run(tmp_path)

    assert "--bare" in cmd
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert "--json-schema" in cmd
    assert "--model" not in cmd


def test_claude_code_adapter_passes_the_model_when_set(tmp_path):
    cmd = dry_run(tmp_path, {"ATTAVO_MODEL": "claude-sonnet-5"})

    assert cmd[cmd.index("--model") + 1] == "claude-sonnet-5"
