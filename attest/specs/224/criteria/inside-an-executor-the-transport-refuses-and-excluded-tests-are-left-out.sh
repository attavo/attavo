#!/bin/sh
set -e
here=$(pwd)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/attavo.XXXXXX")
cp -R "$here/tests/fixtures/build_valid/." "$tmp/tenant"
cd "$tmp/tenant"
git init -q -b main .
git config user.email fixture@attavo.invalid
git config user.name "fixture"
mkdir -p tests
printf 'def test_a():\n    pass\n' > tests/test_a.py
printf 'def test_b():\n    pass\n' > tests/test_b.py
printf 'def test_slow():\n    pass\n' > tests/test_slow.py
printf 'selected.txt\nruns.log\n' > .gitignore
python3 - <<'PY'
from pathlib import Path
p = Path("attavo.toml")
s = p.read_text()
s = s.replace('[tests]\nselect = "cat"\nrun = "true"',
              '[tests]\nselect = "sed -n s#^src/#tests/test_#p"\nrun = "sh -c \'cat > selected.txt; echo run >> runs.log; cat selected.txt\'"\nexclude_in_executor = ["tests/test_slow.py"]')
p.write_text(s)
PY
grep -q "exclude_in_executor" attavo.toml
mkdir -p src
printf 'x = 1\n' > src/a.py
git add -A
git commit -q -m install
git init -q --bare -b main "$tmp/origin.git"
git remote add origin "$tmp/origin.git"
git push -q origin HEAD:main 2>/dev/null
git update-ref refs/remotes/origin/main "$(git rev-parse HEAD)"
git symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main
git checkout -q -b attavo/1-x
printf 'y = 2\n' > src/b.py
git add -A
git commit -q -m "change b"
A="python3 $here/bin/attavo"
runs() { grep -c run runs.log 2>/dev/null || echo 0; }
printf 'z = 1\n' > src/slow.py
git add -A
git commit -q -m "change slow"
$A test 1 > "$tmp/outside.out"
test "$(sort selected.txt | tr '\n' ' ')" = "tests/test_b.py tests/test_slow.py "
export ATTAVO_STAGE=build
$A test 1 --fresh > "$tmp/inside.out"
test "$(cat selected.txt)" = "tests/test_b.py"
grep -q "left out inside an executor" "$tmp/inside.out"
if $A drive 1 > "$tmp/drive.out" 2>&1; then exit 1; fi
grep -q "inside an executor" "$tmp/drive.out"
if $A worker 1 build 01 > "$tmp/worker.out" 2>&1; then exit 1; fi
grep -q "inside an executor" "$tmp/worker.out"
if $A build 1 > "$tmp/build.out" 2>&1; then exit 1; fi
grep -q "inside an executor" "$tmp/build.out"
unset ATTAVO_STAGE
cd "$here"
rm -rf "$tmp"
