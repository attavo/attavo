#!/bin/sh
set -e
here=$(pwd)
tmp=$(mktemp -d)
cd "$tmp"
git init -q -b main .
git config user.email doctor@attavo.invalid
git config user.name "doctor fixture"
printf '[paths]\nrecords = "records"\n\n[forge]\nattestation_command = "sh ./fake-attest"\n' > attavo.toml
printf '#!/bin/sh\nread sha\ngrep -qx "$sha" sealed.txt && printf %s "{\\"_type\\": \\"https://in-toto.io/Statement/v1\\", \\"subject\\": [{\\"digest\\": {\\"sha256\\": \\"$sha\\"}}]}"\n' > fake-attest
printf '' > sealed.txt
git add attavo.toml fake-attest sealed.txt
git commit -q -m install
for i in 1 2
do
  git checkout -q -b "attavo/$i-x"
  mkdir -p "records/$i"
  for stage in spec approval build verify
  do
    printf '{"status": "pass"}' > "records/$i/$stage-01.json"
    git add records
    git commit -q -m "$stage record for item $i"
  done
  git checkout -q main
  git merge -q --no-ff -m "Merge branch 'attavo/$i-x'" "attavo/$i-x"
done
git update-ref refs/remotes/origin/main "$(git rev-parse main)"
git symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main
m1=$(git log --merges --format=%H --grep="attavo/1-x" -1)
m2=$(git log --merges --format=%H --grep="attavo/2-x" -1)
printf '%s\n' "$m1" > sealed.txt
cd "$here"
python3 bin/attavo doctor --json "$tmp" > "$tmp/doctor-one.json" || true
printf '%s\n%s\n' "$m1" "$m2" > "$tmp/sealed.txt"
python3 bin/attavo doctor --json "$tmp" > "$tmp/doctor-both.json" || true
python3 - "$tmp/doctor-one.json" "$tmp/doctor-both.json" "$m2" <<'PY'
import json, sys
def proof(path):
    d = json.load(open(path))
    return [x for x in d["proofs"] if x["proof"] == "every-attested-item-is-sealed"][0]
one = proof(sys.argv[1])
assert one["holds"] is False, one
assert "item 2" in one["reason"] and sys.argv[3][:12] in one["reason"], one
assert one["unsealed"] == ["2"] and one["sealed"] == ["1"], one
both = proof(sys.argv[2])
assert both["holds"] is True and both["sealed"] == ["1", "2"], both
PY
rm -rf "$tmp"
