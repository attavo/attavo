#!/bin/sh
CONCLUSION=in_progress
set -e
here=$(pwd)
tmp=$(mktemp -d)
cp -R "$here/tests/fixtures/build_valid/." "$tmp/tenant"
cd "$tmp/tenant"
git init -q -b main .
git config user.email fixture@attavo.invalid
git config user.name "fixture"
printf '\n[forge]\nchecks_command = "printf '"'"'ci %s\\\\n'"'"'"\n\n[drive]\nstall_after_s = 60\n' "$CONCLUSION" >> attavo.toml
git add -A
git commit -q -m install
git init -q --bare -b main "$tmp/origin.git"
git remote add origin "$tmp/origin.git"
git push -q origin HEAD:main
git update-ref refs/remotes/origin/main "$(git rev-parse HEAD)"
git symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main
export ATTAVO_DRIVE_POLL_BOUND_S=30 ATTAVO_DRIVE_POLL_INTERVAL_S=5
A="python3 $here/bin/attavo"
snapshot() { printf '%s\n%s\n%s\n' "$(git rev-parse HEAD)" "$(git status --porcelain)" "$(ls records/1 2>/dev/null)"; }
$A spec-gate 1 >/dev/null
$A approve 1 --by alice >/dev/null
$A drive 1 >/dev/null
started=$(date +%s)
$A drive 1 --report > "$tmp/report.json"
test $(( $(date +%s) - started )) -lt 10
python3 - "$tmp/report.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
assert (r["state"], r["kind"]) == ("waiting-on-checks", "waits"), r
assert "ci in_progress" in r["says"], r
PY
cd "$here"
rm -rf "$tmp"
