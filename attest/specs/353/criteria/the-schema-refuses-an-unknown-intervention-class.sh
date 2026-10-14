#!/bin/sh
# Depends only on the standard library.
# exit 2: the schema could not be read; exit 1: the intervention class is not the enum amend, launch, transport.
exec python3 - "$(pwd)" <<'PY'
import json, sys
from pathlib import Path
here = Path(sys.argv[1])
try:
    schema = json.loads((here / "schema" / "attestation.schema.json").read_text())
    cls = schema["properties"]["interventions"]["items"]["properties"]["class"]
except Exception as exc:
    print("SETUP: %r" % (exc,))
    sys.exit(2)
if sorted(cls.get("enum") or []) != ["amend", "launch", "transport"]:
    print("ASSERT: the intervention class is %r, not the enum amend, launch, transport" % (cls,))
    sys.exit(1)
print("ok")
PY
