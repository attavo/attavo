#!/bin/sh
set -e
here=$(pwd)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/attavo.XXXXXX")
cp -R "$here/tests/fixtures/build_valid/." "$tmp/tenant"
cd "$tmp/tenant"
git init -q -b main .
git config user.email fixture@attavo.invalid
git config user.name "fixture"
printf '\n[forge]\nchecks_command = "printf '"'"'ci success\\\\n'"'"'"\n\n[drive]\nstall_after_s = 60\n' >> attavo.toml
mkdir -p attavo/adapters/fixture
printf '#!/bin/sh\nif [ "${ATTAVO_STAGE:-build}" = build ]; then sleep 4; fi\nexec sh %s/adapters/fixture/run\n' "$here" > attavo/adapters/fixture/run
chmod +x attavo/adapters/fixture/run
git add -A
git commit -q -m install
git init -q --bare -b main "$tmp/origin.git"
git remote add origin "$tmp/origin.git"
git push -q origin HEAD:main 2>/dev/null
git update-ref refs/remotes/origin/main "$(git rev-parse HEAD)"
git symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main
export ATTAVO_HEARTBEAT_S=1
A="python3 $here/bin/attavo"
$A spec-gate 1 >/dev/null
$A approve 1 --by alice >/dev/null
git add -A
git commit -q -m approved
git push -q origin HEAD:main 2>/dev/null
started=$(date +%s)
$A drive 1 >"$tmp/pass.out" 2>&1
ended=$(date +%s)
test $((ended - started)) -lt 3
test ! -f records/1/build-01.json
git -C "$tmp/origin.git" cat-file -e main:records/1/build-01.started
i=0
until git -C "$tmp/origin.git" cat-file -e main:records/1/build-01.json 2>/dev/null
do
  i=$((i+1))
  test "$i" -lt 60
  sleep 1
done
git -C "$tmp/origin.git" show main:records/1/build-01.json | python3 -c 'import sys,json; r=json.load(sys.stdin); assert r["status"] == "pass", r["status"]'
cd "$here"
rm -rf "$tmp"
