#!/bin/sh
f=tests/test_attest_interventions.py
test -f "$f" || exit 1
grep -q "^def test_doctor_proof_six_rejects_a_v6_statement_without_time_or_cost_whose_acts_or_interventions_differ(" "$f" || exit 1
grep -q "^def test_v6_schema_rejects_an_intervention_class_outside_amend_launch_transport(" "$f" || exit 1
exit 0
