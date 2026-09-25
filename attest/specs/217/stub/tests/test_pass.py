"""The reconciler's R2 (claude/reconciler-brief-2026-09-20.md, rulings A, B,
H): `attavo drive N` is one short pass that reads the report, acts at most
once, and returns; a stage runs in a detached worker with its own
heartbeat; the marker's push is the lease; recovery is the pass's; the
review takes the checks the pass read on the pre-marker head.

Run: python3 -m pytest tests -q
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from helpers import commit_all, git, make_tenant
from test_drive import (
    GREEN_FORGE,
    add_bare_remote,
    add_drive_stall_bound,
    add_forge,
    approve_item,
    head_of,
    latest_record,
    record_files,
    records_dir,
    remote_head,
    run_attavo,
    write_dead_marker,
)

ROOT = Path(__file__).resolve().parent.parent
FAST = {"ATTAVO_HEARTBEAT_S": "0.2", "ATTAVO_DRIVE_POLL_INTERVAL_S": "0.5"}


def slow_adapter(root: Path, seconds: float) -> None:
    """A tenant-side build adapter: the product's own fixture adapter,
    after `seconds` of sleep on the build stage."""
    d = root / "attavo" / "adapters" / "fixture"
    d.mkdir(parents=True, exist_ok=True)
    run = d / "run"
    run.write_text(f'#!/bin/sh\nif [ "${{ATTAVO_STAGE:-build}}" = build ]; then sleep {seconds}; fi\n'
                   f'exec sh {ROOT / "adapters" / "fixture" / "run"}\n')
    run.chmod(0o755)
    commit_all(root, "a slow tenant-side adapter")


def git_dir(root: Path) -> Path:
    return Path(git(root, "rev-parse", "--absolute-git-dir").strip())


def report(root: Path) -> dict[str, Any]:
    run = run_attavo(root, "drive", "1", "--report", env=FAST)
    assert run.returncode == 0, run.stdout + run.stderr
    out: dict[str, Any] = json.loads(run.stdout)
    return out


def pushed(bare: Path, subject: str) -> bool:
    log = subprocess.run(["git", "-C", str(bare), "log", "--format=%s", "main"], capture_output=True, text=True).stdout
    return subject in log


def wait_for(pred: Any, seconds: float = 20) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.2)
    raise AssertionError("condition not met in time")


def built_tenant(tmp_path: Path, seconds: float = 3) -> tuple[Path, Path]:
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    add_drive_stall_bound(root)
    slow_adapter(root, seconds)
    bare = add_bare_remote(root, tmp_path)
    approve_item(root)
    return root, bare


def test_a_pass_returns_while_the_worker_runs_and_the_worker_outlives_it(tmp_path: Path) -> None:
    root, bare = built_tenant(tmp_path, seconds=3)
    started = time.monotonic()
    run = run_attavo(root, "drive", "1", env=FAST)
    elapsed = time.monotonic() - started
    assert run.returncode == 0, run.stdout + run.stderr
    assert elapsed < 2.5, f"the pass took {elapsed:.1f}s: it waited for the stage"
    assert "Build started" in run.stdout, run.stdout
    assert (records_dir(root) / "build-01.started").exists()
    assert remote_head(bare, "main") == head_of(root), "the marker was not pushed before the pass returned"
    assert record_files(root, "build") == [], "the record existed before the worker could have written it"
    wait_for(lambda: record_files(root, "build") != [])
    wait_for(lambda: remote_head(bare, "main") == head_of(root))
    assert latest_record(root, "build")["status"] == "pass"


def test_the_worker_touches_its_heartbeat_and_the_report_reads_running_by_it(tmp_path: Path) -> None:
    root, _ = built_tenant(tmp_path, seconds=3)
    assert run_attavo(root, "drive", "1", env=FAST).returncode == 0
    beat = git_dir(root) / "attavo" / "build-01.heartbeat"
    wait_for(beat.exists, 5)
    first = beat.stat().st_mtime
    time.sleep(0.6)
    assert beat.stat().st_mtime > first, "the heartbeat did not move"
    r = report(root)
    assert (r["state"], r["kind"]) == ("running", "waits"), r
    wait_for(lambda: record_files(root, "build") != [])


def test_a_stale_heartbeat_reads_died_though_the_pid_lives_and_a_fresh_one_reads_alive(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    add_drive_stall_bound(root, 600)
    approve_item(root)
    d = records_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    marker = d / "build-01.started"
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    marker.write_text(json.dumps({"at": now, "host": socket.gethostname(),
                                  "pid": os.getpid(), "sha": head_of(root), "product_version": "fixture"}))
    commit_all(root, "a marker whose pid is this test")
    beat = git_dir(root) / "attavo" / "build-01.heartbeat"
    beat.parent.mkdir(parents=True, exist_ok=True)
    beat.touch()
    os.utime(beat, (time.time() - 60, time.time() - 60))
    r = report(root)
    assert (r["state"], r["stop_class"]) == ("owes-recovery", "died"), r
    beat.touch()
    r = report(root)
    assert r["state"] == "running", r


def test_two_passes_at_once_start_one_worker_and_the_loser_says_so(tmp_path: Path) -> None:
    root, bare = built_tenant(tmp_path, seconds=3)
    commit_all(root, "the approval, so a second checkout carries it")
    git(root, "push", "-q", "origin", "HEAD:main")
    clone = tmp_path / "second"
    git(root.parent, "clone", "-q", "-b", git(root, "rev-parse", "--abbrev-ref", "HEAD").strip(), str(bare), str(clone))
    git(clone, "config", "user.email", "test@example.com")
    git(clone, "config", "user.name", "Attavo Test")
    git(clone, "remote", "set-head", "origin", "-a")
    env = dict(os.environ)
    env.update(FAST)
    procs = [subprocess.Popen([sys.executable, str(ROOT / "bin" / "attavo"), "drive", "1"], cwd=str(c),
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for c in (root, clone)]
    outs = [p.communicate(timeout=30)[0] for p in procs]
    started = [o for o in outs if "Build started" in o]
    held = [o for o in outs if "another pass holds" in o]
    assert len(started) == 1 and len(held) == 1, outs
    log = subprocess.run(["git", "-C", str(bare), "log", "--format=%s", "main"], capture_output=True, text=True).stdout
    assert log.count("build invocation 01 started") == 1, log
    wait_for(lambda: "build record for item 1" in subprocess.run(
        ["git", "-C", str(bare), "log", "--format=%s", "main"], capture_output=True, text=True).stdout)


def test_a_pass_recovers_a_dead_invocation_and_recover_is_gone(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    add_drive_stall_bound(root)
    approve_item(root)
    write_dead_marker(root, "build", 1)
    gone = run_attavo(root, "drive", "--recover", "1", env=FAST)
    assert gone.returncode == 2 and "gone" in gone.stderr, gone.stdout + gone.stderr
    run = run_attavo(root, "drive", "1", env=FAST)
    assert run.returncode == 0, run.stdout + run.stderr
    rec = latest_record(root, "build")
    assert rec["stop"]["class"] == "died", rec["stop"]


def test_the_review_takes_the_checks_the_pass_read_and_never_polls(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "build_valid")
    calls = tmp_path / "forge-calls.txt"
    forge = tmp_path / "forge.sh"
    forge.write_text(f'#!/bin/sh\necho "$1" >> {calls}\nprintf "ci success\\n"\n')
    forge.chmod(0o755)
    add_forge(root, f"sh {forge}")
    add_drive_stall_bound(root)
    slow_adapter(root, 0)
    bare = add_bare_remote(root, tmp_path)
    approve_item(root)
    assert run_attavo(root, "drive", "1", env=FAST).returncode == 0
    wait_for(lambda: pushed(bare, "build record for item 1"))
    calls.write_text("")
    pre_marker = head_of(root)
    run = run_attavo(root, "drive", "1", env=FAST)
    assert "Review started" in run.stdout, run.stdout + run.stderr
    wait_for(lambda: record_files(root, "verify") != [])
    rec = latest_record(root, "verify")
    assert rec["status"] == "pass", rec
    assert rec["forge_checks"]["sha"] == pre_marker, (rec["forge_checks"], pre_marker)
    read_by = [l for l in calls.read_text().split() if l]
    assert read_by == [pre_marker], f"the forge was read {len(read_by)} time(s) after the build: {read_by}"


def test_until_ready_is_a_loop_of_passes_that_returns_at_ready(tmp_path: Path) -> None:
    root, _ = built_tenant(tmp_path, seconds=0)
    run = run_attavo(root, "drive", "1", "--until-ready", env=FAST)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "Build started" in run.stdout and "Review started" in run.stdout, run.stdout
    assert run.stdout.strip().endswith("Ready to merge"), run.stdout
    assert report(root)["state"] == "ready-to-merge"


def test_a_stopped_stage_is_left_alone_by_a_pass_and_started_again_by_again(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    add_drive_stall_bound(root)
    approve_item(root)
    write_dead_marker(root, "build", 1)
    assert run_attavo(root, "drive", "1", env=FAST).returncode == 0
    assert latest_record(root, "build")["stop"]["class"] == "died"
    plain = run_attavo(root, "drive", "1", env=FAST)
    assert plain.returncode == 0 and "Needs you" in plain.stdout, plain.stdout
    assert not (records_dir(root) / "build-02.started").exists()
    again = run_attavo(root, "drive", "1", "--again", env=FAST)
    assert "Build started" in again.stdout, again.stdout + again.stderr
    assert (records_dir(root) / "build-02.started").exists()
    wait_for(lambda: len(record_files(root, "build")) == 2)


def test_all_runs_one_pass_in_every_working_copy_beside_the_checkout(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "build_valid", name="tenant")
    add_forge(root, GREEN_FORGE)
    add_drive_stall_bound(root)
    slow_adapter(root, 0)
    bare = add_bare_remote(root, tmp_path)
    for n in ("1",):
        copy = tmp_path / f"attavo-worktree-{n}"
        git(root, "worktree", "add", "-q", "-b", f"attavo/{n}-x", str(copy), "HEAD")
        git(copy, "config", "user.email", "test@example.com")
        git(copy, "config", "user.name", "Attavo Test")
        approve_item(copy, n)
    run = run_attavo(root, "drive", "--all", env=FAST)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "attavo-worktree-1: attavo drive: Build started" in run.stdout, run.stdout
    wait_for(lambda: record_files(tmp_path / "attavo-worktree-1", "build") != [])
