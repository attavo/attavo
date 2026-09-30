"""The tenant's issues, read through [forge].issues_command.

The issue is the unit of work (ruled 2026-09-29): the plan in the store
(attavo/plan.py) orders issues, and the forge holds what the plan does
not -- each issue's title, whether it is open, when it closed, its
labels, and, before it has a spec, the paths it claims in a "## Claims"
section of its body. The product names no vendor: the tenant's command
prints the issues as JSON, one object per issue, and this module reads
it. The claims are parsed here, not by the tenant's command, so every
tenant's issues are read by one rule.

A read that cannot be made is "not read" with its reason, never an empty
list: no command in the manifest, a command that exits non-zero or runs
past its bound, or output that is not the shape below.

    [{"number": 264, "title": "...", "state": "open" | "closed",
      "closed_at": "<iso>" | null, "labels": ["..."], "body": "..."}]
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypedDict

from attavo.testtool import run_bounded

TIMEOUT_S = 60
STATES = ("open", "closed")


class Issue(TypedDict):
    number: int
    title: str
    state: str
    closed_at: str | None
    labels: list[str]
    claims: list[str] | None


def parse_claims(body: str) -> list[str] | None:
    """The paths under a "## Claims" heading, up to the next "## "
    heading: one "- path" line each. None when the body has no Claims
    section, which is not the same as a section that lists nothing."""
    lines = body.splitlines()
    starts = [i for i, line in enumerate(lines) if line.rstrip() == "## Claims"]
    if not starts:
        return None
    claims = []
    for line in lines[starts[0] + 1:]:
        if line.startswith("## "):
            break
        s = line.strip()
        if s.startswith("- "):
            path = s[2:].strip().strip("`")
            if path:
                claims.append(path)
    return claims


def _shape_error(raw: Any) -> str | None:
    if not isinstance(raw, list):
        return "the output is not a list"
    for i, x in enumerate(raw):
        if not isinstance(x, dict):
            return f"entry {i} is not an object"
        n = x.get("number")
        if not isinstance(n, int) or isinstance(n, bool) or n < 1:
            return f"entry {i} has no issue number"
        if not isinstance(x.get("title"), str):
            return f"#{n} has no title"
        if x.get("state") not in STATES:
            return f"#{n} has state {x.get('state')!r}, not open or closed"
        if x.get("closed_at") is not None and not isinstance(x.get("closed_at"), str):
            return f"#{n} has a closed_at that is not text"
        labels = x.get("labels", [])
        if not isinstance(labels, list) or not all(isinstance(lb, str) for lb in labels):
            return f"#{n} has labels that are not a list of names"
        if not isinstance(x.get("body", ""), str):
            return f"#{n} has a body that is not text"
    return None


def read_issues(root: Path, m: dict[str, Any]) -> tuple[list[Issue] | None, str | None]:
    """(issues, None), or (None, "not read: <why>")."""
    command = (m.get("forge") or {}).get("issues_command")
    if not command:
        return None, "not read: no [forge].issues_command"
    code, out, err, timed_out = run_bounded(command, root, "", TIMEOUT_S)
    if timed_out:
        return None, f"not read: [forge].issues_command ran past {TIMEOUT_S}s"
    if code != 0:
        last = (err.strip().splitlines() or [""])[-1]
        return None, f"not read: [forge].issues_command exited {code}" + (f": {last}" if last else "")
    try:
        raw = json.loads(out)
    except json.JSONDecodeError as e:
        return None, f"not read: [forge].issues_command printed no JSON ({e.msg})"
    bad = _shape_error(raw)
    if bad:
        return None, f"not read: [forge].issues_command: {bad}"
    issues: list[Issue] = [{"number": x["number"], "title": x["title"], "state": x["state"],
                            "closed_at": x.get("closed_at"), "labels": list(x.get("labels", [])),
                            "claims": parse_claims(x.get("body", "") or "")} for x in raw]
    return sorted(issues, key=lambda i: i["number"]), None
