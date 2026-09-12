"""attavo file-stops: opens one tenant issue per stop record of an item
with no issue filed for it yet, via the tenant's [forge].file_issue_command,
naming the class, the provenance, the record path, and the log's last
twenty lines; refuses -- nothing filed -- a stop whose class is unclassed
and whose detail is empty. Issue #89.

Rejection test, written and run red first: on `main` before this issue
there was no `file-stops` subcommand, so it failed with "unknown subcommand".

Run: python3 -m pytest tests -q
"""
import json
import subprocess
import sys
from pathlib import Path

from helpers import commit_all, make_tenant

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"


def run_attavo(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ATTAVO), *args], cwd=str(root),
                          capture_output=True, text=True, check=False)


def configure_filing(root: Path, filed_path: Path) -> None:
    """[forge].file_issue_command reads one stop as JSON on stdin; this
    stand-in just appends it to a local file, one line per call, so the
    test can read back what would have been filed."""
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text() + f'\n[forge]\nfile_issue_command = "cat >> {filed_path}"\n')
    commit_all(root, "configure issue filing")


def write_build_record(root: Path, item: str, n: int, status: str, stop: dict | None,
                       log: str = "adapter transcript\n") -> Path:
    records_dir = root / "records" / item
    records_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "sha": "0" * 40, "start_sha": "0" * 40, "spec_sha": "0" * 40,
        "approval_sha": "0" * 40, "at": "2026-01-01T00:00:00Z",
        "adapter": "fixture", "adapter_config": {},
        "executor_identity": {"name": "x", "email": "x@attavo.invalid"},
        "reports": [], "adapter_version": "fixture-1",
        "model": "fixture", "model_version": None,
        "iterations": 1, "criteria": [], "selected_tests": None, "checks": None,
        "tokens": {"input": 0, "output": 0}, "cost_usd": None,
        "wall_clock_s": 0.1, "status": status, "refusal": None, "halted": None,
        "stop": stop,
    }
    path = records_dir / f"build-{n:02d}.json"
    path.write_text(json.dumps(record))
    path.with_suffix(".log").write_text(log)
    return path


def test_file_stops_refuses_an_unclassed_stop_with_empty_detail(tmp_path):
    """Acceptance row 4 (half). A stop the product cannot even describe --
    class unclassed, detail empty -- is refused by name; nothing is filed."""
    root = make_tenant(tmp_path, "build_valid")
    filed = tmp_path / "filed.jsonl"
    configure_filing(root, filed)

    write_build_record(root, "1", 1, "failed",
                       {"class": "unclassed", "provenance": "product", "detail": ""})

    run = run_attavo(root, "file-stops", "1")
    assert run.returncode == 1
    assert "unclassed" in run.stdout
    assert not filed.exists()


def test_file_stops_files_one_issue_per_stop_record_and_is_idempotent(tmp_path):
    """Acceptance row 4: one issue per stop record, with class,
    provenance, record path, and log tail in the body; a second run files
    nothing new."""
    root = make_tenant(tmp_path, "build_valid")
    filed = tmp_path / "filed.jsonl"
    configure_filing(root, filed)

    write_build_record(root, "1", 1, "refused",
                       {"class": "claims-boundary", "provenance": "spec", "detail": None},
                       log="line one\nline two\nunclaimed.txt\n")
    write_build_record(root, "1", 2, "failed",
                       {"class": "ceiling", "provenance": "product", "detail": None},
                       log="Error: input_too_large\n[adapter exited 1]\n")

    run = run_attavo(root, "file-stops", "1")
    assert run.returncode == 0, run.stdout + run.stderr

    filed_bodies = [json.loads(l) for l in filed.read_text().splitlines() if l.strip()]
    assert [b["class"] for b in filed_bodies] == ["claims-boundary", "ceiling"]
    assert filed_bodies[0]["provenance"] == "spec"
    assert filed_bodies[0]["record"] == "records/1/build-01.json"
    assert "unclaimed.txt" in filed_bodies[0]["log_tail"]
    assert "input_too_large" in filed_bodies[1]["log_tail"]

    # idempotent: a second run files nothing new.
    run2 = run_attavo(root, "file-stops", "1")
    assert run2.returncode == 0
    assert "no stops to file" in run2.stdout
    filed_bodies_after = [json.loads(l) for l in filed.read_text().splitlines() if l.strip()]
    assert filed_bodies_after == filed_bodies


def test_file_stops_leaves_a_pass_record_alone(tmp_path):
    """A pass record carries no stop at all -- nothing to file for it."""
    root = make_tenant(tmp_path, "build_valid")
    filed = tmp_path / "filed.jsonl"
    configure_filing(root, filed)

    write_build_record(root, "1", 1, "pass", None)

    run = run_attavo(root, "file-stops", "1")
    assert run.returncode == 0
    assert "no stops to file" in run.stdout
    assert not filed.exists()


def test_file_stops_refuses_without_a_file_issue_command(tmp_path):
    root = make_tenant(tmp_path, "build_valid")
    write_build_record(root, "1", 1, "failed",
                       {"class": "ceiling", "provenance": "product", "detail": None})

    run = run_attavo(root, "file-stops", "1")
    assert run.returncode == 1
    assert "file_issue_command" in run.stdout
