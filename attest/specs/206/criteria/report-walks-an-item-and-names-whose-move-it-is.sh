#!/bin/sh
CONCLUSION=success
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
state() { $A drive 1 --report | python3 -c 'import json,sys; r=json.load(sys.stdin); print(r["state"], r["kind"])'; }
$A spec-gate 1 >/dev/null
test "$(state)" = "waiting-on-approval yours"
$A approve 1 --by alice >/dev/null
test "$(state)" = "next-build acts"
sleep 0 &
dead=$!
wait "$dead"
python3 - records/1/build-01.started "$dead" "$(git rev-parse HEAD)" <<'PY'
import json, socket, sys
json.dump({"at": "2026-09-17T13:16:08Z", "host": socket.gethostname(), "pid": int(sys.argv[2]),
           "sha": sys.argv[3], "product_version": "fixture"}, open(sys.argv[1], "w"))
PY
git add records
git commit -q -m "invocation marker for item 1"
before=$(snapshot)
$A drive 1 --report > "$tmp/owes.json"
test "$(snapshot)" = "$before"
python3 - "$tmp/owes.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
assert (r["state"], r["kind"], r["stage"], r["attempt"], r["stop_class"]) == ("owes-recovery", "acts", "build", 1, "died"), r
PY
$A drive --recover 1 >/dev/null
printf 'https://example.invalid/issues/9\n' > records/1/build-01.filed
git add records
git commit -q -m "the stop is filed"
$A drive 1 --report > "$tmp/stopped.json"
python3 - "$tmp/stopped.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
assert (r["state"], r["kind"], r["record"], r["stop_class"], r["filed"]) == ("stopped", "yours", "build-01", "died", "https://example.invalid/issues/9"), r
PY
$A drive 1 >/dev/null
test "$(state)" = "next-review acts"
$A drive 1 >/dev/null
test "$(state)" = "ready-to-merge yours"
cd "$here"
rm -rf "$tmp"
