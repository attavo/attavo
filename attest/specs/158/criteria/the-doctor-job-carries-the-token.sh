#!/bin/sh
set -e
python3 - <<'PY'
import re, sys
text = open(".github/workflows/ci.yml").read()
m = re.search(r"^  doctor:\n(.*?)(?=^  [A-Za-z_-]+:\n|\Z)", text, re.S | re.M)
assert m, "no doctor job in ci.yml"
job = m.group(1)
head, _, steps = job.partition("    steps:\n")
assert "GH_TOKEN" in head, "GH_TOKEN is not set at the doctor job's level"
assert "attestations: read" in head, "the doctor job lacks attestations: read"
pytest_lines = [l for l in steps.splitlines() if "pytest" in l and "test_doctor.py" in l]
assert pytest_lines, "no pytest step over test_doctor.py in the doctor job"
assert all("every_attested_item_is_sealed" in l for l in pytest_lines), "the doctor job's pytest step does not select the sealed test"
PY
