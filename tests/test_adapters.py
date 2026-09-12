"""adapters/claude-code/run: its argument construction is checked through
ATTAVO_ADAPTER_DRY_RUN=1, which prints the `claude` command line as a JSON
array instead of running it, and its response handling is checked through
ATTAVO_ADAPTER_RESPONSE_FILE, which feeds it a canned --output-format json
response instead of invoking `claude`. Judgment itself is measured, not
tested, so no real model is ever in this loop.

Issue #24: [executor.<adapter>] is an open configuration table, passed
through as ATTAVO_ADAPTER_CONFIG and copied verbatim into build-NN.json as
adapter_config. claude-code reads auth, model, setting_sources, and
permission_mode from it; ATTAVO_MODEL is retired.

Issue #30: the tenant's checks are part of done. build exports
ATTAVO_CHECKS_COMMAND when the manifest declares [checks]; the
instruction names it as part of done when set, and says nothing about
checks when it is not. Retires the dead test-run-command environment
variable this adapter used to read (dead: bin/attavo never set it).

Issue #34: the adapter keeps the evidence it is handed. The whole result
prints to stdout before the adapter's own final line, so build-NN.log
carries it. is_error, an absent structured_output, or a "question" under
40 characters all exit 1 (build's own "failed", not a halt to rule on).
Every canned response below now carries is_error and structured_output
for this reason -- a response without them now fails before ever
reaching model derivation, which is the point.

The response-handling tests are a separate regression, for a real response
shape: `claude --bare -p ... --output-format json` carries no top-level
"model" field at all -- the model id is the key of "modelUsage", its
per-model cost/usage breakdown (confirmed against `claude --version
2.1.263` on 2026-09-07). A future response-shape change on the executor's
side that drops or renames "modelUsage" would fail these tests before it
ever reached a record.

Issue #38: a non-zero `claude` exit used to reach this adapter's own
stderr only -- never stdout -- so build-NN.log, which reads only stdout,
recorded nothing about the failure. The adapter now prints claude's
stdout and stderr as transcript before exiting with claude's own code.
Specimen: attavo/code-factory build-11.json, a "failed" record with a
0-byte log after a 14-minute run.

Issue #78: the boundary is the tool's own sandbox, confirmed live against
`claude 2.1.268`, not a curated allowed_tools list judged per
sub-command. Every invocation now carries `--settings` with a `sandbox`
object; `--allowedTools` is fixed per stage (`Read,Edit,Write,Bash` for
build, `Read,Bash` for verify) instead of reading `[executor.claude-code]
.allowed_tools`, which is retired -- a manifest still declaring it is
refused by name.

Issue #77: `model` is the requested model verbatim when one was passed,
never picked out of `modelUsage` by token count (a lightweight helper
model's few thousand uncached tokens used to outrank a primary model
whose weight was almost entirely cached reads); `model_version` is the
`modelUsage` key whose `canonicalModel` matches the requested model.
`tokens.input` adds `cache_read_input_tokens` and
`cache_creation_input_tokens` to `usage.input_tokens` -- omitting them
undercounted a cache-heavy iteration by orders of magnitude (specimen:
`attest/records/649/build-01.json`, `tokens.input: 88` against 3.1
million tokens the log itself showed read from cache).

Issue #40: this file is claude-code-specific by construction (every test
invokes CLAUDE_CODE_RUN directly), so it is not where a general executor
contract belongs -- a second adapter inherits none of it. What used to
be here and is genuinely general (the raw result reaching stdout,
is_error and question-quality gating a halt, a non-zero tool exit
passing through) moved, not copied, to
tests/test_executor_conformance.py, which runs the same situations
against any adapter that declares a conformance.json. What stays here
is what is genuinely Claude Code's: --bare, --setting-sources,
--strict-mcp-config, and modelUsage parsing.
test_claude_code_names_the_tenants_checks keeps only its
English-specific half (the instruction must not tell the executor to
run the check) -- the command's mere presence in the instruction is now
the conformance suite's situation 7.

Run: python3 -m pytest tests -q
"""
import json
import os
import subprocess
from pathlib import Path

from helpers import FIXTURES

ROOT = Path(__file__).resolve().parent.parent
CLAUDE_CODE_RUN = ROOT / "adapters" / "claude-code" / "run"


