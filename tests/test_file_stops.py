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
    """[forge].file_issue_command reads one stop as JSON on stdin, appends
    it to a local file (one line per call, so the test can read back what
    would have been filed), and prints a fake issue URL on stdout -- the
    number is the call's own place in filed_path's line count, so two
    calls in one run print two different URLs, the way two real issues
    would."""
    toml = root / "attavo.toml"
    command = (f"n=$(wc -l < {filed_path} 2>/dev/null || echo 0); "
              f"cat >> {filed_path}; "
              f"echo https://github.com/tenant/repo/issues/$((n + 1))")
    toml.write_text(toml.read_text() + f"\n[forge]\nfile_issue_command = '{command}'\n")
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


def test_file_stops_marker_holds_the_filing_commands_stdout(tmp_path):
    """The .filed marker beside a stop record it just filed carries the
    filing command's own stdout -- the issue it opened -- not an empty
    file that only says "filed" with no way to say which issue."""
    root = make_tenant(tmp_path, "build_valid")
    filed = tmp_path / "filed.jsonl"
    configure_filing(root, filed)

    record_path = write_build_record(
        root, "1", 1, "refused",
        {"class": "claims-boundary", "provenance": "spec", "detail": None})

    run = run_attavo(root, "file-stops", "1")
    assert run.returncode == 0, run.stdout + run.stderr

    marker = record_path.with_suffix(".filed")
    assert marker.is_file()
    assert marker.read_text().strip() == "https://github.com/tenant/repo/issues/1"


def test_file_stops_leaves_the_marker_uncommitted(tmp_path):
    """A run of attavo file-stops by hand, outside drive, writes the
    marker and leaves committing to the caller -- the same as build and
    verify leave their own records uncommitted."""
    root = make_tenant(tmp_path, "build_valid")
    filed = tmp_path / "filed.jsonl"
    configure_filing(root, filed)

    record_path = write_build_record(
        root, "1", 1, "refused",
        {"class": "claims-boundary", "provenance": "spec", "detail": None})

    run = run_attavo(root, "file-stops", "1")
    assert run.returncode == 0

    status = subprocess.run(["git", "-C", str(root), "status", "--porcelain",
                             str(record_path.with_suffix(".filed").relative_to(root))],
                            capture_output=True, text=True, check=True).stdout
    assert status.startswith("??")  # untracked -- file-stops never git-adds it


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


def write_counsel_record(root: Path, item: str, n: int, record_field: str,
                         verdict: str = "person",
                         proposal: dict | None = None) -> Path:
    """Issue #100: a counsel-NN.json whose own "record" field names the
    stage record it read (e.g. "build-01") -- what file-stops matches
    against to find the counsel that read a given stop."""
    records_dir = root / "records" / item
    records_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "sha": "0" * 40, "record_stage": record_field.split("-")[0], "record": record_field,
        "at": "2026-01-01T00:00:01Z", "adapter": "fixture", "adapter_config": {},
        "adapter_version": "fixture-1", "model": "fixture", "model_version": None,
        "reports": [], "tokens": {"input": 1, "output": 1}, "cost_usd": 0.0,
        "wall_clock_s": 0.1, "status": "done", "class": "unclassed", "verdict": verdict,
        "citations": [{"kind": "instruction", "path": "specs/1/spec.md", "symbol": None,
                       "ruling_id": None}],
        "proposal": proposal or {"ruling_text": None, "patch": None, "question": "what now?"},
    }
    path = records_dir / f"counsel-{n:02d}.json"
    path.write_text(json.dumps(record))
    return path


