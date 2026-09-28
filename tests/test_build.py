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
from typing import Any, cast

import jsonschema

from helpers import commit_all, make_tenant

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"


def run_attavo(root: Path, *args: str, env: dict[str, Any] | None = None) -> subprocess.CompletedProcess[str]:
    import os
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run([sys.executable, str(ATTAVO), *args], cwd=str(root),
                          capture_output=True, text=True, check=False, env=full_env)


def builds(root: Path, item: str = "1") -> list[Path]:
    return sorted((root / "records" / item).glob("build-*.json"))


def latest_build(root: Path, item: str = "1") -> dict[str, Any]:
    files = builds(root, item)
    assert files, f"no build record for item {item}"
    return cast(dict[str, Any], json.loads(files[-1].read_text()))


def approve_item(root: Path, item: str = "1") -> None:
    assert run_attavo(root, "spec-gate", item).returncode == 0
    assert run_attavo(root, "approve", item, "--by", "alice").returncode == 0


def events_dir(root: Path) -> Path:
    """<git-dir>/attavo -- where the product writes a stage's events
    file (issue #140), outside the working tree the executor commits
    from."""
    git_dir = subprocess.run(["git", "-C", str(root), "rev-parse", "--absolute-git-dir"],
                             capture_output=True, text=True, check=True).stdout.strip()
    return Path(git_dir) / "attavo"


def amend_problem(root: Path) -> None:
    spec_md = root / "specs" / "1" / "spec.md"
    spec_md.write_text(spec_md.read_text().replace(
        "does not emit", "used to not emit"))
    commit_all(root, "amend problem prose")


def test_build_refuses_without_valid_approval(tmp_path: Path) -> None:
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


def test_build_refuses_manifest_without_tests(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "gate_rejection")
    run = run_attavo(root, "build", "1")
    assert run.returncode == 1
    assert "tests" in run.stdout
    assert not (root / "records").is_dir()


def test_build_refuses_write_outside_claims(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "build_valid")
    approve_item(root)

    run = run_attavo(root, "build", "1", env={"ATTAVO_FIXTURE_WRITE": "unclaimed.txt"})
    assert run.returncode == 1

    rec = latest_build(root)
    assert rec["status"] == "refused"
    assert "unclaimed.txt" in rec["refusal"]
    # the adapter's commit is left on the branch, not reverted
    assert (root / "unclaimed.txt").is_file()


def test_build_events_file_lives_outside_the_working_tree(tmp_path: Path) -> None:
    """Issue #140, acceptance row 1. The events file sits under the
    checkout's own git directory, not the working tree the executor
    commits from: `git status` is clean after a stage that wrote
    events, and the file itself exists at the git-dir path. Tenant
    zero's own specimen, #688: the executor's commit carried
    attest/records/688/build-01.events.jsonl because the product wrote
    it inside the tree, and the executor's own commit staged it.
    """
    root = make_tenant(tmp_path, "build_valid")
    approve_item(root)

    run = run_attavo(root, "build", "1")
    assert run.returncode == 0

    # no events file anywhere under the working tree (.git's own
    # contents aside -- that is exactly where it now lives) -- not
    # untracked (which alone wouldn't catch one the executor already
    # committed), not present at all.
    in_tree = [p for p in root.rglob("*.events.jsonl") if ".git" not in p.parts]
    assert in_tree == []

    events_path = events_dir(root) / "build-01.events.jsonl"
    assert events_path.is_file()
    assert json.loads(events_path.read_text().splitlines()[0])["kind"] == "iteration"


