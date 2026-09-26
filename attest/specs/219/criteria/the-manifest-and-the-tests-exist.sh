#!/bin/sh
set -e
here=$(pwd)
export ATTAVO_FIXTURE_PRODUCT="$here"
tmp=$(mktemp -d "${TMPDIR:-/tmp}/attavo.XXXXXX")
cp -R "$here/tests/fixtures/build_valid/." "$tmp/tenant"
cd "$tmp/tenant"
git init -q -b main .
git config user.email fixture@attavo.invalid
git config user.name "fixture"
mkdir -p attavo/adapters/fixture
cp "$here/adapters/fixture/run" attavo/adapters/fixture/run
cat >> attavo.toml <<'TOML'

[forge]
checks_command = "printf 'ci success\\n'"

[drive]
stall_after_s = 60
attempts = 3
retry_after_s = 1

[drive.build]
ceiling_s = 600

[drive.verify]
ceiling_s = 600
TOML
chmod +x attavo/adapters/fixture/run
git add -A
git commit -q -m install
git init -q --bare -b main "$tmp/origin.git"
git remote add origin "$tmp/origin.git"
git push -q origin HEAD:main 2>/dev/null
git update-ref refs/remotes/origin/main "$(git rev-parse HEAD)"
git symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main
export ATTAVO_HEARTBEAT_S=1
A="python3 $here/bin/attavo"
$A spec-gate 1 >/dev/null
$A approve 1 --by alice >/dev/null
git add -A
git commit -q -m approved
git push -q origin HEAD:main 2>/dev/null
wait_on_origin() {
  i=0
  until git -C "$tmp/origin.git" cat-file -e "main:$1" 2>/dev/null
  do
    i=$((i+1))
    test "$i" -lt "$2"
    sleep 1
  done
}
pass_until_on_origin() {
  i=0
  until git -C "$tmp/origin.git" cat-file -e "main:$1" 2>/dev/null
  do
    i=$((i+1))
    test "$i" -lt "$2"
    $A drive 1 >"$tmp/pass.out" 2>&1 || true
    sleep 1
  done
}
show() {
  git -C "$tmp/origin.git" show "main:$1"
}
python3 - <<'PY'
from pathlib import Path
p = Path("attavo.toml")
s = p.read_text()
s = s.replace("\n[drive.build]\nceiling_s = 600\n", "").replace("\n[drive.verify]\nceiling_s = 600\n", "")
p.write_text(s)
PY
git add -A
git commit -q -m "a drive table without ceilings"
if $A doctor . >"$tmp/doctor.out" 2>&1; then exit 1; fi
grep -q "ceiling" "$tmp/doctor.out"
cd "$here"
for t in test_a_transient_stop_retries_plainly_after_the_wait test_died_and_hung_retry_on_the_next_pass test_a_red_check_becomes_a_returned_record_with_the_log_as_evidence test_a_returned_review_retries_with_its_findings_and_counsels_on_the_second test_counsels_verdict_routes_the_next_pass test_a_persons_stop_is_never_retried test_identical_failures_stop_early_and_the_budget_holds test_every_marker_spends_the_budget test_a_stage_past_its_ceiling_is_hung_by_the_ceiling test_again_resets_a_spent_budget_only test_adapters_class_transient
do
  grep -q "^def $t(" tests/test_retry.py
done
grep -q "^def test_report_compares_attempts_at_exhaustion(" tests/test_report.py
grep -q "^def test_doctor_refuses_a_drive_table_without_ceilings(" tests/test_doctor.py
rm -rf "$tmp"