def base_env(tmp_path: Path) -> dict:
    spec_dir = FIXTURES / "build_valid" / "specs" / "1"
    env = dict(os.environ)
    env.pop("ATTAVO_CHECKS_COMMAND", None)
    env.update({
        "ATTAVO_SPEC_DIR": str(spec_dir),
        "ATTAVO_RECORDS_DIR": str(tmp_path / "records"),
        "ATTAVO_RULINGS": str(tmp_path / "rulings.md"),
        "ATTAVO_BRANCH": "attavo/1-example",
        "ATTAVO_ITERATION": "1",
        "ATTAVO_FEEDBACK": str(tmp_path / "feedback.txt"),
    })
    return env


def dry_run(tmp_path: Path, config: dict | None = None,
           extra_env: dict | None = None) -> list[str]:
    env = base_env(tmp_path)
    env["ATTAVO_ADAPTER_DRY_RUN"] = "1"
    env["ATTAVO_ADAPTER_CONFIG"] = json.dumps(config or {})
    if extra_env:
        env.update(extra_env)

    run = subprocess.run([str(CLAUDE_CODE_RUN)], capture_output=True, text=True,
                         check=False, env=env)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)["argv"]


def run_with_response(tmp_path: Path, response: dict | list[dict],
                      extra_env: dict | None = None) -> subprocess.CompletedProcess:
    """response is a single canned result (repeats for every invocation
    this adapter makes) or a list, one result per invocation in order,
    its last element repeating once exhausted (issue #88: the
    non-question retry is a second invocation inside one adapter run)."""
    response_file = tmp_path / "response.json"
    response_file.write_text(json.dumps(response))
    env = base_env(tmp_path)
    env["ATTAVO_ADAPTER_RESPONSE_FILE"] = str(response_file)
    if extra_env:
        env.update(extra_env)
    return subprocess.run([str(CLAUDE_CODE_RUN)], capture_output=True, text=True,
                          check=False, env=env)


def test_claude_code_api_mode_is_default(tmp_path):
    """Acceptance 3. With no [executor.claude-code] table at all, the
    command line is unchanged from v0.3.0, apart from issue #78's
    --settings and its narrowed --allowedTools."""
    cmd = dry_run(tmp_path)

    assert "--bare" in cmd
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert "--json-schema" in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "acceptEdits"
    assert cmd[cmd.index("--allowedTools") + 1] == "Read,Edit,Write,Bash"
    assert "--model" not in cmd
    assert "--setting-sources" not in cmd
    assert "--strict-mcp-config" not in cmd


def test_claude_code_subscription_mode(tmp_path):
    """Acceptance 2. auth = "subscription": no --bare, and both
    --setting-sources user and --strict-mcp-config so the tenant's own
    project hooks and MCP servers do not load -- CLAUDE.md still does.
    Regression: detects the tenant's hooks (tenant zero's editor guard,
    which #576 deletes) creeping back into a subscription build.
    """
    cmd = dry_run(tmp_path, config={"auth": "subscription"})

    assert "--bare" not in cmd
    assert cmd[cmd.index("--setting-sources") + 1] == "user"
    assert "--strict-mcp-config" in cmd


def test_claude_code_build_sandbox_settings(tmp_path):
    """Issue #78, rejection test 2. --settings carries a sandbox object
    with the sandbox enabled and the unsandboxed fallback forbidden;
    --allowedTools names only the tool families the sandbox does not
    itself govern (Edit, Write) plus Bash itself, never a curated
    Bash(...) prefix list. Confirmed live against `claude 2.1.268`
    (issue #78): this settings shape refuses a write outside the working
    directory and an outbound connection.
    """
    cmd = dry_run(tmp_path)
    settings = json.loads(cmd[cmd.index("--settings") + 1])

    assert settings["sandbox"]["enabled"] is True
    assert settings["sandbox"]["allowUnsandboxedCommands"] is False
    assert settings["sandbox"]["network"]["allowedDomains"] == []
    allowed_tools = cmd[cmd.index("--allowedTools") + 1]
    assert allowed_tools == "Read,Edit,Write,Bash"
    assert not any(t.startswith("Bash(") for t in allowed_tools.split(","))


