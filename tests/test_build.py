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


def test_build_logs_adapter_stderr_on_failure(tmp_path):
    """Issue #38, acceptance 2. Whenever the adapter itself exits
    non-zero, build appends its stderr to build-NN.log -- not just its
    stdout -- so a message the adapter writes about its own failure
    (as opposed to a wrapped tool's stdout, which the adapter is
    responsible for relaying itself) still reaches the transcript.
    """
    root = make_tenant(tmp_path, "build_valid")
    adapter_dir = root / "attavo" / "adapters" / "failing"
    adapter_dir.mkdir(parents=True)
    adapter_run = adapter_dir / "run"
    adapter_run.write_text(
        "#!/bin/sh\n"
        "echo 'stderr from the failing adapter' >&2\n"
        "exit 1\n")
    adapter_run.chmod(0o755)

    toml = root / "attavo.toml"
    toml.write_text(toml.read_text().replace('build = "fixture"', 'build = "failing"'))
    commit_all(root, "use a failing adapter")
    approve_item(root)

    run = run_attavo(root, "build", "1")
    assert run.returncode == 1

    rec = latest_build(root)
    assert rec["status"] == "failed"

    log = (root / "records" / "1" / "build-01.log").read_text()
    assert "stderr from the failing adapter" in log


def test_build_records_a_silently_failing_adapter_without_being_refused(tmp_path):
    """Issue #53, rejection test 3. An adapter that exits non-zero with
    nothing on stdout and nothing on stderr -- tenant zero's build-11, the
    claude-code adapter's account error swallowed before v0.3.6 kept
    stderr as transcript -- would otherwise write a failed record with an
    empty log and no refusal or halted to explain it. cmd_build's own
    guarantee puts the adapter's exit status into the log before the
    write, so the write is not refused.
    """
    root = make_tenant(tmp_path, "build_valid")
    adapter_dir = root / "attavo" / "adapters" / "silent"
    adapter_dir.mkdir(parents=True)
    adapter_run = adapter_dir / "run"
    adapter_run.write_text("#!/bin/sh\nexit 1\n")
    adapter_run.chmod(0o755)

    toml = root / "attavo.toml"
    toml.write_text(toml.read_text().replace('build = "fixture"', 'build = "silent"'))
    commit_all(root, "use a silently failing adapter")
    approve_item(root)

    run = run_attavo(root, "build", "1")
    assert run.returncode == 1

    rec = latest_build(root)
    assert rec["status"] == "failed"
    assert rec["refusal"] is None
    assert rec["halted"] is None

    log = (root / "records" / "1" / "build-01.log").read_text()
    assert log.strip() != ""
    assert "1" in log  # the exit status, so the record explains itself


def test_executor_commits_carry_the_executor_identity(tmp_path):
    """Issue #33, acceptance 1. attavo build exports the executor
    identity -- set by the product, never the tenant's git config -- as
    GIT_AUTHOR_NAME/EMAIL and GIT_COMMITTER_NAME/EMAIL; every commit the
    adapter makes between start_sha and its own sha carries it, and the
    record names it under executor_identity. Regression: the specimen is
    attavo/code-factory@e22fa85, an executor commit indistinguishable in
    git log from the operator's own amendments beside it, because no
    identity was exported for it to carry.
    """
    root = make_tenant(tmp_path, "build_valid")
    approve_item(root)

    run = run_attavo(root, "build", "1")
    assert run.returncode == 0

    rec = latest_build(root)
    assert rec["status"] == "pass"
    assert rec["executor_identity"] == {
        "name": "attavo executor (fixture)",
        "email": "executor+fixture@attavo.invalid",
    }

    log = subprocess.run(
        ["git", "-C", str(root), "log", "--format=%an/%ae",
         f"{rec['start_sha']}..{rec['sha']}"],
        capture_output=True, text=True, check=True).stdout
    authors = {l for l in log.splitlines() if l.strip()}
    assert authors == {"attavo executor (fixture)/executor+fixture@attavo.invalid"}


def write_failing_tests_script(root: Path) -> None:
    """A [tests].run that always fails, printing a pytest-style summary
    line -- the shape build's own log must carry so a reader never has to
    rerun the gates by hand (issue #89, tenant zero's #659 build-01)."""
    script = root / "run_tests.sh"
    script.write_text(
        "#!/bin/sh\n"
        "cat >/dev/null\n"
        "echo '1 failed, 2 passed in 0.05s'\n"
        "exit 1\n")
    script.chmod(0o755)
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text().replace('run = "true"', 'run = "./run_tests.sh"'))
    commit_all(root, "configure a [tests].run that always fails")


