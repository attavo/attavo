#!/bin/sh
set -e
here=$(pwd)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/attavo.XXXXXX")
cp -R "$here/tests/fixtures/build_valid/." "$tmp/tenant"
cd "$tmp/tenant"
git init -q -b main .
git config user.email fixture@attavo.invalid
git config user.name "fixture"
A="python3 $here/bin/attavo"
install() {
  git add -A
  git commit -q -m install
  git update-ref refs/remotes/origin/main "$(git rev-parse HEAD)"
  git symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main
  git checkout -q -b attavo/1-x
}
printf 'checks-ran.txt\nprobe-ran.txt\n' > .gitignore
printf '\n[checks]\ncommand = "echo ran >> checks-ran.txt"\nprobe = "echo probed >> probe-ran.txt"\nprobe_timeout_s = 30\n' >> attavo.toml
install
$A spec-gate 1 >/dev/null
$A approve 1 --by alice >/dev/null
$A build 1 >/dev/null
test "$(grep -c probed probe-ran.txt)" = 1
test "$(grep -c ran checks-ran.txt)" = 1
cd "$here"
rm -rf "$tmp"
tmp=$(mktemp -d "${TMPDIR:-/tmp}/attavo.XXXXXX")
cp -R "$here/tests/fixtures/build_valid/." "$tmp/tenant"
cd "$tmp/tenant"
git init -q -b main .
git config user.email fixture@attavo.invalid
git config user.name "fixture"
printf '\n[checks]\ncommand = "true"\nprobe = "exit 3"\nprobe_timeout_s = 30\n' >> attavo.toml
install
$A spec-gate 1 >/dev/null
$A approve 1 --by alice >/dev/null
if $A build 1 >/dev/null; then exit 1; fi
test ! -e built.txt
python3 - <<'PY'
import json
r = json.load(open("records/1/build-01.json"))
assert r["status"] == "refused" and r["stop"]["class"] == "environment", r
PY
cd "$here"
rm -rf "$tmp"
