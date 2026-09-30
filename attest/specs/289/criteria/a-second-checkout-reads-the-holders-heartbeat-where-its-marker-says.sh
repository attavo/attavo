#!/bin/sh
set -e
here=$(pwd)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/attavo.XXXXXX")
cp -R "$here/tests/fixtures/valid/." "$tmp/tenant"
cd "$tmp/tenant"
git init -q -b main .
git config user.email fixture@attavo.invalid
git config user.name "fixture"
git add -A
git commit -q -m install
mkdir -p "$tmp/other-checkout-git/attavo"
python3 - "$here" "$tmp/tenant" "$tmp/other-checkout-git/attavo" <<'PY'
import json, os, socket, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from attavo.doctor import write_invocation_marker
from attavo.drive import read_invocation
root, elsewhere = Path(sys.argv[2]), Path(sys.argv[3])
records = root / "records" / "1"
marker = write_invocation_marker(root, records, "build", 1)
info = json.loads(marker.read_text())
own = Path(os.popen(f"git -C {root} rev-parse --absolute-git-dir").read().strip()) / "attavo"
assert info.get("events") == str(own), info
now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
info = {"at": now, "host": socket.gethostname(), "sha": info["sha"], "product_version": "x",
        "events": str(elsewhere)}
(elsewhere / "build-01.heartbeat").write_text(str(os.getpid()))
assert read_invocation(root, 600, marker, info).cls == "running", "a fresh heartbeat where the marker says"
(elsewhere / "build-01.heartbeat").unlink()
assert read_invocation(root, 600, marker, info).cls == "running", "no first beat yet, younger than the bound"
old = dict(info, at="2026-09-17T13:16:08Z")
assert read_invocation(root, 600, marker, old).cls == "died", "no heartbeat, older than the bound"
legacy = {k: v for k, v in info.items() if k != "events"}
assert read_invocation(root, 600, marker, legacy).cls == "died", "a marker naming no directory reads as before"
PY
cd "$here"
for t in test_a_second_checkout_on_this_host_reads_the_first_ones_worker_as_running test_the_holders_heartbeat_is_read_where_its_marker_says test_the_marker_a_pass_writes_names_its_checkouts_heartbeat_directory
do
  grep -q "^def $t(" tests/test_pass.py
done
rm -rf "$tmp"