def test_build_log_carries_each_iterations_gate_output(tmp_path):
    """Issue #89, rejection test 1. A build whose selected tests fail on
    every iteration exhausts the budget as failed; the log carries each
    iteration's gate output -- criteria exits, the selected tests' own
    stdout, the tenant's checks' tail -- under a heading, so the pytest
    summary line is in the log rather than only in a feedback.txt this
    command already deleted by the time anyone could read it. Red before
    this fix: the log was the adapter's transcript alone, and the fixture
    adapter prints nothing about the tests it never sees.

    ATTAVO_FIXTURE_REQUIRE_FEEDBACK holds the fixture off committing on
    iteration 1 (no feedback yet), so gates run against an empty tree
    first; iteration 3 tries to re-commit the same built.txt with nothing
    changed and the fixture's own `git commit` aborts it before any gate
    of its own runs -- exactly what exhausts the budget as failed here,
    since the tests this build selects fail regardless of built.txt.
    """
    root = make_tenant(tmp_path, "build_valid")
    write_failing_tests_script(root)
    approve_item(root)

    run = run_attavo(root, "build", "1",
                     env={"ATTAVO_FIXTURE_REQUIRE_FEEDBACK": "1"})
    assert run.returncode == 1

    rec = latest_build(root)
    assert rec["status"] == "failed"
    assert rec["iterations"] == 3

    log = (root / "records" / "1" / "build-01.log").read_text()
    assert "--- attavo gates, iteration 1 ---" in log
    assert "--- attavo gates, iteration 2 ---" in log
    assert "1 failed, 2 passed in 0.05s" in log


def test_build_pass_record_carries_no_stop(tmp_path):
    """Issue #89, rejection test 2 (half). A pass record carries stop:
    null -- only a non-pass record needs a class and a cause."""
    root = make_tenant(tmp_path, "build_valid")
    approve_item(root)

    run = run_attavo(root, "build", "1")
    assert run.returncode == 0

    rec = latest_build(root)
    assert rec["status"] == "pass"
    assert rec["stop"] is None


def test_build_refused_on_a_claims_violation_carries_claims_boundary_stop(tmp_path):
    """Issue #89, rejection test 3: a refusal naming a claims path is
    claims-boundary/spec -- the write-outside-claims specimen."""
    root = make_tenant(tmp_path, "build_valid")
    approve_item(root)

    run = run_attavo(root, "build", "1", env={"ATTAVO_FIXTURE_WRITE": "unclaimed.txt"})
    assert run.returncode == 1

    rec = latest_build(root)
    assert rec["status"] == "refused"
    assert rec["stop"] == {"class": "claims-boundary", "provenance": "spec", "detail": None}


def test_build_refused_on_a_foreign_commit_is_unclassed(tmp_path):
    """A refusal that is not a claims path -- "commit ... not authored by
    the executor" -- carries no rule of its own in this lane; it falls to
    unclassed/product, with the log's last line as its detail."""
    root = make_tenant(tmp_path, "build_valid")
    approve_item(root)

    run = run_attavo(root, "build", "1", env={"ATTAVO_FIXTURE_FOREIGN_COMMIT": "1"})
    assert run.returncode == 1

    rec = latest_build(root)
    assert rec["status"] == "refused"
    assert rec["stop"]["class"] == "unclassed"
    assert rec["stop"]["provenance"] == "product"
    assert rec["stop"]["detail"]


def test_build_ceiling_error_line_carries_ceiling_stop(tmp_path):
    """Issue #89, rejection test 3: an adapter exit with a tool error line
    matching a declared ceiling is ceiling/product -- issue #86's own
    specimen, codex's "input_too_large" on a diff too large to judge."""
    root = make_tenant(tmp_path, "build_valid")
    approve_item(root)

    run = run_attavo(root, "build", "1", env={"ATTAVO_FIXTURE_CEILING_ERROR": "1"})
    assert run.returncode == 1

    rec = latest_build(root)
    assert rec["status"] == "failed"
    assert rec["stop"] == {"class": "ceiling", "provenance": "product", "detail": None}
    log = (root / "records" / "1" / "build-01.log").read_text()
    assert "input_too_large" in log


def test_build_halted_on_a_plain_report_is_unclassed(tmp_path):
    """A halt whose question, refusal, and log name none of the
    enumerated classes -- the fixture adapter's own halt, which (unlike
    claude-code and codex since issue #88) never names a class at all --
    falls to unclassed/product, with the log's last line as its detail;
    the fixture's own question line parses as the adapter's report rather
    than transcript, so that last line is the feedback marker the fixture
    prints ahead of it."""
    root = make_tenant(tmp_path, "build_valid")
    approve_item(root)

    run = run_attavo(root, "build", "1",
                     env={"ATTAVO_FIXTURE_QUESTION": "which overlay wins?"})
    assert run.returncode == 3

    rec = latest_build(root)
    assert rec["status"] == "halted"
    assert rec["stop"]["class"] == "unclassed"
    assert rec["stop"]["provenance"] == "product"
    log = (root / "records" / "1" / "build-01.log").read_text()
    assert rec["stop"]["detail"] == log.splitlines()[-1]


