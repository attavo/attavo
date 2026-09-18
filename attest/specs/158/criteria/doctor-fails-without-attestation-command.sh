#!/bin/sh
set -e
here=$(pwd)
tmp=$(mktemp -d)
cd "$tmp"
git init -q -b main .
git config user.email doctor@attavo.invalid
git config user.name "doctor fixture"
printf '[paths]\nrecords = "records"\n' > attavo.toml
mkdir -p records
git add attavo.toml
git commit -q -m install
git update-ref refs/remotes/origin/main "$(git rev-parse main)"
git symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main
cd "$here"
python3 bin/attavo doctor --json "$tmp" | python3 -c 'import json,sys; p=[x for x in json.load(sys.stdin)["proofs"] if x["proof"]=="every-attested-item-is-sealed"][0]; assert p["holds"] is False and "attestation_command" in p["reason"], p'
python3 -c 'import json; s=json.load(open("schema/manifest.schema.json")); assert "attestation_command" in s["properties"]["forge"]["properties"], s["properties"]["forge"]'
rm -rf "$tmp"
