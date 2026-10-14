#!/bin/sh
# exit 2: the scenario could not be set up; exit 1: the schema accepted a class the producer never emits.
exec python3 - "$(pwd)" <<'PY'
import json, shutil, sys, tempfile
from pathlib import Path
here = Path(sys.argv[1])
sys.path.insert(0, str(here / "tests"))
tmp = Path(tempfile.mkdtemp(prefix="attavo-353-"))
try:
    import jsonschema
    from test_attest import STATEMENT_SCHEMA
    from test_attest_time import INVOCATIONS, attest, make_item
    root, _ = make_item(tmp, INVOCATIONS)
    statement = attest(root)
    good = {"class": "transport", "sha": "a" * 40, "by": "someone", "at": "2026-01-01T00:00:00Z"}
    statement["interventions"] = [good]
    jsonschema.validate(statement, STATEMENT_SCHEMA)
except Exception as exc:
    print(f"SETUP: {exc}")
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(2)
shutil.rmtree(tmp, ignore_errors=True)
statement["interventions"] = [{**good, "class": "bogus"}]
try:
    jsonschema.validate(statement, STATEMENT_SCHEMA)
except jsonschema.ValidationError:
    print("ok")
    sys.exit(0)
print("ASSERT: the schema accepted an intervention of class 'bogus'")
sys.exit(1)
PY
