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
mkdir -p attavo/adapters/fixture
printf '#!/bin/sh\necho built > built.txt\nprintf %s\\\\n %s\n' "'{\"status\":\"done\",\"question\":null,\"model\":\"fixture\",\"model_version\":null,\"adapter_version\":\"fixture-1\",\"tokens\":{\"input\":1,\"output\":1},\"cost_usd\":0.0}'" > attavo/adapters/fixture/run
chmod +x attavo/adapters/fixture/run
git add attavo/adapters/fixture/run
git commit -q -m "a tenant-side executor that writes the change and never commits it"
start=$(git rev-parse HEAD)
if $A build 1 > "$tmp/build.out" 2>&1; then exit 1; fi
test -f built.txt
test "$(git rev-parse HEAD)" = "$start"
python3 - <<'PY'
import json
r = json.load(open("records/1/build-01.json"))
assert r["status"] == "halted", r["status"]
assert r["sha"] == r["start_sha"], (r["sha"], r["start_sha"])
assert r["stop"]["class"] == "empty" and r["stop"]["provenance"] == "executor", r["stop"]
assert "the executor committed nothing" in r["stop"]["detail"], r["stop"]
assert all(c["exit"] != 0 for c in r["criteria"]), r["criteria"]
PY
grep -q "you committed nothing" records/1/build-01.log
grep -q "the gate reads the commit, not the tree" records/1/build-01.log
if $A verify 1 > "$tmp/verify.out" 2>&1; then exit 1; fi
grep -q "refused: latest build record is 'halted', not pass" "$tmp/verify.out"
test -z "$(ls records/1 | grep '^verify-')"
cd "$here"
rm -rf "$tmp"
