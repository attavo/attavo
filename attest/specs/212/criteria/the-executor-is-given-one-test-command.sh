#!/bin/sh
set -e
! grep -q 'ATTAVO_CHECKS_COMMAND' attavo/build.py adapters/claude-code/run adapters/codex/run
grep -q 'ATTAVO_TEST_COMMAND' attavo/build.py
grep -q 'ATTAVO_TEST_COMMAND' adapters/claude-code/run
grep -q 'ATTAVO_TEST_COMMAND' adapters/codex/run
python3 - <<'PY'
import json
s = json.load(open("schema/manifest.schema.json"))["properties"]
assert "probe" in s["checks"]["properties"] and s["checks"]["dependentRequired"] == {"probe": ["probe_timeout_s"]}, s["checks"]
assert "call_timeout_s" in s["tests"]["properties"], s["tests"]
PY
for t in test_build_runs_the_probe_and_not_the_whole_checks_before_the_executor test_build_without_a_probe_asks_the_old_way_and_says_so_in_the_feed test_a_failing_probe_refuses_the_build_as_an_environment_stop_before_the_executor test_a_probe_past_its_bound_is_ended_and_refuses_the_build test_the_manifest_schema_requires_a_bound_with_a_probe test_attavo_test_runs_what_the_tenant_selects_for_the_items_changes_committed_or_not test_attavo_test_exits_as_the_run_does_and_says_when_nothing_is_selected test_attavo_test_ends_a_run_past_its_bound_with_everything_it_started test_build_hands_the_executor_the_test_command_and_no_checks_command test_this_repositorys_selector_fails_safe_on_a_path_it_does_not_know test_this_repositorys_selector_maps_a_module_to_its_own_tests_and_prose_to_nothing test_every_test_file_the_selector_names_exists_and_every_module_is_mapped_or_falls_through test_this_repositorys_manifest_declares_the_probe_a_gate_without_the_suite_and_a_bound
do
  grep -q "^def $t(" tests/test_testtool.py
done
grep -q '^def test_situation_7_the_one_test_command_in_the_invocation(' tests/test_executor_conformance.py
