#!/bin/sh
set -e
here=$(pwd)
tmp=$(mktemp -d)
cd "$tmp"
git init -q -b main .
git config user.email person@attavo.invalid
git config user.name "recover fixture"
printf '[paths]\nrecords = "records"\n\n[drive]\nstall_after_s = 60\n\n[executor]\nbuild = "fixture"\n' > attavo.toml
git add attavo.toml
git commit -q -m install
git checkout -q -b attavo/1-x
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
python3 "$here/bin/attavo" drive --recover 1
test -f records/1/build-01.json
test -f records/1/build-01.log
python3 - records/1/build-01.json <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
assert r["status"] == "failed", r
assert r["stop"]["class"] == "died" and r["stop"]["provenance"] == "product", r["stop"]
assert "wrote no build-01.json" in r["stop"]["detail"], r["stop"]
PY
test "$(git log -1 --format=%an)" = "attavo driver"
test -z "$(git status --porcelain)"
cd "$here"
python3 bin/attavo doctor --json "$tmp" | python3 -c 'import json,sys; p=[x for x in json.load(sys.stdin)["proofs"] if x["proof"]=="no-invocation-lacks-record"][0]; assert p["holds"] is True, p'
rm -rf "$tmp"
