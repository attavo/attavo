#!/bin/sh
set -e
here=$(pwd)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/attavo.XXXXXX")
cp -R "$here/tests/fixtures/build_valid/." "$tmp/tenant"
printf '#!/bin/sh\necho "$1" >> %s/forge-calls.txt\nprintf "ci success\\n"\n' "$tmp" > "$tmp/forge.sh"
cd "$tmp/tenant"
git init -q -b main .
git config user.email fixture@attavo.invalid
git config user.name "fixture"
printf '\n[forge]\nchecks_command = "sh %s/forge.sh"\n\n[drive]\nstall_after_s = 60\n' "$tmp" >> attavo.toml
git add -A
git commit -q -m install
git init -q --bare -b main "$tmp/origin.git"
git remote add origin "$tmp/origin.git"
git push -q origin HEAD:main 2>/dev/null
git update-ref refs/remotes/origin/main "$(git rev-parse HEAD)"
git symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main
export ATTAVO_HEARTBEAT_S=1 ATTAVO_DRIVE_POLL_BOUND_S=2 ATTAVO_DRIVE_POLL_INTERVAL_S=1
A="python3 $here/bin/attavo"
$A spec-gate 1 >/dev/null
$A approve 1 --by alice >/dev/null
git add -A
git commit -q -m approved
git push -q origin HEAD:main 2>/dev/null
$A drive 1 >"$tmp/pass1.out" 2>&1
i=0
until git -C "$tmp/origin.git" cat-file -e main:records/1/build-01.json 2>/dev/null
do
  i=$((i+1))
  test "$i" -lt 60
  sleep 1
done
git pull -q --ff-only origin main
: > "$tmp/forge-calls.txt"
pre_marker=$(git rev-parse HEAD)
$A drive 1 >"$tmp/pass2.out" 2>&1
i=0
until git -C "$tmp/origin.git" cat-file -e main:records/1/verify-01.json 2>/dev/null
do
  i=$((i+1))
  test "$i" -lt 60
  sleep 1
done
git -C "$tmp/origin.git" show main:records/1/verify-01.json | python3 -c 'import sys,json; r=json.load(sys.stdin); assert r["status"] == "pass", r["status"]; assert r["forge_checks"]["sha"] == sys.argv[1], (r["forge_checks"]["sha"], sys.argv[1])' "$pre_marker"
test "$(tr -s '\n' '\n' < "$tmp/forge-calls.txt" | grep -c .)" = 1
test "$(cat "$tmp/forge-calls.txt")" = "$pre_marker"
cd "$here"
rm -rf "$tmp"
