"""attavo frontier: the waves, built by the product.

Three reads, nothing written. The plan's latest version in the store
(attavo/plan.py) gives the order: epic rank, then issue rank within the
epic; an open issue the plan does not place is Backlog, last, by number.
The tenant's issues through [forge].issues_command (attavo/issues.py)
give each issue's state, class and, before it has a spec, its Claims.
Work in flight from git (attavo/claims.py) gives wave 0.

Wave 0 is the work holding claims now: every branch not on the default
branch, with the paths it declares and changes. Wave 1 walks the order
and places each open issue whose predecessors (its depends and ruled
edges) are closed or placed in an earlier wave, whose Claims overlap no
claim already placed in the wave, none still held in wave 0, and -- strict
opportunism (ruled 2026-09-29) -- none of an open issue ranked above it
that is not yet placed, ready or not: a lower-ranked issue never takes a
path a higher-ranked one wants -- unless that issue waits on it, directly
or through others, since a predecessor never yields to its own dependents. Wave k+1 walks what is left as if the
waves before it had merged. Overlap is attavo.claims.overlap, the
product's only rule. An issue with no Claims is not placed: nothing shows
it disjoint from anything. Every placed issue carries a why line for each
reason it waited; wave 1's say nothing. The output has no dates: waves
are an order, not a forecast.

Epic progress is closed issues over all issues under the epic, as the
forge reports them, never typed.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from attavo import store
from attavo.claims import ITEM_BRANCH, _git, default_branch, in_flight, overlap
from attavo.common import MANIFEST, read_manifest
from attavo.issues import Issue, read_issues
from attavo.plan import BACKLOG, DOC
from attavo.records import RecordItemMismatch, _load_record

ORDER = ("epic rank, then issue rank within the epic; an open issue the plan does not place "
         "is Backlog, last, by number")
RECORD = re.compile(r"^(spec|approval|build|verify)-([0-9]{2,})\.(json|started)$")


def _overlaps(mine: list[str], theirs: list[str]) -> list[str]:
    return sorted({t for t in theirs for c in mine if overlap(c, t)} |
                  {c for c in mine for t in theirs if overlap(c, t)})


def branch_state(root: Path, ref: str, records_rel: str, item: str | None) -> str:
    """Where an item branch stands, from the records it carries: "spec
    pushed" (no gate record yet), "spec refused", "awaiting approval",
    "approved", "building", "in review". A branch with no item says
    "lane"."""
    if item is None:
        return "lane"
    listed = _git(root, "ls-tree", "--name-only", f"{ref}:{records_rel}/{item}").stdout.split()
    seen: dict[str, list[int]] = {}
    for name in listed:
        mm = RECORD.match(name)
        if mm:
            seen.setdefault(mm.group(1), []).append(int(mm.group(2)))
    if "verify" in seen:
        return "in review"
    if "build" in seen:
        return "building"
    if "approval" in seen:
        return "approved"
    if "spec" not in seen:
        return "spec pushed"
    name = f"spec-{max(seen['spec']):02d}.json"
    text = _git(root, "show", f"{ref}:{records_rel}/{item}/{name}").stdout
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / item / name
        path.parent.mkdir()
        path.write_text(text)
        try:
            status = _load_record(path, item).get("status")
        except (ValueError, RecordItemMismatch):
            status = None
    return "awaiting approval" if status == "pass" else "spec refused"


def compute(body: dict[str, Any], issues: list[Issue], flights: list[dict[str, Any]],
            states: dict[str, str] | None = None) -> dict[str, Any]:
    """The waves and epic progress, from the plan, the issues and the
    work in flight. Pure: the reads are the caller's."""
    states = states or {}
    by_number = {i["number"]: i for i in issues}
    epic_of: dict[int, str] = {}
    order: list[int] = []
    for e in body["epics"]:
        for n in e["issues"]:
            epic_of[n] = e["id"]
            order.append(n)
    unplaced = sorted(n for n, i in by_number.items() if i["state"] == "open" and n not in epic_of)
    for n in unplaced:
        epic_of[n] = BACKLOG
    backlog_at = next((k for k, e in enumerate(body["epics"]) if e["id"] == BACKLOG), len(body["epics"]))
    rank: list[int] = []
    for k, e in enumerate(body["epics"]):
        rank += e["issues"]
        if k == backlog_at:
            rank += unplaced
    preds: dict[int, list[tuple[int, str]]] = {}
    for g in body.get("edges", []):
        preds.setdefault(g["to"], []).append((g["from"], g["type"]))

    def ancestors(n: int) -> set[int]:
        """Every issue n waits on, directly or through others."""
        seen: set[int] = set()
        stack = [p for p, _ in preds.get(n, [])]
        while stack:
            p = stack.pop()
            if p not in seen:
                seen.add(p)
                stack += [q for q, _ in preds.get(p, [])]
        return seen

    def title(n: int) -> str:
        return by_number[n]["title"] if n in by_number else f"#{n} (not read from the forge)"

    wave0: list[dict[str, Any]] = []
    held: list[tuple[str, list[str]]] = []
    in_flight_items: set[int] = set()
    for f in flights:
        claims = f["declared"] + [p for p in f["changed"] if p not in f["declared"]]
        n = int(f["item"]) if f.get("item") and str(f["item"]).isdigit() else None
        if n is not None:
            in_flight_items.add(n)
        wave0.append({"issue": n, "branch": f["branch"], "title": title(n) if n else f["branch"],
                      "epic": epic_of.get(n) if n else None,
                      "class": by_number[n]["item_class"] if n in by_number else None,
                      "claims": claims, "spec_state": states.get(f["branch"], "lane" if n is None else "spec pushed"),
                      "why": []})
        held.append((f["branch"], claims))

    def is_open(n: int) -> bool:
        return n in by_number and by_number[n]["state"] == "open"

    remaining = [n for n in rank if is_open(n) and n not in in_flight_items]
    placed_wave: dict[int, int] = {n: 0 for n in in_flight_items}
    not_placed: list[dict[str, Any]] = []
    why: dict[int, list[str]] = {}

    def note(n: int, line: str) -> None:
        why.setdefault(n, [])
        if line not in why[n]:
            why[n].append(line)

    for n in list(remaining):
        if by_number[n]["claims"] is None:
            not_placed.append({"issue": n, "title": title(n), "epic": epic_of[n],
                               "why": ["no claims: the issue has no Claims section and no spec"]})
            remaining.remove(n)

    waves: list[list[dict[str, Any]]] = [wave0]
    k = 0
    while remaining:
        k += 1
        this_wave: list[tuple[int, list[str]]] = []
        progressed = False
        for idx, n in enumerate(remaining):
            mine = by_number[n]["claims"] or []
            blocked = False
            for p, kind in preds.get(n, []):
                if p in by_number and by_number[p]["state"] == "closed":
                    continue
                if placed_wave.get(p, k) < k:
                    continue
                note(n, f"waits on #{p} ({kind})")
                blocked = True
            if k == 1:
                for branch, claims in held:
                    hit = _overlaps(mine, claims)
                    if hit:
                        note(n, f"waits on branch {branch}: {', '.join(hit)}")
                        blocked = True
            for m, claims in this_wave:
                hit = _overlaps(mine, claims)
                if hit:
                    note(n, f"shares {', '.join(hit)} with #{m} in wave {k}")
                    blocked = True
            for m in remaining[:idx]:
                if placed_wave.get(m) is not None:
                    continue
                if n in ancestors(m):
                    # m waits on n: n never yields to its own dependents,
                    # or a rank that disagrees with an edge deadlocks both.
                    continue
                hit = _overlaps(mine, by_number[m]["claims"] or [])
                if hit:
                    note(n, f"yields {', '.join(hit)} to #{m}, ranked above")
                    blocked = True
            if not blocked:
                this_wave.append((n, mine))
                placed_wave[n] = k
                progressed = True
        if not progressed and k == 1 and held:
            # Nothing can start while the work in flight holds its paths;
            # wave 2 reads as if that work had merged. An empty wave 1 says so.
            waves.append([])
            continue
        if not progressed:
            for n in remaining:
                not_placed.append({"issue": n, "title": title(n), "epic": epic_of[n],
                                   "why": why.get(n) or ["cannot be ordered: its predecessors are never placed"]})
            break
        waves.append([{"issue": n, "title": title(n), "epic": epic_of[n],
                       "class": by_number[n]["item_class"], "claims": claims,
                       "spec_state": "no spec", "why": why.get(n, []) if k > 1 else []}
                      for n, claims in this_wave])
        remaining = [n for n in remaining if n not in placed_wave]

    epics = []
    for e in body["epics"]:
        members = list(e["issues"]) + (unplaced if e["id"] == BACKLOG else [])
        known = [n for n in members if n in by_number]
        epics.append({"id": e["id"], "title": e["title"],
                      "closed": sum(1 for n in known if by_number[n]["state"] == "closed"),
                      "total": len(known)})
    return {"order": ORDER, "epics": epics, "waves": waves, "not_placed": not_placed}


