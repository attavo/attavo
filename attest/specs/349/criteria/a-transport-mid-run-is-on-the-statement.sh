#!/bin/sh
# exit 2: the scenario could not be set up; exit 1: the statement is wrong.
exec python3 - "$(pwd)" <<'PY'
import json, shutil, sys, tempfile
from pathlib import Path
here = Path(sys.argv[1])
sys.path.insert(0, str(here / "tests"))
tmp = Path(tempfile.mkdtemp(prefix="attavo-349-"))
try:
    from helpers import commit_all, git, make_tenant
    from test_attest import configure_forge, merge_into_main, run_attavo
    root = make_tenant(tmp, "build_valid")
    configure_forge(root)
    git(root, "checkout", "-b", "attavo/1-x")
    for args, msg in ((("spec-gate", "1"), "spec-gate record"), (("approve", "1", "--by", "alice"), "approval record")):
        r = run_attavo(root, *args)
        if r.returncode != 0:
            raise RuntimeError(f"{args[0]}: {r.stdout}{r.stderr}")
        commit_all(root, f"attavo: {msg} for #1")
    git(root, "checkout", "main")
    (root / "OTHER.md").write_text("landed on main while the item was in flight\n")
    commit_all(root, "an unrelated change on main")
    git(root, "checkout", "attavo/1-x")
    git(root, "merge", "--no-ff", "-m", "Merge main into attavo/1-x", "main")
    transport = git(root, "rev-parse", "HEAD").strip()
    for stage in ("build", "verify"):
        r = run_attavo(root, stage, "1")
        if r.returncode != 0:
            raise RuntimeError(f"{stage}: {r.stdout}{r.stderr}")
        commit_all(root, f"attavo: {stage} record for #1")
    merge_into_main(root, "attavo/1-x")
    run = run_attavo(root, "attest")
    if run.returncode != 0:
        raise RuntimeError(f"attest: {run.stdout}{run.stderr}")
    statement = json.loads(run.stdout)
except Exception as exc:
    print(f"SETUP: {exc}")
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(2)
shutil.rmtree(tmp, ignore_errors=True)
iv = statement.get("interventions") or []
acts = {a.get("act") for a in statement.get("acts") or [] if isinstance(a, dict)}
if not any(isinstance(i, dict) and i.get("sha") == transport and i.get("class") == "transport" for i in iv):
    print(f"ASSERT: the transport {transport[:12]} is not among interventions: {iv}")
    sys.exit(1)
if not {"approve", "merge"} <= acts:
    print(f"ASSERT: acts lack approve or merge: {statement.get('acts')}")
    sys.exit(1)
print("ok")
PY
