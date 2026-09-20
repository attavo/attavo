#!/bin/sh
set -e
python3 - <<'PY'
import json
s = json.load(open("schema/drive-report.schema.json"))
assert s["additionalProperties"] is False and set(s["required"]) == set(s["properties"]), s["required"]
for state in ("waiting-on-approval", "running", "owes-recovery", "stopped", "next-build", "waiting-on-checks",
              "blocked-on-checks", "next-review", "ready-to-merge", "merged", "refused"):
    assert state in s["properties"]["state"]["enum"], state
assert s["properties"]["kind"]["enum"] == ["yours", "waits", "acts", "done"]
PY
grep -q '"DriveReport": "drive-report.schema.json"' tests/test_types_mirror_schemas.py
for t in test_report_names_a_red_check_and_the_head_and_writes_nothing test_report_never_waits_on_a_check_that_is_still_running test_report_walks_an_item_from_approval_to_ready test_report_says_what_recovery_would_record_for_a_dead_invocation test_report_names_the_stopped_record_its_class_its_counsel_and_its_issue test_report_reads_an_expired_approval_as_yours_and_says_why test_report_reads_a_live_invocation_inside_the_bound_as_running test_report_reads_a_live_invocation_past_the_bound_as_hung_and_ends_nothing test_report_reads_a_review_of_an_earlier_build_as_review_next test_report_does_not_read_a_failed_build_of_an_earlier_approval_as_stopped test_report_names_the_counsel_record_that_read_the_stop test_report_reads_a_merged_item_as_merged test_report_reads_every_item_this_repository_carries
do
  grep -q "^def $t(" tests/test_report.py
done
