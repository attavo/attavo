#!/bin/sh
set -e
here=$(pwd)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/attavo.XXXXXX")
cp -R "$here/tests/fixtures/valid/." "$tmp/tenant"
cd "$tmp/tenant"
sed -i.bak '/^Class: /d' specs/1/spec.md && rm -f specs/1/spec.md.bak
git init -q -b main .
git config user.email fixture@attavo.invalid
git config user.name "fixture"
git add -A
git commit -q -m install
A="python3 $here/bin/attavo"
if $A spec-gate 1 > "$tmp/g1.out" 2>&1; then exit 1; fi
python3 - <<'PY'
import json, glob
r = json.load(open(sorted(glob.glob("records/1/spec-*.json"))[-1]))
assert r["refusal"].startswith("class: none declared"), r["refusal"]
assert r["criteria"] == [], r["criteria"]
PY
python3 - <<'PY'
p = "specs/1/spec.md"; s = open(p).read()
open(p, "w").write(s.replace("## Problem\n", "## Problem\n\nClass: core\n", 1))
PY
git commit -qam "class core"
$A spec-gate 1 > "$tmp/g2.out"
python3 - <<'PY'
import json, glob
r = json.load(open(sorted(glob.glob("records/1/spec-*.json"))[-1]))
assert r["status"] == "pass" and r["class"] == "core", r
assert r["research"] == "not required by policy", r
PY
python3 - <<'PY'
p = "attavo.toml"; s = open(p).read()
open(p, "w").write(s.replace("[spec]\n", '[spec]\nresearch = "required"\n', 1))
PY
git commit -qam "research required"
if $A spec-gate 1 > "$tmp/g3.out" 2>&1; then exit 1; fi
grep -q "research: \[spec\].research is required" "$tmp/g3.out"
cd "$here"
for t in test_spec_gate_refuses_a_spec_that_declares_no_class_before_any_criterion_runs test_spec_gate_refuses_a_class_other_than_core_or_fix test_spec_gate_refuses_a_root_cause_item_declared_fix test_spec_gate_records_the_class_and_that_research_was_not_required test_with_research_required_a_core_spec_owes_a_research_record_and_a_fix_spec_does_not test_an_approved_and_built_item_whose_spec_declares_no_class_still_regates_built
do
  grep -q "^def $t(" tests/test_spec_gate.py
done
rm -rf "$tmp"