def test_build_refuses_an_executor_commit_under_records(tmp_path: Path) -> None:
    """Issue #140, acceptance row 2. paths.records is the product's own
    directory, not a claim any spec can make -- an executor commit
    touching it is refused by name, class claims-boundary, provenance
    executor (not "spec": the boundary crossed is the product's own,
    not the definition of done the spec's claims describe). Tenant
    zero's own specimen, #688, would be refused this way, naming
    attest/records/688/build-01.events.jsonl.
    """
    root = make_tenant(tmp_path, "build_valid")
    approve_item(root)

    run = run_attavo(root, "build", "1", env={"ATTAVO_FIXTURE_WRITE": "records/1/sneaky.txt"})
    assert run.returncode == 1

    rec = latest_build(root)
    assert rec["status"] == "refused"
    assert rec["refusal"] == "records/1/sneaky.txt"
    assert rec["stop"] == {"class": "claims-boundary", "provenance": "executor",
                       "detail": "records/1/sneaky.txt", "signature": "records//sneaky.txt",
                       "bound": None}


def test_build_refuses_an_executor_commit_under_specs(tmp_path: Path) -> None:
    """Issue #140, acceptance row 3. The same guard for paths.specs --
    the definition of done, not a claim the build itself may touch."""
    root = make_tenant(tmp_path, "build_valid")
    approve_item(root)

    run = run_attavo(root, "build", "1", env={"ATTAVO_FIXTURE_WRITE": "specs/1/sneaky.txt"})
    assert run.returncode == 1

    rec = latest_build(root)
    assert rec["status"] == "refused"
    assert rec["refusal"] == "specs/1/sneaky.txt"
    assert rec["stop"] == {"class": "claims-boundary", "provenance": "executor",
                       "detail": "specs/1/sneaky.txt", "signature": "specs//sneaky.txt",
                       "bound": None}


def test_fixture_adapter_traverses_build(tmp_path: Path) -> None:
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


def test_build_writes_an_events_file_and_leaves_the_record_unchanged(tmp_path: Path) -> None:
    """Issue #26, acceptance row 1. The product now always sets
    ATTAVO_EVENTS before invoking the adapter: build-01.events.jsonl
    carries the product's own iteration and gate lines (one of each --
    this fixture's single criterion passes on the first iteration) plus
    the fixture adapter's own three, each a JSON object with exactly
    at/kind/text -- and the record is exactly the one
    test_fixture_adapter_traverses_build already asserts: the event
    stream changes nothing about it.
    """
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

    events_path = events_dir(root) / "build-01.events.jsonl"
    assert events_path.is_file()
    lines = [json.loads(l) for l in events_path.read_text().splitlines() if l.strip()]
    assert all(set(e) == {"at", "kind", "text"} for e in lines)
    kinds = [e["kind"] for e in lines]
    assert kinds.count("iteration") == 1
    assert kinds.count("gate") == 1
    assert kinds.count("message") == 1
    assert kinds.count("tool_call") == 1
    assert kinds.count("tool_result") == 1
    gate_line = next(e for e in lines if e["kind"] == "gate")
    assert "checks_built.sh" in gate_line["text"]


def test_adapter_config_reaches_the_adapter(tmp_path: Path) -> None:
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


def test_build_halts_on_question(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "build_valid")
    approve_item(root)

    run = run_attavo(root, "build", "1",
                     env={"ATTAVO_FIXTURE_QUESTION": "which overlay wins?"})
    assert run.returncode == 3

    rec = latest_build(root)
    assert rec["status"] == "halted"
    assert rec["halted"] == {"question": "which overlay wins?"}
    assert rec["iterations"] == 1


def test_build_iterates_with_feedback(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "build_valid")
    approve_item(root)

    run = run_attavo(root, "build", "1",
                     env={"ATTAVO_FIXTURE_REQUIRE_FEEDBACK": "1"})
    assert run.returncode == 0

    rec = latest_build(root)
    assert rec["status"] == "pass"
    assert rec["iterations"] == 2


def test_build_without_a_checks_table(tmp_path: Path) -> None:
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


def test_build_fails_when_the_tenant_checks_fail(tmp_path: Path) -> None:
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


def test_checks_output_reaches_the_next_iteration(tmp_path: Path) -> None:
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


