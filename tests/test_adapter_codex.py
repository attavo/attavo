"""adapters/codex/run's event mapping (issue #138): with ATTAVO_EVENTS
set, the adapter maps its own item.completed events -- agent_message to
"message", command_execution to "tool_call" (the command's own first
line) and "tool_result" (its output's own first line) -- to the same
{at, kind, text} shape claude-code already writes (issue #26). Exercised
through the adapter's own canned-response hook,
ATTAVO_ADAPTER_RESPONSE_FILE, whose "events" list is exactly the
recorded JSONL transcript a live `codex exec --json` run would produce
-- codex needs no second, stream-only hook the way claude-code did,
since invoke() already parses "events" the same way whether canned or
live (tests/test_adapters_codex.py exercises the same mechanism for the
report-line derivation).

Rejection tests, written and run red first: on `main` before this issue
the codex adapter never checked ATTAVO_EVENTS at all.

Run: python3 -m pytest tests -q
"""
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from helpers import FIXTURES

ROOT = Path(__file__).resolve().parent.parent
CODEX_RUN = ROOT / "adapters" / "codex" / "run"

DONE_USAGE = {"input_tokens": 5, "output_tokens": 9, "cached_input_tokens": 0,
             "cache_write_input_tokens": 0, "reasoning_output_tokens": 0}

# The agent_message shape is confirmed live (issue #41, 2026-09-08,
# codex-cli 0.153.4) -- adapters/codex/conformance.json's own "done"
# situation carries this exact item. command_execution's field names
# (command, aggregated_output) are not independently confirmed live
# this session; see item_events's own docstring in adapters/codex/run.
RECORDED_STREAM: list[dict[str, Any]] = [
    {"type": "thread.started"},
    {"type": "turn.started"},
    {"type": "item.completed",
     "item": {"id": "item_0", "type": "agent_message", "text": "Reading the spec first."}},
    {"type": "item.completed",
     "item": {"id": "item_1", "type": "command_execution",
              "command": "cat spec.md", "aggregated_output": "## Problem\nsecond line\n",
              "exit_code": 0, "status": "completed"}},
    {"type": "turn.completed", "usage": DONE_USAGE},
]


def base_env(tmp_path: Path) -> dict[str, Any]:
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
        "ATTAVO_ADAPTER_CONFIG": json.dumps({"model": "test-model"}),
    })
    return env


def run_with_events(tmp_path: Path, events: list[dict[str, Any]], final: dict[str, Any],
                    events_env: bool = True) -> subprocess.CompletedProcess[str]:
    response = {"events": events, "final": final, "exit_code": 0}
    response_file = tmp_path / "response.json"
    response_file.write_text(json.dumps(response))
    env = base_env(tmp_path)
    env["ATTAVO_ADAPTER_RESPONSE_FILE"] = str(response_file)
    if events_env:
        env["ATTAVO_EVENTS"] = str(tmp_path / "events.jsonl")
    return subprocess.run([str(CODEX_RUN)], capture_output=True, text=True,
                          check=False, env=env)


def read_events(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "events.jsonl"
    if not path.is_file():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def test_codex_recorded_fixtures_command_execution_item_matches_tenant_zeros_field_names() -> None:
    """Issue #144, acceptance row 2. RECORDED_STREAM's own command_execution
    item is the live shape, not a guess: tenant zero's verify logs on
    attavo/code-factory (every item.completed line since #649) carry
    exactly these six keys on a command_execution item. A fixture edited
    by hand back to a guessed shape -- say, just command and
    aggregated_output -- fails this."""
    command_execution_item = RECORDED_STREAM[3]["item"]
    assert command_execution_item["type"] == "command_execution"
    assert set(command_execution_item) == {
        "aggregated_output", "command", "exit_code", "id", "status", "type",
    }


def test_codex_maps_a_recorded_stream_to_events_and_the_record_is_unchanged(tmp_path: Path) -> None:
    """Acceptance row 1. A recorded item.completed stream maps to
    message/tool_call/tool_result events, in order, each {at, kind,
    text}; the final report line is exactly what it would have been
    without ATTAVO_EVENTS set -- the record is unchanged.
    """
    final = {"status": "done", "question": None}
    run = run_with_events(tmp_path, RECORDED_STREAM, final)
    assert run.returncode == 0, run.stdout + run.stderr

    line = json.loads(run.stdout.splitlines()[-1])
    assert line["status"] == "done"
    assert line["model"] == "test-model"
    assert line["tokens"] == {"input": 5, "output": 9}

    events = read_events(tmp_path)
    assert [e["kind"] for e in events] == ["message", "tool_call", "tool_result"]
    assert all(set(e) == {"at", "kind", "text"} for e in events)
    assert events[0]["text"] == "Reading the spec first."
    assert events[1]["text"] == "cat spec.md"
    assert events[2]["text"] == "## Problem"

    run_no_events = run_with_events(tmp_path, RECORDED_STREAM, final, events_env=False)
    assert run_no_events.returncode == 0, run_no_events.stdout + run_no_events.stderr
    line_no_events = json.loads(run_no_events.stdout.splitlines()[-1])
    assert line_no_events == line


def test_codex_no_events_file_when_atavo_events_is_unset(tmp_path: Path) -> None:
    """With ATTAVO_EVENTS unset, no events file is written at all -- the
    mapping only ever writes to a path the product handed it."""
    run = run_with_events(tmp_path, RECORDED_STREAM, {"status": "done", "question": None},
                          events_env=False)
    assert run.returncode == 0, run.stdout + run.stderr
    assert not (tmp_path / "events.jsonl").exists()


def test_codex_tool_call_event_is_capped_at_400_chars(tmp_path: Path) -> None:
    """text is capped at 400 characters and is one line: a command
    whose own first line is long is still capped -- the feed renders
    lines, not files."""
    long_command = "x" * 500
    stream = [
        {"type": "item.completed", "item": {"id": "item_0", "type": "command_execution",
         "command": long_command, "aggregated_output": "ok\n", "exit_code": 0}},
        {"type": "turn.completed", "usage": DONE_USAGE},
    ]
    run = run_with_events(tmp_path, stream, {"status": "done", "question": None})
    assert run.returncode == 0, run.stdout + run.stderr

    events = read_events(tmp_path)
    tool_call = next(e for e in events if e["kind"] == "tool_call")
    assert len(tool_call["text"]) == 400
    assert "\n" not in tool_call["text"]


def test_codex_events_are_one_line_across_multiline_command_and_output(tmp_path: Path) -> None:
    """A multi-line command and a multi-line output each map to just
    their own first line."""
    stream = [
        {"type": "item.completed", "item": {"id": "item_0", "type": "command_execution",
         "command": "cat spec.md\nsecond line", "aggregated_output": "line one\nline two\n",
         "exit_code": 0}},
        {"type": "turn.completed", "usage": DONE_USAGE},
    ]
    run = run_with_events(tmp_path, stream, {"status": "done", "question": None})
    assert run.returncode == 0, run.stdout + run.stderr

    events = read_events(tmp_path)
    tool_call = next(e for e in events if e["kind"] == "tool_call")
    tool_result = next(e for e in events if e["kind"] == "tool_result")
    assert tool_call["text"] == "cat spec.md"
    assert tool_result["text"] == "line one"
