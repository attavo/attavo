#!/bin/sh
# exit 2: the scenario could not be set up; exit 1: the claim's at and the version's at disagree.
exec python3 - "$(pwd)" <<'PY'
import os, shutil, sys, tempfile
from pathlib import Path
here = Path(sys.argv[1])
sys.path.insert(0, str(here))
sys.path.insert(0, str(here / "tests"))
tmp = Path(tempfile.mkdtemp(prefix="attavo-339-"))
try:
    from attavo import plan as plan_mod, store as store_mod
    from test_plan import tenant_with_plan
    ticks = iter(range(1, 1000))
    def ticking():
        return f"2026-01-01T00:00:{next(ticks):02d}Z"
    plan_mod.now_iso = ticking
    store_mod.now_iso = ticking
    root = tenant_with_plan(tmp)
    os.chdir(root)
    rc = plan_mod.cmd_plan(["claims", "#262", "attavo/frontier.py", "--by", "bob"])
    if rc != 0:
        raise RuntimeError(f"plan claims exited {rc}")
    doc = store_mod.get(root, "plan")
    claim_at = doc["body"]["claims"]["262"]["at"]
    version_at = doc["at"]
except Exception as exc:
    print(f"SETUP: {exc}")
    os.chdir(here)
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(2)
os.chdir(here)
shutil.rmtree(tmp, ignore_errors=True)
if claim_at != version_at:
    print(f"ASSERT: the claim is stamped {claim_at} and the version {version_at}: two clock reads")
    sys.exit(1)
print("ok")
PY
