## Problem

Class: core

Root-cause: classification-by-reading-prose

On #267, build-01 and build-02 each ended in two seconds on the Claude Code CLI's own "Failed to authenticate: OAuth session expired and could not be refreshed". The executor's sign-in had expired. The CLI says so as a structured field on its stream line, `"error": "authentication_failed"` (with `"is_api_error_message": true`), then exits non-zero with no result line. The adapter returns that exit code and prints no report line, so the build record's stop is `unclassed`, provenance `product`, detail `[adapter exited 1]`. Counsel was asked about it twice and file-stops filed an issue each time, for a failure only the operator can fix by signing in again.

Root cause 5's invariant is that a stop's class comes from structured output, never from the transcript's words. The structure was there; the adapter did not read it. This item reads it: when any stream line carries `"error": "authentication_failed"`, the adapter prints the report line `{"class": "environment", "detail": <that line's own text>}` and exits 1, as its transient path already does for a vendor's capacity refusal. `derive_stop` already maps a report-line class to that class with provenance `executor`, and `environment` is already a stop class the retry rule never retries, so the record reads `environment` and nothing else in the product changes. The field decides, never the words: another error value, or the same words with no field, is not this.

Not here: counsel and file-stops still run on an `environment` stop (the drive's rule; `attavo/drive.py` is held by the re-entry run), and counsel's own invocation will fail the same way until the operator signs in. Changing that is a drive item.

The stub under `stub/` is the change: `auth_failure` and its call in `invoke_once` in `adapters/claude-code/run`, and two tests in `tests/test_adapter_claude_code.py`, red on `7a5cbfb`.

Retires: `unclassed/product` as the record of an expired sign-in; counsel runs and filed issues for a failure only the operator can fix.

## Claims

- adapters/claude-code/run
- tests/test_adapter_claude_code.py

## Criteria

- an-authentication-failure-is-the-operators-act.sh: a recorded stream carrying `"error": "authentication_failed"` makes the adapter exit 1 with the last stdout line `{"class": "environment", "detail": "Failed to authenticate: OAuth session expired and could not be refreshed"}`; the same words with `"error": "invalid_request"` print no environment line; and the two tests named exist
