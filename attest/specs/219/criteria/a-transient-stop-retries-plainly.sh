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
if [ "${ATTAVO_STAGE:-build}" = build ] && [ ! -f "$ATTAVO_RECORDS_DIR/build-01.json" ]; then
  echo '{"status":"failed","question":null,"class":"transient","detail":"API error: overloaded, retry after 30s","model":"fixture","model_version":null,"adapter_version":"fixture-1","tokens":{"input":1,"output":1},"cost_usd":0.0}'
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
$A drive 1 >"$tmp/pass1.out" 2>&1
wait_on_origin records/1/build-01.json 60
show records/1/build-01.json | python3 -c 'import sys,json; r=json.load(sys.stdin); assert r["status"] == "failed", r["status"]; assert r["stop"]["class"] == "transient", r["stop"]'
pass_until_on_origin records/1/build-02.json 40
show records/1/build-02.json | python3 -c 'import sys,json; r=json.load(sys.stdin); assert r["status"] == "pass", r["status"]; assert r.get("retry_of") == "build-01", r.get("retry_of")'
test -z "$(git -C "$tmp/origin.git" ls-tree --name-only main:records/1/ | grep '^counsel-')"
cd "$here"
rm -rf "$tmp"
