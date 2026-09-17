#!/bin/sh
set -e
here=$(pwd)
tmp=$(mktemp -d)
cd "$tmp"
git init -q -b main .
git config user.email person@attavo.invalid
git config user.name "ledger fixture"
printf '[paths]\nrecords = "records"\nspecs = "specs"\n' > attavo.toml
git add attavo.toml
git commit -q -m install
for i in 1 2 3 4 5 6
do
  git checkout -q -b "attavo/$i-x"
  mkdir -p "specs/$i" "records/$i"
  printf '# item %s\n' "$i" > "specs/$i/spec.md"
  git add specs
  git commit -q -m "spec for item $i"
  for stage in spec approval build verify
  do
    printf '{"status": "pass"}' > "records/$i/$stage-01.json"
    git add records
    git commit -q -m "$stage record for item $i"
  done
  git checkout -q main
  git merge -q --no-ff -m "Merge branch 'attavo/$i-x'" "attavo/$i-x"
done
GIT_TRACE="$tmp/trace" python3 "$here/bin/attavo" ledger --json > "$tmp/ledger.json"
python3 - "$tmp/ledger.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
items = d["items"] if isinstance(d, dict) else d
assert len(items) == 6, len(items)
assert all(len(x["rows"]) >= 5 for x in items), [len(x["rows"]) for x in items]
PY
n=$(grep -c 'built-in: git' "$tmp/trace")
echo "git processes for six items: $n"
cd "$here"
rm -rf "$tmp"
test "$n" -le 60
