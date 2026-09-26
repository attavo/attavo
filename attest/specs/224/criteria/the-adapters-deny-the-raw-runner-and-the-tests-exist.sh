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
cd "$here"
export ATTAVO_ADAPTER_DRY_RUN=1 ATTAVO_STAGE=build ATTAVO_SPEC_DIR="$tmp/tenant/specs/1" ATTAVO_TEST_COMMAND="python3 bin/attavo test 1"
python3 adapters/claude-code/run > "$tmp/dry.json" 2>/dev/null
python3 - "$tmp/dry.json" <<'PY'
import sys, json
env = json.loads([l for l in open(sys.argv[1]) if l.strip().startswith("{")][-1])
argv = env["argv"]
denied = argv[argv.index("--disallowedTools") + 1]
for pat in ("Bash(pytest:*)", "Bash(python3 -m pytest:*)", "Bash(python -m pytest:*)"):
    assert pat in denied, denied
text = env["instruction"]
assert "when the criteria and" in text and "stop" in text, text
PY
unset ATTAVO_ADAPTER_DRY_RUN ATTAVO_STAGE ATTAVO_SPEC_DIR ATTAVO_TEST_COMMAND
python3 -c 'import json; c=json.load(open("adapters/codex/conformance.json")); assert "denied_commands" in c, c; c2=json.load(open("adapters/claude-code/conformance.json")); assert "denied_commands" in c2, c2'
for t in test_attavo_test_answers_from_the_cache_on_an_unchanged_tree test_attavo_test_runs_a_named_test_and_the_whole_suite_on_request test_attavo_test_writes_one_event_per_run test_attavo_test_leaves_out_excluded_tests_inside_an_executor test_the_transport_refuses_inside_an_executor test_the_claude_code_adapter_denies_the_raw_runner_and_says_when_to_stop test_every_module_under_attavo_is_mapped_or_named_shared
do
  grep -q "^def $t(" tests/test_testtool.py
done
grep -q "^def test_help_names_the_test_modes_the_cache_and_the_transport_refusal(" tests/test_cli_help.py
cd "$here"
rm -rf "$tmp"
