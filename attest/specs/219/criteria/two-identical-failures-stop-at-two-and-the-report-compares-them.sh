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
  at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '{"at":"%s","kind":"tool_call","text":"fixture-tool waiting on lock 4471"}\n' "$at" >> "$ATTAVO_EVENTS"
  kill -9 $PPID
  sleep 5
  exit 1
fi
exec sh "$ATTAVO_FIXTURE_PRODUCT/adapters/fixture/run"
ADAPTER
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
n=0
while [ "$n" -lt 40 ] && ! git -C "$tmp/origin.git" cat-file -e main:records/1/build-02.json 2>/dev/null
do
  n=$((n+1))
  $A drive 1 >"$tmp/pass.out" 2>&1 || true
  sleep 1
done
git -C "$tmp/origin.git" cat-file -e main:records/1/build-02.json
show records/1/build-01.json | python3 -c 'import sys,json; r=json.load(sys.stdin); assert r["stop"]["class"] == "died", r["stop"]; assert "waiting on lock" in r["stop"]["signature"], r["stop"]'
show records/1/build-02.json | python3 -c 'import sys,json; r=json.load(sys.stdin); assert r["stop"]["class"] == "died", r["stop"]'
test "$(show records/1/build-01.json | python3 -c 'import sys,json; print(json.load(sys.stdin)["stop"]["signature"])')" = "$(show records/1/build-02.json | python3 -c 'import sys,json; print(json.load(sys.stdin)["stop"]["signature"])')"
n=0
while [ "$n" -lt 6 ]
do
  n=$((n+1))
  $A drive 1 >"$tmp/pass.out" 2>&1 || true
  sleep 1
done
git pull -q --ff-only origin main
test ! -f records/1/build-03.started
$A drive 1 --report > "$tmp/report.json"
python3 - "$tmp/report.json" <<'PY'
import sys, json
r = json.load(open(sys.argv[1]))
assert r["state"] == "stopped", r["state"]
assert len(r["attempts"]) == 2, r["attempts"]
assert r["signatures_match"] is True, r
assert any("again" in x for x in r["rulings_available"]), r["rulings_available"]
PY
cd "$here"
rm -rf "$tmp"
