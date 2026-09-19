"""adapters/claude-code/run at its boundary with the executor's output:
nothing on the stream makes the adapter raise, and an absent question is
a report, never an exit with no report line (attavo/attavo#150 and the
#151 build-02 crash, 2026-09-17)."""
import json
from pathlib import Path

from test_adapter_claude_code import RESULT_LINE, read_events, run_with_stream


def test_claude_code_ignores_a_stream_event_with_a_string_message(tmp_path: Path) -> None:
    diagnostic = {"type": "rate_limit_event", "message": "rate limit status"}
    run = run_with_stream(tmp_path, [diagnostic, RESULT_LINE])
    assert run.returncode == 0, run.stdout + run.stderr
    assert json.dumps(diagnostic) in run.stdout
    assert json.loads(run.stdout.splitlines()[-1])["status"] == "done"
    assert read_events(tmp_path) == []


def test_claude_code_ignores_a_stream_event_whose_content_is_not_a_list(tmp_path: Path) -> None:
    odd = {"type": "assistant", "message": {"content": "a bare string"}}
    run = run_with_stream(tmp_path, [odd, RESULT_LINE])
    assert run.returncode == 0, run.stdout + run.stderr
    assert json.loads(run.stdout.splitlines()[-1])["status"] == "done"
    assert read_events(tmp_path) == []


def test_claude_code_null_question_is_a_report_and_ends_as_non_question(tmp_path: Path) -> None:
    null_question = dict(RESULT_LINE, structured_output={"status": "question", "question": None})
    run = run_with_stream(tmp_path, [null_question])
    assert run.returncode == 0, run.stdout + run.stderr
    line = json.loads(run.stdout.splitlines()[-1])
    assert line["status"] == "question"
    assert line["class"] == "non-question"
    assert line["tokens"]["input"] > 0
