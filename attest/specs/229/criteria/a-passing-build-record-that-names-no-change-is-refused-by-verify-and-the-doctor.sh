#!/bin/sh
set -e
here=$(pwd)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/attavo.XXXXXX")
cp -R "$here/tests/fixtures/build_valid/." "$tmp/tenant"
cd "$tmp/tenant"
git init -q -b main .
git config user.email fixture@attavo.invalid
git config user.name "fixture"
printf '\n[forge]\nchecks_command = "printf '"'"'ci success\\\\n'"'"'"\n' >> attavo.toml
git add -A
git commit -q -m install
git init -q --bare -b main "$tmp/origin.git"
git remote add origin "$tmp/origin.git"
git push -q origin HEAD:main 2>/dev/null
git update-ref refs/remotes/origin/main "$(git rev-parse HEAD)"
git symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main
git checkout -q -b attavo/1-x
A="python3 $here/bin/attavo"
$A spec-gate 1 > "$tmp/gate.out"
$A approve 1 --by alice > "$tmp/approve.out"
git add records/1/approval-01.json
git commit -q --only records/1/approval-01.json -m "attavo: approval record for item 1"
$A build 1 > "$tmp/build.out"
python3 - <<'PY'
import json
p = "records/1/build-01.json"
r = json.load(open(p))
assert r["status"] == "pass" and r["sha"] != r["start_sha"], r
r["sha"] = r["start_sha"]
open(p, "w").write(json.dumps(r, indent=2) + "\n")
PY
if $A verify 1 > "$tmp/verify.out" 2>&1; then exit 1; fi
grep -q "names no change" "$tmp/verify.out"
grep -q "sha equals start_sha" "$tmp/verify.out"
test -z "$(ls records/1 | grep '^verify-')"
printf '{"status": "pass", "sha": "v1", "build_sha": "%s"}\n' "$(python3 -c 'import json; print(json.load(open("records/1/build-01.json"))["sha"])')" > records/1/verify-01.json
git add -A
git commit -q -m "records"
git checkout -q main
git merge -q --no-ff -m "Merge branch 'attavo/1-x'" attavo/1-x
merge=$(git rev-parse HEAD)
$A doctor --json . > "$tmp/doctor.json" || true
python3 - "$tmp/doctor.json" "$merge" <<'PY'
import sys, json
d = json.load(open(sys.argv[1]))
p = [x for x in d["proofs"] if x["proof"] == "no-attested-item-is-empty"]
assert p, [x["proof"] for x in d["proofs"]]
assert not p[0]["holds"], p[0]
assert "nothing was built" in p[0]["reason"] and sys.argv[2][:12] in p[0]["reason"], p[0]["reason"]
PY
printf '\n[doctor]\nbuilt_since = "%s"\n' "$merge" >> attavo.toml
git add attavo.toml
git commit -q -m "window after the specimen"
$A doctor --json . > "$tmp/doctor2.json" || true
python3 - "$tmp/doctor2.json" <<'PY'
import sys, json
d = json.load(open(sys.argv[1]))
p = [x for x in d["proofs"] if x["proof"] == "no-attested-item-is-empty"][0]
assert p["holds"], p
assert p["before_window"] == ["1"], p
PY
cd "$here"
python3 -c 'import tomllib; m=tomllib.load(open("attavo.toml","rb")); assert m["doctor"]["built_since"], m'
grep -q "^def test_the_gate_reads_the_commit_and_an_executor_that_commits_nothing_is_halted_empty(" tests/test_build.py
grep -q "^def test_an_empty_iteration_is_fed_back_and_a_later_commit_passes(" tests/test_build.py
grep -q "^def test_verify_refuses_a_passing_build_record_that_names_no_change(" tests/test_verify.py
grep -q "^def test_doctor_proof_seven_fails_on_an_attested_item_whose_build_committed_nothing(" tests/test_doctor.py
grep -q "^def test_doctor_proof_seven_holds_on_a_build_that_names_a_change_and_names_the_specimen_before_its_window(" tests/test_doctor.py
grep -q "^def test_no_attested_item_is_empty(" tests/test_doctor.py
grep -q "^def test_help_names_the_seventh_proof_and_the_empty_stop(" tests/test_cli_help.py
cd "$here"
rm -rf "$tmp"
