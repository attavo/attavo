#!/bin/sh
f=tests/test_plan.py
test -f "$f" || exit 1
grep -q "^def test_a_plan_write_stamps_the_claim_and_the_version_from_one_clock_read(" "$f" || exit 1
exit 0
