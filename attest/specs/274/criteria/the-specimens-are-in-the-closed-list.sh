#!/bin/sh
set -e
here=$(pwd)
PYTHONPATH="$here" python3 - <<'PY'
import re
from pathlib import Path
from attavo.spec import listed_root_causes
path = Path("attest/root-causes.md")
six = {"shared-working-copy", "deny-list-tool-surface", "dogfood-tests-told-apart-by-a-list",
       "liveness-by-proxy", "classification-by-reading-prose", "tests-on-real-clocks"}
listed = listed_root_causes(path)
assert sorted(listed) == sorted(six), listed
text = path.read_text()
assert "\n## Specimens\n" in text, "no Specimens section"
rows = {}
for line in text.splitlines():
    m = re.match(r"^\| (\d+) \| (.+?) \| (.+?) \| (.+?) \|$", line)
    if m:
        rows[int(m.group(1))] = (m.group(2), m.group(3), m.group(4))
assert sorted(rows) == list(range(1, 31)), sorted(rows)
causes = {c for _, c, _ in rows.values()}
for slug in six:
    assert any(c.startswith(slug) for c in causes), slug
for n, (_, cause, _) in rows.items():
    assert cause.split(",")[0] in six or cause in ("unconfirmed", "none: a ruling", "none: the screen's"), (n, cause)
for n in (19, 23, 24):
    assert rows[n][1] == "none: a ruling", (n, rows[n])
assert rows[17][1] == "unconfirmed" and "not reproduced on main" in rows[17][2], rows[17]
swc = next(l for l in text.splitlines() if l.startswith("- shared-working-copy:"))
assert "not reproduced on main" in swc, "the shared-working-copy line keeps the unconfirmed specimen unqualified"
PY
