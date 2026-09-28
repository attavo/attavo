#!/bin/sh
set -e
here=$(pwd)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/attavo.XXXXXX")
mkdir -p "$tmp/stub/pkg" "$tmp/stub/tests"
printf 'x = 1\n' > "$tmp/stub/pkg/a.py"
printf 'def test_a():\n    assert True\n' > "$tmp/stub/tests/test_a.py"
cd "$tmp"
PYTHONPATH="$here" python3 - "$tmp" <<'PY'
import sys
from pathlib import Path
from attavo.verify import stub_ratio, stub_test_paths
tmp = Path(sys.argv[1])
stub = tmp / "stub"
select = "while read p; do case $p in tests/test_*) echo \"$p::test_a\";; *) echo tests;; esac; done"
tests = stub_test_paths(tmp, stub, select)
assert tests == frozenset({"tests/test_a.py"}), tests
def d(path, *lines):
    body = "".join(f"+{l}\n" for l in lines)
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n{body}"
text = d("pkg/a.py", "x = 1", "y = 2") + d("tests/test_a.py", "def test_a():", "    assert True")
assert stub_ratio(text, stub, ("specs", "records"), tests) == 0.5
assert stub_ratio(text, stub, ("specs", "records"), None) is None
assert stub_test_paths(tmp, stub, None) is None
assert stub_test_paths(tmp, stub, "exit 3") is None
PY
cd "$here"
for t in test_stub_ratio_leaves_out_the_stubs_test_files_on_both_sides test_stub_ratio_is_null_when_the_stubs_tests_cannot_be_told_apart test_stub_test_paths_are_the_stub_files_the_tenants_selector_selects_for_themselves test_stub_test_paths_are_unknown_without_a_selector_or_when_it_fails
do
  grep -q "^def $t(" tests/test_stub_ratio.py
done
rm -rf "$tmp"
