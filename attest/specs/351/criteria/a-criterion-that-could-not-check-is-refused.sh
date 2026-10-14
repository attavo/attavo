#!/bin/sh
# Depends only on the product, tests/helpers.py and the standard library.
# exit 2: the scenario could not be set up; exit 1: the gate accepted a criterion that could not check.
exec python3 - "$(pwd)" <<'PY'
import json, shutil, subprocess, sys, tempfile
from pathlib import Path
here = Path(sys.argv[1])
sys.path.insert(0, str(here / "tests"))
tmp = Path(tempfile.mkdtemp(prefix="attavo-351-"))
try:
    from helpers import commit_all, make_tenant
    root = make_tenant(tmp, "valid")
    spec_dir = root / "specs" / "1"
    spec_md = spec_dir / "spec.md"
    head = spec_md.read_text().split("## Criteria")[0]
    spec_md.write_text(head + "## Criteria\n\n- [structural] checks_marker.sh: the marker file exists\n"
                       "- [behavioural] broken.sh: a scenario that cannot be set up\n")
    criteria = spec_dir / "criteria"
    for old in criteria.iterdir():
        old.unlink()
    for name, body in (("checks_marker.sh", "test -f src/marker.txt || exit 1"), ("broken.sh", "exit 2")):
        (criteria / name).write_text("#!/bin/sh\n" + body + "\n")
        (criteria / name).chmod(0o755)
    shutil.rmtree(spec_dir / "stub")
    for rel, content in (("src/marker.txt", "ok\n"), ("src/logic.txt", "todo\n")):
        (spec_dir / "stub" / rel).parent.mkdir(parents=True, exist_ok=True)
        (spec_dir / "stub" / rel).write_text(content)
    commit_all(root, "criteria with kinds")
    run = subprocess.run([sys.executable, str(here / "bin" / "attavo"), "spec-gate", "1"], cwd=str(root),
                         capture_output=True, text=True, check=False)
    recs = sorted((root / "records" / "1").glob("spec-*.json"))
    rec = json.loads(recs[-1].read_text())
except Exception as exc:
    print("SETUP: %r" % (exc,))
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(2)
shutil.rmtree(tmp, ignore_errors=True)
if run.returncode != 1 or rec.get("refusal") != "criterion could not check (exit 2): broken.sh":
    print("ASSERT: the gate said exit %d, status %s, refusal %r" % (run.returncode, rec.get("status"), rec.get("refusal")))
    sys.exit(1)
print("ok")
PY
