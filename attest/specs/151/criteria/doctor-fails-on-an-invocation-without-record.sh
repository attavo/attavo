#!/bin/sh
set -e
here=$(pwd)
tmp=$(mktemp -d)
cd "$tmp"
git init -q -b main .
git config user.email doctor@attavo.invalid
git config user.name "doctor fixture"
printf '[paths]\nrecords = "records"\n\n[drive]\nstall_after_s = 60\n' > attavo.toml
git add attavo.toml
git commit -q -m install
mkdir -p records/1
sleep 0 &
dead=$!
wait "$dead"
python3 - "$tmp/records/1/build-01.started" "$dead" <<'PY'
import json, socket, sys
json.dump({"at": "2026-09-17T13:16:08Z", "host": socket.gethostname(), "pid": int(sys.argv[2]),
           "sha": "0000000000000000000000000000000000000000", "product_version": "fixture"},
          open(sys.argv[1], "w"))
PY
git add records
git commit -q -m "invocation marker for item 1"
cd "$here"
python3 bin/attavo doctor --json "$tmp" > "$tmp/doctor-without.json" || true
printf '{"status": "pass"}' > "$tmp/records/1/build-01.json"
python3 bin/attavo doctor --json "$tmp" > "$tmp/doctor-with.json" || true
python3 - "$tmp/doctor-without.json" "$tmp/doctor-with.json" <<'PY'
import json, sys
def proof(path):
    d = json.load(open(path))
    return [x for x in d["proofs"] if x["proof"] == "no-invocation-lacks-record"][0]
without = proof(sys.argv[1])
assert without["holds"] is False, without
assert "build-01" in without["reason"] and "never wrote its record" in without["reason"], without
with_record = proof(sys.argv[2])
assert with_record["holds"] is True, with_record
PY
rm -rf "$tmp"