def cmd_frontier(argv: list[str]) -> int:
    as_json = "--json" in argv
    root = Path(".").resolve()
    m = read_manifest(root)
    if m is None:
        print(f"attavo frontier: no {MANIFEST} at {root}", file=sys.stderr)
        return 2
    default = default_branch(root)
    if default is None:
        print("attavo frontier: refused: refs/remotes/origin/HEAD is not set; "
              "run `git remote set-head origin -a`", file=sys.stderr)
        return 2
    try:
        env = store.get(root, DOC)
    except store.StoreRefusal as r:
        print(f"attavo frontier: not read: {r}", file=sys.stderr)
        return 2
    if env is None:
        print("attavo frontier: not read: no plan in the store", file=sys.stderr)
        return 2
    issues, why = read_issues(root, m)
    if issues is None:
        print(f"attavo frontier: {why}", file=sys.stderr)
        return 2
    flights = in_flight(root, m, default)
    records_rel = m["paths"]["records"].strip("/")
    states = {f["branch"]: branch_state(root, f"refs/remotes/origin/{f['branch']}", records_rel,
                                        f["item"] if f.get("item") and ITEM_BRANCH.match(f["branch"]) else None)
              for f in flights}
    out = compute(env["body"], issues, flights, states)
    out = {"default": default, "plan_version": env["version"], **out}
    if as_json:
        print(json.dumps(out, indent=2))
        return 0
    print(f"frontier against {default[len('refs/remotes/'):]}, plan version {env['version']}; order: {out['order']}")
    for e in out["epics"]:
        print(f"  {e['id']}: {e['closed']} of {e['total']} closed - {e['title']}")
    for k, wave in enumerate(out["waves"]):
        print(f"wave {k}{' (in flight)' if k == 0 else ''}: {len(wave)}")
        for x in wave:
            label = f"#{x['issue']}" if x["issue"] else x.get("branch", "")
            print(f"  {label} [{x['spec_state']}] {x['title']}")
            for line in x["why"]:
                print(f"      {line}")
    if out["not_placed"]:
        print(f"not placed: {len(out['not_placed'])}")
        for x in out["not_placed"]:
            print(f"  #{x['issue']} {x['title']}: {'; '.join(x['why'])}")
    return 0
