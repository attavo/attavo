"""attavo claims: what is in flight, and whether an item collides with it.

Work in flight is read from git alone, needing nothing from the session
doing it: every branch under refs/remotes/origin/ whose tip is not on the
default branch, with the paths it changes (outside paths.records and
paths.specs) and, when it carries an item's spec, that spec's Claims. The
default branch is refs/remotes/origin/HEAD, as everywhere else in the
product (issue #52, #169); unset, this refuses by name rather than guess.

With an item, the item's own Claims are compared with every other branch
in flight, and any overlap is named with the branch and the paths. A claim
is a glob (fnmatch, as build's boundary reads it); two claims overlap when
either matches the other, or, where one carries a wildcard, when their
literal prefixes nest -- "may collide" is reported, never "cannot".

Nothing here writes, gates, or blocks. It is a read, for the operator,
the Plan area and the screen (the Plan Map's L1, 2026-09-27).
"""
from __future__ import annotations

import fnmatch
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from attavo.common import MANIFEST, derive_item_id, read_manifest
from attavo.spec import parse_spec

WILDCARDS = "*?["
ITEM_BRANCH = re.compile(r"^attavo/(\d+)-")


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)


def _literal_prefix(claim: str) -> str:
    cut = min((claim.index(c) for c in WILDCARDS if c in claim), default=len(claim))
    return claim[:cut]


def overlap(a: str, b: str) -> bool:
    """Whether two claims (paths or globs) may name the same path."""
    if a == b or fnmatch.fnmatch(a, b) or fnmatch.fnmatch(b, a):
        return True
    if any(c in a for c in WILDCARDS) or any(c in b for c in WILDCARDS):
        pa, pb = _literal_prefix(a), _literal_prefix(b)
        return pa.startswith(pb) or pb.startswith(pa)
    return False


def default_branch(root: Path) -> str | None:
    run = _git(root, "symbolic-ref", "-q", "refs/remotes/origin/HEAD")
    ref = run.stdout.strip()
    return ref or None


def in_flight(root: Path, m: dict[str, Any], default: str) -> list[dict[str, Any]]:
    specs_rel = m["paths"]["specs"].strip("/")
    records_rel = m["paths"]["records"].strip("/")
    refs = _git(root, "for-each-ref", "--format=%(refname)", "refs/remotes/origin/").stdout.split()
    target = _git(root, "rev-parse", default).stdout.strip()
    flights = []
    for ref in sorted(refs):
        if ref in ("refs/remotes/origin/HEAD", default):
            continue
        if _git(root, "rev-parse", ref).stdout.strip() == target:
            continue
        if _git(root, "merge-base", "--is-ancestor", ref, default).returncode == 0:
            continue
        branch = ref[len("refs/remotes/origin/"):]
        changed_all = _git(root, "diff", "--name-only", f"{default}...{ref}").stdout.split()
        changed = sorted(p for p in changed_all
                         if not (p.startswith(specs_rel + "/") or p.startswith(records_rel + "/")))
        spec_md = next((p for p in changed_all
                        if re.fullmatch(re.escape(specs_rel) + r"/[^/]+/spec\.md", p)), None)
        declared: list[str] = []
        item = None
        if spec_md:
            item = spec_md.split("/")[-2]
            declared = _claims_of_text(_git(root, "show", f"{ref}:{spec_md}").stdout)
        mm = ITEM_BRANCH.match(branch)
        if item is None and mm:
            item = mm.group(1)
        if changed or declared:
            flights.append({"branch": branch, "item": item, "declared": declared, "changed": changed})
    return flights


def _claims_of_text(text: str) -> list[str]:
    """A spec's Claims from its text, through parse_spec itself so the
    two can never disagree about what a claim is."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "spec.md"
        p.write_text(text)
        parsed, _ = parse_spec(p)
    return list((parsed or {}).get("claims") or [])


def collisions(claims: list[str], flights: list[dict[str, Any]], item: str | None) -> list[dict[str, Any]]:
    found = []
    for f in flights:
        if item is not None and f["item"] == item:
            continue
        theirs = f["declared"] + f["changed"]
        paths = sorted({t for t in theirs for c in claims if overlap(c, t)})
        if paths:
            found.append({"branch": f["branch"], "item": f["item"], "paths": paths})
    return found


def cmd_claims(argv: list[str]) -> int:
    as_json = "--json" in argv
    args = [a for a in argv if a != "--json"]
    root = Path(".").resolve()
    m = read_manifest(root)
    if m is None:
        print(f"attavo claims: no {MANIFEST} at {root}", file=sys.stderr)
        return 2
    default = default_branch(root)
    if default is None:
        print("attavo claims: refused: refs/remotes/origin/HEAD is not set; "
              "run `git remote set-head origin -a`", file=sys.stderr)
        return 2
    item = args[0] if args else derive_item_id(root)
    flights = in_flight(root, m, default)
    claims: list[str] = []
    found: list[dict[str, Any]] = []
    if item is not None:
        spec_md = root / m["paths"]["specs"] / item / "spec.md"
        if not spec_md.is_file():
            print(f"attavo claims: no spec at {spec_md.parent}", file=sys.stderr)
            return 2
        parsed, refusal = parse_spec(spec_md)
        if parsed is None:
            print(f"attavo claims: refused: the spec does not parse: {refusal}", file=sys.stderr)
            return 2
        claims = list(parsed["claims"])
        found = collisions(claims, flights, item)
    if as_json:
        print(json.dumps({"default": default, "in_flight": flights, "item": item,
                          "claims": claims, "collisions": found}, indent=2))
    else:
        print(f"in flight against {default[len('refs/remotes/'):]}: {len(flights)} branch(es)")
        for f in flights:
            label = f"item {f['item']}" if f["item"] else "no spec"
            paths = f["declared"] + [p for p in f["changed"] if p not in f["declared"]]
            print(f"  {f['branch']} ({label}): {', '.join(paths)}")
        if item is not None:
            if found:
                print(f"item {item} collides with {len(found)} branch(es) in flight:")
                for c in found:
                    print(f"  {c['branch']}: {', '.join(c['paths'])}")
            else:
                print(f"item {item}: no collision with work in flight")
    return 1 if found else 0
