#!/bin/sh
set -e
here=$(pwd)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/attavo.XXXXXX")
cp -R "$here/tests/fixtures/build_valid/." "$tmp/tenant"
cd "$tmp/tenant"
git init -q -b main .
git config user.email fixture@attavo.invalid
git config user.name "fixture"
printf '\n[forge]\nchecks_command = "printf '"'"'ci success\\\\n'"'"'"\n\n[drive]\nstall_after_s = 60\n' >> attavo.toml
git add -A
git commit -q -m install
git init -q --bare -b main "$tmp/origin.git"
git remote add origin "$tmp/origin.git"
git push -q origin HEAD:main 2>/dev/null
git update-ref refs/remotes/origin/main "$(git rev-parse HEAD)"
git symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main
A="python3 $here/bin/attavo"
$A spec-gate 1 >/dev/null
$A approve 1 --by alice >/dev/null
git add -A
git commit -q -m approved
python3 - <<'PY'
import json, socket, subprocess, sys
proc = subprocess.Popen([sys.executable, "-c", "pass"])
proc.wait()
sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
open("records/1/build-01.started", "w").write(json.dumps({
    "at": "2026-09-17T13:16:08Z", "host": socket.gethostname(), "pid": proc.pid, "sha": sha, "product_version": "fixture"}))
PY
git add -A
git commit -q -m "invocation marker for item 1"
git push -q origin HEAD:main 2>/dev/null
if $A drive --recover 1 >/dev/null 2>&1; then exit 1; fi
test ! -f records/1/build-01.json
$A drive 1 >"$tmp/pass.out" 2>&1
git -C "$tmp/origin.git" show main:records/1/build-01.json | python3 -c 'import sys,json; r=json.load(sys.stdin); assert r["status"] == "failed", r["status"]; assert r["stop"]["class"] == "died", r["stop"]'
cd "$here"
rm -rf "$tmp"