def test_build_records_failed_on_adapter_is_error(tmp_path: Path) -> None:
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


def test_build_logs_adapter_stderr_on_failure(tmp_path: Path) -> None:
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


def test_build_records_a_silently_failing_adapter_without_being_refused(tmp_path: Path) -> None:
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


def test_executor_commits_carry_the_executor_identity(tmp_path: Path) -> None:
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


def test_build_log_carries_each_iterations_gate_output(tmp_path: Path) -> None:
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


def test_build_pass_record_carries_no_stop(tmp_path: Path) -> None:
    """Issue #89, rejection test 2 (half). A pass record carries stop:
    null -- only a non-pass record needs a class and a cause."""
    root = make_tenant(tmp_path, "build_valid")
    approve_item(root)

    run = run_attavo(root, "build", "1")
    assert run.returncode == 0

    rec = latest_build(root)
    assert rec["status"] == "pass"
    assert rec["stop"] is None


def test_build_refused_on_a_claims_violation_carries_claims_boundary_stop(tmp_path: Path) -> None:
    """Issue #89, rejection test 3: a refusal naming a claims path is
    claims-boundary/spec -- the write-outside-claims specimen."""
    root = make_tenant(tmp_path, "build_valid")
    approve_item(root)

    run = run_attavo(root, "build", "1", env={"ATTAVO_FIXTURE_WRITE": "unclaimed.txt"})
    assert run.returncode == 1

    rec = latest_build(root)
    assert rec["status"] == "refused"
    assert rec["stop"] == {"class": "claims-boundary", "provenance": "spec",
                       "detail": "unclaimed.txt", "signature": "unclaimed.txt", "bound": None}


def test_build_refused_on_a_foreign_commit_is_foreign_commit_tenant(tmp_path: Path) -> None:
    """A refusal for a commit the executor did not author is class
    foreign-commit, provenance tenant, the refusal string (which names the
    commit) its detail (issue #241, #229 build-01)."""
    root = make_tenant(tmp_path, "build_valid")
    approve_item(root)

    run = run_attavo(root, "build", "1", env={"ATTAVO_FIXTURE_FOREIGN_COMMIT": "1"})
    assert run.returncode == 1

    rec = latest_build(root)
    assert rec["status"] == "refused"
    assert rec["stop"]["class"] == "foreign-commit"
    assert rec["stop"]["provenance"] == "tenant"
    assert rec["stop"]["detail"] == rec["refusal"]
    assert "not authored by the executor" in rec["stop"]["detail"]


def test_build_a_tool_error_line_naming_a_ceiling_is_not_a_ceiling(tmp_path: Path) -> None:
    """Issue #241: the log's words decide nothing. A tool's own
    "input_too_large" error line, with no report line beside it, is
    unclassed/product, the line itself the detail; a ceiling is what an
    adapter reports (below), never what the product reads."""
    root = make_tenant(tmp_path, "build_valid")
    approve_item(root)

    run = run_attavo(root, "build", "1", env={"ATTAVO_FIXTURE_CEILING_ERROR": "1"})
    assert run.returncode == 1

    rec = latest_build(root)
    assert rec["status"] == "failed"
    assert rec["stop"]["class"] == "unclassed"
    assert rec["stop"]["provenance"] == "product"
    assert rec["stop"]["detail"] == "[adapter exited 1]"
    log = (root / "records" / "1" / "build-01.log").read_text()
    assert "input_too_large" in log


