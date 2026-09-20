"""`attavo drive N --report`: what one item is waiting on and for whom, as a
pure function of its records, its markers, and one read of the forge. It
writes nothing, starts nothing, and never waits (#171: a refusal nobody
could read, twice on #194).

Run: python3 -m pytest tests -q
"""
from __future__ import annotations

import json
import os
import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from helpers import commit_all, git, make_tenant
from test_attest_time import INVOCATIONS, make_item
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


def write_live_marker(root: Path, stage: str, nn: int, age_s: int) -> None:
    """A marker whose pid is this test's own process: alive, on this host,
    begun age_s seconds ago, with no events file."""
    at = (datetime.now(timezone.utc) - timedelta(seconds=age_s)).strftime("%Y-%m-%dT%H:%M:%SZ")
    marker = records_dir(root) / f"{stage}-{nn:02d}.started"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"at": at, "host": socket.gethostname(), "pid": os.getpid(),
                                  "sha": head_of(root), "product_version": "fixture"}))
    commit_all(root, "invocation marker for item 1")


def test_report_reads_an_expired_approval_as_yours_and_says_why(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    approve_item(root)
    spec = root / "specs" / "1" / "spec.md"
    spec.write_text(spec.read_text() + "\n")
    commit_all(root, "touch the spec after approval")
    out = report(root)
    assert (out["state"], out["kind"]) == ("waiting-on-approval", "yours"), out
    assert "expired" in out["says"], out


def test_report_reads_a_live_invocation_inside_the_bound_as_running(tmp_path: Path) -> None:
    """Launch stayed on the screen while #194's build ran: a view guessed
    whose move it was."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    add_drive_stall_bound(root, 60)
    approve_item(root)
    write_live_marker(root, "build", 1, age_s=5)
    out = report(root)
    assert (out["state"], out["kind"], out["stage"], out["attempt"]) == ("running", "waits", "build", 1), out
    assert out["since"] and isinstance(out["last_event_age_s"], int), out
    assert out["says"].startswith("Build running since "), out


def test_report_reads_a_live_invocation_past_the_bound_as_hung_and_ends_nothing(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    add_drive_stall_bound(root, 60)
    approve_item(root)
    write_live_marker(root, "build", 1, age_s=600)
    out = report(root)
    assert (out["state"], out["kind"], out["stop_class"]) == ("owes-recovery", "acts", "hung"), out
    os.kill(os.getpid(), 0)  # the report terminated nothing: this process is its "hung" pid


def test_report_reads_a_review_of_an_earlier_build_as_review_next(tmp_path: Path) -> None:
    """#109: a later build supersedes the latest review; the item is not
    ready and is not stopped."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    add_bare_remote(root, tmp_path)
    approve_item(root)
    assert run_attavo(root, "drive", "1").returncode == 0
    assert run_attavo(root, "drive", "1").returncode == 0
    assert report(root)["state"] == "ready-to-merge"
    build = json.loads(record_files(root, "build")[-1].read_text())
    build["sha"] = "f" * 40
    (records_dir(root) / "build-02.json").write_text(json.dumps(build))
    commit_all(root, "a second build record, minted by hand")
    out = report(root)
    assert (out["state"], out["kind"], out["stage"]) == ("next-review", "acts", "verify"), out


def test_report_does_not_read_a_failed_build_of_an_earlier_approval_as_stopped(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    approve_item(root)
    (records_dir(root) / "build-01.json").write_text(json.dumps(
        {"status": "failed", "sha": "b" * 40, "approval_sha": "0" * 40,
         "stop": {"class": "gate", "provenance": "tenant", "detail": None}}))
    commit_all(root, "a failed build bound to an approval that is not the current one")
    out = report(root)
    assert (out["state"], out["kind"]) == ("next-build", "acts"), out


def test_report_names_the_counsel_record_that_read_the_stop(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    add_drive_stall_bound(root)
    approve_item(root)
    write_dead_marker(root, "build", 1)
    assert run_attavo(root, "drive", "--recover", "1").returncode == 0
    (records_dir(root) / "counsel-01.json").write_text(json.dumps(
        {"record": "build-01", "record_stage": "build", "verdict": "person", "status": "done"}))
    commit_all(root, "counsel read the stop")
    out = report(root)
    assert (out["state"], out["counsel"]) == ("stopped", "counsel-01"), out
    assert "counsel-01" in out["says"] and "person" in out["says"], out


def test_report_reads_a_merged_item_as_merged(tmp_path: Path) -> None:
    """The screen showed #204 as waiting, its pull request "not read", after
    it had merged: the branch survived and no view read the history."""
    root, merge = make_item(tmp_path, INVOCATIONS)
    run = run_attavo(root, "drive", "1", "--report")
    assert run.returncode == 0, run.stdout + run.stderr
    out = json.loads(run.stdout)
    assert (out["state"], out["kind"]) == ("merged", "done"), out
    assert merge[:7] in out["says"], out


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
