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
$A counsel 1 > "$tmp/c1.out"
grep -q "^log handed: yes" records/1/counsel-01.log
test -n "$(sect records/1/counsel-01.log ATTAVO_COUNSEL_EVENTS | tr -d '[:space:]')"
sect records/1/counsel-01.log ATTAVO_COUNSEL_EVENTS | python3 -c 'import sys,json; rows=[json.loads(l) for l in sys.stdin if l.strip()]; assert rows and all("kind" in r and "text" in r for r in rows), rows'
$A counsel 1 --ask "why did it stop?" > "$tmp/c2.out"
grep -q "^log handed: no" records/1/counsel-02.log
test -n "$(sect records/1/counsel-02.log ATTAVO_COUNSEL_EVENTS | tr -d '[:space:]')"
$A counsel 1 --ask "what does the log say at the end?" > "$tmp/c3.out"
grep -q "^log handed: yes" records/1/counsel-03.log
cd "$here"
rm -rf "$tmp"
