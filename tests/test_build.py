"""attavo build: refuse unless approved, invoke the adapter the manifest
names, check the claims boundary, run the criteria and the tenant's
selected tests, iterate on failure up to a budget, and write build-NN.json.

Rejection tests, written and run red before the capability existed: on
`main` at 600abb7 there was no `build` subcommand, so every one of these
failed with "unknown subcommand".

Run: python3 -m pytest tests -q
"""
import json
import subprocess
import sys
from pathlib import Path

from helpers import commit_all, make_tenant

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"


def run_attavo(root: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    import os
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run([sys.executable, str(ATTAVO), *args], cwd=str(root),
                          capture_output=True, text=True, check=False, env=full_env)


def builds(root: Path, item: str = "1") -> list[Path]:
    return sorted((root / "records" / item).glob("build-*.json"))


def latest_build(root: Path, item: str = "1") -> dict:
    files = builds(root, item)
    assert files, f"no build record for item {item}"
    return json.loads(files[-1].read_text())


def approve_item(root: Path, item: str = "1") -> None:
    assert run_attavo(root, "spec-gate", item).returncode == 0
    assert run_attavo(root, "approve", item, "--by", "alice").returncode == 0


def amend_problem(root: Path) -> None:
    spec_md = root / "specs" / "1" / "spec.md"
    spec_md.write_text(spec_md.read_text().replace(
        "does not emit", "used to not emit"))
    commit_all(root, "amend problem prose")


def test_build_refuses_without_valid_approval(tmp_path):
    root = make_tenant(tmp_path, "valid")

    # no approval at all
    absent = run_attavo(root, "build", "1")
    assert absent.returncode == 1
    assert "refused" in absent.stdout
    assert builds(root) == []

    # approve, then move the spec above the line without a fresh gate run
    approve_item(root)
    amend_problem(root)

    expired = run_attavo(root, "build", "1")
    assert expired.returncode == 1
    assert "refused" in expired.stdout
    assert builds(root) == []


def test_build_refuses_manifest_without_tests(tmp_path):
    root = make_tenant(tmp_path, "gate_rejection")
    run = run_attavo(root, "build", "1")
    assert run.returncode == 1
    assert "tests" in run.stdout
    assert not (root / "records").is_dir()


def test_build_refuses_write_outside_claims(tmp_path):
    root = make_tenant(tmp_path, "build_valid")
    approve_item(root)

    run = run_attavo(root, "build", "1", env={"ATTAVO_FIXTURE_WRITE": "unclaimed.txt"})
    assert run.returncode == 1

    rec = latest_build(root)
    assert rec["status"] == "refused"
    assert "unclaimed.txt" in rec["refusal"]
    # the adapter's commit is left on the branch, not reverted
    assert (root / "unclaimed.txt").is_file()


def test_fixture_adapter_traverses_build(tmp_path):
    root = make_tenant(tmp_path, "build_valid")
    approve_item(root)

    run = run_attavo(root, "build", "1")
    assert run.returncode == 0

    rec = latest_build(root)
    assert rec["status"] == "pass"
    assert rec["adapter"] == "fixture"
    assert rec["iterations"] == 1
    assert rec["criteria"] == [{"name": "checks_built.sh", "exit": 0}]
    assert rec["model"] == "fixture"
    assert rec["selected_tests"]["exit"] == 0

    # the select command in this fixture is `cat`: it echoes back exactly
    # the touched paths, so the recorded count matches that line-for-line.
    touched = subprocess.run(["git", "-C", str(root), "diff", "--name-only",
                             rec["start_sha"], rec["sha"]],
                             capture_output=True, text=True, check=True).stdout
    assert rec["selected_tests"]["count"] == len([l for l in touched.splitlines() if l.strip()])


def test_build_halts_on_question(tmp_path):
    root = make_tenant(tmp_path, "build_valid")
    approve_item(root)

    run = run_attavo(root, "build", "1",
                     env={"ATTAVO_FIXTURE_QUESTION": "which overlay wins?"})
    assert run.returncode == 3

    rec = latest_build(root)
    assert rec["status"] == "halted"
    assert rec["halted"] == {"question": "which overlay wins?"}
    assert rec["iterations"] == 1


def test_build_iterates_with_feedback(tmp_path):
    root = make_tenant(tmp_path, "build_valid")
    approve_item(root)

    run = run_attavo(root, "build", "1",
                     env={"ATTAVO_FIXTURE_REQUIRE_FEEDBACK": "1"})
    assert run.returncode == 0

    rec = latest_build(root)
    assert rec["status"] == "pass"
    assert rec["iterations"] == 2
