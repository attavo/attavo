#!/bin/sh
# exit 2: the scenario could not be set up; exit 1: the gate accepted a criterion that could not check.
exec python3 - "$(pwd)" <<'PY'
import json, shutil, sys, tempfile
from pathlib import Path
here = Path(sys.argv[1])
sys.path.insert(0, str(here / "tests"))
tmp = Path(tempfile.mkdtemp(prefix="attavo-351-"))
try:
    from helpers import make_tenant
    from test_spec_gate import STRUCT, THIN_STUB, latest_record, run_attavo, set_criteria
    root = make_tenant(tmp, "valid")
    set_criteria(root, [STRUCT, "- [behavioural] broken.sh: a scenario that cannot be set up"],
                 {"checks_marker.sh": "test -f src/marker.txt || exit 1", "broken.sh": "exit 2"}, THIN_STUB)
    run = run_attavo(root, "spec-gate", "1")
    rec = latest_record(root, "1", "spec")
except Exception as exc:
    print(f"SETUP: {exc}")
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(2)
shutil.rmtree(tmp, ignore_errors=True)
if run.returncode != 1 or rec.get("refusal") != "criterion could not check (exit 2): broken.sh":
    print(f"ASSERT: the gate said exit {run.returncode}, status {rec.get('status')}, refusal {rec.get('refusal')!r}")
    sys.exit(1)
print("ok")
PY
