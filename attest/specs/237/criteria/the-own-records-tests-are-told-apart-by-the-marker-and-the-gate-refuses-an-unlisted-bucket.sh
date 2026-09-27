#!/bin/sh
set -e
here=$(pwd)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/attavo.XXXXXX")
python3 - "$here" <<'PY'
import sys, re, ast, tomllib
from pathlib import Path
root = Path(sys.argv[1])
m = tomllib.load(open(root / "attavo.toml", "rb"))
run = m["tests"]["run"]
assert "-m 'not own_records'" in run and "--deselect" not in run, run
assert "--deselect" not in m["checks"]["command"], m["checks"]["command"]
ci = (root / ".github/workflows/ci.yml").read_text()
assert "--deselect" not in ci, "ci.yml still names a test by hand"
assert "python3 -m pytest tests -q --durations=5 -m 'not own_records'" in ci
assert ci.count("python3 -m pytest tests -q -m own_records") == 2, "the inherit path and the doctor job"
src = (root / "tests/test_doctor.py").read_text()
tree = ast.parse(src)
for n in tree.body:
    if isinstance(n, ast.FunctionDef) and n.name.startswith("test_") and "run_doctor(ROOT)" in (ast.get_source_segment(src, n) or ""):
        assert any(ast.unparse(d) == "pytest.mark.own_records" for d in n.decorator_list), n.name
guard = (root / "tests/test_own_records_guard.py").read_text()
assert "deselected_by_the_test_job" not in guard, "the guard still reads a deselect list"
assert "def test_every_doctor_proof_test_carries_the_marker" in guard
assert "def test_tests_run_and_the_test_job_exclude_the_own_records_tests_by_marker" in (root / "tests/test_self_tenancy.py").read_text()
assert "def test_spec_gate_refuses_a_bucket_the_tenant_does_not_list_and_passes_a_listed_one" in (root / "tests/test_spec_gate.py").read_text()
buckets = (root / "attest/buckets.md").read_text()
for slug in ("shared-working-copy", "deny-list-tool-surface", "dogfood-tests-told-apart-by-a-list", "liveness-by-proxy", "classification-by-reading-prose"):
    assert re.search(rf"^- {slug}: \S", buckets, re.M), slug
PY
cp -R "$here/tests/fixtures/valid/." "$tmp/tenant"
cd "$tmp/tenant"
git init -q -b main .
git config user.email fixture@attavo.invalid
git config user.name "fixture"
python3 - <<'PY'
p = "specs/1/spec.md"
s = open(p).read().replace("## Problem\n", "## Problem\n\nBucket: liveness-by-proxy\n", 1)
open(p, "w").write(s)
PY
git add -A
git commit -q -m install
A="python3 $here/bin/attavo"
if $A spec-gate 1 > "$tmp/gate1.out" 2>&1; then exit 1; fi
grep -q "bucket: liveness-by-proxy named but buckets.md does not exist" "$tmp/gate1.out"
printf '# buckets\n\n- shared-working-copy: one checkout, many writers\n' > buckets.md
git add buckets.md
git commit -q -m "one bucket"
if $A spec-gate 1 > "$tmp/gate2.out" 2>&1; then exit 1; fi
grep -q "bucket: liveness-by-proxy is not listed in buckets.md" "$tmp/gate2.out"
printf -- '- liveness-by-proxy: a pid stood in for the holder\n' >> buckets.md
git add buckets.md
git commit -q -m "the bucket"
$A spec-gate 1 > "$tmp/gate3.out" 2>&1
grep -q "spec-gate: pass" "$tmp/gate3.out"
python3 - <<'PY'
import json, glob
recs = sorted(glob.glob("records/1/spec-*.json"))
assert len(recs) == 3, recs
s = [json.load(open(r))["status"] for r in recs]
assert s == ["refused", "refused", "pass"], s
assert json.load(open(recs[0]))["criteria"] == [], "refused before any criterion ran"
PY
cd "$here"
rm -rf "$tmp"
