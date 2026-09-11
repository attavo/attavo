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
import subprocess
import sys
from pathlib import Path

from helpers import FIXTURES

ROOT = Path(__file__).resolve().parent.parent
CODEX_RUN = ROOT / "adapters" / "codex" / "run"


def base_env(tmp_path: Path, config: dict | None = None) -> dict:
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


def run_codex(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run([str(CODEX_RUN)], capture_output=True, text=True,
                          check=False, env=env)


def dry_run(tmp_path: Path, config: dict | None = None,
           extra_env: dict | None = None) -> dict:
    env = base_env(tmp_path, config)
    env["ATTAVO_ADAPTER_DRY_RUN"] = "1"
    if extra_env:
        env.update(extra_env)
    run = run_codex(env)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def run_with_response(tmp_path: Path, events: list[dict], final, exit_code: int = 0,
                      config: dict | None = None, extra_env: dict | None = None
                      ) -> subprocess.CompletedProcess:
    response_file = tmp_path / "response.json"
    response_file.write_text(json.dumps({"events": events, "final": final, "exit_code": exit_code}))
    env = base_env(tmp_path, config)
    env["ATTAVO_ADAPTER_RESPONSE_FILE"] = str(response_file)
    if extra_env:
        env.update(extra_env)
    return run_codex(env)


DONE_USAGE = {"input_tokens": 15272, "cached_input_tokens": 12160,
             "cache_write_input_tokens": 0, "output_tokens": 5,
             "reasoning_output_tokens": 0}
DONE_EVENTS = [
    {"type": "thread.started", "thread_id": "t1"},
    {"type": "turn.started"},
    {"type": "turn.completed", "usage": DONE_USAGE},
]


def test_model_is_required(tmp_path):
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


def test_model_reported_is_exactly_the_configured_one_never_derived(tmp_path):
    """The report's model is [executor.codex].model verbatim -- there is
    nothing in a real response to derive it from, unlike claude-code's
    modelUsage breakdown."""
    run = run_with_response(tmp_path, DONE_EVENTS, {"status": "done", "question": None},
                            config={"model": "gpt-5.1-codex"})
    assert run.returncode == 0, run.stderr
    line = json.loads(run.stdout.splitlines()[-1])
    assert line["model"] == "gpt-5.1-codex"


def test_subscription_auth_refuses_with_an_api_key_in_the_environment(tmp_path):
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


def test_subscription_auth_is_the_default_and_proceeds_with_no_key(tmp_path):
    run = run_with_response(tmp_path, DONE_EVENTS, {"status": "done", "question": None},
                            config={"model": "gpt-test"})
    assert run.returncode == 0, run.stderr


def test_api_auth_refuses_without_an_api_key(tmp_path):
    env = base_env(tmp_path, config={"model": "gpt-test", "auth": "api"})
    run = run_codex(env)
    assert run.returncode == 1
    assert "api" in run.stderr
    assert "OPENAI_API_KEY" in run.stderr


def test_cost_usd_is_always_null(tmp_path):
    """No dollar figure appears anywhere codex reports under any auth
    mode (confirmed live 2026-09-08); a non-null cost on a codex record
    would be evidence the wrong credential was used, not a measurement."""
    run = run_with_response(tmp_path, DONE_EVENTS, {"status": "done", "question": None},
                            config={"model": "gpt-test"})
    assert run.returncode == 0, run.stderr
    line = json.loads(run.stdout.splitlines()[-1])
    assert line["cost_usd"] is None


def test_sandbox_is_workspace_write_for_build_and_read_only_for_verify(tmp_path):
    """verify's instruction says "make no edits, write no files, commit
    nothing"; the sandbox enforces it mechanically rather than this
    adapter trusting the instruction alone."""
    build = dry_run(tmp_path)
    assert build["argv"][build["argv"].index("--sandbox") + 1] == "workspace-write"

    verify = dry_run(tmp_path, extra_env={"ATTAVO_STAGE": "verify"})
    assert verify["argv"][verify["argv"].index("--sandbox") + 1] == "read-only"


def test_prompt_is_never_in_argv(tmp_path):
    """codex's `-p` is `--profile`, not a prompt flag -- confirmed live
    2026-09-08 -- so the instruction must never appear in argv at all;
    it goes in on stdin, which is why dry_run's own envelope carries it
    as a separate "instruction" key rather than inside "argv"."""
    dry = dry_run(tmp_path)
    assert "-p" not in dry["argv"]
    assert not any(dry["instruction"] in str(a) for a in dry["argv"])
    assert "-" in dry["argv"]


def test_model_flag_from_config(tmp_path):
    dry = dry_run(tmp_path, config={"model": "gpt-5.1-codex"})
    assert dry["argv"][dry["argv"].index("--model") + 1] == "gpt-5.1-codex"


def test_real_invocation_tolerates_tracing_lines_in_the_event_stream(tmp_path):
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


def test_verify_instruction_names_the_diffs_path_not_its_text(tmp_path):
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


def test_build_instruction_names_the_feedback_files_path_not_its_text(tmp_path):
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


def test_nonzero_exit_with_error_only_on_stderr_reaches_the_log(tmp_path):
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
