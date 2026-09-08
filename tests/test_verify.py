"""attavo verify: refuse without a passing build record for the item, read
the forge's own check conclusions on the build record's commit before any
adapter is called, and write verify-NN.json. Issue #40, section 2.

Regression tests, written and run red first: on `main` at 719d39f there
was no `verify` subcommand, so every one of these failed with "unknown
subcommand".

Run: python3 -m pytest tests -q
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from helpers import commit_all, make_tenant

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"

# A stand-in forge read: ignores the sha argv build passes it and prints
# one fixed "<name> <conclusion>" line, green or red.
GREEN_FORGE = "printf 'ci success\\n'"
RED_FORGE = "printf 'ci failure\\n'"


def run_attavo(root: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
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


def latest_verify(root: Path, item: str = "1") -> dict:
    files = sorted((root / "records" / item).glob("verify-*.json"))
    assert files, f"no verify record for item {item}"
    return json.loads(files[-1].read_text())


def test_verify_refuses_without_a_build_record(tmp_path):
    """Acceptance 4a. No build record at all for the item: refused,
    naming the cause, no verify record written."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    approve_item(root)

    run = run_attavo(root, "verify", "1")
    assert run.returncode == 1
    assert "build" in run.stdout
    assert list((root / "records" / "1").glob("verify-*.json")) == []


def test_verify_refuses_when_latest_build_is_not_pass(tmp_path):
    """Acceptance 4b. A build record exists but its status is not pass:
    refused, naming the cause, no verify record written."""
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text() + '\n[checks]\ncommand = "exit 7"\n')
    commit_all(root, "configure a check the build can never pass")
    approve_item(root)

    build = run_attavo(root, "build", "1")
    assert build.returncode == 1  # issue #30: the tenant's checks are part of done

    run = run_attavo(root, "verify", "1")
    assert run.returncode == 1
    assert "not pass" in run.stdout
    assert list((root / "records" / "1").glob("verify-*.json")) == []


def test_verify_returns_when_the_forge_is_red(tmp_path):
    """Acceptance 5. The forge's own check conclusions on the build
    record's commit are read before any judge is called: a failing
    conclusion writes status "returned" and calls no adapter at all --
    not even a transcript log exists to show it ran.
    """
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, RED_FORGE)
    approve_item(root)
    assert run_attavo(root, "build", "1").returncode == 0

    run = run_attavo(root, "verify", "1")
    assert run.returncode == 0

    rec = latest_verify(root)
    assert rec["status"] == "returned"
    assert rec["adapter"] is None
    assert rec["model"] is None
    assert rec["findings"] is None
    assert rec["forge_checks"]["conclusions"] == [{"name": "ci", "conclusion": "failure"}]
    # no adapter was ever invoked -- no transcript log for this attempt
    assert not (root / "records" / "1" / "verify-01.log").exists()


def test_verify_merges_stage_config(tmp_path):
    """Acceptance 6. [executor.<adapter>.verify] shadows
    [executor.<adapter>] for verify alone; build sees the parent,
    verify sees the merged result.
    """
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text()
                    + '\n[executor.fixture]\nx = "parent"\n'
                    + '\n[executor.fixture.verify]\nx = "override"\n')
    commit_all(root, "configure per-stage adapter config")
    approve_item(root)

    assert run_attavo(root, "build", "1").returncode == 0
    build_rec = json.loads(sorted((root / "records" / "1").glob("build-*.json"))[-1].read_text())
    assert build_rec["adapter_config"] == {"x": "parent"}

    run = run_attavo(root, "verify", "1")
    assert run.returncode == 0
    verify_rec = latest_verify(root)
    assert verify_rec["status"] == "pass"
    assert verify_rec["adapter_config"] == {"x": "override"}
