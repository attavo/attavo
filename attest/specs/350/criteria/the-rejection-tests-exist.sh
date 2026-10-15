#!/bin/sh
f=tests/test_ledger.py
test -f "$f" || exit 1
grep -q "^def test_each_ledger_row_names_what_its_commit_carries(" "$f" || exit 1
grep -q "^def test_the_merge_row_says_not_read_when_the_store_does_not_answer(" "$f" || exit 1
exit 0
