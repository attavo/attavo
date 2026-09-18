#!/bin/sh
set -e
here=$(pwd)
tmp=$(mktemp -d)
cd "$tmp"
git init -q -b main .
git config user.email doctor@attavo.invalid
git config user.name "doctor fixture"
printf '[paths]\nrecords = "records"\n\n[forge]\nattestation_command = "sh ./fake-attest"\n' > attavo.toml
printf '#!/bin/sh\nread sha\ngrep -qx "$sha" sealed.txt && printf %s "{\\"_type\\": \\"https://in-toto.io/Statement/v1\\"}"\n' > fake-attest
printf '' > sealed.txt
git add attavo.toml fake-attest sealed.txt
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
m1=$(git rev-parse HEAD)
printf '%s\n' "$m1" > sealed.txt
git add sealed.txt
git commit -q -m "seal item 1"
git checkout -q -b attavo/2-x
mkdir -p records/2
for stage in spec approval build verify
do
  printf '{"status": "pass"}' > "records/2/$stage-01.json"
  git add records
  git commit -q -m "$stage record for item 2"
done
git checkout -q --detach
git merge -q --no-ff -m "synthetic test merge of attavo/2-x into main" main
cd "$here"
python3 bin/attavo doctor --json "$tmp" > "$tmp/doctor-no-ref.json" || true
cd "$tmp"
git update-ref refs/remotes/origin/main "$m1"
git symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main
cd "$here"
python3 bin/attavo doctor --json "$tmp" > "$tmp/doctor-ref.json" || true
python3 - "$tmp/doctor-no-ref.json" "$tmp/doctor-ref.json" <<'PY'
import json, sys
def proof(path):
    d = json.load(open(path))
    return [x for x in d["proofs"] if x["proof"] == "every-attested-item-is-sealed"][0]
noref = proof(sys.argv[1])
assert noref["holds"] is False and "refs/remotes/origin/HEAD" in noref["reason"], noref
ref = proof(sys.argv[2])
assert ref["holds"] is True, ref
assert ref["sealed"] == ["1"] and ref["not_merged"] == ["2"] and ref["unsealed"] == [], ref
PY
rm -rf "$tmp"
