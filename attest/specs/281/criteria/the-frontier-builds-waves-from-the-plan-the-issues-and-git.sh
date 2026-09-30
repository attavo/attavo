#!/bin/sh
set -e
here=$(pwd)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/attavo.XXXXXX")
unset ATTAVO_STORE
cp -R "$here/tests/fixtures/valid/." "$tmp/tenant"
cat > "$tmp/issues.json" <<'JSON'
[{"number": 1, "title": "one", "state": "open", "closed_at": null, "labels": [], "body": "Class: core\n\n## Claims\n\n- x/one.py\n"},
 {"number": 2, "title": "two", "state": "open", "closed_at": null, "labels": [], "body": "Class: core\n\n## Claims\n\n- shared.py\n"},
 {"number": 3, "title": "three", "state": "open", "closed_at": null, "labels": [], "body": "Class: fix\n\n## Claims\n\n- shared.py\n"},
 {"number": 4, "title": "four", "state": "open", "closed_at": null, "labels": [], "body": "Class: fix\n\n## Claims\n\n- y/four.py\n"},
 {"number": 5, "title": "five", "state": "closed", "closed_at": "2026-09-29T00:00:00Z", "labels": [], "body": ""},
 {"number": 6, "title": "six", "state": "open", "closed_at": null, "labels": [], "body": "no claims yet"}]
JSON
printf 'import sys\nsys.stdout.write(open(sys.argv[1]).read())\n' > "$tmp/cmd.py"
cd "$tmp/tenant"
printf '\n[forge]\nissues_command = "python3 %s %s"\n' "$tmp/cmd.py" "$tmp/issues.json" >> attavo.toml
git init -q -b main .
git config user.email fixture@attavo.invalid
git config user.name "fixture"
git add -A
git commit -q -m install
git update-ref refs/remotes/origin/main "$(git rev-parse HEAD)"
git symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main
A="python3 $here/bin/attavo"
if $A frontier --json 2> "$tmp/nr.err"; then exit 1; fi
grep -q "not read: no plan in the store" "$tmp/nr.err"
cat > "$tmp/plan.json" <<'JSON'
{"schema": "plan/1",
 "epics": [{"id": "E1", "title": "One", "issues": [1, 2, 5]},
           {"id": "backlog", "title": "Backlog", "issues": [3, 4]}],
 "edges": [{"type": "depends", "from": 1, "to": 2}],
 "policy": {}}
JSON
$A plan put "$tmp/plan.json" --base 0 --by alice > /dev/null
git checkout -q -b lane/9-other
mkdir -p y
printf 'z\n' > y/four.py
git add -A
git commit -q -m other
git update-ref refs/remotes/origin/lane/9-other "$(git rev-parse HEAD)"
git checkout -q main
$A frontier --json > "$tmp/f.json"
python3 - "$tmp/f.json" <<'PY'
import json, sys
f = json.load(open(sys.argv[1]))
waves = [[(x["issue"], x.get("branch")) for x in w] for w in f["waves"]]
assert waves[0] == [(None, "lane/9-other")], waves
w = {x["issue"]: k for k, wave in enumerate(f["waves"]) for x in wave if x["issue"]}
assert (w[1], w[2], w[3], w[4]) == (1, 2, 3, 2), w
why3 = next(x["why"] for wave in f["waves"] for x in wave if x["issue"] == 3)
assert "yields shared.py to #2, ranked above" in why3, why3
why4 = next(x["why"] for wave in f["waves"] for x in wave if x["issue"] == 4)
assert why4 == ["waits on branch lane/9-other: y/four.py"], why4
assert [x["issue"] for x in f["not_placed"]] == [6], f["not_placed"]
assert [(e["id"], e["closed"], e["total"]) for e in f["epics"]] == [("E1", 1, 3), ("backlog", 0, 3)], f["epics"]
assert f["plan_version"] == 1 and "epic rank" in f["order"]
PY
test -z "$(git status --porcelain)"
cd "$here"
for t in test_order_is_epic_rank_then_issue_rank_and_an_unplaced_open_issue_is_backlog_last test_an_issue_with_an_open_predecessor_waits_and_a_closed_one_does_not test_two_issues_whose_claims_overlap_never_share_a_wave_and_the_lower_ranked_one_waits test_strict_opportunism_a_lower_ranked_issue_never_takes_a_path_a_higher_ranked_one_wants test_an_issue_overlapping_a_branch_in_flight_is_not_in_wave_one_and_says_which_branch test_an_issue_with_no_claims_is_not_placed_and_its_dependents_say_why test_epic_progress_is_closed_over_total_from_the_forge test_the_command_reads_the_store_the_issues_and_git_and_reports_the_waves test_the_command_is_not_read_without_a_plan_or_an_issues_command_and_refuses_without_origin_head
do
  grep -q "^def $t(" tests/test_frontier.py
done
grep -q "^def test_the_class_is_read_from_the_body_line_or_a_single_label_and_is_none_otherwise(" tests/test_issues.py
rm -rf "$tmp"
