"""Issue #174: proof five judged a marker from another host exactly like
one from this host with no live pid -- "died by absence" -- so every
verify on the forge's own runners failed the doctor and the `test` job on
the very commit the drive then waited on, since a marker's pid is never
alive there. This file is red before the change: the age judgement (a
young marker from elsewhere holds, in `in_flight`; an old one still
fails) does not exist yet.

Acceptance 3's own regression -- a `returned` verify record must not be
read as a pass, or --until-ready loops writing a new marker every poll
bound (#158's own twenty minutes) -- moved on under the reconciler's R2
(issue #217): `_drive_step`, the synchronous single-process call this
file used to drive directly, is retired along with the forge poll it
carried, and a `returned` record can no longer arise from the drive path
at all -- a pass only ever starts a review once its own single read of
the forge (ruling B) has already found it green, and hands the worker
that same read (ATTAVO_FORGE_CHECKS) rather than letting it poll a forge
that could since have gone unconcluded. The live case --until-ready
giving up on a forge that never concludes -- is
test_drive_until_ready_waits_out_the_bound_on_a_forge_still_running in
tests/test_drive.py now.

Run: python3 -m pytest tests -q
"""
import json
import os
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from helpers import commit_all, init_repo

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"

OTHER_HOST = "elsewhere.example"
STALL_BOUND_S = 60


def iso_ago(seconds: float) -> str:
    stamp = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def run_doctor(root: Path) -> dict[str, Any]:
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


def test_proof_five_holds_a_young_marker_from_another_host_as_in_flight(tmp_path: Path) -> None:
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


def test_proof_five_fails_an_old_marker_from_another_host_past_the_bound(tmp_path: Path) -> None:
    """Acceptance 1 (other half). The same marker, older than the bound,
    is failed by absence exactly as a marker with no host at all would
    be -- naming it."""
    root = _proof_five_fixture(tmp_path, "old_elsewhere")
    _write_marker(root, OTHER_HOST, 4242, iso_ago(STALL_BOUND_S * 10))

    p = run_doctor(root)["no-invocation-lacks-record"]
    assert not p["holds"]
    assert "verify-01" in p["reason"] and OTHER_HOST in p["reason"]
    assert p["in_flight"] == []


def test_proof_five_still_fails_a_dead_invocation_on_this_host(tmp_path: Path) -> None:
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


def test_proof_five_still_holds_a_live_invocation_on_this_host_as_running(tmp_path: Path) -> None:
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
