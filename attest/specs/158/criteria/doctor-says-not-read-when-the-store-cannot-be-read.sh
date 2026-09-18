#!/bin/sh
set -e
here=$(pwd)
tmp=$(mktemp -d)
cd "$tmp"
git init -q -b main .
git config user.email doctor@attavo.invalid
git config user.name "doctor fixture"
printf '[paths]\nrecords = "records"\n\n[forge]\nattestation_command = "sh ./unreadable-store"\n' > attavo.toml
printf '#!/bin/sh\nread sha\necho "gh: not logged in" >&2\nexit 2\n' > unreadable-store
git add attavo.toml unreadable-store
git commit -q -m install
git checkout -q -b attavo/1-x
mkdir -p records/1
for stage in spec approval build verify
do
  printf '{"status": "pass"}' > "records/1/$stage-01.json"
  git add records
  git commit -q -m "$stage record for item 1"
done
git checkout -q main
git merge -q --no-ff -m "Merge branch 'attavo/1-x'" attavo/1-x
git update-ref refs/remotes/origin/main "$(git rev-parse main)"
git symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main
cd "$here"
python3 bin/attavo doctor --json "$tmp" > "$tmp/doctor.json" || true
python3 - "$tmp/doctor.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
p = [x for x in d["proofs"] if x["proof"] == "every-attested-item-is-sealed"][0]
assert p["holds"] is False, p
assert "not read" in p["reason"] and "unreadable-store" in p["reason"] and "exited 2" in p["reason"], p
assert p["not_read"] == ["1"] and p["unsealed"] == [] and p["sealed"] == [], p
PY
rm -rf "$tmp"
