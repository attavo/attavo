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
$A spec-gate 1 >/dev/null
$A approve 1 --by alice >/dev/null
$A drive 1 >/dev/null 2>&1
$A drive 1 >/dev/null 2>&1
python3 - "$here" <<'PY'
import json, sys
r = json.load(open("records/1/verify-01.json"))
assert r["status"] == "pass", r["status"]
ratio = r["stub_ratio"]
assert isinstance(ratio, (int, float)) and not isinstance(ratio, bool) and 0 <= ratio <= 1, ratio
assert "weak_stub" not in r and "mutation" not in r, sorted(r)
s = json.load(open(sys.argv[1] + "/schema/records/verify.schema.json"))
assert s["$id"].endswith("/verify/6"), s["$id"]
assert "weak_stub" not in s["properties"] and "mutation" not in s["properties"], sorted(s["properties"])
assert "stub_ratio" in s["required"] and s["properties"]["stub_ratio"].get("description"), s["properties"]["stub_ratio"]
PY
cd "$here"
rm -rf "$tmp"
