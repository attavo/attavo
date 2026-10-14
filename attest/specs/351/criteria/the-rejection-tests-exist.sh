#!/bin/sh
f=tests/test_spec_gate.py
test -f "$f" || exit 1
grep -q "^def test_a_behavioural_criterion_that_exits_other_than_1_on_both_is_refused_by_name(" "$f" || exit 1
grep -q "^def test_a_structural_criterion_that_exits_other_than_1_on_the_tree_is_refused_by_name(" "$f" || exit 1
exit 0
