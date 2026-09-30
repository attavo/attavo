"""adapters/claude-code/run's stream-json mapping (issue #26): a real
invocation now speaks `--output-format stream-json --verbose`, read line
by line as it runs rather than buffered until the process exits.
ATTAVO_ADAPTER_STREAM_FILE feeds a recorded transcript (one JSONL line
per entry, the shape a real invocation's stdout would carry) through the
same mapping and terminal "result" read a live invocation gets, without
running `claude` -- distinct from ATTAVO_ADAPTER_RESPONSE_FILE
(tests/test_adapters.py), which still feeds the final report object
directly and is untouched by this lane.

Rejection tests, written and run red first: on `main` before this issue
`--output-format` was "json", a single buffered object, and
ATTAVO_ADAPTER_STREAM_FILE did not exist.

Run: python3 -m pytest tests -q
"""
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from helpers import FIXTURES

ROOT = Path(__file__).resolve().parent.parent
CLAUDE_CODE_RUN = ROOT / "adapters" / "claude-code" / "run"

RESULT_LINE = {
    "type": "result",
    "is_error": False,
    "structured_output": {"status": "done", "question": None},
    "usage": {"input_tokens": 5, "output_tokens": 9},
    "total_cost_usd": 0.01,
    "modelUsage": {"claude-sonnet-5": {"inputTokens": 5, "outputTokens": 9}},
}

