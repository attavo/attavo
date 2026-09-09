"""Issue #41, acceptance 6 and 7: the proof this lane owes, not a proof
against any tenant. [executor].verify names codex while [executor].build
stays fixture, on the same throwaway fixture tenant every other test in
this suite uses (never a real, hosted tenant) -- build's own criterion,
checks_built.sh, asserts only that built.txt exists, not what it
contains, so any defect in its content is genuinely one "the criteria do
not cover," not a contrived one. codex's own invocation is canned through
ATTAVO_ADAPTER_RESPONSE_FILE (judgment is measured, not tested, the same
principle every adapter's own module docstring states); what this proves
mechanically is the wiring acceptance 6 and 7 name: the build record and
the verify record carry different adapters, and file-findings files what
verify reported.

Run: python3 -m pytest tests -q
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from helpers import commit_all, make_tenant

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"
GREEN_FORGE = "printf 'ci success\\n'"


def run_attavo(root: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run([sys.executable, str(ATTAVO), *args], cwd=str(root),
                          capture_output=True, text=True, check=False, env=full_env)


def latest_record(root: Path, stage: str, item: str = "1") -> dict:
    files = sorted((root / "records" / item).glob(f"{stage}-*.json"))
    assert files, f"no {stage} record for item {item}"
    return json.loads(files[-1].read_text())


def test_build_is_fixture_verify_is_codex_and_the_finding_is_filed(tmp_path):
    root = make_tenant(tmp_path, "build_valid")
    toml = root / "attavo.toml"
    toml.write_text(
        toml.read_text().replace(
            'build = "fixture"', 'build = "fixture"\nverify = "codex"')
        + '\n[executor.codex]\nmodel = "gpt-test"\n'
        + f'\n[forge]\nchecks_command = "{GREEN_FORGE}"\n'
        + 'file_issue_command = "cat >> filed.jsonl"\n')
    commit_all(root, "point verify at codex, build stays fixture")

    assert run_attavo(root, "spec-gate", "1").returncode == 0
    assert run_attavo(root, "approve", "1", "--by", "alice").returncode == 0

    build = run_attavo(root, "build", "1")
    assert build.returncode == 0, build.stdout
    build_rec = latest_record(root, "build")
    assert build_rec["adapter"] == "fixture"
    assert build_rec["status"] == "pass"

    response_file = tmp_path / "codex-response.json"
    response_file.write_text(json.dumps({
        "events": [{"type": "turn.completed", "usage": {"input_tokens": 421, "output_tokens": 88}}],
        "final": {
            "findings": [{
                "title": "checks_built.sh asserts only that built.txt exists, not what it contains",
                "specimen": "specs/1/criteria/checks_built.sh",
                "retires": "the assumption that a passing criterion says anything about content",
                "rejection_test": "test_checks_built_rejects_an_empty_built_txt",
                "severity": "info",
            }],
            "tests_named": ["tests/test_fixture.py::test_checks_built_rejects_an_empty_built_txt"],
        },
        "exit_code": 0,
    }))

    verify = run_attavo(root, "verify", "1",
                        env={"ATTAVO_ADAPTER_RESPONSE_FILE": str(response_file)})
    assert verify.returncode == 0, verify.stdout
    verify_rec = latest_record(root, "verify")
    assert verify_rec["status"] == "pass"
    assert verify_rec["adapter"] == "codex"
    assert verify_rec["findings"]
    assert verify_rec["findings"][0]["title"].startswith("checks_built.sh asserts only")

    # acceptance 7: the build and verify records name different adapters
    # for the same item -- separate invocations, not one tool wearing
    # two hats.
    assert build_rec["adapter"] != verify_rec["adapter"]

    filed = run_attavo(root, "file-findings", "1")
    assert filed.returncode == 0, filed.stdout
    filed_lines = (root / "filed.jsonl").read_text().splitlines()
    assert len(filed_lines) == 1
    assert json.loads(filed_lines[0])["title"] == verify_rec["findings"][0]["title"]