def test_claude_code_verify_sandbox_settings(tmp_path):
    """Issue #78, rejection test 3. verify's --settings additionally
    denies writes to the working directory itself -- mechanically
    read-only, not just instructed to be -- and --allowedTools omits
    Edit and Write, which the sandbox does not govern. Confirmed live
    (issue #78): a bare `denyWrite: true` did not stop a write inside
    the directory; a list naming the directory did.
    """
    cmd = dry_run(tmp_path, extra_env={"ATTAVO_STAGE": "verify"})
    settings = json.loads(cmd[cmd.index("--settings") + 1])

    assert settings["sandbox"]["enabled"] is True
    assert settings["sandbox"]["network"]["allowedDomains"] == []
    assert settings["sandbox"]["filesystem"]["denyWrite"] == [str(Path.cwd())]
    assert cmd[cmd.index("--allowedTools") + 1] == "Read,Bash"


def test_claude_code_refuses_the_retired_allowed_tools_key(tmp_path):
    """Issue #78, rejection test 4. allowed_tools is retired, not
    ignored: a manifest that still declares
    [executor.claude-code].allowed_tools is refused by name rather than
    silently accepted or silently dropped, on either stage.
    """
    env = base_env(tmp_path)
    env["ATTAVO_ADAPTER_CONFIG"] = json.dumps({"allowed_tools": ["Read"]})
    run = subprocess.run([str(CLAUDE_CODE_RUN)], capture_output=True, text=True,
                         check=False, env=env)
    assert run.returncode == 1
    assert "allowed_tools" in run.stderr


def test_claude_code_model_from_config(tmp_path):
    """Acceptance 4. model in the table appears as --model; the retired
    ATTAVO_MODEL, even if still set in the environment, is ignored."""
    cmd = dry_run(tmp_path, config={"model": "claude-sonnet-5"},
                 extra_env={"ATTAVO_MODEL": "should-be-ignored"})

    assert cmd.count("--model") == 1
    assert cmd[cmd.index("--model") + 1] == "claude-sonnet-5"
    assert "should-be-ignored" not in cmd


def test_claude_code_names_the_tenants_checks(tmp_path):
    """Issue #30, acceptance 5, reworded by issue #36. The instruction
    must not tell the executor to run the check itself. Regression:
    build-10 on tenant zero, where "must exit 0 before you commit" made
    the executor the runner (146 and 123 turns, 72 of 83 executor
    minutes waiting on tools) of a check attavo build already runs after
    every commit.

    The command's mere presence in the instruction (with it, and its
    absence without it) is now tests/test_executor_conformance.py's
    situation 7 -- general across any adapter. What stays here is the
    part that is an assertion about English and does not transfer.
    """
    with_checks = dry_run(tmp_path, extra_env={
        "ATTAVO_CHECKS_COMMAND": "python3 bin/factory-lane check --base origin/main"})
    instruction = with_checks[with_checks.index("-p") + 1]
    assert "are run for you" in instruction
    assert "before you commit" not in instruction

    without_checks = dry_run(tmp_path)
    instruction_without = without_checks[without_checks.index("-p") + 1]
    assert "are run for you" not in instruction_without


def test_claude_code_adapter_derives_model_from_usage_breakdown(tmp_path):
    """The real response shape: no top-level "model", one modelUsage key."""
    response = {
        "is_error": False,
        "structured_output": {"status": "done", "question": None},
        "usage": {"input_tokens": 5, "output_tokens": 9},
        "total_cost_usd": 0.01,
        "modelUsage": {
            "claude-sonnet-5": {"inputTokens": 5, "outputTokens": 9},
        },
    }
    run = run_with_response(tmp_path, response)
    assert run.returncode == 0, run.stderr
    line = json.loads(run.stdout.splitlines()[-1])
    assert line["model"] == "claude-sonnet-5"


def test_claude_code_adapter_derives_model_from_dominant_usage_key(tmp_path):
    """Several modelUsage keys (a lightweight helper model alongside the
    primary one): the key with the most total tokens wins."""
    response = {
        "is_error": False,
        "structured_output": {"status": "done", "question": None},
        "usage": {"input_tokens": 907, "output_tokens": 21},
        "total_cost_usd": 0.05,
        "modelUsage": {
            "claude-haiku-4-5-20251001": {"inputTokens": 897, "outputTokens": 12},
            "claude-sonnet-5": {"inputTokens": 10, "outputTokens": 9},
        },
    }
    run = run_with_response(tmp_path, response)
    assert run.returncode == 0, run.stderr
    line = json.loads(run.stdout.splitlines()[-1])
    assert line["model"] == "claude-haiku-4-5-20251001"


