#!/bin/sh
set -e
for t in test_doctor_rejects_null_time_without_cost test_doctor_rejects_large_integer_that_rounds_to_summed_float
do
  grep -q "^def $t(" tests/test_attest_time.py
done
for t in test_stub_ratio_is_one_when_every_added_line_is_the_stubs test_stub_ratio_is_the_share_across_files_and_ignores_blank_lines test_stub_ratio_matches_a_line_only_against_the_stubs_file_at_the_same_path test_stub_ratio_does_not_count_the_specs_or_the_records_directory test_stub_ratio_is_null_with_no_stub_or_no_added_line_and_never_zero test_verify_records_the_ratio_and_no_longer_carries_the_two_fields_nothing_computed
do
  grep -q "^def $t(" tests/test_stub_ratio.py
done
! grep -rqE 'weak_stub|"mutation"' attavo/verify.py attavo/types.py schema/records/verify.schema.json
