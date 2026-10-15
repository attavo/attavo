#!/bin/sh
ok=0
test -f tests/test_plan.py && grep -q "^def test_a_plan_claims_write_calls_now_iso_exactly_once(" tests/test_plan.py || ok=1
test -f tests/test_store.py && grep -q "^def test_put_stamps_its_own_at_when_the_caller_passes_none(" tests/test_store.py || ok=1
test -f tests/test_spec_gate.py && grep -q "^def test_a_structural_criterion_that_exits_2_on_the_stub_is_refused_as_could_not_check(" tests/test_spec_gate.py || ok=1
test -f tests/test_types_mirror_schemas.py && grep -q "^def test_the_intervention_class_type_mirrors_the_schemas_enum(" tests/test_types_mirror_schemas.py || ok=1
exit $ok
