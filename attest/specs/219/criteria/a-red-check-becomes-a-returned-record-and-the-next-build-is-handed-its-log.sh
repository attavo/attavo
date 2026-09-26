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
  n=$(ls "$ATTAVO_RECORDS_DIR" | grep -c '^build-.*\.started$')
  cp "${ATTAVO_FEEDBACK:-/dev/null}" "$ATTAVO_FIXTURE_FEEDBACK_COPY.$n" 2>/dev/null || true
  if [ -f built.txt ]; then
    echo "built again ($n)" > built.txt
    git add built.txt
    git commit -q -m "fixture: write built.txt again"
    echo '{"status":"done","question":null,"model":"fixture","model_version":null,"adapter_version":"fixture-1","tokens":{"input":1,"output":1},"cost_usd":0.0}'
    exit 0
  fi
fi
exec sh "$ATTAVO_FIXTURE_PRODUCT/adapters/fixture/run"
ADAPTER
cat > "$tmp/forge.sh" <<'FORGE'
#!/bin/sh
n=$(cat "$ATTAVO_FIXTURE_FORGE_COUNT" 2>/dev/null || echo 0)
n=$((n+1))
printf '%s' "$n" > "$ATTAVO_FIXTURE_FORGE_COUNT"
if [ "$n" = 1 ]; then printf 'typecheck failure\n'; else printf 'typecheck success\n'; fi
FORGE
cat > "$tmp/checklog.sh" <<'LOG'
#!/bin/sh
printf 'attavo/x.py:12: error: Incompatible return value type\n'
LOG
export ATTAVO_FIXTURE_FORGE_COUNT="$tmp/forge-count" ATTAVO_FIXTURE_FEEDBACK_COPY="$tmp/feedback"
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
python3 - "$tmp" <<'PY'
import sys, re
from pathlib import Path
tmp = sys.argv[1]
p = Path("attavo.toml")
s = p.read_text()
s = s.replace("checks_command = \"printf 'ci success\\\\n'\"", f"checks_command = \"sh {tmp}/forge.sh\"\ncheck_log_command = \"sh {tmp}/checklog.sh\"")
p.write_text(s)
PY
grep -q "forge.sh" attavo.toml
git add -A
git commit -q -m "a forge that reads red once"
git push -q origin HEAD:main 2>/dev/null
$A drive 1 >"$tmp/pass1.out" 2>&1
wait_on_origin records/1/build-01.json 60
pass_until_on_origin records/1/verify-01.json 20
show records/1/verify-01.json | python3 -c 'import sys,json; r=json.load(sys.stdin); assert r["status"] == "returned", r["status"]; assert r["stop"]["class"] == "returned", r["stop"]; assert "typecheck" in r["stop"]["signature"], r["stop"]'
pass_until_on_origin records/1/build-02.json 40
show records/1/build-02.json | python3 -c 'import sys,json; r=json.load(sys.stdin); assert r["status"] == "pass", r["status"]; assert r.get("retry_of") == "build-01", r.get("retry_of")'
grep -q "typecheck" "$tmp/feedback.2"
grep -q "Incompatible return value type" "$tmp/feedback.2"
pass_until_on_origin records/1/verify-02.json 40
show records/1/verify-02.json | python3 -c 'import sys,json; r=json.load(sys.stdin); assert r["status"] == "pass", r["status"]'
test "$(git -C "$tmp/origin.git" log --format=%an main | grep -vc 'attavo driver\|attavo executor\|fixture')" = 0
cd "$here"
rm -rf "$tmp"
