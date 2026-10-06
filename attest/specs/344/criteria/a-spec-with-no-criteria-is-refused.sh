#!/bin/sh
set -e
here=$(pwd)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/attavo.XXXXXX")
cp -R "$here/tests/fixtures/build_valid/." "$tmp/tenant"
cd "$tmp/tenant"
printf '## Problem\n\nClass: fix\n\nA spec that asserts nothing.\n\n## Claims\n\n- built.txt\n\n## Criteria\n\n' > specs/1/spec.md
rm -r specs/1/criteria
git init -q -b main .
git config user.email fixture@attavo.invalid
git config user.name fixture
git add -A
git commit -q -m install
git checkout -q -b attavo/1-x
if python3 "$here/bin/attavo" spec-gate 1 > "$tmp/gate.out" 2>&1; then
  cd "$here"; rm -rf "$tmp"; exit 1
fi
grep -q "criteria: empty; the spec asserts nothing" "$tmp/gate.out"
cd "$here"
rm -rf "$tmp"
