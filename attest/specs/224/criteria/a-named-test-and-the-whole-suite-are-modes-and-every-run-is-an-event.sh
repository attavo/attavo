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
export ATTAVO_EVENTS="$tmp/events.jsonl"
$A test 1 tests/test_a.py::test_a > "$tmp/named.out"
test "$(cat selected.txt)" = "tests/test_a.py::test_a"
$A test 1 --all > "$tmp/all.out"
test "$(cat selected.txt)" = "tests"
$A test 1 > "$tmp/mapped.out"
test "$(cat selected.txt)" = "tests/test_b.py"
python3 - "$ATTAVO_EVENTS" <<'PY'
import sys, json
rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
tests = [r for r in rows if r.get("kind") == "test"]
assert len(tests) == 3, rows
modes = [r["text"] for r in tests]
assert any("named" in t for t in modes) and any("all" in t for t in modes) and any("mapped" in t for t in modes), modes
assert all("exit 0" in t for t in modes), modes
PY
cd "$here"
rm -rf "$tmp"
