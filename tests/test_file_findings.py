"""attavo file-findings: opens one tenant issue per finding in the item's
latest verify record, via the tenant's [forge].file_issue_command, and
refuses -- nothing filed -- a record carrying a finding with no retires.
Issue #40, section 2.

Regression test, written and run red first: on `main` at 719d39f there
was no `file-findings` subcommand, so it failed with "unknown subcommand".

Run: python3 -m pytest tests -q
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from helpers import commit_all, make_tenant

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"


def run_attavo(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(ATTAVO), *args], cwd=str(root),
                          capture_output=True, text=True, check=False)


def configure_filing(root: Path, filed_path: Path) -> None:
    """[forge].file_issue_command reads one finding as JSON on stdin;
    this stand-in just appends it to a local file, one line per call, so
    the test can read back what would have been filed."""
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text() + f'\n[forge]\nfile_issue_command = "cat >> {filed_path}"\n')
    commit_all(root, "configure issue filing")


def write_verify_record(root: Path, item: str, findings: list[Any]) -> None:
    records_dir = root / "records" / item
    records_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "sha": "0" * 40, "build_sha": "0" * 40, "build_record": "build-01",
        "approval_sha": "0" * 40, "at": "2026-01-01T00:00:00Z",
        "adapter": "fixture", "adapter_config": {}, "adapter_version": "fixture-1",
        "model": "fixture", "model_version": None,
        "forge_checks": {"command": "true", "sha": "0" * 40, "conclusions": []},
        "findings": findings, "tests_named": [],
        "mutation": None, "weak_stub": False, "stub_ratio": None,
        "tokens": {"input": 0, "output": 0}, "cost_usd": None,
        "wall_clock_s": 0.1, "status": "pass",
    }
    (records_dir / "verify-01.json").write_text(json.dumps(record))


def test_file_findings_refuses_a_finding_with_no_retires(tmp_path: Path) -> None:
    """Acceptance 7. A finding with no retires refuses the whole record --
    nothing is filed, not even the other findings alongside it.
    """
    root = make_tenant(tmp_path, "build_valid")
    filed = tmp_path / "filed.jsonl"
    configure_filing(root, filed)

    write_verify_record(root, "1", [
        {"title": "criteria never assert the delete path", "specimen": "spec.md",
         "rejection_test": "none yet", "severity": "medium"},
    ])

    run = run_attavo(root, "file-findings", "1")
    assert run.returncode == 1
    assert "criteria never assert the delete path" in run.stdout
    assert not filed.exists()


def test_file_findings_files_one_issue_per_finding(tmp_path: Path) -> None:
    """The mechanism this refusal protects: every well-formed finding is
    filed, one command invocation each."""
    root = make_tenant(tmp_path, "build_valid")
    filed = tmp_path / "filed.jsonl"
    configure_filing(root, filed)

    write_verify_record(root, "1", [
        {"title": "finding one", "specimen": "a", "retires": "nothing",
         "rejection_test": "t1", "severity": "low"},
        {"title": "finding two", "specimen": "b", "retires": "nothing",
         "rejection_test": "t2", "severity": "low"},
    ])

    run = run_attavo(root, "file-findings", "1")
    assert run.returncode == 0

    filed_titles = [json.loads(l)["title"] for l in filed.read_text().splitlines() if l.strip()]
    assert filed_titles == ["finding one", "finding two"]