def test_claude_code_adapter_fails_without_a_model_id(tmp_path):
    """No top-level "model" and empty modelUsage: the adapter must fail
    loudly rather than emit an empty model that would let a "pass" record
    through with no identity behind it. The raw result still reaches
    stdout (issue #34) -- only the final report line is withheld."""
    response = {"is_error": False, "structured_output": {"status": "done", "question": None},
               "usage": {"input_tokens": 0, "output_tokens": 0}, "modelUsage": {}}
    run = run_with_response(tmp_path, response)
    assert run.returncode == 1
    assert "model" in run.stderr
    assert run.stdout == json.dumps(response) + "\n"


def test_claude_code_requested_model_wins_over_a_dominant_helper_key(tmp_path):
    """Issue #77, rejection test (model requested). Specimen:
    attest/records/649/build-01.json -- a helper model's few thousand
    uncached tokens used to outrank the primary model's hundreds of
    thousands of cached ones, recording model: claude-haiku-4-5-20251001
    when --model claude-sonnet-5 was passed. model is now the requested
    value verbatim; model_version is the modelUsage key whose
    canonicalModel matches it -- the dated id the tool actually ran.
    """
    response = {
        "is_error": False,
        "structured_output": {"status": "done", "question": None},
        "usage": {"input_tokens": 16, "output_tokens": 2672,
                  "cache_read_input_tokens": 306949, "cache_creation_input_tokens": 21636},
        "total_cost_usd": 0.17,
        "modelUsage": {
            "claude-haiku-4-5-20251001": {
                "inputTokens": 4042, "outputTokens": 14,
                "canonicalModel": "claude-haiku-4-5"},
            "claude-sonnet-5": {
                "inputTokens": 16, "outputTokens": 2672,
                "cacheReadInputTokens": 306949, "cacheCreationInputTokens": 21636,
                "canonicalModel": "claude-sonnet-5"},
        },
    }
    run = run_with_response(tmp_path, response,
                            extra_env={"ATTAVO_ADAPTER_CONFIG": json.dumps({"model": "claude-sonnet-5"})})
    assert run.returncode == 0, run.stderr
    line = json.loads(run.stdout.splitlines()[-1])
    assert line["model"] == "claude-sonnet-5"
    assert line["model_version"] == "claude-sonnet-5"


def test_claude_code_unrequested_model_counts_cached_tokens_too(tmp_path):
    """Issue #77, rejection test (model not requested). The modelUsage
    key wins on inputTokens + cacheReadInputTokens +
    cacheCreationInputTokens + outputTokens, not on inputTokens +
    outputTokens alone -- otherwise a helper model's few thousand
    uncached tokens outranks a primary model whose real weight is almost
    entirely cached, the same distortion as when a model is requested.
    """
    response = {
        "is_error": False,
        "structured_output": {"status": "done", "question": None},
        "usage": {"input_tokens": 4058, "output_tokens": 2686,
                  "cache_read_input_tokens": 306949, "cache_creation_input_tokens": 21636},
        "modelUsage": {
            "claude-haiku-4-5-20251001": {
                "inputTokens": 4042, "outputTokens": 14,
                "canonicalModel": "claude-haiku-4-5"},
            "claude-sonnet-5": {
                "inputTokens": 16, "outputTokens": 2672,
                "cacheReadInputTokens": 306949, "cacheCreationInputTokens": 21636,
                "canonicalModel": "claude-sonnet-5"},
        },
    }
    run = run_with_response(tmp_path, response)
    assert run.returncode == 0, run.stderr
    line = json.loads(run.stdout.splitlines()[-1])
    assert line["model"] == "claude-sonnet-5"
    assert line["model_version"] == "claude-sonnet-5"


