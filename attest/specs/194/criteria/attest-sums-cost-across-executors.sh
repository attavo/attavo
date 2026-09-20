#!/bin/sh
set -e
here=$(pwd)
tmp=$(mktemp -d)
cd "$tmp"
git init -q -b main .
git config user.email fixture@attavo.invalid
git config user.name "fixture"
printf '[paths]\nrecords = "records"\nspecs = "specs"\n\n[forge]\nattestation_command = "sh ./fake-store"\n' > attavo.toml
printf '#!/bin/sh\nread sha\ntest -f "statements/$sha.json" && cat "statements/$sha.json"\n' > fake-store
git add attavo.toml fake-store
git commit -q -m install
git checkout -q -b attavo/1-x
mkdir -p records/1
printf '{"status": "pass", "sha": "s1", "at": "2026-01-01T00:00:00Z"}' > records/1/spec-01.json
printf '{"sha": "a1", "spec_sha": "s1", "at": "2026-01-01T00:00:00Z"}' > records/1/approval-01.json
printf '{"status": "failed", "sha": "b1", "adapter": "alpha", "approval_sha": "a1", "cost_usd": 0.25, "wall_clock_s": 100.5}' > records/1/build-01.json
printf '{"status": "failed", "sha": "b2", "adapter": "alpha", "approval_sha": "a1", "cost_usd": 0.5, "wall_clock_s": 200.0}' > records/1/build-02.json
printf '{"status": "pass", "sha": "b3", "adapter": "alpha", "approval_sha": "a1", "cost_usd": 0.125, "wall_clock_s": 50.25}' > records/1/build-03.json
printf '{"status": "returned", "sha": "v1", "adapter": "beta", "build_sha": "b3", "cost_usd": null, "wall_clock_s": 10.0}' > records/1/verify-01.json
printf '{"status": "pass", "sha": "v2", "adapter": "beta", "build_sha": "b3", "cost_usd": null, "wall_clock_s": 30.0}' > records/1/verify-02.json
printf '{"status": "done", "sha": "c1", "adapter": "beta", "cost_usd": null, "wall_clock_s": 7.5}' > records/1/counsel-01.json
git add records
git commit -q -m "records for item 1"
git checkout -q main
git merge -q --no-ff -m "Merge branch attavo/1-x" attavo/1-x
git update-ref refs/remotes/origin/main "$(git rev-parse main)"
git symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main
merge=$(git rev-parse HEAD)
merged_at=$(git show -s --format=%ct HEAD)
python3 "$here/bin/attavo" attest 1 > statement.json
python3 - <<'PY'
import json
c = json.load(open("statement.json"))["cost"]
assert c["usd"] == 0.875 and c["by_stage_usd"] == {"build": 0.875}, c
assert c["by_adapter_usd"] == {"alpha": 0.875}, c
assert c["partial"] is True, c
assert c["not_read"] == ["verify-01", "verify-02", "counsel-01"], c
PY
git checkout -q -b attavo/priced
for f in verify-01 verify-02 counsel-01
do
  python3 -c 'import json,sys; p=sys.argv[1]; d=json.load(open(p)); d["cost_usd"]=0.0625; json.dump(d, open(p, "w"))' "records/1/$f.json"
done
git commit -q -a -m "the second executor reports a cost"
python3 "$here/bin/attavo" attest 1 > statement.json
python3 - <<'PY'
import json
c = json.load(open("statement.json"))["cost"]
assert c["usd"] == 1.0625, c
assert c["by_adapter_usd"] == {"alpha": 0.875, "beta": 0.1875}, c
assert c["by_stage_usd"] == {"build": 0.875, "verify": 0.125, "counsel": 0.0625}, c
assert c["partial"] is False and c["not_read"] == [], c
PY
cd "$here"
rm -rf "$tmp"