def test_file_stops_puts_counsels_verdict_citations_and_proposal_in_the_body(tmp_path):
    """Issue #100, acceptance row 3. file-stops puts counsel's verdict,
    citations, and proposal in the issue body, matched to the stage
    record counsel actually read."""
    root = make_tenant(tmp_path, "build_valid")
    filed = tmp_path / "filed.jsonl"
    configure_filing(root, filed)

    write_build_record(root, "1", 1, "halted",
                       {"class": "unclassed", "provenance": "product", "detail": "x"})
    write_counsel_record(root, "1", 1, "build-01", verdict="patch",
                         proposal={"ruling_text": None,
                                  "patch": {"file": "bin/attavo", "symbol": "cmd_build",
                                           "rejection_test": "tests/test_build.py::test_x",
                                           "diff": "--- a\n+++ b\n"},
                                  "question": None})

    run = run_attavo(root, "file-stops", "1")
    assert run.returncode == 0, run.stdout + run.stderr

    filed_bodies = [json.loads(l) for l in filed.read_text().splitlines() if l.strip()]
    assert len(filed_bodies) == 1
    assert filed_bodies[0]["counsel_verdict"] == "patch"
    assert filed_bodies[0]["counsel_citations"] == [
        {"kind": "instruction", "path": "specs/1/spec.md", "symbol": None, "ruling_id": None}]
    assert filed_bodies[0]["counsel_proposal"]["patch"]["file"] == "bin/attavo"


def test_file_stops_leaves_counsel_fields_null_with_no_counsel_record(tmp_path):
    """A stop file-stops files without counsel having run first (e.g. run
    standalone, outside drive) still files -- the counsel fields are null
    rather than blocking the filing."""
    root = make_tenant(tmp_path, "build_valid")
    filed = tmp_path / "filed.jsonl"
    configure_filing(root, filed)

    write_build_record(root, "1", 1, "halted",
                       {"class": "unclassed", "provenance": "product", "detail": "x"})

    run = run_attavo(root, "file-stops", "1")
    assert run.returncode == 0, run.stdout + run.stderr

    filed_bodies = [json.loads(l) for l in filed.read_text().splitlines() if l.strip()]
    assert filed_bodies[0]["counsel_verdict"] is None
    assert filed_bodies[0]["counsel_citations"] == []
    assert filed_bodies[0]["counsel_proposal"] is None


def test_file_stops_composes_a_title_naming_class_provenance_item_and_record(tmp_path):
    """Issue #114, acceptance row 1. The filed body carries a non-empty
    title naming class, provenance, item, and record -- not the tenant
    side's <untitled> fallback."""
    root = make_tenant(tmp_path, "build_valid")
    filed = tmp_path / "filed.jsonl"
    configure_filing(root, filed)

    write_build_record(root, "1", 1, "refused",
                       {"class": "claims-boundary", "provenance": "spec", "detail": None})

    run = run_attavo(root, "file-stops", "1")
    assert run.returncode == 0, run.stdout + run.stderr

    filed_bodies = [json.loads(l) for l in filed.read_text().splitlines() if l.strip()]
    assert filed_bodies[0]["title"] == "stop claims-boundary/spec on item 1: build-01"


def test_file_stops_refuses_a_stop_whose_composed_title_would_be_empty(tmp_path):
    """Issue #114, acceptance row 2. An empty class is a record defect,
    not a filing: file-stops refuses by name and files nothing."""
    root = make_tenant(tmp_path, "build_valid")
    filed = tmp_path / "filed.jsonl"
    configure_filing(root, filed)

    write_build_record(root, "1", 1, "failed",
                       {"class": "", "provenance": "product", "detail": None})

    run = run_attavo(root, "file-stops", "1")
    assert run.returncode == 1
    assert "empty title" in run.stdout
    assert not filed.exists()
    assert not (root / "records" / "1" / "build-01.filed").exists()


def test_file_stops_refuses_without_a_file_issue_command(tmp_path):
    root = make_tenant(tmp_path, "build_valid")
    write_build_record(root, "1", 1, "failed",
                       {"class": "ceiling", "provenance": "product", "detail": None})

    run = run_attavo(root, "file-stops", "1")
    assert run.returncode == 1
    assert "file_issue_command" in run.stdout