def test_claude_code_requested_model_with_no_matching_usage_key(tmp_path):
    """Issue #77. A requested model that matches no modelUsage
    canonicalModel still records verbatim as model, with model_version
    left null rather than guessed -- the mismatch is reported on stderr,
    not swallowed.
    """
    response = {
        "is_error": False,
        "structured_output": {"status": "done", "question": None},
        "usage": {"input_tokens": 5, "output_tokens": 9},
        "modelUsage": {
            "claude-haiku-4-5-20251001": {
                "inputTokens": 5, "outputTokens": 9,
                "canonicalModel": "claude-haiku-4-5"},
        },
    }
    run = run_with_response(tmp_path, response,
                            extra_env={"ATTAVO_ADAPTER_CONFIG": json.dumps({"model": "claude-sonnet-5"})})
    assert run.returncode == 0, run.stderr
    line = json.loads(run.stdout.splitlines()[-1])
    assert line["model"] == "claude-sonnet-5"
    assert line["model_version"] is None
    assert "claude-sonnet-5" in run.stderr


def make_diff(tmp_path: Path, n_files: int, lines_per_file: int) -> Path:
    diff_path = tmp_path / "diff.patch"
    chunks = []
    for i in range(n_files):
        chunks.append(f"diff --git a/file{i}.py b/file{i}.py\n")
        chunks.append("+line of a large deletion diff\n" * lines_per_file)
    diff_path.write_text("".join(chunks))
    return diff_path


def test_verify_instruction_names_the_diffs_path_not_its_text(tmp_path):
    """Issue #86, rejection test 1. Specimen: tenant zero #659, verify-01,
    the codex adapter's own reproduction -- ATTAVO_DIFF inlined in full
    made the invocation's own size track the diff's, and the first item
    with a real deletion exceeded a judge's input ceiling. The
    instruction now names the diff's path, byte count, line count, and
    files touched, and stays small whatever the diff's size.
    """
    diff_path = make_diff(tmp_path, n_files=3, lines_per_file=30000)
    assert diff_path.stat().st_size > 2_000_000

    cmd = dry_run(tmp_path, extra_env={"ATTAVO_STAGE": "verify", "ATTAVO_DIFF": str(diff_path)})
    instruction = cmd[cmd.index("-p") + 1]

    assert len(instruction.encode()) < 16 * 1024
    assert str(diff_path) in instruction
    assert str(diff_path.stat().st_size) in instruction
    assert "3 files touched" in instruction
    assert "line of a large deletion diff" not in instruction


def test_build_instruction_names_the_feedback_files_path_not_its_text(tmp_path):
    """Issue #86, rejection test (build side): the same size-dependence
    applied to a large ATTAVO_FEEDBACK file -- the previous iteration's
    failing criteria, test output, and checks output -- inlined whole.
    """
    feedback_path = tmp_path / "feedback.txt"
    feedback_path.write_text("a failing test line\n" * 120000)
    assert feedback_path.stat().st_size > 2_000_000

    cmd = dry_run(tmp_path, extra_env={"ATTAVO_FEEDBACK": str(feedback_path)})
    instruction = cmd[cmd.index("-p") + 1]

    assert len(instruction.encode()) < 16 * 1024
    assert str(feedback_path) in instruction
    assert str(feedback_path.stat().st_size) in instruction
    assert "a failing test line" not in instruction


def _response(question=None, status="done", subagent_stats=None) -> dict:
    response = {
        "is_error": False,
        "structured_output": {"status": status, "question": question},
        "usage": {"input_tokens": 5, "output_tokens": 9},
        "total_cost_usd": 0.01,
        "modelUsage": {"claude-sonnet-5": {"inputTokens": 5, "outputTokens": 9}},
    }
    if subagent_stats is not None:
        response["subagent_stats"] = subagent_stats
    return response


REPORT_1 = ("I've traced the spec's 5 criteria against the tree and "
           "dispatched an implementation agent to continue the work.")
REPORT_2 = ("Running the acceptance suite in the background to find the "
           "specific test failure that is blocking this item.")
PROPER_QUESTION = ("Which of the two conflicting rulings on issue #12 "
                  "applies to criterion 3?")


def test_claude_code_disallowedtools_names_the_subagent_tools(tmp_path):
    """Issue #88, rejection test 3 (part 1). Both build and verify carry
    the subagent tool names in --disallowedTools -- confirmed live
    (claude 2.1.269) that --allowedTools alone does not stop a spawn a
    tenant-zero specimen made with neither tool in it at all.
    """
    build = dry_run(tmp_path)
    assert build[build.index("--disallowedTools") + 1] == "Agent,Workflow"

    verify = dry_run(tmp_path, extra_env={"ATTAVO_STAGE": "verify"})
    assert verify[verify.index("--disallowedTools") + 1] == "Agent,Workflow"


