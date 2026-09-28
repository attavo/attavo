#!/bin/sh
set -e
here=$(pwd)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/attavo.XXXXXX")
cp -R "$here/tests/fixtures/valid/." "$tmp/tenant"
cd "$tmp/tenant"
git init -q -b main .
git config user.email fixture@attavo.invalid
git config user.name "fixture"
git add -A
git commit -q -m install
git update-ref refs/remotes/origin/main "$(git rev-parse HEAD)"
git symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main
git checkout -q -b lane/9-other
mkdir -p docs
printf 'x\n' > docs/readme.md
git add -A
git commit -q -m other
git update-ref refs/remotes/origin/lane/9-other "$(git rev-parse HEAD)"
git checkout -q main
A="python3 $here/bin/attavo"
$A claims 1 > "$tmp/c1.out"
grep -q "item 1: no collision with work in flight" "$tmp/c1.out"
$A claims --json > "$tmp/c2.json"
python3 -c 'import sys,json; r=json.load(open(sys.argv[1])); assert [f["branch"] for f in r["in_flight"]] == ["lane/9-other"], r' "$tmp/c2.json"
git checkout -q lane/9-other
mkdir -p src
printf 'y\n' > src/marker.txt
git add -A
git commit -q -m collide
git update-ref refs/remotes/origin/lane/9-other "$(git rev-parse HEAD)"
git checkout -q main
if $A claims 1 > "$tmp/c3.out"; then exit 1; fi
grep -q "lane/9-other: src/marker.txt" "$tmp/c3.out"
cd "$here"
for t in test_overlap_reads_paths_and_globs_both_ways test_claims_lists_unmerged_branches_with_their_spec_claims_and_skips_merged_ones test_an_item_that_collides_with_work_in_flight_is_named_by_branch_and_path_and_exits_one test_an_item_with_disjoint_claims_exits_zero_and_its_own_branch_is_not_a_collision test_claims_refuses_by_name_when_the_default_branch_is_not_set
do
  grep -q "^def $t(" tests/test_claims.py
done
rm -rf "$tmp"
