#!/bin/sh
set -e
here=$(pwd)
tmp=$(mktemp -d)
printf '%s\n' '{"type": "result", "is_error": false, "structured_output": {"status": "question", "question": null}, "usage": {"input_tokens": 5, "output_tokens": 9}, "total_cost_usd": 0.01, "modelUsage": {"claude-sonnet-5": {"inputTokens": 5, "outputTokens": 9}}}' > "$tmp/stream.jsonl"
env -u ATTAVO_CHECKS_COMMAND ATTAVO_SPEC_DIR="$here/tests/fixtures/build_valid/specs/1" ATTAVO_RECORDS_DIR="$tmp/records" ATTAVO_RULINGS="$tmp/rulings.md" ATTAVO_BRANCH=attavo/1-example ATTAVO_ITERATION=1 ATTAVO_FEEDBACK="$tmp/feedback.txt" ATTAVO_ADAPTER_STREAM_FILE="$tmp/stream.jsonl" python3 adapters/claude-code/run > "$tmp/out.txt" 2> "$tmp/err.txt"
tail -1 "$tmp/out.txt" | python3 -c 'import sys,json; l=json.loads(sys.stdin.read()); assert l["status"] == "question" and l["class"] == "non-question", l; assert l["tokens"]["input"] > 0, l'
rm -rf "$tmp"
