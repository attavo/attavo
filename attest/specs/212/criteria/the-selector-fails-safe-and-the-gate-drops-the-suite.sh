#!/bin/sh
set -e
S="python3 .attavo/bin/select-tests"
test "$(printf 'attavo/a_module_nobody_mapped.py\n' | $S)" = "tests"
test "$(printf 'attavo/report.py\n' | $S)" = "tests/test_report.py"
test -z "$(printf 'README.md\nattest/records/1/build-01.json\n' | $S)"
python3 - <<'PY'
import tomllib
m = tomllib.load(open("attavo.toml", "rb"))
assert "pytest tests" not in m["checks"]["command"] and "mypy" in m["checks"]["command"], m["checks"]
assert m["checks"]["probe"] and isinstance(m["checks"]["probe_timeout_s"], int), m["checks"]
assert isinstance(m["tests"]["call_timeout_s"], int), m["tests"]
PY
