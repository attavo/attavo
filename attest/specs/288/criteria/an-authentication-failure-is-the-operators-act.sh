#!/bin/sh
set -e
here=$(pwd)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/attavo.XXXXXX")
printf '%s\n' '{"type": "system", "subtype": "init"}' '{"type": "assistant", "error": "authentication_failed", "is_api_error_message": true, "message": {"content": [{"type": "text", "text": "Failed to authenticate: OAuth session expired and could not be refreshed"}]}}' > "$tmp/auth.jsonl"
printf '%s\n' '{"type": "system", "subtype": "init"}' '{"type": "assistant", "error": "invalid_request", "message": {"content": [{"type": "text", "text": "Failed to authenticate: OAuth session expired and could not be refreshed"}]}}' > "$tmp/other.jsonl"
run() {
  ATTAVO_ADAPTER_STREAM_FILE="$1" ATTAVO_SPEC_DIR="$here/tests/fixtures/build_valid/specs/1" ATTAVO_RECORDS_DIR="$tmp/records" ATTAVO_RULINGS="$tmp/rulings.md" ATTAVO_BRANCH=attavo/1-example ATTAVO_ITERATION=1 ATTAVO_FEEDBACK="$tmp/feedback.txt" "$here/adapters/claude-code/run" > "$2" 2>/dev/null && echo 0 > "$2.rc" || echo $? > "$2.rc"
}
run "$tmp/auth.jsonl" "$tmp/auth.out"
run "$tmp/other.jsonl" "$tmp/other.out"
test "$(cat "$tmp/auth.out.rc")" = 1
test "$(cat "$tmp/other.out.rc")" = 1
python3 - "$tmp" <<'PY'
import json, sys
from pathlib import Path
tmp = Path(sys.argv[1])
last = json.loads((tmp / "auth.out").read_text().strip().splitlines()[-1])
assert last == {"class": "environment", "detail": "Failed to authenticate: OAuth session expired and could not be refreshed"}, last
assert '"class": "environment"' not in (tmp / "other.out").read_text()
PY
for t in test_claude_code_classes_an_authentication_failure_as_environment_from_the_field test_claude_code_does_not_class_other_stream_errors_or_matching_words_as_environment
do
  grep -q "^def $t(" tests/test_adapter_claude_code.py
done
rm -rf "$tmp"
