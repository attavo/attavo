"""`attavo drive N --report`: what one item is waiting on and for whom, as a
pure function of its records, its markers, and one read of the forge. It
writes nothing, starts nothing, and never waits (#171: a refusal nobody
could read, twice on #194).

Run: python3 -m pytest tests -q
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from helpers import commit_all, git, make_tenant
from test_drive import (
    GREEN_FORGE,
    RED_FORGE,
    UNCONCLUDED_FORGE,
    add_bare_remote,
    add_drive_stall_bound,
    add_forge,
    approve_item,
    head_of,
    record_files,
    records_dir,
    run_attavo,
    write_dead_marker,
)

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "schema" / "drive-report.schema.json").read_text())


def report(root: Path, item: str = "1", env: dict[str, Any] | None = None) -> dict[str, Any]:
    before = (head_of(root), git(root, "status", "--porcelain"),
              sorted(p.name for p in records_dir(root, item).glob("*")))
    run = run_attavo(root, "drive", item, "--report", env=env)
    assert run.returncode == 0, run.stdout + run.stderr
    after = (head_of(root), git(root, "status", "--porcelain"),
             sorted(p.name for p in records_dir(root, item).glob("*")))
    assert before == after, "the report wrote something"
    out: dict[str, Any] = json.loads(run.stdout)
    assert set(out) == set(SCHEMA["required"]) == set(SCHEMA["properties"]), out
    assert out["state"] in SCHEMA["properties"]["state"]["enum"], out
    assert out["kind"] in SCHEMA["properties"]["kind"]["enum"], out
    assert "drive" not in out["says"].lower() and "verify" not in out["says"].lower(), out
    return out


def test_report_names_a_red_check_and_the_head_and_writes_nothing(tmp_path: Path) -> None:
    """#171: on a concluded red check the product printed one line into a
    log nobody read, exited 1, and wrote nothing. The report says it for as
    long as it is true."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, RED_FORGE)
    add_bare_remote(root, tmp_path)
    approve_item(root)
    assert run_attavo(root, "drive", "1").returncode == 0
    out = report(root)
    assert (out["state"], out["kind"], out["stage"]) == ("blocked-on-checks", "waits", "verify"), out
    assert out["checks"] == [{"name": "ci", "conclusion": "failure"}], out
    assert "ci failure" in out["says"] and head_of(root)[:7] in out["says"], out
    assert record_files(root, "verify") == []
    assert report(root) == out, "two reads of one level differ"


def test_report_never_waits_on_a_check_that_is_still_running(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, UNCONCLUDED_FORGE)
    add_bare_remote(root, tmp_path)
    approve_item(root)
    assert run_attavo(root, "drive", "1").returncode == 0
    started = time.monotonic()
    out = report(root, env={"ATTAVO_DRIVE_POLL_BOUND_S": "30", "ATTAVO_DRIVE_POLL_INTERVAL_S": "5"})
    assert time.monotonic() - started < 4, "the report polled"
    assert (out["state"], out["kind"]) == ("waiting-on-checks", "waits"), out
    assert "ci in_progress" in out["says"], out


def test_report_walks_an_item_from_approval_to_ready(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    add_bare_remote(root, tmp_path)
    assert run_attavo(root, "spec-gate", "1").returncode == 0
    out = report(root)
    assert (out["state"], out["kind"]) == ("waiting-on-approval", "yours"), out
    approve_item(root)
    assert (report(root)["state"], report(root)["kind"]) == ("next-build", "acts")
    assert run_attavo(root, "drive", "1").returncode == 0
    assert (report(root)["state"], report(root)["stage"]) == ("next-review", "verify")
    assert run_attavo(root, "drive", "1").returncode == 0
    out = report(root)
    assert (out["state"], out["kind"], out["says"]) == ("ready-to-merge", "yours", "Ready to merge"), out


def test_report_says_what_recovery_would_record_for_a_dead_invocation(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    add_drive_stall_bound(root)
    approve_item(root)
    write_dead_marker(root, "build", 1)
    out = report(root)
    assert (out["state"], out["kind"], out["stage"], out["attempt"], out["stop_class"]) == (
        "owes-recovery", "acts", "build", 1, "died"), out
    assert not (records_dir(root) / "build-01.json").exists()


def test_report_names_the_stopped_record_its_class_its_counsel_and_its_issue(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    add_drive_stall_bound(root)
    approve_item(root)
    write_dead_marker(root, "build", 1)
    assert run_attavo(root, "drive", "--recover", "1").returncode == 0
    d = records_dir(root)
    (d / "build-01.filed").write_text("https://example.invalid/issues/9\n")
    commit_all(root, "the stop is filed")
    out = report(root)
    assert (out["state"], out["kind"], out["record"], out["stop_class"], out["filed"]) == (
        "stopped", "yours", "build-01", "died", "https://example.invalid/issues/9"), out
    assert out["says"].startswith("Needs you: build attempt 01 stopped as died"), out


def test_report_reads_every_item_this_repository_carries(tmp_path: Path) -> None:
    """A new reader must not wedge on a record that already exists (#194's
    verify-01). Every item under this repository's own records directory
    yields a report, in-process, with no forge read of its own."""
    from attavo.common import read_manifest
    from attavo.report import drive_report
    m = read_manifest(ROOT)
    assert m is not None
    m = {**m, "forge": {**(m.get("forge") or {}), "checks_command": "printf 'ci success\\n'"}}
    items = sorted(p.name for p in (ROOT / m["paths"]["records"]).iterdir() if p.is_dir())
    assert items
    for item in items:
        out = drive_report(ROOT, m, item)
        assert out["state"] in SCHEMA["properties"]["state"]["enum"], (item, out)
