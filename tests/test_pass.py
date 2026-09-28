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


def test_a_died_stage_retries_plainly_and_again_is_a_no_op_inside_the_budget(tmp_path: Path) -> None:
    """R3: died is one of the four plain retryable families -- the very
    next pass retries it on its own, with no operator and no --again
    needed; `--again` inside the budget is a no-op that says so, rather
    than starting a second attempt of its own."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    add_drive_stall_bound(root)
    approve_item(root)
    write_dead_marker(root, "build", 1)
    assert run_attavo(root, "drive", "1", env=FAST).returncode == 0
    assert latest_record(root, "build")["stop"]["class"] == "died"
    again = run_attavo(root, "drive", "1", "--again", "--by", "alice", env=FAST)
    assert again.returncode == 0 and "retries on its own" in again.stdout, again.stdout
    assert not (records_dir(root) / "build-02.started").exists()
    plain = run_attavo(root, "drive", "1", env=FAST)
    assert "Build started" in plain.stdout, plain.stdout + plain.stderr
    assert (records_dir(root) / "build-02.started").exists()
    wait_for(lambda: len(record_files(root, "build")) == 2)
    assert latest_record(root, "build")["retry_of"] == "build-01"


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


def test_the_report_reads_running_for_the_whole_of_counsel_and_stopped_after_the_push(tmp_path: Path) -> None:
    """The heartbeat is the holder's, so it runs until the holder exits: a
    stopped build's worker is asked for counsel, and for the length of
    that run the report reads running and a pass starts and recovers
    nothing."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    add_drive_stall_bound(root)
    manifest = root / "attavo.toml"
    manifest.write_text(manifest.read_text().replace(
        '[executor]\nbuild = "fixture"', '[executor]\nbuild = "fixture"\ncounsel = "fixture"'))
    commit_all(root, "counsel configured")
    bare = add_bare_remote(root, tmp_path)
    approve_item(root)
    release = tmp_path / "release-counsel"
    env = {**FAST, "ATTAVO_FIXTURE_QUESTION": "which overlay wins?",
           "ATTAVO_FIXTURE_COUNSEL_WAIT_FOR": str(release)}
    assert run_attavo(root, "drive", "1", env=env).returncode == 0
    beat = git_dir(root) / "attavo" / "build-01.heartbeat"
    wait_for(lambda: record_files(root, "build") != [])
    worker = int(beat.read_text())
    # The stage command has returned; the worker is in counsel. Let the
    # heartbeat's own interval pass three times over: a beat that stopped
    # with the command reads stale by now.
    time.sleep(1.0)
    os.kill(worker, 0)
    assert time.time() - beat.stat().st_mtime < 0.6, "the heartbeat is stale while the worker is alive"
    r = report(root)
    assert (r["state"], r["kind"]) == ("running", "waits"), r
    run = run_attavo(root, "drive", "1", env=FAST)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "started" not in run.stdout and "ecover" not in run.stdout, run.stdout
    assert not (records_dir(root) / "build-02.started").exists()
    os.kill(worker, 0)
    release.touch()
    wait_for(lambda: pushed(bare, "build record for item 1 (build-01)"))
    assert latest_record(root, "counsel")["record"] == "build-01"
    assert report(root)["state"] == "stopped"


def test_a_worker_whose_commit_is_refused_beats_until_it_returns_and_then_stops(
        tmp_path: Path, monkeypatch: Any) -> None:
    import threading

    from attavo import drive
    from attavo.doctor import heartbeat_path

    root = make_tenant(tmp_path, "build_valid")
    add_drive_stall_bound(root)
    approve_item(root)
    monkeypatch.chdir(root)
    monkeypatch.setenv("ATTAVO_HEARTBEAT_S", "0.1")
    seen: dict[str, float] = {}

    def stage(argv: list[str]) -> int:
        (records_dir(root) / "build-01.json").write_text("{}")
        return 0

    def refuse(*_: Any) -> None:
        time.sleep(0.5)
        seen["age"] = time.time() - heartbeat_path(root, "build", 1).stat().st_mtime
        raise RuntimeError("refused")

    monkeypatch.setattr(drive, "cmd_build", stage)
    monkeypatch.setattr(drive, "latest_record", lambda *_: {"status": "pass"})
    monkeypatch.setattr(drive, "commit_record", refuse)
    before = threading.active_count()
    assert drive._stage_body("1", "build", 1) == 1
    assert seen["age"] < 0.3, f"the heartbeat had stopped {seen['age']:.2f}s before the refused commit"
    assert threading.active_count() == before, "the heartbeat thread outlived the worker's body"
