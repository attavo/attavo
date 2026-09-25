#!/bin/sh
set -e
here=$(pwd)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/attavo.XXXXXX")
cp -R "$here/tests/fixtures/build_valid/." "$tmp/tenant"
cd "$tmp/tenant"
git init -q -b main .
git config user.email fixture@attavo.invalid
git config user.name "fixture"
printf '\n[forge]\nchecks_command = "printf '"'"'ci failure\\\\n'"'"'"\n' >> attavo.toml
printf 'built\n' > specs/1/stub/built.txt
git add -A
git commit -q -m install
git init -q --bare -b main "$tmp/origin.git"
git remote add origin "$tmp/origin.git"
git push -q origin HEAD:main 2>/dev/null
git update-ref refs/remotes/origin/main "$(git rev-parse HEAD)"
git symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main
git checkout -q -b attavo/1-x
export ATTAVO_DRIVE_POLL_BOUND_S=1 ATTAVO_DRIVE_POLL_INTERVAL_S=1
A="python3 $here/bin/attavo"
$A spec-gate 1 >/dev/null
$A approve 1 --by alice >/dev/null
$A drive 1 >/dev/null 2>&1
$A verify 1 >/dev/null
python3 - <<'PY'
import json
r = json.load(open("records/1/verify-01.json"))
assert r["status"] == "returned", r["status"]
ratio = r["stub_ratio"]
assert isinstance(ratio, (int, float)) and not isinstance(ratio, bool), ratio
PY
cd "$here"
rm -rf "$tmp"
