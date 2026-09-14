"""No template carries a github.com/<owner>/<name> URL, in code or in a
comment. A template is copied verbatim into every tenant, and tenant
zero's factory-doctor scans its workflows for hardcoded repository URLs
(its coupling check matches github.com/<x>/<y>); the v0.19.0 pin
(attavo/code-factory, branch pin-v0.19.0, 2026-09-14) was refused on a
docs URL in the spec-gate template's header comment, cited there by
#116's lane. A page is cited by name in a template and by URL in the
pull request. Written and run red against the v0.19.0 template, then
the comment was changed.
"""
from __future__ import annotations

import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates" / "workflows"
REPOSITORY_URL = re.compile(r"github\.com/[\w-]+/[\w.-]+")


def test_no_template_carries_a_repository_url():
    offenders = []
    for path in sorted(TEMPLATES.glob("*.yml")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if REPOSITORY_URL.search(line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, "\n".join(offenders)
