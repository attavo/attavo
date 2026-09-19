"""adapters/codex/run: its argument construction is checked through
ATTAVO_ADAPTER_DRY_RUN=1, which prints {"argv", "instruction", "schema"}
instead of running, and its response handling is checked through
ATTAVO_ADAPTER_RESPONSE_FILE, which feeds it a canned {"events", "final",
"exit_code"} instead of invoking `codex`. Judgment itself is measured,
not tested, so no real model is ever in this loop.

Issue #41: this file is codex-specific by construction (every test
invokes CODEX_RUN directly), the same split issue #40 made for
claude-code -- what is genuinely general (a report line with a model,
is_error/absent-output/question-quality gating a halt, a non-zero tool
exit passing through, the executor identity, the verify schema and its
required wording, vendor independence's "reports" declaration) lives in
tests/test_executor_conformance.py and runs against both adapters from
the same parametrization. What stays here is what is genuinely codex's:
the model-required refusal (no model id appears anywhere codex itself
reports), the auth-mode refusal (an OPENAI_API_KEY left in the
environment would silently bill the API account on a plan-auth run),
the sandbox flag per stage, and JSONL event parsing tolerant of the
tracing lines codex writes to the same stream.

Run: python3 -m pytest tests -q
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from helpers import FIXTURES

ROOT = Path(__file__).resolve().parent.parent
CODEX_RUN = ROOT / "adapters" / "codex" / "run"


def base_env(tmp_path: Path, config: dict[str, Any] | None = None) -> dict[str, Any]:
    spec_dir = FIXTURES / "build_valid" / "specs" / "1"
    env = dict(os.environ)
    env.pop("ATTAVO_CHECKS_COMMAND", None)
    env.pop("OPENAI_API_KEY", None)
    env.update({
        "ATTAVO_SPEC_DIR": str(spec_dir),
        "ATTAVO_RECORDS_DIR": str(tmp_path / "records"),
        "ATTAVO_RULINGS": str(tmp_path / "rulings.md"),
        "ATTAVO_BRANCH": "attavo/1-example",
        "ATTAVO_ITERATION": "1",
        "ATTAVO_FEEDBACK": str(tmp_path / "feedback.txt"),
        "ATTAVO_ADAPTER_CONFIG": json.dumps(config if config is not None else {"model": "gpt-test"}),
    })
    return env


def run_codex(env: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(CODEX_RUN)], capture_output=True, text=True,
                          check=False, env=env)


def dry_run(tmp_path: Path, config: dict[str, Any] | None = None,
           extra_env: dict[str, Any] | None = None) -> dict[str, Any]:
    env = base_env(tmp_path, config)
    env["ATTAVO_ADAPTER_DRY_RUN"] = "1"
    if extra_env:
        env.update(extra_env)
    run = run_codex(env)
    assert run.returncode == 0, run.stderr
    return cast(dict[str, Any], json.loads(run.stdout))


def run_with_response(tmp_path: Path, events: list[dict[str, Any]], final: dict[str, Any], exit_code: int = 0,
                      config: dict[str, Any] | None = None, extra_env: dict[str, Any] | None = None
                      ) -> subprocess.CompletedProcess[str]:
    response_file = tmp_path / "response.json"
    response_file.write_text(json.dumps({"events": events, "final": final, "exit_code": exit_code}))
    env = base_env(tmp_path, config)
    env["ATTAVO_ADAPTER_RESPONSE_FILE"] = str(response_file)
    if extra_env:
        env.update(extra_env)
    return run_codex(env)


def run_with_responses(tmp_path: Path, canned: list[dict[str, Any]],
                       config: dict[str, Any] | None = None, extra_env: dict[str, Any] | None = None
                       ) -> subprocess.CompletedProcess[str]:
    """canned is a queue of {"events", "final", "exit_code"} objects, one
    per invocation this adapter makes in order, its last element
    repeating once exhausted (issue #88: the non-question retry is a
    second invocation inside one adapter run)."""
    response_file = tmp_path / "response.json"
    response_file.write_text(json.dumps(canned))
    env = base_env(tmp_path, config)
    env["ATTAVO_ADAPTER_RESPONSE_FILE"] = str(response_file)
    if extra_env:
        env.update(extra_env)
    return run_codex(env)


DONE_USAGE = {"input_tokens": 15272, "cached_input_tokens": 12160,
             "cache_write_input_tokens": 0, "output_tokens": 5,
             "reasoning_output_tokens": 0}
DONE_EVENTS: list[dict[str, Any]] = [
    {"type": "thread.started", "thread_id": "t1"},
    {"type": "turn.started"},
    {"type": "turn.completed", "usage": DONE_USAGE},
]


def test_model_is_required(tmp_path: Path) -> None:
    """No model id appears anywhere codex itself reports (confirmed live
    2026-09-08 against codex-cli 0.153.4: not the event stream, not
    `codex doctor`, not any config file), so [executor.codex].model is
    required rather than optional -- the adapter refuses before ever
    invoking codex, both for a dry run and for a real one.
    """
    dry = run_codex({**base_env(tmp_path, config={}), "ATTAVO_ADAPTER_DRY_RUN": "1"})
    assert dry.returncode == 1
    assert "model" in dry.stderr

    real = run_with_response(tmp_path, DONE_EVENTS, {"status": "done", "question": None},
                             config={})
    assert real.returncode == 1
    assert "model" in real.stderr


def test_model_reported_is_exactly_the_configured_one_never_derived(tmp_path: Path) -> None:
    """The report's model is [executor.codex].model verbatim -- there is
    nothing in a real response to derive it from, unlike claude-code's
    modelUsage breakdown."""
    run = run_with_response(tmp_path, DONE_EVENTS, {"status": "done", "question": None},
                            config={"model": "gpt-5.1-codex"})
    assert run.returncode == 0, run.stderr
    line = json.loads(run.stdout.splitlines()[-1])
    assert line["model"] == "gpt-5.1-codex"


def test_subscription_auth_refuses_with_an_api_key_in_the_environment(tmp_path: Path) -> None:
    """Issue #41 (2026-09-09 addendum). [executor.codex].auth mirrors
    claude-code's auth = "subscription": the operator's own machine logs
    in via a ChatGPT plan, and OPENAI_API_KEY left in the environment
    would silently switch codex to API billing on a run meant for the
    plan. The adapter refuses rather than silently falling back --
    checked deterministically against OPENAI_API_KEY, not a live `codex
    doctor` call, so this holds with no real codex installed.
    """
    env = base_env(tmp_path, config={"model": "gpt-test"})
    env["OPENAI_API_KEY"] = "sk-should-not-be-used"
    run = run_codex(env)
    assert run.returncode == 1
    assert "subscription" in run.stderr
    assert "OPENAI_API_KEY" in run.stderr


def test_subscription_auth_is_the_default_and_proceeds_with_no_key(tmp_path: Path) -> None:
    run = run_with_response(tmp_path, DONE_EVENTS, {"status": "done", "question": None},
                            config={"model": "gpt-test"})
    assert run.returncode == 0, run.stderr


def test_api_auth_refuses_without_an_api_key(tmp_path: Path) -> None:
    env = base_env(tmp_path, config={"model": "gpt-test", "auth": "api"})
    run = run_codex(env)
    assert run.returncode == 1
    assert "api" in run.stderr
    assert "OPENAI_API_KEY" in run.stderr


def test_cost_usd_is_always_null(tmp_path: Path) -> None:
    """No dollar figure appears anywhere codex reports under any auth
    mode (confirmed live 2026-09-08); a non-null cost on a codex record
    would be evidence the wrong credential was used, not a measurement."""
    run = run_with_response(tmp_path, DONE_EVENTS, {"status": "done", "question": None},
                            config={"model": "gpt-test"})
    assert run.returncode == 0, run.stderr
    line = json.loads(run.stdout.splitlines()[-1])
    assert line["cost_usd"] is None


def test_sandbox_is_workspace_write_for_build_and_read_only_for_verify(tmp_path: Path) -> None:
    """verify's instruction says "make no edits, write no files, commit
    nothing"; the sandbox enforces it mechanically rather than this
    adapter trusting the instruction alone."""
    build = dry_run(tmp_path)
    assert build["argv"][build["argv"].index("--sandbox") + 1] == "workspace-write"

    verify = dry_run(tmp_path, extra_env={"ATTAVO_STAGE": "verify"})
    assert verify["argv"][verify["argv"].index("--sandbox") + 1] == "read-only"


def test_prompt_is_never_in_argv(tmp_path: Path) -> None:
    """codex's `-p` is `--profile`, not a prompt flag -- confirmed live
    2026-09-08 -- so the instruction must never appear in argv at all;
    it goes in on stdin, which is why dry_run's own envelope carries it
    as a separate "instruction" key rather than inside "argv"."""
    dry = dry_run(tmp_path)
    assert "-p" not in dry["argv"]
    assert not any(dry["instruction"] in str(a) for a in dry["argv"])
    assert "-" in dry["argv"]


def test_model_flag_from_config(tmp_path: Path) -> None:
    dry = dry_run(tmp_path, config={"model": "gpt-5.1-codex"})
    assert dry["argv"][dry["argv"].index("--model") + 1] == "gpt-5.1-codex"


def test_real_invocation_tolerates_tracing_lines_in_the_event_stream(tmp_path: Path) -> None:
    """codex writes ERROR tracing lines to the same stdout stream as its
    JSONL events under --json (confirmed live 2026-09-08: retried
    websocket connections interleave "ERROR codex_api::..." lines with
    valid JSON). A tracing line is not JSON and must be skipped, not
    treated as a parse failure.
    """
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    fake_codex = fake_bin / "codex"
    fake_codex.write_text(
        "#!/bin/sh\n"
        "echo '2026-09-08T21:47:26Z ERROR codex_api::endpoint: not json' 1>&2\n"
        "echo '2026-09-08T21:47:26Z ERROR codex_api::endpoint: not json'\n"
        "printf '{\"type\":\"thread.started\",\"thread_id\":\"t1\"}\\n'\n"
        "printf '{\"type\":\"turn.completed\",\"usage\":{\"input_tokens\":7,\"output_tokens\":3}}\\n'\n"
        "out=\"\"\n"
        "prev=\"\"\n"
        "for arg in \"$@\"; do\n"
        "  if [ \"$prev\" = \"--output-last-message\" ]; then out=\"$arg\"; fi\n"
        "  prev=\"$arg\"\n"
        "done\n"
        "printf '{\"status\":\"done\",\"question\":null}' > \"$out\"\n")
    fake_codex.chmod(0o755)

    env = base_env(tmp_path, config={"model": "gpt-test"})
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    run = run_codex(env)
    assert run.returncode == 0, run.stderr
    line = json.loads(run.stdout.splitlines()[-1])
    assert line["tokens"] == {"input": 7, "output": 3}


def make_diff(tmp_path: Path, n_files: int, lines_per_file: int) -> Path:
    diff_path = tmp_path / "diff.patch"
    chunks = []
    for i in range(n_files):
        chunks.append(f"diff --git a/file{i}.py b/file{i}.py\n")
        chunks.append("+line of a large deletion diff\n" * lines_per_file)
    diff_path.write_text("".join(chunks))
    return diff_path


def test_verify_instruction_names_the_diffs_path_not_its_text(tmp_path: Path) -> None:
    """Issue #86, rejection test 1. Specimen: tenant zero #659, verify-01,
    codex exec exit 1, "Input exceeds the maximum length of 1048576
    characters (actual_chars: 1297762)" -- ATTAVO_DIFF inlined in full
    made the invocation's own size track the diff's, and the first item
    with a real deletion exceeded codex's input ceiling. The instruction
    now names the diff's path, byte count, line count, and files
    touched, and stays small whatever the diff's size.
    """
    diff_path = make_diff(tmp_path, n_files=3, lines_per_file=30000)
    assert diff_path.stat().st_size > 2_000_000

    dry = dry_run(tmp_path, extra_env={"ATTAVO_STAGE": "verify", "ATTAVO_DIFF": str(diff_path)})
    instruction = dry["instruction"]

    assert len(instruction.encode()) < 16 * 1024
    assert str(diff_path) in instruction
    assert str(diff_path.stat().st_size) in instruction
    assert "3 files touched" in instruction
    assert "line of a large deletion diff" not in instruction


def test_build_instruction_names_the_feedback_files_path_not_its_text(tmp_path: Path) -> None:
    """Issue #86, rejection test (build side): the same size-dependence
    applied to a large ATTAVO_FEEDBACK file -- the previous iteration's
    failing criteria, test output, and checks output -- inlined whole.
    """
    feedback_path = tmp_path / "feedback.txt"
    feedback_path.write_text("a failing test line\n" * 120000)
    assert feedback_path.stat().st_size > 2_000_000

    dry = dry_run(tmp_path, extra_env={"ATTAVO_FEEDBACK": str(feedback_path)})
    instruction = dry["instruction"]

    assert len(instruction.encode()) < 16 * 1024
    assert str(feedback_path) in instruction
    assert str(feedback_path.stat().st_size) in instruction
    assert "a failing test line" not in instruction


REPORT_1 = ("I've traced the spec's 5 criteria against the tree and "
           "dispatched an implementation agent to continue the work.")
REPORT_2 = ("Running the acceptance suite in the background to find the "
           "specific test failure that is blocking this item.")
PROPER_QUESTION = ("Which of the two conflicting rulings on issue #12 "
                  "applies to criterion 3?")


def _events(question: str | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return DONE_EVENTS, {"status": "question", "question": question}


def test_codex_report_without_question_mark_is_reinstructed_once(tmp_path: Path) -> None:
    """Issue #88, rejection test 1 (codex). A "question" with no "?" is a
    report, not a question: the adapter re-invokes once, appending
    REINSTRUCTION, and a proper question on the retry is returned as the
    question -- no halt on the first report at all.
    """
    events1, final1 = _events(REPORT_1)
    events2, final2 = _events(PROPER_QUESTION)
    canned = [{"events": events1, "final": final1, "exit_code": 0},
             {"events": events2, "final": final2, "exit_code": 0}]
    run = run_with_responses(tmp_path, canned, config={"model": "gpt-test"})
    assert run.returncode == 0, run.stderr
    assert REPORT_1 in run.stdout
    assert PROPER_QUESTION in run.stdout
    line = json.loads(run.stdout.splitlines()[-1])
    assert line["status"] == "question"
    assert line["question"] == PROPER_QUESTION
    assert "class" not in line


def test_codex_two_reports_in_a_row_halts_with_class_non_question(tmp_path: Path) -> None:
    """Issue #88, rejection test 2 (codex). Two non-questions in a row
    still halt, but the report line names the class and both raw results
    reach the transcript.
    """
    events1, final1 = _events(REPORT_1)
    events2, final2 = _events(REPORT_2)
    canned = [{"events": events1, "final": final1, "exit_code": 0},
             {"events": events2, "final": final2, "exit_code": 0}]
    run = run_with_responses(tmp_path, canned, config={"model": "gpt-test"})
    assert run.returncode == 0, run.stderr
    assert REPORT_1 in run.stdout
    assert REPORT_2 in run.stdout
    line = json.loads(run.stdout.splitlines()[-1])
    assert line["status"] == "question"
    assert line["class"] == "non-question"


def _events_with_usage(question: str | None, status: str, input_tokens: int, output_tokens: int
                       ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    events: list[dict[str, Any]] = [
        {"type": "thread.started", "thread_id": "t1"},
        {"type": "turn.started"},
        {"type": "turn.completed",
         "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens}},
    ]
    return events, {"status": status, "question": question}


@pytest.mark.parametrize("second_status,second_question", [
    ("done", None),
    ("question", PROPER_QUESTION),
    ("question", REPORT_2),
])
def test_codex_retry_sums_tokens_across_both_invocations(
        tmp_path: Path, second_status: str, second_question: str) -> None:
    """Issue #88 (codex). A non-question costs two executor turns, not
    one: the report line must sum both calls' tokens, whatever the
    retry's own outcome, or the record undercounts by exactly the
    first, discarded call. cost_usd stays null on codex regardless
    (issue #41): summing null with null is still null.
    """
    events1, final1 = _events_with_usage(REPORT_1, "question", 5, 9)
    events2, final2 = _events_with_usage(second_question, second_status, 7, 3)
    canned = [{"events": events1, "final": final1, "exit_code": 0},
             {"events": events2, "final": final2, "exit_code": 0}]
    run = run_with_responses(tmp_path, canned, config={"model": "gpt-test"})
    assert run.returncode == 0, run.stderr
    line = json.loads(run.stdout.splitlines()[-1])
    assert line["tokens"]["input"] == 5 + 7
    assert line["tokens"]["output"] == 9 + 3
    assert line["cost_usd"] is None


def test_codex_declares_it_cannot_check_the_subagent_guard(tmp_path: Path) -> None:
    """Issue #88, rejection test 4. codex exec has no subagent or
    background-task concept anywhere in its surface (confirmed against
    `codex exec --help`, codex-cli 0.153.4) -- its conformance.json
    declares this rather than asserting a check against a concept that
    is not there, so this is not an undeclared blind spot.
    """
    conformance = json.loads((ROOT / "adapters" / "codex" / "conformance.json").read_text())
    guard = conformance.get("subagent_guard")
    assert guard is not None
    assert guard["supported"] is False
    assert guard.get("reason")


def test_nonzero_exit_with_error_only_on_stderr_reaches_the_log(tmp_path: Path) -> None:
    """Issue #86, rejection test 3 (confirmation). Specimen: tenant zero
    #659 verify-01 -- codex exec exit 1 with "Input exceeds the maximum
    length..." and the failed record's own log carried only
    thread.started and "[adapter exited 1]", the error reaching neither
    captured channel. Whichever channel codex writes its own error line
    to -- here, stderr, with nothing on stdout at all -- must reach this
    adapter's own stdout, which build-NN.log/verify-NN.log is built
    from. Already true of the current invoke(): this closes the gap
    explicitly rather than relying on situation 5's two-channel case.
    """
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    fake_codex = fake_bin / "codex"
    fake_codex.write_text(
        "#!/bin/sh\n"
        "echo 'Error: turn failed: Input exceeds the maximum length of "
        "1048576 characters (actual_chars: 1297762)' 1>&2\n"
        "exit 1\n")
    fake_codex.chmod(0o755)

    env = base_env(tmp_path, config={"model": "gpt-test"})
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    run = run_codex(env)
    assert run.returncode == 1
    assert "Input exceeds the maximum length" in run.stdout


def test_ceiling_refuses_an_oversized_instruction(tmp_path: Path) -> None:
    """Issue #93, rejection test 2. codex's own declared limit
    (conformance.json's input_limit: 1048576 chars, its own
    input_too_large error) is patched down via
    ATTAVO_ADAPTER_INPUT_LIMIT_CHARS to a small number, small enough
    that even the default fixture's instruction exceeds it: the dry-run
    refuses by name, naming both numbers and the word "ceiling", rather
    than accepting a prompt this adapter can measure is over the ceiling
    before codex ever sees it (tenant zero #659, verify-01: exit 1,
    three seconds, no findings, no tokens).
    """
    dry_env = base_env(tmp_path, config={"model": "gpt-test"})
    dry_env["ATTAVO_ADAPTER_DRY_RUN"] = "1"
    dry_env["ATTAVO_ADAPTER_INPUT_LIMIT_CHARS"] = "10"
    dry = run_codex(dry_env)
    assert dry.returncode == 1
    assert "ceiling" in dry.stderr
    assert "10" in dry.stderr
    numbers = [int(n) for n in re.findall(r"\d+", dry.stderr)]
    assert 10 in numbers
    assert any(n > 10 for n in numbers), "both numbers (limit and size) must appear"


def test_ceiling_refuses_before_invoking_codex(tmp_path: Path) -> None:
    """Issue #93, rejection test 2 (invokes nothing). A fake codex on
    PATH would leave a sentinel file if it ran; the ceiling refusal
    happens before the adapter ever shells out, and before the model
    check even matters here."""
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    sentinel = tmp_path / "invoked"
    fake_codex = fake_bin / "codex"
    fake_codex.write_text(f"#!/bin/sh\ntouch {sentinel}\nexit 0\n")
    fake_codex.chmod(0o755)

    env = base_env(tmp_path, config={"model": "gpt-test"})
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["ATTAVO_ADAPTER_INPUT_LIMIT_CHARS"] = "10"
    run = run_codex(env)
    assert run.returncode == 1
    assert not sentinel.exists(), "codex must never be invoked over the ceiling"


def counsel_extra_env(tmp_path: Path) -> dict[str, Any]:
    """Issue #100: the record, its log, the diff, the tenant manifest,
    and this product's own root, all handed by path."""
    record_path = tmp_path / "build-01.json"
    record_path.write_text(json.dumps({"status": "halted"}))
    log_path = tmp_path / "build-01.log"
    log_path.write_text("adapter transcript\n")
    diff_path = tmp_path / "diff.patch"
    diff_path.write_text("diff --git a/x.py b/x.py\n")
    manifest_path = tmp_path / "attavo.toml"
    manifest_path.write_text("schema_version = 1\n")
    return {
        "ATTAVO_STAGE": "counsel",
        "ATTAVO_COUNSEL_RECORD": str(record_path),
        "ATTAVO_COUNSEL_LOG": str(log_path),
        "ATTAVO_COUNSEL_DIFF": str(diff_path),
        "ATTAVO_MANIFEST": str(manifest_path),
        "ATTAVO_PRODUCT_ROOT": str(ROOT),
    }


def test_codex_counsel_sandbox_is_read_only(tmp_path: Path) -> None:
    """Issue #100, acceptance row 4. counsel runs under the same
    read-only sandbox as verify."""
    dry = dry_run(tmp_path, extra_env=counsel_extra_env(tmp_path))
    assert dry["argv"][dry["argv"].index("--sandbox") + 1] == "read-only"


def test_codex_counsel_names_the_product_root_and_the_record(tmp_path: Path) -> None:
    """Issue #100, acceptance row 4. The instruction names this product's
    own root and the record path -- both handed by path, not inlined."""
    extra = counsel_extra_env(tmp_path)
    dry = dry_run(tmp_path, extra_env=extra)
    instruction = dry["instruction"]

    assert str(ROOT) in instruction
    assert extra["ATTAVO_COUNSEL_RECORD"] in instruction
    assert "bin/attavo" in instruction


def test_codex_counsel_schema_requires_the_structured_answer(tmp_path: Path) -> None:
    dry = dry_run(tmp_path, extra_env=counsel_extra_env(tmp_path))
    assert set(dry["schema"]["required"]) == {"class", "verdict", "citations", "proposal"}


def test_codex_counsel_instruction_stays_under_the_bound_with_large_files(tmp_path: Path) -> None:
    """Issue #100, acceptance row 4 -- counsel handles a large record log
    the same way build and verify already do (issue #86): named by path,
    never inlined."""
    extra = counsel_extra_env(tmp_path)
    large_log = make_diff(tmp_path, n_files=3, lines_per_file=30000)
    extra["ATTAVO_COUNSEL_LOG"] = str(large_log)
    assert large_log.stat().st_size > 2_000_000

    dry = dry_run(tmp_path, extra_env=extra)
    instruction = dry["instruction"]
    assert len(instruction.encode()) < 16 * 1024
    assert str(large_log) in instruction


def test_codex_verify_instruction_states_what_a_stub_is(tmp_path: Path) -> None:
    """Issue #90 (closed by #93). ATTAVO_SPEC_DIR's own spec, fixtures'
    build_valid/specs/1, carries a stub/ -- the instruction now names its
    path beside a sentence saying it is the fixture the criteria were
    shown to pass against, not the intended change, so a difference
    between the built tree and the stub is not itself a finding. Red
    before this fix: the judge twice reported the built tree as failing
    to match its stub (tenant zero #664, #644)."""
    dry = dry_run(tmp_path, extra_env={"ATTAVO_STAGE": "verify"})
    instruction = dry["instruction"]
    assert "stub" in instruction.lower()
    assert "is not the intended change" in instruction
    assert "not a finding" in instruction


def test_codex_build_instruction_says_nothing_about_the_stub(tmp_path: Path) -> None:
    """The stub sentence is verify's objective, not build's -- a spec
    carrying a stub/ must not change the build instruction at all."""
    dry = dry_run(tmp_path)
    assert "is not the intended change" not in dry["instruction"]


def test_codex_verify_instruction_says_nothing_about_a_stub_when_absent(tmp_path: Path) -> None:
    """A spec with no stub/ (fixtures' prose_criterion/specs/1) gets no
    stub section at all -- the sentence is conditioned on the stub
    existing, not always appended."""
    spec_dir = FIXTURES / "prose_criterion" / "specs" / "1"
    dry = dry_run(tmp_path, extra_env={
        "ATTAVO_STAGE": "verify", "ATTAVO_SPEC_DIR": str(spec_dir)})
    assert "stub" not in dry["instruction"].lower()
