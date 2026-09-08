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


def test_adapter_config_reaches_the_adapter(tmp_path):
    """Issue #24, acceptance 1. [executor.<adapter-name>] is an open table:
    attavo build serializes it to JSON, passes it to the adapter as
    ATTAVO_ADAPTER_CONFIG, and copies it verbatim into build-NN.json as
    adapter_config. Regression: a pass-through break here would silently
    reset every tenant's executor to defaults.
    """
    root = make_tenant(tmp_path, "build_valid")
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text() + "\n[executor.fixture]\nx = 1\n")
    commit_all(root, "configure the fixture adapter")
    approve_item(root)

    run = run_attavo(root, "build", "1")
    assert run.returncode == 0

    rec = latest_build(root)
    assert rec["adapter_config"] == {"x": 1}

    log = (root / "records" / "1" / "build-01.log").read_text()
    assert json.dumps({"x": 1}) in log


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


def test_build_without_a_checks_table(tmp_path):
    """Issue #30, acceptance 3. A tenant with no [checks] table is not
    refused -- unlike [tests], which build requires -- and the record
    carries checks: null."""
    root = make_tenant(tmp_path, "build_valid")
    approve_item(root)

    run = run_attavo(root, "build", "1")
    assert run.returncode == 0

    rec = latest_build(root)
    assert rec["status"] == "pass"
    assert rec["checks"] is None


def test_build_fails_when_the_tenant_checks_fail(tmp_path):
    """Issue #30, acceptance 1. The tenant's checks are part of done: a
    build whose criteria and tests all pass still fails, and never
    writes pass, if [checks].command exits non-zero. Regression:
    detects any later change that writes pass without the tenant's
    checks -- the specimen is attavo/code-factory@2c5708d, where a
    build passed while breaking three of the tenant's six required
    checks because the executor was never told they were part of done.
    """
    root = make_tenant(tmp_path, "build_valid")
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text() + '\n[checks]\ncommand = "exit 7"\n')
    commit_all(root, "configure checks")
    approve_item(root)

    run = run_attavo(root, "build", "1")
    assert run.returncode == 1

    rec = latest_build(root)
    assert rec["status"] == "failed"
    assert rec["checks"] == {"command": "exit 7", "exit": 7}
    # the criteria and the tests passed -- only the tenant's checks failed
    assert rec["criteria"] == [{"name": "checks_built.sh", "exit": 0}]
    assert rec["selected_tests"]["exit"] == 0


def test_checks_output_reaches_the_next_iteration(tmp_path):
    """Issue #30, acceptance 2. A non-zero checks exit is fed back to the
    next iteration -- "checks exit N" and the bounded tail of its
    output -- beside the failing criteria and the test output.
    Regression: the feedback file is the executor's only channel; a
    silent drop burns the iteration budget and lands back on the forge.
    """
    root = make_tenant(tmp_path, "build_valid")
    checks_script = root / "checks.sh"
    checks_script.write_text(
        "#!/bin/sh\n"
        "if [ -f built.txt ]; then\n"
        "  echo 'checks: built.txt present'\n"
        "  exit 0\n"
        "else\n"
        "  echo 'checks: built.txt missing'\n"
        "  exit 1\n"
        "fi\n")
    checks_script.chmod(0o755)
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text() + '\n[checks]\ncommand = "./checks.sh"\n')
    commit_all(root, "configure checks")
    approve_item(root)

    run = run_attavo(root, "build", "1", env={"ATTAVO_FIXTURE_REQUIRE_FEEDBACK": "1"})
    assert run.returncode == 0

    rec = latest_build(root)
    assert rec["status"] == "pass"
    assert rec["iterations"] == 2
    assert rec["checks"] == {"command": "./checks.sh", "exit": 0}

    log = (root / "records" / "1" / "build-01.log").read_text()
    assert "checks exit 1" in log
    assert "checks: built.txt missing" in log


def test_build_records_failed_on_adapter_is_error(tmp_path):
    """Issue #34, acceptance 2 (build half). The claude-code adapter's
    ATTAVO_ADAPTER_RESPONSE_FILE path, fed an is_error result, exits 1;
    attavo build records the attempt as failed, not a halt someone must
    rule on. Regression: an error dressed as a question, which costs a
    ruling act -- the specimen is attavo/code-factory's build-07.json and
    build-09.json, both a status: "placeholder"-question halt for what
    the raw result would have shown was something else.
    """
    root = make_tenant(tmp_path, "build_valid")
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text().replace('build = "fixture"', 'build = "claude-code"'))
    commit_all(root, "use the claude-code adapter")
    approve_item(root)

    response_file = tmp_path / "response.json"
    response_file.write_text(json.dumps({
        "is_error": True,
        "result": "API error: overloaded",
        "structured_output": {"status": "done", "question": None},
        "usage": {"input_tokens": 1, "output_tokens": 1},
        "modelUsage": {"claude-sonnet-5": {"inputTokens": 1, "outputTokens": 1}},
    }))

    run = run_attavo(root, "build", "1",
                     env={"ATTAVO_ADAPTER_RESPONSE_FILE": str(response_file)})
    assert run.returncode == 1

    rec = latest_build(root)
    assert rec["status"] == "failed"
    assert rec["adapter"] == "claude-code"
    # the adapter itself fails immediately on is_error -- not three
    # iterations exhausting the budget on an unrelated criterion failure
    assert rec["iterations"] == 1
