#!/bin/sh
set -e
here=$(pwd)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/attavo.XXXXXX")
cp -R "$here/tests/fixtures/build_valid/." "$tmp/tenant"
cd "$tmp/tenant"
git init -q -b main .
git config user.email fixture@attavo.invalid
git config user.name "fixture"
python3 - <<'PY'
from pathlib import Path
p = Path("attavo.toml")
s = p.read_text().replace('[executor]\nbuild = "fixture"', '[executor]\nbuild = "fixture"\ncounsel = "fixture"')
s += '\n[forge]\nchecks_command = "printf \'ci success\\\\n\'"\n'
p.write_text(s)
PY
git add -A
git commit -q -m install
git init -q --bare -b main "$tmp/origin.git"
git remote add origin "$tmp/origin.git"
git push -q origin HEAD:main 2>/dev/null
git update-ref refs/remotes/origin/main "$(git rev-parse HEAD)"
git symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main
git checkout -q -b attavo/1-x
A="python3 $here/bin/attavo"
printf '{"model":"fixture","model_version":null,"adapter_version":"fixture-1","tokens":{"input":1,"output":1},"cost_usd":0.0,"class":"unclassed","verdict":"answer","citations":[],"proposal":{"ruling_text":"continue from the existing tree","patch":null,"question":null}}\n' > "$tmp/answer.json"
export ATTAVO_FIXTURE_COUNSEL_RESPONSE="$tmp/answer.json"
$A spec-gate 1 > "$tmp/gate.out"
$A approve 1 --by alice > "$tmp/approve.out"
git add records/1/approval-01.json
git commit -q --only records/1/approval-01.json -m "attavo: approval record for item 1"
sect() { awk -v m="--- $2 ---" 'index($0, m) == 1 {on=1; next} on && index($0, "--- end ") == 1 {exit} on {print}' "$1"; }
if ATTAVO_FIXTURE_QUESTION="which overlay wins?" $A build 1 > "$tmp/build.out" 2>&1; then exit 1; fi
test -f records/1/build-01.json
$A counsel 1 > "$tmp/c1.out"
test -f records/1/counsel-01.json
python3 -c 'import json; r=json.load(open("records/1/counsel-01.json")); assert r["answers"] is None and r["question"] is None, r'
test -z "$(sect records/1/counsel-01.log ATTAVO_COUNSEL_PRIOR | tr -d '[:space:]')"
$A counsel 1 --ask "why did it stop?" > "$tmp/c2.out"
test -f records/1/counsel-02.json
python3 -c 'import json; r=json.load(open("records/1/counsel-02.json")); assert r["answers"] == "counsel-01" and r["question"] == "why did it stop?" and r["record"] == "build-01", r'
sect records/1/counsel-02.log ATTAVO_COUNSEL_PRIOR > "$tmp/prior.json"
python3 -c 'import json,sys; p=json.load(open(sys.argv[1])); assert [x["record"] for x in p] == ["counsel-01"], p; assert p[0]["ruling_text"] == "continue from the existing tree" and p[0]["verdict"] == "answer", p' "$tmp/prior.json"
$A counsel 1 --ask "and then?" > "$tmp/c3.out"
python3 -c 'import json; r=json.load(open("records/1/counsel-03.json")); assert r["answers"] == "counsel-02", r'
cd "$here"
rm -rf "$tmp"
