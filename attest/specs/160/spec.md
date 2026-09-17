## Problem

The claude-code adapter reads the executor's output as if its shapes were the adapter's to assume. Two failures on 2026-09-17, three and one specimens: an executor that ended its turn with `status: question, question: null` (a status update in the question channel, three times: #148 build-01, #151 build-01, #153 build-01) was classed `fail` by `question_class`, exit 1 with no report line, so each record is `no-cause / executor` with detail null, tokens 0, and ten minutes lost; and a stream line whose `message` was a string (#151 build-02) raised `AttributeError` inside `stream_line_events`, exit 1, `unclassed / product`, and 1064 s lost. `question_class` already has the class for the first (`report`, issue #88: a re-invocation with the nudge, then `non-question` with both results kept); the null question fell one branch short of it. The second is one field guarded of many.

Retires: `fail` as the class of an absent question; the adapter raising on any shape in the executor's stream.

## Claims

- adapters/claude-code/run
- tests/test_adapter_boundary.py
- tests/test_adapters.py
- tests/test_adapter_claude_code.py

## Criteria

- a-string-message-is-transcript-not-a-crash.sh: a recorded stream carrying a line whose message is a string, then a valid result, exits 0 with status done, keeps the line on stdout as transcript, and writes no event for it
- a-null-question-is-a-report.sh: a recorded stream whose result is status question with question null exits 0 with a report line of status question and class non-question carrying the tokens spent
- the-boundary-has-its-tests.sh: the two boundary tests exist by name under tests/
