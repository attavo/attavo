"""attavo verify reads the checks on its head with poll_forge_checks, the
same bound and interval the drive uses, instead of a single
read_forge_checks -- a marker commit's checks get room to conclude before
verify judges them. Issue #178.

Regression tests, written and run red first: on `main` at d0691ec verify
called read_forge_checks once, so a check still carrying an unconcluded
status on the first read returned "returned" at once instead of waiting.

Run: python3 -m pytest tests -q
"""
import pytest
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

from helpers import commit_all, git, make_tenant

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"

# The real defaults (ATTAVO_DRIVE_POLL_BOUND_S=600, _INTERVAL_S=20) are a
# real forge's clock, not a test's; poll_forge_checks re-reads both at
# call time from these environment variables (issue #84), so every case
# that drives it sets them to fractions of a second through
# monkeypatch.setenv and no case waits out a real bound (issue #181).
# The bound gives room for several reads at this interval before it expires.
POLL_BOUND_S = "0.5"
POLL_INTERVAL_S = "0.05"


def set_poll_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATTAVO_DRIVE_POLL_BOUND_S", POLL_BOUND_S)
    monkeypatch.setenv("ATTAVO_DRIVE_POLL_INTERVAL_S", POLL_INTERVAL_S)


def run_attavo(root: Path, *args: str, env: dict[str, Any] | None = None) -> subprocess.CompletedProcess[str]:
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run([sys.executable, str(ATTAVO), *args], cwd=str(root),
                          capture_output=True, text=True, check=False, env=full_env)


def add_forge(root: Path, checks_command: str) -> None:
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text() + f'\n[forge]\nchecks_command = "{checks_command}"\n')
    commit_all(root, "configure the forge read")


def approve_item(root: Path, item: str = "1") -> None:
    assert run_attavo(root, "spec-gate", item).returncode == 0
    assert run_attavo(root, "approve", item, "--by", "alice").returncode == 0


def latest_verify(root: Path, item: str = "1") -> dict[str, Any]:
    files = sorted((root / "records" / item).glob("verify-*.json"))
    assert files, f"no verify record for item {item}"
    return cast(dict[str, Any], json.loads(files[-1].read_text()))


def make_sequenced_forge(tmp_path: Path, name: str, sequence: list[str]) -> str:
    """A [forge].checks_command that answers with the next "<name>
    <conclusion>" line of `sequence` on each read, one read per
    poll_forge_checks attempt, holding on the last entry once the
    sequence is exhausted -- read_forge_checks' own line shape. The sha
    argv verify appends is ignored; the count lives in a file beside the
    script since each read is its own subprocess."""
    counter = tmp_path / f"{name}.count"
    counter.write_text("0")
    script = tmp_path / f"{name}.sh"
    cases = "\n".join(f'  {i}) echo "{line}" ;;' for i, line in enumerate(sequence, start=1))
    script.write_text(f"""#!/bin/sh
n=$(($(cat {counter}) + 1))
[ "$n" -gt {len(sequence)} ] && n={len(sequence)}
echo "$n" > {counter}
case "$n" in
{cases}
esac
""")
    script.chmod(0o755)
    return str(script)


def test_verify_calls_the_adapter_once_the_forge_concludes_green(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Acceptance row 1a. A checks_command that answers unconcluded twice
    and green on the third read: verify polls rather than returning on
    the first read, calls the adapter, and writes a pass-shaped record."""
    set_poll_clock(monkeypatch)
    root = make_tenant(tmp_path, "build_valid")
    forge = make_sequenced_forge(tmp_path, "twice_then_green",
                                 ["ci in_progress", "ci in_progress", "ci success"])
    add_forge(root, forge)
    approve_item(root)
    assert run_attavo(root, "build", "1").returncode == 0

    run = run_attavo(root, "verify", "1")
    assert run.returncode == 0, run.stdout + run.stderr

    rec = latest_verify(root)
    assert rec["status"] == "pass"
    assert rec["adapter"] == "fixture"
    assert rec["forge_checks"]["conclusions"] == [{"name": "ci", "conclusion": "success"}]


def test_verify_returns_with_the_conclusions_once_the_forge_concludes_red(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Acceptance row 1b. A checks_command that answers unconcluded twice
    and red (a concluded, failing conclusion) on the third read: verify
    still polls up to that read, then returns with those conclusions on
    the record -- no adapter call."""
    set_poll_clock(monkeypatch)
    root = make_tenant(tmp_path, "build_valid")
    forge = make_sequenced_forge(tmp_path, "twice_then_red",
                                 ["ci in_progress", "ci in_progress", "ci failure"])
    add_forge(root, forge)
    approve_item(root)
    assert run_attavo(root, "build", "1").returncode == 0

    run = run_attavo(root, "verify", "1")
    assert run.returncode == 0, run.stdout + run.stderr

    rec = latest_verify(root)
    assert rec["status"] == "returned"
    assert rec["adapter"] is None
    assert rec["forge_checks"]["conclusions"] == [{"name": "ci", "conclusion": "failure"}]


def test_verify_returns_forge_unconcluded_at_the_bound_when_the_forge_never_concludes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Acceptance row 1c. A checks_command that never concludes: verify
    polls until the bound, then returns "returned"/forge-unconcluded as
    it does today on a single unconcluded read -- no adapter call."""
    set_poll_clock(monkeypatch)
    root = make_tenant(tmp_path, "build_valid")
    forge = make_sequenced_forge(tmp_path, "never_concludes", ["ci in_progress"])
    add_forge(root, forge)
    approve_item(root)
    assert run_attavo(root, "build", "1").returncode == 0

    run = run_attavo(root, "verify", "1")
    assert run.returncode == 0, run.stdout + run.stderr

    rec = latest_verify(root)
    assert rec["status"] == "returned"
    assert rec["adapter"] is None
    assert rec["forge_checks"]["conclusions"] == [{"name": "ci", "conclusion": "in_progress"}]
    assert rec["stop"] == {"class": "forge-unconcluded", "provenance": "tenant", "detail": None,
                       "signature": None, "bound": None}
