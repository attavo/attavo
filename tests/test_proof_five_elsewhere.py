"""Issue #174: proof five judged a marker from another host exactly like
one from this host with no live pid -- "died by absence" -- so every
verify on the forge's own runners failed the doctor and the `test` job on
the very commit the drive then waited on, since a marker's pid is never
alive there. This file is red before the change: the age judgement (a
young marker from elsewhere holds, in `in_flight`; an old one still
fails) does not exist yet, and `_drive_step` treats a `returned` verify
as if it had passed, so `--until-ready` writes a new marker and a new
record every poll bound instead of stopping.

Run: python3 -m pytest tests -q
"""
import importlib.machinery
import importlib.util
import json
import os
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from helpers import commit_all, init_repo

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"

OTHER_HOST = "elsewhere.example"
STALL_BOUND_S = 60


def attavo_module():
    """bin/attavo imported in-process and fresh each call, so a test can
    monkeypatch a module-level function (drive_state, poll_forge_checks,
    cmd_verify, ...) without leaking into any other test."""
    loader = importlib.machinery.SourceFileLoader("attavo_proof_five_under_test", str(ATTAVO))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def iso_ago(seconds: float) -> str:
    stamp = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def run_doctor(root: Path) -> dict:
    out = subprocess.run([sys.executable, str(ATTAVO), "doctor", str(root), "--json"],
                         capture_output=True, text=True, check=False)
    report = json.loads(out.stdout)
    assert out.returncode == (0 if report["done"] else 1)
    return {p["proof"]: p for p in report["proofs"]}


def _proof_five_fixture(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / "attavo.toml").write_text(
        '[paths]\nrecords = "records"\n\n'
        f'[drive]\nstall_after_s = {STALL_BOUND_S}\n')
    init_repo(root, "install")
    return root


def _write_marker(root: Path, host: str, pid: int, at: str, stage: str = "verify",
                  nn: int = 1, item: str = "1") -> None:
    records = root / "records" / item
    records.mkdir(parents=True, exist_ok=True)
    (records / f"{stage}-{nn:02d}.started").write_text(json.dumps({
        "at": at, "host": host, "pid": pid, "sha": "0" * 40,
        "product_version": "fixture"}))
    commit_all(root, f"invocation marker for item {item}")


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_proof_five_holds_a_young_marker_from_another_host_as_in_flight(tmp_path):
    """Acceptance 1 (half). A marker from a host that is not this one, and
    younger than [drive].stall_after_s, is an invocation still running
    elsewhere -- proof five holds and names it in in_flight, not among
    the failures."""
    root = _proof_five_fixture(tmp_path, "young_elsewhere")
    _write_marker(root, OTHER_HOST, 4242, iso_ago(STALL_BOUND_S / 4))

    p = run_doctor(root)["no-invocation-lacks-record"]
    assert p["holds"], p["reason"]
    assert len(p["in_flight"]) == 1
    assert "verify-01" in p["in_flight"][0] and OTHER_HOST in p["in_flight"][0]
    assert p["running_invocations"] == []


def test_proof_five_fails_an_old_marker_from_another_host_past_the_bound(tmp_path):
    """Acceptance 1 (other half). The same marker, older than the bound,
    is failed by absence exactly as a marker with no host at all would
    be -- naming it."""
    root = _proof_five_fixture(tmp_path, "old_elsewhere")
    _write_marker(root, OTHER_HOST, 4242, iso_ago(STALL_BOUND_S * 10))

    p = run_doctor(root)["no-invocation-lacks-record"]
    assert not p["holds"]
    assert "verify-01" in p["reason"] and OTHER_HOST in p["reason"]
    assert p["in_flight"] == []


def test_proof_five_still_fails_a_dead_invocation_on_this_host(tmp_path):
    """Acceptance 2 (half), a regression guard: a marker naming this
    host's own hostname and a dead pid fails exactly as before -- the
    age judgement is for another host only, and must not loosen the
    local reading into holding on a dead local invocation just because
    it is still young."""
    root = _proof_five_fixture(tmp_path, "dead_local")
    _write_marker(root, socket.gethostname(), _dead_pid(), iso_ago(1))

    p = run_doctor(root)["no-invocation-lacks-record"]
    assert not p["holds"]
    assert "verify-01" in p["reason"] and "never wrote its record" in p["reason"]
    assert p["in_flight"] == []


