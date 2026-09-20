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
mkdir statements
python3 - "$merge" <<'PY'
import json, sys
s = json.load(open("statement.json"))
assert "time" in s, "the statement carries no time block"
env = {"_type": "https://in-toto.io/Statement/v1", "predicate": s}
json.dump(env, open(f"statements/{sys.argv[1]}.json", "w"))
s["time"]["invocation_s"] = 20.0
json.dump(env, open("wrong.json", "w"))
del s["time"]
json.dump(env, open("before-time.json", "w"))
PY
python3 "$here/bin/attavo" doctor --json "$tmp" > agree.json || true
cp wrong.json "statements/$merge.json"
python3 "$here/bin/attavo" doctor --json "$tmp" > wrong-doctor.json || true
cp before-time.json "statements/$merge.json"
python3 "$here/bin/attavo" doctor --json "$tmp" > before-doctor.json || true
python3 - <<'PY'
import json
def six(path):
    return [p for p in json.load(open(path))["proofs"] if p["proof"] == "every-attested-item-is-sealed"][0]
agree, wrong, before = six("agree.json"), six("wrong-doctor.json"), six("before-doctor.json")
assert agree["holds"] is True and agree["sealed"] == ["1"], agree
assert wrong["holds"] is False, wrong
assert "item 1" in wrong["reason"] and "time.invocation_s" in wrong["reason"], wrong
assert before["holds"] is True, before
PY
cd "$here"
rm -rf "$tmp"
