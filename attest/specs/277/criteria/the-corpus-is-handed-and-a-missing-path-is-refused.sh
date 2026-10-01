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
printf '\n[counsel]\nreads = ["docs/rules.md", "docs/brief.md"]\n' >> attavo.toml
if $A counsel 1 > "$tmp/refused.out" 2>&1; then exit 1; fi
grep -q "refused: \[counsel\].reads names docs/rules.md, docs/brief.md, which does not exist" "$tmp/refused.out"
test -z "$(ls records/1 | grep '^counsel-')"
mkdir -p docs
printf '# rules\n' > docs/rules.md
printf '# brief\n' > docs/brief.md
$A counsel 1 > "$tmp/c1.out"
rt=$(pwd -P)
test "$(sect records/1/counsel-01.log ATTAVO_COUNSEL_READS | tr '\n' ' ')" = "$rt/docs/rules.md $rt/docs/brief.md "
cd "$here"
python3 -c 'import json,tomllib; m=tomllib.load(open("attavo.toml","rb")); reads=m["counsel"]["reads"]; assert len(reads) >= 3 and all(r.startswith("docs/") for r in reads), reads; import os; assert all(os.path.getsize(r) > 200 for r in reads), reads; s=json.load(open("schema/manifest.schema.json")); assert "counsel" in s["properties"], "no [counsel] in the manifest schema"'
for t in test_a_follow_up_is_handed_the_prior_readings_and_names_the_one_it_answers test_a_follow_up_is_handed_the_events_tail_and_the_log_only_when_asked test_counsel_reads_the_tenants_corpus_and_refuses_a_missing_path
do
  grep -q "^def $t(" tests/test_counsel.py
done
grep -q "^def test_the_manifests_counsel_reads_exist_and_docs_carries_the_corpus(" tests/test_self_tenancy.py
grep -q "^def test_help_names_counsels_corpus_the_follow_up_and_the_events_tail(" tests/test_cli_help.py
cd "$here"
rm -rf "$tmp"
