#!/bin/sh
set -e
python3 - <<'PY'
import json
for stage in ("build", "verify"):
    enum = json.load(open(f"schema/records/{stage}.schema.json"))["properties"]["stop"]["properties"]["class"]["enum"]
    assert "died" in enum and "hung" in enum, (stage, enum)
drive = json.load(open("schema/manifest.schema.json"))["properties"]["drive"]
assert "stall_after_s" in drive["properties"], drive
PY
grep -q '"died", "hung"' bin/attavo