def _canned_response(question=None, status="done", subagent_stats=None,
                     input_tokens=5, output_tokens=9, cost_usd=0.01) -> dict:
    """A real `claude --output-format json` shape (mirrors
    tests/test_adapters.py's own _response()): is_error and
    structured_output both present, so the response reaches model
    derivation instead of failing before it (issue #34)."""
    response = {
        "is_error": False,
        "structured_output": {"status": status, "question": question},
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "total_cost_usd": cost_usd,
        "modelUsage": {"claude-sonnet-5": {"inputTokens": input_tokens,
                                          "outputTokens": output_tokens}},
    }
    if subagent_stats is not None:
        response["subagent_stats"] = subagent_stats
    return response


def use_claude_code_adapter(root: Path) -> None:
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text().replace('build = "fixture"', 'build = "claude-code"'))
    commit_all(root, "use the claude-code adapter")


def test_build_halt_naming_non_question_in_its_report_line_carries_that_stop_class(tmp_path):
    """Issue #89 union with issue #88 (merged to main as v0.16.0, tag
    dedebef). Two non-questions in a row halt adapters/claude-code/run
    with a report line carrying "class": "non-question"; build.schema.json
    is untouched by #88 on purpose -- the class lives only in the
    transcript and the report line, not a structured field cmd_build
    forwards -- so derive_stop must pick it up from the log.
    """
    root = make_tenant(tmp_path, "build_valid")
    use_claude_code_adapter(root)
    approve_item(root)

    report_1 = ("I've traced the spec's 5 criteria against the tree and "
               "dispatched an implementation agent to continue the work.")
    report_2 = ("Running the acceptance suite in the background to find "
               "the specific test failure that is blocking this item.")
    response_file = tmp_path / "responses.json"
    response_file.write_text(json.dumps([
        _canned_response(question=report_1, status="question"),
        _canned_response(question=report_2, status="question"),
    ]))

    run = run_attavo(root, "build", "1",
                     env={"ATTAVO_ADAPTER_RESPONSE_FILE": str(response_file)})
    assert run.returncode == 3

    rec = latest_build(root)
    assert rec["status"] == "halted"
    assert rec["stop"] == {"class": "non-question", "provenance": "executor", "detail": None}

    log = (root / "records" / "1" / "build-01.log").read_text()
    assert "class non-question" in log


def test_build_subagent_refusal_carries_outlived_invocation_stop_class(tmp_path):
    """Issue #89 union with issue #88. claude-code refuses (exit 1, not a
    halt) a result whose subagent_stats.spawned is not zero; the record
    ends up failed with the refusal text only in the log (build's own
    refusal field, set by cmd_build's own claims/identity checks, is
    untouched by this adapter-side path). The refusal's exact wording --
    "...spawned work that can outlive this invocation..." -- never spells
    the enum name itself, which is why CLASS_TEXT_MARKERS carries it as a
    synonym for outlived-invocation rather than relying on the bare
    substring match that already covers non-question.
    """
    root = make_tenant(tmp_path, "build_valid")
    use_claude_code_adapter(root)
    approve_item(root)

    response_file = tmp_path / "response.json"
    response_file.write_text(json.dumps(_canned_response(
        subagent_stats={"spawned": 3, "completed": 1,
                        "killed": {"parent": 0, "user": 0, "system": 2},
                        "started_in_background": 3})))

    run = run_attavo(root, "build", "1",
                     env={"ATTAVO_ADAPTER_RESPONSE_FILE": str(response_file)})
    assert run.returncode == 1

    rec = latest_build(root)
    assert rec["status"] == "failed"
    assert rec["stop"] == {"class": "outlived-invocation", "provenance": "executor",
                          "detail": None}

    log = (root / "records" / "1" / "build-01.log").read_text()
    assert "subagent_stats.spawned=3" in log


def test_build_refuses_a_commit_not_authored_by_the_executor(tmp_path):
    """Issue #33, acceptance 2. A commit whose author is not the executor
    identity build exported -- however it got there -- refuses the build
    the same class as a write outside the claims: the identity is only
    evidence if a foreign commit cannot pass through build unremarked.
    """
    root = make_tenant(tmp_path, "build_valid")
    approve_item(root)

    run = run_attavo(root, "build", "1",
                     env={"ATTAVO_FIXTURE_FOREIGN_COMMIT": "1"})
    assert run.returncode == 1

    rec = latest_build(root)
    assert rec["status"] == "refused"
    assert "not authored by the executor" in rec["refusal"]
