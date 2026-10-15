#!/bin/sh
# Depends only on the product and the standard library.
# exit 2: the schema or attavo.types could not be read; exit 1: no intervention type carries the enum as a Literal.
exec python3 - "$(pwd)" <<'PY'
import json, sys, typing
from pathlib import Path
here = Path(sys.argv[1])
sys.path.insert(0, str(here))
try:
    from attavo import types
    schema = json.loads((here / "schema" / "attestation.schema.json").read_text())
    enum = set(schema["properties"]["interventions"]["items"]["properties"]["class"]["enum"])
except Exception as exc:
    print("SETUP: %r" % (exc,)); sys.exit(2)
found = None
for name in dir(types):
    obj = getattr(types, name)
    hints = getattr(obj, "__annotations__", None)
    if isinstance(hints, dict) and "class" in hints and "sha" in hints:
        try:
            found = typing.get_type_hints(obj)["class"]
        except Exception:
            found = hints["class"]
if found is None or typing.get_origin(found) is not typing.Literal or set(typing.get_args(found)) != enum:
    print("ASSERT: the intervention class type is %r, not Literal%r" % (found, sorted(enum))); sys.exit(1)
print("ok")
PY
