#!/bin/sh
set -e
test -n "$(printf 'tests/test_this_file_was_deleted.py\n' | python3 .attavo/bin/select-tests)"
test "$(printf 'tests/test_report.py\n' | python3 .attavo/bin/select-tests)" = "tests/test_report.py"
for t in test_a_returned_review_carries_the_ratio_too
do
  grep -q "^def $t(" tests/test_stub_ratio.py
done
for t in test_build_removes_inherited_attavo_checks_command_from_executor_environment test_deleted_test_file_selects_the_whole_suite
do
  grep -q "^def $t(" tests/test_testtool.py
done
