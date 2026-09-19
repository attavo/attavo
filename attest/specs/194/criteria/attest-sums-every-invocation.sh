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
python3 - "$here" "$merged_at" <<'PY'
import json, sys
here, merged_at = sys.argv[1], int(sys.argv[2])
s = json.load(open("statement.json"))
schema = json.load(open(here + "/schema/attestation.schema.json"))
assert schema["$id"].endswith("/v3"), schema["$id"]
assert set(schema["required"]) <= set(s), sorted(set(schema["required"]) - set(s))
assert set(s) <= set(schema["properties"]), sorted(set(s) - set(schema["properties"]))
assert set(s["time"]) == set(schema["properties"]["time"]["required"]), s["time"]
t = s["time"]
assert t["build"] == {"attempts": 3, "invocation_s": 350.75, "partial": False}, t
assert t["verify"] == {"attempts": 2, "invocation_s": 40.0, "partial": False}, t
assert t["counsel"] == {"attempts": 1, "invocation_s": 7.5, "partial": False}, t
assert t["invocation_s"] == 398.25 and t["partial"] is False and t["not_read"] == [], t
assert s["wall_clock_by_stage_s"] == {"build": 50.25, "verify": 30.0}, s
assert t["lead_s"] == merged_at - 1767225600, t
assert t["waiting_s"] == round(t["lead_s"] - 398.25, 3), t
PY
cd "$here"
rm -rf "$tmp"
