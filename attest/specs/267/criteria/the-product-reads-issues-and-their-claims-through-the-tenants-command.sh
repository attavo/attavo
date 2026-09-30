#!/bin/sh
set -e
here=$(pwd)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/attavo.XXXXXX")
mkdir -p "$tmp/bin"
cat > "$tmp/bin/gh" <<'GH'
#!/bin/sh
cat <<'JSON'
[{"number": 7, "title": "seven", "state": "OPEN", "closedAt": null, "labels": [{"name": "class:fix"}], "body": "x\n\n## Claims\n\n- src/a.py\n- `src/b.py`\n\n## Acceptance\n\n- not a claim\n"},
 {"number": 3, "title": "three", "state": "CLOSED", "closedAt": "2026-09-29T00:00:00Z", "labels": [], "body": "no section"}]
JSON
GH
chmod +x "$tmp/bin/gh"
PATH="$tmp/bin:$PATH" python3 "$here/.attavo/bin/forge-issues" > "$tmp/issues.json"
printf 'import sys\nsys.stdout.write(open(sys.argv[1]).read())\n' > "$tmp/cmd.py"
cd "$tmp"
python3 - "$here" "$tmp" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from attavo import issues
tmp = Path(sys.argv[2])
got, why = issues.read_issues(tmp, {"forge": {"issues_command": f"{sys.executable} {tmp}/cmd.py {tmp}/issues.json"}})
assert why is None, why
assert [(i["number"], i["state"], i["labels"], i["claims"]) for i in got] == [
    (3, "closed", [], None), (7, "open", ["class:fix"], ["src/a.py", "src/b.py"])], got
none, why = issues.read_issues(tmp, {})
assert none is None and why == "not read: no [forge].issues_command", why
bad, why = issues.read_issues(tmp, {"forge": {"issues_command": "exit 4"}})
assert bad is None and why.startswith("not read: [forge].issues_command exited 4"), why
PY
cd "$here"
grep -q '^issues_command = "python3 .attavo/bin/forge-issues"$' attavo.toml
grep -q '"issues_command"' schema/manifest.schema.json
for t in test_claims_are_read_from_the_bodys_claims_section_and_nowhere_else test_the_command_is_read_into_issues_with_their_claims_in_number_order test_no_command_is_not_read_and_never_an_empty_list test_a_failing_command_or_output_of_the_wrong_shape_is_not_read_and_says_why test_a_command_that_runs_past_its_bound_is_not_read test_the_manifest_schema_takes_issues_command_and_this_repository_declares_it test_this_repositorys_command_translates_the_forges_words_into_the_shape
do
  grep -q "^def $t(" tests/test_issues.py
done
rm -rf "$tmp"
