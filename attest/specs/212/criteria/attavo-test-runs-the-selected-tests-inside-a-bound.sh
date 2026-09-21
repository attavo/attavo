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
python3 - <<'PY'
p = "attavo.toml"
t = open(p).read().replace('[tests]\nselect = "cat"\nrun = "true"',
    '[tests]\nselect = "grep ^src/"\ncall_timeout_s = 2\nrun = "sh -c \'cat > selected.txt; if grep -q slow selected.txt; then sleep 30; fi; if grep -q bad selected.txt; then exit 7; fi\'"')
open(p, "w").write(t)
PY
printf 'selected.txt\n' > .gitignore
install
mkdir src
printf 'x\n' > src/good.py
git add -A
git commit -q -m "a change"
$A test 1 >/dev/null
test "$(cat selected.txt)" = "src/good.py"
printf 'x\n' > src/bad.py
git add -A
git commit -q -m "a change whose tests fail"
code=0
$A test 1 >/dev/null || code=$?
test "$code" = 7
git rm -q src/bad.py
printf 'x\n' > src/slow.py
git add -A
git commit -q -m "a change whose tests never end"
started=$(date +%s)
code=0
$A test 1 >/dev/null || code=$?
test "$code" = 124
test $(( $(date +%s) - started )) -lt 20
cd "$here"
rm -rf "$tmp"