def test_proof_five_still_holds_a_live_invocation_on_this_host_as_running(tmp_path):
    """Acceptance 2 (other half), a regression guard: a marker naming
    this host and a live pid holds as running_invocations, not
    in_flight -- the two lists stay distinct by what invocation_alive
    itself can confirm."""
    root = _proof_five_fixture(tmp_path, "live_local")
    _write_marker(root, socket.gethostname(), os.getpid(), iso_ago(1))

    p = run_doctor(root)["no-invocation-lacks-record"]
    assert p["holds"], p["reason"]
    assert len(p["running_invocations"]) == 1
    assert "verify-01" in p["running_invocations"][0]
    assert p["in_flight"] == []


# --- acceptance 3: a returned verify ends --until-ready, not another marker ---

def _drive_fixture(tmp_path: Path):
    """A real (if minimal) repository and bare remote -- everything
    _drive_run_stage itself touches (write_invocation_marker,
    commit_record, push_record_commit) -- with a fake verify stage
    standing in for cmd_verify: the same contract (a "returned" record,
    written to disk, exit code 0 -- issue #174's own point, that exit
    code says nothing about whether the record is a pass) without any
    adapter, forge, or approval machinery those functions do not touch.
    drive_state and poll_forge_checks are faked too, so this exercises
    exactly _drive_step's own done judgement -- the line this issue
    changes -- and nothing upstream of it."""
    module = attavo_module()
    root = tmp_path / "fake_stage"
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    init_repo(root, "install")
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--quiet", "--bare", "-b", "main", str(bare)], check=True)
    module.git(root, "remote", "add", "origin", str(bare))
    module.git(root, "push", "origin", "HEAD:main")

    records_dir = root / "records" / "1"
    m = {"paths": {"records": "records"}, "forge": {"checks_command": "true"}}

    def fake_cmd_verify(argv):
        nn = module.next_attempt_number(records_dir, "verify")
        path = records_dir / f"verify-{nn:02d}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "status": "returned", "sha": "0" * 40,
            "stop": {"class": "forge-unconcluded", "provenance": "tenant", "detail": None}}))
        return 0  # cmd_verify's own contract: exit 0 on a returned record too.

    module.drive_state = lambda root_, m_, item_id: ("verify", None, records_dir)
    module.poll_forge_checks = lambda root_, checks_command, sha: ({"conclusions": []}, True)
    module.cmd_verify = fake_cmd_verify
    module.cmd_counsel = lambda argv: 1
    module.cmd_file_stops = lambda argv: 1
    return module, root, m, records_dir


def test_drive_step_stops_on_a_returned_verify(tmp_path):
    """Acceptance 3 (half). A single _drive_step call against a fake
    verify stage that writes a "returned" record reports done=True --
    before this change it read done from cmd_verify's own exit code
    (0, since returning is not an operational error) and reported
    done=False instead, as if the stage had just passed."""
    module, root, m, records_dir = _drive_fixture(tmp_path)

    rc, done = module._drive_step(root, m, "1")

    assert done, "a returned verify must be a stop, not a pass"
    assert len(list(records_dir.glob("verify-*.json"))) == 1
    assert len(list(records_dir.glob("verify-*.started"))) == 1


def test_drive_until_ready_stops_after_one_step_on_a_returned_verify(tmp_path):
    """Acceptance 3 (other half). The same fake stage, driven through
    --until-ready's own loop shape (repeat while not done, bounded here
    at 3 iterations so a regression fails fast instead of hanging):
    exactly one step runs, one marker and one record are written. On
    main at 5da8b82 this loops for all 3 iterations, writing verify-02
    and verify-03 markers and records -- #158's own twenty minutes, in
    miniature."""
    module, root, m, records_dir = _drive_fixture(tmp_path)

    steps = 0
    for _ in range(3):
        steps += 1
        rc, done = module._drive_step(root, m, "1")
        if done:
            break

    assert steps == 1, f"the loop took {steps} steps for one returned verify"
    assert len(list(records_dir.glob("verify-*.started"))) == 1
    assert len(list(records_dir.glob("verify-*.json"))) == 1
