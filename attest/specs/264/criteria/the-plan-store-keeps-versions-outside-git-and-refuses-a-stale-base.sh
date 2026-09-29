#!/bin/sh
set -e
here=$(pwd)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/attavo.XXXXXX")
unset ATTAVO_STORE
cp -R "$here/tests/fixtures/valid/." "$tmp/tenant"
cd "$tmp/tenant"
git init -q -b main .
git config user.email fixture@attavo.invalid
git config user.name "fixture"
git add -A
git commit -q -m install
git update-ref refs/remotes/origin/main "$(git rev-parse HEAD)"
git symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main
A="python3 $here/bin/attavo"
cat > "$tmp/plan.json" <<'JSON'
{"schema": "plan/1",
 "epics": [{"id": "E1", "title": "One", "issues": [3, 4]},
           {"id": "backlog", "title": "Backlog", "issues": [9]}],
 "edges": [{"type": "depends", "from": 3, "to": 4}],
 "policy": {}}
JSON
$A plan put "$tmp/plan.json" --base 0 --by alice > "$tmp/p1.out"
grep -q "plan version 1 written by alice" "$tmp/p1.out"
test -f "$(git rev-parse --git-common-dir)/attavo/store/plan/1.json"
$A plan rank "#4" --before "#3" --by bob > "$tmp/p2.out"
grep -q "plan version 2 written by bob" "$tmp/p2.out"
$A plan get > "$tmp/g.json" 2> "$tmp/g.err"
python3 -c 'import sys,json; b=json.load(open(sys.argv[1])); assert b["epics"][0]["issues"] == [4, 3], b' "$tmp/g.json"
grep -q "plan version 2 by bob" "$tmp/g.err"
if $A plan put "$tmp/plan.json" --base 1 --by carol 2> "$tmp/p3.err"; then exit 1; fi
grep -q "version 2" "$tmp/p3.err"
grep -q "bob" "$tmp/p3.err"
test "$($A plan log | wc -l | tr -d ' ')" = 2
test -z "$(git status --porcelain)"
cd "$here"
for t in test_a_put_from_the_latest_base_writes_the_next_version_and_get_and_log_read_it test_a_stale_base_is_refused_by_name_with_the_latest_version_and_its_author test_two_writers_from_one_base_leave_one_version_and_one_refusal test_a_writer_that_loses_the_link_race_is_refused_naming_the_winner test_a_body_the_check_refuses_and_a_missing_author_write_nothing test_the_store_is_under_the_git_common_dir_shared_by_a_worktree_and_never_staged test_attavo_store_overrides_the_location_and_a_directory_outside_git_is_refused test_a_document_name_that_is_not_a_name_is_refused
do
  grep -q "^def $t(" tests/test_store.py
done
for t in test_a_plan_passes_its_own_check_and_its_schema test_the_check_and_the_schema_refuse_the_same_bodies test_the_check_refuses_what_a_schema_cannot_say test_the_global_order_is_epic_rank_then_issue_rank test_rank_moves_one_epic_or_one_issue_within_its_epic_and_nothing_else test_move_places_an_issue_last_in_an_epic_and_takes_it_out_of_its_old_one test_the_command_puts_gets_ranks_and_refuses_a_stale_base_with_nothing_in_git test_the_command_refuses_a_bad_body_a_missing_author_and_an_empty_store
do
  grep -q "^def $t(" tests/test_plan.py
done
rm -rf "$tmp"