STREAM_LINES: list[dict[str, Any]] = [
    {"type": "system", "subtype": "init"},
    {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Reading the spec before I start."}]}},
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Read", "input": {"file_path": "spec.md"}}]}},
    {"type": "user", "message": {"content": [
        {"type": "tool_result", "content": "## Problem\nsecond line\n"}]}},
    RESULT_LINE,
]


def base_env(tmp_path: Path) -> dict[str, Any]:
    spec_dir = FIXTURES / "build_valid" / "specs" / "1"
    env = dict(os.environ)
    env.pop("ATTAVO_TEST_COMMAND", None)
    env.pop("ATTAVO_TEST_TIMEOUT_S", None)
    env.update({
        "ATTAVO_SPEC_DIR": str(spec_dir),
        "ATTAVO_RECORDS_DIR": str(tmp_path / "records"),
        "ATTAVO_RULINGS": str(tmp_path / "rulings.md"),
        "ATTAVO_BRANCH": "attavo/1-example",
        "ATTAVO_ITERATION": "1",
        "ATTAVO_FEEDBACK": str(tmp_path / "feedback.txt"),
    })
    return env


def run_with_stream(tmp_path: Path, lines: list[dict[str, Any]], events: bool = True,
                    extra_env: dict[str, Any] | None = None) -> subprocess.CompletedProcess[str]:
    stream_file = tmp_path / "stream.jsonl"
    stream_file.write_text("\n".join(json.dumps(l) for l in lines) + "\n")
    env = base_env(tmp_path)
    env["ATTAVO_ADAPTER_STREAM_FILE"] = str(stream_file)
    if events:
        env["ATTAVO_EVENTS"] = str(tmp_path / "events.jsonl")
    if extra_env:
        env.update(extra_env)
    return subprocess.run([str(CLAUDE_CODE_RUN)], capture_output=True, text=True,
                          check=False, env=env)


def read_events(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "events.jsonl"
    if not path.is_file():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def test_claude_code_maps_a_recorded_stream_to_events_and_the_same_report_fields(tmp_path: Path) -> None:
    """Acceptance row 3. A recorded stream-json transcript maps to one
    event per assistant text block, tool_use block, and tool_result
    block, in order, each {at, kind, text}; the terminal "result" object
    feeds the same model/tokens/cost derivation the old --output-format
    json response did -- the record is byte-for-byte what it was.
    """
    run = run_with_stream(tmp_path, STREAM_LINES)
    assert run.returncode == 0, run.stdout + run.stderr

    line = json.loads(run.stdout.splitlines()[-1])
    assert line["model"] == "claude-sonnet-5"
    assert line["tokens"] == {"input": 5, "output": 9}
    assert line["cost_usd"] == 0.01
    assert line["status"] == "done"

    events = read_events(tmp_path)
    assert [e["kind"] for e in events] == ["message", "tool_call", "tool_result"]
    assert all(set(e) == {"at", "kind", "text"} for e in events)
    assert events[0]["text"] == "Reading the spec before I start."
    assert events[1]["text"] == "Read spec.md"
    assert events[2]["text"] == "## Problem"


def test_claude_code_stream_transcript_still_reaches_stdout(tmp_path: Path) -> None:
    """Every raw line the stream carried, mapped or not, still reaches
    stdout (and so build-NN.log) -- issue #34's guarantee, unbroken by
    the switch to reading the stream line by line."""
    run = run_with_stream(tmp_path, STREAM_LINES)
    assert run.returncode == 0, run.stdout + run.stderr
    for raw_line in STREAM_LINES:
        assert json.dumps(raw_line) in run.stdout


def test_claude_code_tool_call_event_is_one_line_and_capped_at_400_chars(tmp_path: Path) -> None:
    """Acceptance row 4. A tool's argument can be a whole file: the
    mapped event text is collapsed to one line and capped at 400
    characters, the feed renders lines."""
    long_arg = "line one\nline two\n" + ("x" * 500)
    stream: list[dict[str, Any]] = [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": long_arg}}]}},
        RESULT_LINE,
    ]
    run = run_with_stream(tmp_path, stream)
    assert run.returncode == 0, run.stdout + run.stderr

    events = read_events(tmp_path)
    assert len(events) == 1
    text = events[0]["text"]
    assert "\n" not in text
    assert len(text) <= 400


def test_claude_code_no_events_file_when_atavo_events_is_unset(tmp_path: Path) -> None:
    """An adapter invocation with no ATTAVO_EVENTS writes no events file
    -- the mapping only ever writes to a path the product handed it."""
    run = run_with_stream(tmp_path, STREAM_LINES, events=False)
    assert run.returncode == 0, run.stdout + run.stderr
    assert not (tmp_path / "events.jsonl").exists()


def test_claude_code_stream_missing_result_line_fails(tmp_path: Path) -> None:
    """A 0-exit stream that never carries a terminal "result" line is a
    failure, the same class build already treats a tool error or an
    absent structured_output as -- not a crash, and not a silent pass."""
    stream = [{"type": "assistant", "message": {"content": [
        {"type": "text", "text": "still working"}]}}]
    run = run_with_stream(tmp_path, stream)
    assert run.returncode == 1


AUTH_STREAM: list[dict[str, Any]] = [
    {"type": "system", "subtype": "init"},
    {"type": "assistant", "error": "authentication_failed", "is_api_error_message": True,
     "message": {"content": [{"type": "text",
                              "text": "Failed to authenticate: OAuth session expired and could not be refreshed"}]}},
]


def test_claude_code_classes_an_authentication_failure_as_environment_from_the_field(tmp_path: Path) -> None:
    """#267 build-01 and build-02: the CLI's own "error":
    "authentication_failed" field makes the report line's class
    environment (the operator's act, never retried), with the CLI's own
    words as the detail -- read from the field, not the text."""
    run = run_with_stream(tmp_path, AUTH_STREAM)
    assert run.returncode == 1
    last = json.loads(run.stdout.strip().splitlines()[-1])
    assert last == {"class": "environment",
                    "detail": "Failed to authenticate: OAuth session expired and could not be refreshed"}


def test_claude_code_does_not_class_other_stream_errors_or_matching_words_as_environment(tmp_path: Path) -> None:
    """Another error value, or the same words with no error field, is not
    an authentication failure: no environment report line."""
    other = [dict(AUTH_STREAM[0]), {**AUTH_STREAM[1], "error": "invalid_request"}]
    words = [dict(AUTH_STREAM[0]), {k: v for k, v in AUTH_STREAM[1].items() if k != "error"}]
    for i, lines in enumerate((other, words)):
        sub = tmp_path / str(i)
        sub.mkdir()
        run = run_with_stream(sub, lines)
        assert run.returncode == 1
        assert '"class": "environment"' not in run.stdout
