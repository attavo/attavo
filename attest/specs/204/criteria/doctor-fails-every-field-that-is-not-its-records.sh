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
python3 - "$merge" "$here" "$tmp" <<'PY'
import copy, json, subprocess, sys
merge, here, tmp = sys.argv[1:4]
true = json.load(open("statement.json"))

def six(statement):
    env = {"_type": "https://in-toto.io/Statement/v1", "predicate": statement}
    json.dump(env, open(f"statements/{merge}.json", "w"))
    out = subprocess.run([sys.executable, f"{here}/bin/attavo", "doctor", "--json", tmp],
                         capture_output=True, text=True).stdout
    return [p for p in json.loads(out)["proofs"] if p["proof"] == "every-attested-item-is-sealed"][0]

held = six(true)
assert held["holds"] is True and held["sealed"] == ["1"], held

printed = copy.deepcopy(true)
assert printed["time"]["verify"]["invocation_s"] == 40.0
printed["time"]["verify"]["invocation_s"] = 40
del printed["time"]["counsel"]
assert six(printed)["holds"] is True, "a whole float printed as an integer, or a field the statement never carried, is not a disagreement"

cases = [
    (("time", "build", "invocation_s"), 1.0, "time.build"),
    (("time", "lead_s"), 1.0, "time.lead_s"),
    (("time", "waiting_s"), 1.0, "time.waiting_s"),
    (("time", "not_read"), ["build-02"], "time.not_read"),
    (("cost", "usd"), 0.5, "cost.usd"),
    (("cost", "by_adapter_usd"), {"alpha": 0.5}, "cost.by_adapter_usd"),
    (("cost", "partial"), False, "cost.partial"),
    (("cost", "not_read"), [], "cost.not_read"),
    (("cost", "rate_usd"), 1.0, "cost.rate_usd"),
]
for path, value, named in cases:
    forged = copy.deepcopy(true)
    node = forged
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    got = six(forged)
    assert got["holds"] is False, (path, got)
    assert "item 1" in got["reason"] and named in got["reason"], (path, got)

one_block = copy.deepcopy(true)
del one_block["cost"]
assert six(one_block)["holds"] is False, "time without cost held"

before = copy.deepcopy(true)
del before["time"]
del before["cost"]
assert six(before)["holds"] is True, "a statement sealed before the accounting existed was checked"
PY
cd "$here"
rm -rf "$tmp"
