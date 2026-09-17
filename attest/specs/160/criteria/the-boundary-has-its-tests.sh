#!/bin/sh
grep -rq 'def test_claude_code_null_question_is_a_report_and_ends_as_non_question' tests/ && grep -rq 'def test_claude_code_ignores_a_stream_event_with_a_string_message' tests/