def test_build_adapter_ceiling_refusal_on_a_declared_limit_carries_ceiling_stop(tmp_path: Path) -> None:
    """Issue #93 union with issue #89. adapters/claude-code/run now
    refuses pre-emptively -- before claude is ever invoked -- when its
    instruction exceeds its declared or fixed input limit, naming both
    numbers and the word "ceiling" (ATTAVO_ADAPTER_INPUT_LIMIT_CHARS
    overrides the bound here so the fixture's own small instruction is
    enough to exceed it, standing in for a real oversized one). Unlike
    issue #86's specimen -- a raw tool error line ("input_too_large")
    caught by CEILING_MARKERS -- this refusal is the adapter's own text,
    which now arrives as a report line carrying "class": "ceiling"
    (issue #241) and classes ceiling/executor: the adapter reported what
    it refused, and the product read no words to find out.
    """
    root = make_tenant(tmp_path, "build_valid")
    use_claude_code_adapter(root)
    approve_item(root)

    run = run_attavo(root, "build", "1",
                     env={"ATTAVO_ADAPTER_INPUT_LIMIT_CHARS": "10"})
    assert run.returncode == 1

    rec = latest_build(root)
    assert rec["status"] == "failed"
    assert rec["stop"] == {"class": "ceiling", "provenance": "executor", "detail": None,
                       "signature": None, "bound": None}
    log = (root / "records" / "1" / "build-01.log").read_text()
    assert "ceiling" in log
    assert "10" in log


def test_build_halted_on_a_genuine_question_carries_question_stop(tmp_path: Path) -> None:
    """Issue #100's own followups. A halt carrying a real question -- the
    fixture adapter's own halt, which (unlike claude-code and codex since
    issue #88) never names a class at all -- is question/spec, with the
    question's own first line as detail: exactly the specimen issue #100
    itself retires (tenant zero #663 build-01), previously classed
    unclassed/product and filed with the wrong provenance -- a question
    is about the definition of done, not the product's own mechanism."""
    root = make_tenant(tmp_path, "build_valid")
    approve_item(root)

    run = run_attavo(root, "build", "1",
                     env={"ATTAVO_FIXTURE_QUESTION": "which overlay wins?"})
    assert run.returncode == 3

    rec = latest_build(root)
    assert rec["status"] == "halted"
    assert rec["stop"] == {"class": "question", "provenance": "spec",
                           "detail": "which overlay wins?", "signature": "which overlay wins?",
                           "bound": None}


def test_build_halted_question_detail_is_only_the_first_line(tmp_path: Path) -> None:
    """A real question's own account of the conflict is often long (the
    tenant zero specimen runs to several paragraphs); stop.detail holds
    only its first line -- the whole question still reaches a person
    through halted.question, and, once counseled, proposal.question."""
    root = make_tenant(tmp_path, "build_valid")
    use_claude_code_adapter(root)
    approve_item(root)

    question = ("Which overlay wins here?\n\nContext: the base tree and "
               "the tenant's own overlay disagree on three files, and the "
               "spec does not say which should be preferred.")
    response_file = tmp_path / "response.json"
    response_file.write_text(json.dumps(_canned_response(question=question, status="question")))

    run = run_attavo(root, "build", "1",
                     env={"ATTAVO_ADAPTER_RESPONSE_FILE": str(response_file)})
    assert run.returncode == 3

    rec = latest_build(root)
    assert rec["status"] == "halted"
    assert rec["stop"] == {"class": "question", "provenance": "spec",
                           "detail": "Which overlay wins here?",
                           "signature": "Which overlay wins here?", "bound": None}


def attavo_module() -> Any:
    """attavo.stops imported in-process, for a direct unit test of
    derive_stop -- getting a tool's own event JSON or error text onto the
    literal last line of a real build's transcript is not something any
    adapter hook here controls; the cap is a fact about derive_stop
    itself."""
    import importlib
    return importlib.import_module("attavo.stops")


def test_unclassed_detail_is_capped_at_200_characters() -> None:
    """Issue #100's own followups. An unclassed detail -- the log's last
    line -- is capped at 200 characters: some tool's own event JSON or
    error text on that line can run to thousands of characters, and the
    full picture already lives in the log itself (and, for a stop
    file-stops files, its own log_tail)."""
    attavo = attavo_module()
    long_line = "x" * 500
    stop = attavo.derive_stop("failed", log=f"first line\n{long_line}")
    assert stop == {"class": "unclassed", "provenance": "product", "detail": long_line[:200]}
    assert len(stop["detail"]) == 200


