#!/bin/sh
set -e
f=tests/test_attest_time.py
test -f "$f"
grep -q '^def test_attest_sums_every_invocation_per_stage_and_per_item' "$f"
grep -q '^def test_attest_names_a_record_without_wall_clock_and_never_sums_it_as_zero' "$f"
grep -q '^def test_attest_sums_cost_across_executors_and_says_when_the_total_is_partial' "$f"
grep -q '^def test_doctor_proof_six_fails_a_statement_whose_time_is_not_its_records' "$f"
python3 - <<'PY'
import json
from attavo import types
schema = json.load(open("schema/attestation.schema.json"))
assert set(types.AttestRecord.__annotations__) == set(schema["properties"])
assert {"time", "cost"} <= set(schema["required"])
PY
