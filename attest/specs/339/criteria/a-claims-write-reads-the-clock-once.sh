#!/bin/sh
# Depends only on the product, tests/helpers.py and the standard library.
# exit 2: the scenario could not be set up; exit 1: the claim's at and the version's at disagree.
exec python3 - "$(pwd)" <<'PY'
import os, shutil, sys, tempfile
from pathlib import Path
here = Path(sys.argv[1])
sys.path.insert(0, str(here))
sys.path.insert(0, str(here / "tests"))
tmp = Path(tempfile.mkdtemp(prefix="attavo-339-"))
try:
    from helpers import make_tenant
    from attavo import plan as plan_mod, store as store_mod
    PLAN = {"schema": "plan/1",
            "epics": [{"id": "E4", "title": "Plan on the screen", "outcome": "waves on the screen", "issues": [262, 263]},
                      {"id": "backlog", "title": "Backlog", "issues": [231, 232]}],
            "edges": [], "policy": {"research": "not required"}}
    root = make_tenant(tmp, "valid")
    store_mod.put(root, "plan", PLAN, 0, "alice")
    ticks = iter(range(1, 1000))
    def ticking():
        return "2026-01-01T00:00:%02dZ" % next(ticks)
    plan_mod.now_iso = ticking
    store_mod.now_iso = ticking
    os.chdir(root)
    rc = plan_mod.cmd_plan(["claims", "#262", "attavo/frontier.py", "--by", "bob"])
    if rc != 0:
        raise RuntimeError("plan claims exited %d" % rc)
    doc = store_mod.get(root, "plan")
    claim_at = doc["body"]["claims"]["262"]["at"]
    version_at = doc["at"]
except Exception as exc:
    print("SETUP: %r" % (exc,))
    os.chdir(here)
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(2)
os.chdir(here)
shutil.rmtree(tmp, ignore_errors=True)
if claim_at != version_at:
    print("ASSERT: the claim is stamped %s and the version %s: two clock reads" % (claim_at, version_at))
    sys.exit(1)
print("ok")
PY
