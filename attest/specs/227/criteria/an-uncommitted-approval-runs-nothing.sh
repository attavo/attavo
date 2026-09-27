#!/bin/sh
set -e
here=$(pwd)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/attavo.XXXXXX")
cp -R "$here/tests/fixtures/build_valid/." "$tmp/tenant"
cd "$tmp/tenant"
git init -q -b main .
git config user.email fixture@attavo.invalid
git config user.name "fixture"
git add -A
git commit -q -m install
git init -q --bare -b main "$tmp/origin.git"
git remote add origin "$tmp/origin.git"
git push -q origin HEAD:main 2>/dev/null
git update-ref refs/remotes/origin/main "$(git rev-parse HEAD)"
git symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main
git checkout -q -b attavo/1-x
git push -q -u origin attavo/1-x 2>/dev/null
A="python3 $here/bin/attavo"
$A spec-gate 1 > "$tmp/gate.out"
$A approve 1 --by alice > "$tmp/approve.out"
test -f records/1/approval-01.json
if $A approval-status 1 > "$tmp/status.out"; then exit 1; fi
test "$(cat "$tmp/status.out")" = uncommitted
before=$(git rev-parse HEAD)
if $A build 1 > "$tmp/build.out" 2>&1; then exit 1; fi
grep -q "refused: approval is uncommitted" "$tmp/build.out"
test -z "$(ls records/1 | grep '^build-')"
$A drive 1 --report > "$tmp/report.json"
python3 - "$tmp/report.json" <<'PY'
import sys, json
r = json.load(open(sys.argv[1]))
assert r["state"] == "waiting-on-approval" and r["kind"] == "yours", r
assert "not committed" in r["says"] and "approval-NN.json" in r["says"], r["says"]
PY
$A drive 1 > "$tmp/drive.out" 2>&1
test "$(git rev-parse HEAD)" = "$before"
test -z "$(ls records/1 | grep '^build-')"
git add records/1/approval-01.json
git commit -q --only records/1/approval-01.json -m "attavo: approval record for item 1"
$A approval-status 1 > "$tmp/status2.out"
test "$(cat "$tmp/status2.out")" = valid
$A drive 1 --report > "$tmp/report2.json"
python3 -c 'import sys,json; r=json.load(open(sys.argv[1])); assert r["state"] == "next-build", r' "$tmp/report2.json"
cd "$here"
for t in test_approval_status_reads_uncommitted_until_the_record_is_in_head test_build_refuses_an_uncommitted_approval_and_writes_no_record test_approve_says_the_record_must_be_committed
do
  grep -q "^def $t(" tests/test_approve.py
done
grep -q "^def test_a_pass_starts_nothing_on_an_uncommitted_approval_and_the_report_says_what_to_commit(" tests/test_report.py
grep -q "^def commit_approval(" tests/helpers.py
rm -rf "$tmp"
