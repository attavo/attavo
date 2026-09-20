#!/bin/sh
set -e
for t in test_doctor_rejects_a_subsecond_time_invocation_mismatch test_doctor_rejects_statement_whose_time_or_cost_breakdown_disagrees_with_records test_doctor_holds_a_true_statement_however_its_store_prints_it
do
  grep -q "^def $t(" tests/test_attest_time.py
done
! grep -q '_time_disagrees' attavo/doctor.py
! grep -q 'abs(stated - summed)' attavo/doctor.py
test "$(grep -c 'item_time_and_cost(' attavo/doctor.py)" = 1
