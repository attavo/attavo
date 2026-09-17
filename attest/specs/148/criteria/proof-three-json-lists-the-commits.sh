#!/bin/sh
set -e
here=$(pwd)
tmp=$(mktemp -d)
cd "$tmp"
git init -q -b main .
git config user.email doctor@attavo.invalid
git config user.name "doctor fixture"
printf '[paths]\nrecords = "records"\n\n[spec.boundary]\npaths = ["boundary/**"]\n' > attavo.toml
git add attavo.toml
git commit -q -m install
git checkout -q -b attavo/1-x
mkdir -p records/1
printf '{"status": "pass"}' > records/1/spec-01.json
git add records
git commit -q -m "spec record for item 1"
git checkout -q main
git merge -q --no-ff -m "Merge branch 'attavo/1-x'" attavo/1-x
git checkout -q -b ordinary-change
mkdir -p boundary
printf 'v2\n' > boundary/thing.txt
git add boundary
git commit -q -m "an ordinary change"
git checkout -q main
git merge -q --no-ff -m "Merge branch 'ordinary-change'" ordinary-change
printf 'direct\n' > direct.txt
git add direct.txt
git commit -q -m "a change committed straight to main"
cd "$here"
python3 bin/attavo doctor --json "$tmp" > "$tmp/doctor.json" || true
python3 - "$tmp/doctor.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
p = [x for x in d["proofs"] if x["proof"] == "no-commit-lacks-record"][0]
assert p["ordinary"] == 1 and p["unrecorded"] == 1, p
o = p["ordinary_commits"]
u = p["unrecorded_commits"]
assert [c["subject"] for c in o] == ["Merge branch 'ordinary-change'"], o
assert o[0]["boundary_evasion"] is True and len(o[0]["sha"]) == 40, o
assert [c["subject"] for c in u] == ["a change committed straight to main"], u
PY
