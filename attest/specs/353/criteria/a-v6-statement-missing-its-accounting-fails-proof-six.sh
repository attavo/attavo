#!/bin/sh
# exit 2: the scenario could not be set up; exit 1: proof six held a statement it should have failed.
exec python3 - "$(pwd)" <<'PY'
import json, shutil, sys, tempfile
from pathlib import Path
here = Path(sys.argv[1])
sys.path.insert(0, str(here / "tests"))
tmp = Path(tempfile.mkdtemp(prefix="attavo-353-"))
try:
    from test_attest_time import INVOCATIONS, attest, make_item, proof_six, seal
    root, merge = make_item(tmp, INVOCATIONS)
    statement = attest(root)
    forged = json.loads(json.dumps(statement))
    del forged["time"]
    del forged["cost"]
    forged["acts"] = [{"act": "approve", "sha": "f" * 40, "by": "nobody", "at": "2026-01-01T00:00:00Z"}]
    seal(root, merge, forged, "https://attavo.dev/attestation/v6")
    result = proof_six(root)
except Exception as exc:
    print(f"SETUP: {exc}")
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(2)
shutil.rmtree(tmp, ignore_errors=True)
if result.get("holds") is not False or "item 1" not in str(result.get("reason", "")):
    print(f"ASSERT: proof six held a v6 statement with no time or cost and forged acts: {result}")
    sys.exit(1)
print("ok")
PY