def test_unclassed_detail_under_the_cap_is_unchanged() -> None:
    """The cap truncates; it does not pad or otherwise alter a detail
    already under it."""
    attavo = attavo_module()
    stop = attavo.derive_stop("failed", log="first line\nshort last line")
    assert stop["detail"] == "short last line"


def _canned_response(question: str | None = None, status: str = "done",
                     subagent_stats: Any = None, input_tokens: int = 5,
                     output_tokens: int = 9, cost_usd: float = 0.01) -> dict[str, Any]:
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


def test_build_halt_naming_non_question_in_its_report_line_carries_that_stop_class(tmp_path: Path) -> None:
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
    assert rec["stop"] == {"class": "non-question", "provenance": "executor", "detail": None,
                       "signature": None, "bound": None}

    log = (root / "records" / "1" / "build-01.log").read_text()
    assert "class non-question" in log


def test_build_subagent_refusal_carries_outlived_invocation_stop_class(tmp_path: Path) -> None:
    """Issue #89 union with issue #88, updated by issue #94. claude-code
    refuses (exit 1, not a halt) a result whose subagent_stats.spawned is
    not zero; the record ends up failed with the refusal text only in the
    log (build's own refusal field, set by cmd_build's own claims/identity
    checks, is untouched by this adapter-side path). This refusal path
    prints no report line at all, so there is no payload "class" key for
    derive_stop to read (issue #94 retires the text scan that used to
    stand in for one): the stop falls to unclassed/product, with the
    refusal's own wording -- "...spawned work that can outlive this
    invocation..." -- as its detail, rather than a fabricated
    outlived-invocation class.
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
    assert rec["stop"]["class"] == "unclassed"
    assert rec["stop"]["provenance"] == "product"
    assert "subagent_stats.spawned=3" in rec["stop"]["detail"]

    log = (root / "records" / "1" / "build-01.log").read_text()
    assert "subagent_stats.spawned=3" in log


def test_build_halt_report_line_class_field_sets_stop_class(tmp_path: Path) -> None:
    """Issue #94, rejection test 4: stop.class is read from the adapter's
    report line via payload.get("class") itself -- not a text scan over
    refusal/halted/log, which CLASS_TEXT_MARKERS and its bare substring
    match used to provide before this issue retired both. Proven with the
    fixture adapter's report line carrying "class": "non-question"
    alongside a question whose own wording never once says
    "non-question" -- a text scan over refusal/halted/log would find
    nothing here, so this fails today (the field itself is unread) and
    only holds once derive_stop reads payload.get("class") directly."""
    root = make_tenant(tmp_path, "build_valid")
    approve_item(root)

    run = run_attavo(root, "build", "1",
                     env={"ATTAVO_FIXTURE_QUESTION": "which overlay wins?",
                          "ATTAVO_FIXTURE_REPORT_CLASS": "non-question"})
    assert run.returncode == 3

    rec = latest_build(root)
    assert rec["status"] == "halted"
    assert rec["stop"] == {"class": "non-question", "provenance": "executor", "detail": None,
                       "signature": None, "bound": None}


def test_build_refuses_a_commit_not_authored_by_the_executor(tmp_path: Path) -> None:
    """Issue #33, acceptance 2. A commit whose author is not the executor
    identity build exported -- however it got there -- refuses the build:
    the identity is only evidence if a foreign commit cannot pass through
    build unremarked.
    """
    root = make_tenant(tmp_path, "build_valid")
    approve_item(root)

    run = run_attavo(root, "build", "1",
                     env={"ATTAVO_FIXTURE_FOREIGN_COMMIT": "1"})
    assert run.returncode == 1

    rec = latest_build(root)
    assert rec["status"] == "refused"
    assert "not authored by the executor" in rec["refusal"]


def test_build_refuses_when_checks_command_exits_2_on_the_approved_tree(tmp_path: Path) -> None:
    """Issue #115, acceptance rows 1 and 3. [checks].command exiting 2 on
    the approved tree -- the command itself cannot run, not a failing
    check -- refuses the build before the adapter is ever invoked: class
    environment, provenance tenant, nothing spent. Regression: item
    #106's build-01 spent three iterations, 940s, and $1.98 discovering
    a missing jsonschema import only after the fact, filed
    unclassed/product; a later change that moves this preflight after
    the invocation, or drops it, spends a build on a broken environment
    again.
    """
    root = make_tenant(tmp_path, "build_valid")
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text()
                    + '\n[checks]\ncommand = "echo no jsonschema; exit 2"\n')
    commit_all(root, "configure a broken checks command")
    approve_item(root)

    run = run_attavo(root, "build", "1")
    assert run.returncode == 1

    rec = latest_build(root)
    assert rec["status"] == "refused"
    assert rec["stop"] == {"class": "environment", "provenance": "tenant",
                           "detail": "no jsonschema", "signature": "no jsonschema",
                           "bound": None}
    assert rec["checks"] == {"command": "echo no jsonschema; exit 2", "exit": 2}
    # the adapter never ran: no commit, no model, the stub file untouched
    assert rec["sha"] == rec["start_sha"]
    assert rec["model"] is None
    assert not (root / "built.txt").is_file()

    schema = json.loads((ROOT / "schema" / "records" / "build.schema.json").read_text())
    jsonschema.validate(rec, schema)


def test_build_proceeds_when_checks_command_exits_1_on_the_approved_tree(tmp_path: Path) -> None:
    """Issue #115, acceptance row 2. A [checks].command exiting 1 --
    failing checks, the expected state before a build -- does not refuse:
    the preflight only catches exit 2 (the command cannot run at all).
    Regression: a preflight that refuses on failing checks would refuse
    every build, since failing criteria are the expected state before
    one runs.
    """
    root = make_tenant(tmp_path, "build_valid")
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text() + '\n[checks]\ncommand = "exit 1"\n')
    commit_all(root, "configure a failing checks command")
    approve_item(root)

    run = run_attavo(root, "build", "1")
    assert run.returncode == 1

    rec = latest_build(root)
    # the adapter ran -- unlike the exit-2 case above, which never invokes it
    assert rec["model"] == "fixture"
    assert rec["status"] == "failed"
    assert rec["checks"] == {"command": "exit 1", "exit": 1}
    assert rec["stop"]["class"] == "gate"
    assert rec["stop"]["provenance"] == "executor"
    assert rec["stop"]["detail"] == "checks: exit 1"


def test_build_a_red_selected_tests_run_classes_gate_executor_with_exit_and_count(tmp_path: Path) -> None:
    """Issue #241, #229 build-04 and #237 build-01: a build failed at the
    gate on the selected tests' exit is gate/executor, naming the exit and
    how many tests were selected -- read from the record's own
    selected_tests, whatever the transcript says."""
    root = make_tenant(tmp_path, "build_valid")
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text().replace('run = "true"', 'run = "false"'))
    commit_all(root, "a selected-tests run that is red")
    approve_item(root)

    run = run_attavo(root, "build", "1")
    assert run.returncode == 1

    rec = latest_build(root)
    assert rec["status"] == "failed"
    assert rec["selected_tests"]["exit"] == 1
    assert rec["checks"] is None
    assert rec["stop"]["class"] == "gate"
    assert rec["stop"]["provenance"] == "executor"
    assert rec["stop"]["detail"] == f"selected tests: exit 1, {rec['selected_tests']['count']} selected"


# --- an empty build is not a pass (item 227): the executor copied the
# change into the working tree, committed nothing, and answered done; the
# gate read the tree and passed; the review passed; the merge queue sealed
# an item whose change did not exist. Written red first against v0.29.0.


def test_the_gate_reads_the_commit_and_an_executor_that_commits_nothing_is_halted_empty(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "build_valid")
    approve_item(root)
    run = run_attavo(root, "build", "1", env={"ATTAVO_FIXTURE_NO_COMMIT": "1"})
    assert run.returncode == 3, run.stdout + run.stderr
    rec = latest_build(root)
    assert rec["status"] == "halted", rec
    assert rec["sha"] == rec["start_sha"], rec
    assert (rec["stop"]["class"], rec["stop"]["provenance"]) == ("empty", "executor"), rec["stop"]
    assert "the executor committed nothing" in rec["stop"]["detail"], rec["stop"]
    assert "1 path(s) modified or untracked" in rec["stop"]["detail"], rec["stop"]
    assert (root / "built.txt").is_file(), "the fixture did not leave the file in the tree"
    assert all(c["exit"] != 0 for c in rec["criteria"]), (
        "a criterion passed on a tree whose commit does not carry the change", rec["criteria"])
    log = (root / "records" / "1" / "build-01.log").read_text()
    assert "you committed nothing" in log and "the gate reads the commit, not the tree" in log, log
    assert rec["iterations"] == 3, rec["iterations"]


def test_a_retried_attempt_that_adopts_a_prior_valid_commit_needs_no_commit_of_its_own(
        tmp_path: Path) -> None:
    """Item 229 -- the build-01/counsel-03/build-02 race this item answers:
    a build whose start_sha already carries a prior attempt's valid
    commit, inherited from history rather than made by this invocation,
    is not empty just because this attempt commits nothing of its own.
    The comparison is against the approval, not this attempt's own
    start_sha."""
    root = make_tenant(tmp_path, "build_valid")
    approve_item(root)
    approval_sha = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, check=True).stdout.strip()
    # a prior attempt's own valid commit, already on the branch before
    # this invocation's start_sha -- exactly what build-01 left behind
    # once its own record, not its commit, was discarded.
    (root / "built.txt").write_text("built\n")
    subprocess.run(["git", "-C", str(root), "add", "built.txt"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "a prior attempt's own valid commit"],
                   check=True)

    run = run_attavo(root, "build", "1", env={"ATTAVO_FIXTURE_NO_COMMIT": "1"})
    assert run.returncode == 0, run.stdout + run.stderr
    rec = latest_build(root)
    assert rec["status"] == "pass", rec
    assert rec["sha"] == rec["start_sha"], rec
    assert rec["sha"] != approval_sha, rec
    assert rec["iterations"] == 1, rec


def test_an_empty_iteration_is_fed_back_and_a_later_commit_passes(tmp_path: Path) -> None:
    """The loop is kept: an executor that answers done without a commit on
    its first iteration is told so and iterates; a commit on the next
    iteration passes as before."""
    root = make_tenant(tmp_path, "build_valid")
    approve_item(root)
    adapter_dir = root / "attavo" / "adapters" / "fixture"
    adapter_dir.mkdir(parents=True)
    run_path = adapter_dir / "run"
    run_path.write_text('#!/bin/sh\necho built > built.txt\n'
                        'if [ -s "$ATTAVO_FEEDBACK" ]; then git add built.txt; git commit -q -m built; fi\n'
                        'printf \'{"status":"done","question":null,"model":"fixture","model_version":null,'
                        '"adapter_version":"fixture-1","tokens":{"input":1,"output":1},"cost_usd":0.0}\\n\'\n')
    run_path.chmod(0o755)
    commit_all(root, "a tenant-side adapter that commits only once told")
    run = run_attavo(root, "build", "1")
    assert run.returncode == 0, run.stdout + run.stderr
    rec = latest_build(root)
    assert (rec["status"], rec["iterations"]) == ("pass", 2), rec
    assert rec["sha"] != rec["start_sha"]