def test_claude_code_refuses_a_result_with_spawned_subagents(tmp_path):
    """Issue #88, rejection test 3 (part 2). Specimen: tenant zero #659
    build-01 -- subagent_stats.spawned: 3 with two killed at the tool's
    own 600s ceiling, reported as done. A response carrying spawned work
    is refused by name, with the counts, rather than accepted -- checked
    on the response itself, not the flag that was supposed to forbid it.
    """
    response = _response(
        subagent_stats={"spawned": 3, "completed": 1,
                        "killed": {"parent": 0, "user": 0, "system": 2},
                        "started_in_background": 3})
    run = run_with_response(tmp_path, response)
    assert run.returncode == 1
    assert "subagent_stats.spawned=3" in run.stderr
    assert "killed=2" in run.stderr


def test_claude_code_report_without_question_mark_is_reinstructed_once(tmp_path):
    """Issue #88, rejection test 1. A "question" with no "?" is a report,
    not a question (five of tenant zero's twelve non-pass build
    records): the adapter re-invokes once, appending REINSTRUCTION, and
    a proper question on the retry is returned as the question -- no
    halt on the first report at all.
    """
    responses = [_response(question=REPORT_1, status="question"),
                _response(question=PROPER_QUESTION, status="question")]
    run = run_with_response(tmp_path, responses)
    assert run.returncode == 0, run.stderr
    assert REPORT_1 in run.stdout
    assert PROPER_QUESTION in run.stdout
    line = json.loads(run.stdout.splitlines()[-1])
    assert line["status"] == "question"
    assert line["question"] == PROPER_QUESTION
    assert "class" not in line


def test_claude_code_report_without_question_mark_can_finish_done_on_retry(tmp_path):
    """Issue #88, rejection test 1 (done on the retry). REINSTRUCTION
    offers "continue to done" as the other way out of a report; this is
    not itself in the acceptance table but is the plain reading of it.
    """
    responses = [_response(question=REPORT_1, status="question"),
                _response(status="done")]
    run = run_with_response(tmp_path, responses)
    assert run.returncode == 0, run.stderr
    line = json.loads(run.stdout.splitlines()[-1])
    assert line["status"] == "done"
    assert line["question"] is None


def test_claude_code_two_reports_in_a_row_halts_with_class_non_question(tmp_path):
    """Issue #88, rejection test 2. Two non-questions in a row still
    halt -- someone must look -- but the report line names the class so
    a reader can tell it apart from a genuine question, and both raw
    results (not just the second, previously the only one kept) reach
    the transcript.
    """
    responses = [_response(question=REPORT_1, status="question"),
                _response(question=REPORT_2, status="question")]
    run = run_with_response(tmp_path, responses)
    assert run.returncode == 0, run.stderr
    assert REPORT_1 in run.stdout
    assert REPORT_2 in run.stdout
    line = json.loads(run.stdout.splitlines()[-1])
    assert line["status"] == "question"
    assert line["class"] == "non-question"


def test_claude_code_tokens_input_includes_cached_tokens(tmp_path):
    """Issue #77, rejection test. usage.input_tokens alone undercounts a
    cache-heavy iteration by orders of magnitude (specimen:
    attest/records/649/build-01.json, tokens.input: 88 against 3.1
    million tokens the log itself showed read from cache); tokens.input
    must add cache_read_input_tokens and cache_creation_input_tokens.
    """
    response = {
        "is_error": False,
        "structured_output": {"status": "done", "question": None},
        "usage": {"input_tokens": 16, "output_tokens": 9,
                  "cache_read_input_tokens": 100, "cache_creation_input_tokens": 5},
        "modelUsage": {
            "claude-sonnet-5": {"inputTokens": 16, "outputTokens": 9,
                               "canonicalModel": "claude-sonnet-5"},
        },
    }
    run = run_with_response(tmp_path, response)
    assert run.returncode == 0, run.stderr
    line = json.loads(run.stdout.splitlines()[-1])
    assert line["tokens"]["input"] == 16 + 100 + 5

