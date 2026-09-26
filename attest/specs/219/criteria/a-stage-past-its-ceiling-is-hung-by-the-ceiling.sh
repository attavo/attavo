#!/bin/sh
set -e
here=$(pwd)
export ATTAVO_FIXTURE_PRODUCT="$here"
tmp=$(mktemp -d "${TMPDIR:-/tmp}/attavo.XXXXXX")
cp -R "$here/tests/fixtures/build_valid/." "$tmp/tenant"
cd "$tmp/tenant"
git init -q -b main .
git config user.email fixture@attavo.invalid
git config user.name "fixture"
mkdir -p attavo/adapters/fixture
cat > attavo/adapters/fixture/run <<'ADAPTER'
#!/bin/sh
if [ "${ATTAVO_STAGE:-build}" = build ]; then
  sleep 25
fi
exec sh "$ATTAVO_FIXTURE_PRODUCT/adapters/fixture/run"
ADAPTER
mkdir -p attavo/adapters/fixture
printf '{"streams_events": false}\n' > attavo/adapters/fixture/conformance.json
cat >> attavo.toml <<'TOML'

[forge]
checks_command = "printf 'ci success\\n'"

[drive]
stall_after_s = 60
attempts = 3
retry_after_s = 1

[drive.build]
ceiling_s = 600

[drive.verify]
ceiling_s = 600
TOML
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
wait_on_origin() {
  i=0
  until git -C "$tmp/origin.git" cat-file -e "main:$1" 2>/dev/null
  do
    i=$((i+1))
    test "$i" -lt "$2"
    sleep 1
  done
}
pass_until_on_origin() {
  i=0
  until git -C "$tmp/origin.git" cat-file -e "main:$1" 2>/dev/null
  do
    i=$((i+1))
    test "$i" -lt "$2"
    $A drive 1 >"$tmp/pass.out" 2>&1 || true
    sleep 1
  done
}
show() {
  git -C "$tmp/origin.git" show "main:$1"
}
python3 - <<'PY'
from pathlib import Path
p = Path("attavo.toml")
p.write_text(p.read_text().replace("[drive.build]\nceiling_s = 600", "[drive.build]\nceiling_s = 2"))
PY
grep -q "ceiling_s = 2$" attavo.toml
git add -A
git commit -q -m "a two second build ceiling"
git push -q origin HEAD:main 2>/dev/null
$A drive 1 >"$tmp/pass1.out" 2>&1
sleep 3
$A drive 1 --report > "$tmp/report.json"
python3 - "$tmp/report.json" <<'PY'
import sys, json
r = json.load(open(sys.argv[1]))
assert r["state"] == "owes-recovery", r
assert "event" not in r["says"], r["says"]
PY
pass_until_on_origin records/1/build-01.json 20
show records/1/build-01.json | python3 -c 'import sys,json; r=json.load(sys.stdin); assert r["stop"]["class"] == "hung", r["stop"]; assert r["stop"]["bound"] == "ceiling", r["stop"]'
cd "$here"
rm -rf "$tmp"
