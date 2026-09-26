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
$A test 1 > "$tmp/first.out"
test "$(runs)" = 1
test "$(cat selected.txt)" = "tests/test_b.py"
started=$(date +%s)
$A test 1 > "$tmp/second.out"
ended=$(date +%s)
test "$(runs)" = 1
grep -q "not rerun" "$tmp/second.out"
test $((ended - started)) -lt 3
printf 'y = 3\n' > src/b.py
$A test 1 > "$tmp/third.out"
test "$(runs)" = 2
$A test 1 --fresh > "$tmp/fourth.out"
test "$(runs)" = 3
cd "$here"
rm -rf "$tmp"
