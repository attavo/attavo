"""attavo plan: the Plan's order, kept in the product's datastore.

The plan is one document in the store (attavo/store.py), named "plan":
the epics in rank order, each with its issues in rank order; the edges
between issues ("depends", and "ruled" with who ruled it and when); the
draft claims of issues that have no spec yet (who set them and when); and
the policy lines. It holds no issue text or state -- the forge holds
those -- and it is never committed: priority is working
material the attestation does not bind to (the storage ruling), so a
re-rank is a new version of this document, not a commit, a pull request
or a merge. Git takes over when an issue becomes a spec.

The global order is epic rank, then issue rank within the epic. Backlog
is an epic like any other and must exist, so an issue always has a home.

    attavo plan get [--version V]
    attavo plan put FILE --base V --by LOGIN
    attavo plan log
    attavo plan rank <EPIC|#N> (--before <EPIC|#M> | --last) --by LOGIN
    attavo plan move #N --epic EPIC --by LOGIN
    attavo plan claims #N (<PATH>... | --clear) --by LOGIN

get prints the body as JSON on stdout and its version, author and time on
stderr, so `get > f`, an edit, and `put f --base V` round-trip. put writes
the next version or refuses: a stale base (naming the latest version and
its author), a body that fails the plan's shape, or a writer that landed
first. rank and move read the latest version, change one position or one
membership, and write the next. claims sets, or with --clear removes, the
paths an issue will change before it has a spec; once its branch exists
the branch's own claims govern (attavo/claims.py) and these are not read.
Nothing here writes to git.
"""
from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path
from typing import Any

from attavo import store
from attavo.common import MANIFEST, now_iso, read_manifest
from attavo import Command

DOC = "plan"
SCHEMA = "plan/1"
BACKLOG = "backlog"
EPIC_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
EDGE_TYPES = ("depends", "ruled")
EPIC_KEYS = {"id", "title", "outcome", "proof", "issues"}
EDGE_KEYS = {"type", "from", "to", "by", "at", "why", "evidence", "applies"}
TOP_KEYS = {"schema", "epics", "edges", "policy", "claims"}
CLAIM_KEYS = {"paths", "by", "at"}


def _is_issue(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool) and x > 0


def path_error(p: Any) -> str | None:
    """Why p is not a repository-relative claim (a path or a glob), None when it is."""
    if not isinstance(p, str) or not p.strip() or p.startswith("/") or "\\" in p or ".." in p.split("/"):
        return f"{p!r} is not a repository-relative path"
    return None


def shape_errors(body: Any) -> list[str]:
    """What is wrong with a plan body, [] when nothing is. The contract is
    schema/plan.schema.json; this is its check with the standard library,
    as records.py checks records, because bin/attavo takes no dependency."""
    if not isinstance(body, dict):
        return ["the plan is not an object"]
    errs = [f"unknown key {k!r}" for k in sorted(set(body) - TOP_KEYS)]
    if body.get("schema") != SCHEMA:
        errs.append(f"schema is not {SCHEMA!r}")
    epics = body.get("epics")
    if not isinstance(epics, list):
        return errs + ["epics is not a list"]
    seen_epics: set[str] = set()
    placed: dict[int, str] = {}
    for e in epics:
        if not isinstance(e, dict):
            errs.append("an epic is not an object")
            continue
        eid = e.get("id")
        errs += [f"epic {eid}: unknown key {k!r}" for k in sorted(set(e) - EPIC_KEYS)]
        if not isinstance(eid, str) or not EPIC_ID.match(eid):
            errs.append(f"epic id {eid!r} is not a name")
            continue
        if eid in seen_epics:
            errs.append(f"epic {eid} appears twice")
        seen_epics.add(eid)
        if not isinstance(e.get("title"), str) or not e["title"].strip():
            errs.append(f"epic {eid} has no title")
        for k in ("outcome", "proof"):
            if k in e and not isinstance(e[k], str):
                errs.append(f"epic {eid}: {k} is not text")
        issues = e.get("issues")
        if not isinstance(issues, list):
            errs.append(f"epic {eid}: issues is not a list")
            continue
        for n in issues:
            if not _is_issue(n):
                errs.append(f"epic {eid}: {n!r} is not an issue number")
            elif n in placed:
                errs.append(f"#{n} is in both {placed[n]} and {eid}")
            else:
                placed[n] = eid
    if BACKLOG not in seen_epics:
        errs.append(f"there is no {BACKLOG} epic")
    edges = body.get("edges")
    if not isinstance(edges, list):
        return errs + ["edges is not a list"]
    for i, g in enumerate(edges):
        if not isinstance(g, dict):
            errs.append(f"edge {i} is not an object")
            continue
        errs += [f"edge {i}: unknown key {k!r}" for k in sorted(set(g) - EDGE_KEYS)]
        if g.get("type") not in EDGE_TYPES:
            errs.append(f"edge {i}: type is not one of {', '.join(EDGE_TYPES)}")
        if not _is_issue(g.get("from")) or not _is_issue(g.get("to")):
            errs.append(f"edge {i}: from and to are not issue numbers")
        elif g["from"] == g["to"]:
            errs.append(f"edge {i}: #{g['from']} depends on itself")
        if g.get("type") == "ruled" and not (isinstance(g.get("by"), str) and isinstance(g.get("at"), str)):
            errs.append(f"edge {i}: a ruled edge names who ruled it and when (by, at)")
        if "applies" in g and g["applies"] != "spec":
            errs.append(f"edge {i}: applies is only \"spec\"")
    policy = body.get("policy", {})
    if not isinstance(policy, dict):
        errs.append("policy is not an object")
    claims = body.get("claims", {})
    if not isinstance(claims, dict):
        return errs + ["claims is not an object"]
    for key, c in claims.items():
        if not (key.isdigit() and int(key) > 0):
            errs.append(f"claims key {key!r} is not an issue number")
        if not isinstance(c, dict):
            errs.append(f"claims {key} is not an object")
            continue
        errs += [f"claims {key}: unknown key {k!r}" for k in sorted(set(c) - CLAIM_KEYS)]
        if not (isinstance(c.get("by"), str) and isinstance(c.get("at"), str)):
            errs.append(f"claims {key}: names who set them and when (by and at)")
        paths = c.get("paths")
        if not isinstance(paths, list):
            errs.append(f"claims {key}: paths is not a list")
        elif not paths:
            errs.append(f"claims {key} has no paths; clear them instead")
        else:
            errs += [f"claims {key}: {e}" for e in filter(None, map(path_error, paths))]
    return errs


def global_order(body: dict[str, Any]) -> list[tuple[str, int]]:
    """(epic, issue) for every placed issue: epic rank, then issue rank."""
    return [(e["id"], n) for e in body["epics"] for n in e["issues"]]


def _issue_arg(s: str) -> int | None:
    t = s[1:] if s.startswith("#") else s
    return int(t) if t.isdigit() and int(t) > 0 else None


def _epic_of(body: dict[str, Any], n: int) -> dict[str, Any] | None:
    return next((e for e in body["epics"] if n in e["issues"]), None)


def rank(body: dict[str, Any], what: str, before: str | None) -> dict[str, Any]:
    """A copy of body with one epic, or one issue within its epic, moved
    before another (or last when before is None). Raises StoreRefusal."""
    new: dict[str, Any] = copy.deepcopy(body)
    n = _issue_arg(what)
    if n is None:
        ids = [e["id"] for e in new["epics"]]
        if what not in ids:
            raise store.StoreRefusal(f"no epic {what} in the plan")
        epic = new["epics"].pop(ids.index(what))
        if before is None:
            new["epics"].append(epic)
        else:
            ids = [e["id"] for e in new["epics"]]
            if before not in ids:
                raise store.StoreRefusal(f"no epic {before} in the plan to rank {what} before")
            new["epics"].insert(ids.index(before), epic)
        return new
    home = _epic_of(new, n)
    if home is None:
        raise store.StoreRefusal(f"#{n} is not in the plan; place it with `attavo plan move #{n} --epic E`")
    home["issues"].remove(n)
    if before is None:
        home["issues"].append(n)
        return new
    m = _issue_arg(before)
    if m is None or m not in home["issues"]:
        raise store.StoreRefusal(f"#{n} ranks only within its epic ({home['id']}); "
                                 f"{before} is not an issue there -- move it first")
    home["issues"].insert(home["issues"].index(m), n)
    return new


def move(body: dict[str, Any], what: str, epic: str) -> dict[str, Any]:
    """A copy of body with issue what placed last in epic, taken out of
    whichever epic held it. Raises StoreRefusal."""
    n = _issue_arg(what)
    if n is None:
        raise store.StoreRefusal(f"{what} is not an issue number")
    new: dict[str, Any] = copy.deepcopy(body)
    target = next((e for e in new["epics"] if e["id"] == epic), None)
    if target is None:
        raise store.StoreRefusal(f"no epic {epic} in the plan")
    home = _epic_of(new, n)
    if home is not None:
        home["issues"].remove(n)
    target["issues"].append(n)
    return new


def set_claims(body: dict[str, Any], what: str, paths: list[str], by: str, at: str) -> dict[str, Any]:
    """A copy of body with issue what's draft claims set to paths, who set
    them and when (at, read once by the caller and also given to the
    store write, so the claim and its version agree). Raises StoreRefusal."""
    n = _issue_arg(what)
    if n is None:
        raise store.StoreRefusal(f"{what} is not an issue number")
    if _epic_of(body, n) is None:
        raise store.StoreRefusal(f"#{n} is not in the plan; place it with `attavo plan move #{n} --epic E`")
    bad = [e for e in map(path_error, paths) if e]
    if not paths or bad:
        raise store.StoreRefusal(bad[0] if bad else "claims names at least one path")
    new: dict[str, Any] = copy.deepcopy(body)
    new.setdefault("claims", {})[str(n)] = {"paths": list(paths), "by": by, "at": at}
    return new


def clear_claims(body: dict[str, Any], what: str) -> dict[str, Any]:
    """A copy of body without issue what's draft claims. Raises StoreRefusal."""
    n = _issue_arg(what)
    if n is None:
        raise store.StoreRefusal(f"{what} is not an issue number")
    if _epic_of(body, n) is None:
        raise store.StoreRefusal(f"#{n} is not in the plan; place it with `attavo plan move #{n} --epic E`")
    if str(n) not in body.get("claims", {}):
        raise store.StoreRefusal(f"#{n} has no claims to clear")
    new: dict[str, Any] = copy.deepcopy(body)
    del new["claims"][str(n)]
    return new


def _opt(args: list[str], flag: str) -> str | None:
    if flag in args:
        i = args.index(flag)
        if i + 1 < len(args):
            return args[i + 1]
    return None


def _refuse(msg: str) -> int:
    print(f"attavo plan: refused: {msg}", file=sys.stderr)
    return 2


def _write(root: Path, body: dict[str, Any], base: int, by: str | None, at: str | None = None) -> int:
    if not by:
        return _refuse("a write names its author (--by LOGIN)")
    try:
        env = store.put(root, DOC, body, base, by, check=shape_errors, at=at)
    except store.StoreRefusal as r:
        return _refuse(str(r))
    print(f"plan version {env['version']} written by {env['by']} at {env['at']} (sha256 {env['sha256'][:12]})")
    return 0


def cmd_plan(argv: list[str]) -> int:
    root = Path(".").resolve()
    if read_manifest(root) is None:
        print(f"attavo plan: no {MANIFEST} at {root}", file=sys.stderr)
        return 2
    if not argv or argv[0] not in ("get", "put", "log", "rank", "move", "claims"):
        print("usage: attavo plan get [--version V] | put FILE --base V --by LOGIN | log | "
              "rank <EPIC|#N> (--before <EPIC|#M> | --last) --by LOGIN | move #N --epic EPIC --by LOGIN | "
              "claims #N (<PATH>... | --clear) --by LOGIN",
              file=sys.stderr)
        return 2
    verb, args = argv[0], argv[1:]
    try:
        if verb == "get":
            v = _opt(args, "--version")
            env = store.get(root, DOC, int(v) if v and v.isdigit() else None)
            if env is None:
                return _refuse("no plan in the store" + (f" at version {v}" if v else ""))
            print(json.dumps(env["body"], indent=2, ensure_ascii=False))
            print(f"plan version {env['version']} by {env['by']} at {env['at']}", file=sys.stderr)
            return 0
        if verb == "log":
            entries = store.log(root, DOC)
            if not entries:
                return _refuse("no plan in the store")
            for e in entries:
                print(f"{e['version']}\tbase {e['base']}\t{e['by']}\t{e['at']}\t{e['sha256'][:12]}")
            return 0
        by = _opt(args, "--by")
        if verb == "put":
            base = _opt(args, "--base")
            if not args or args[0].startswith("--") or base is None or not base.isdigit():
                return _refuse("put FILE --base V --by LOGIN")
            try:
                body = json.loads(Path(args[0]).read_text())
            except (OSError, json.JSONDecodeError) as e:
                return _refuse(f"{args[0]} is not a readable JSON file: {e}")
            return _write(root, body, int(base), by)
        current = store.get(root, DOC)
        if current is None:
            return _refuse("no plan in the store; put the first version with base 0")
        if verb == "rank":
            before = _opt(args, "--before")
            if not args or args[0].startswith("--") or (before is None and "--last" not in args):
                return _refuse("rank <EPIC|#N> (--before <EPIC|#M> | --last) --by LOGIN")
            return _write(root, rank(current["body"], args[0], before), current["version"], by)
        if verb == "claims":
            words = [w for i, w in enumerate(args) if w != "--by" and not (i and args[i - 1] == "--by")]
            clear = "--clear" in words
            paths = [w for w in words[1:] if w != "--clear"]
            if not words or words[0].startswith("--") or clear == bool(paths):
                return _refuse("plan claims '#N' <path>... --by LOGIN, or plan claims '#N' --clear --by LOGIN")
            if not by:
                return _refuse("a write names its author (--by LOGIN)")
            if clear:
                return _write(root, clear_claims(current["body"], words[0]), current["version"], by)
            at = now_iso()
            return _write(root, set_claims(current["body"], words[0], paths, by, at),
                           current["version"], by, at=at)
        epic = _opt(args, "--epic")
        if not args or args[0].startswith("--") or epic is None:
            return _refuse("move #N --epic EPIC --by LOGIN")
        return _write(root, move(current["body"], args[0], epic), current["version"], by)
    except store.StoreRefusal as r:
        return _refuse(str(r))


COMMANDS = (
    Command(
        name='plan',
        run=cmd_plan,
        usage=('attavo plan get [--version V] | put FILE --base V --by LOGIN | log', 'attavo plan rank <EPIC|#N> (--before <EPIC|#M> | --last) --by LOGIN', 'attavo plan move #N --epic EPIC --by LOGIN', 'attavo plan claims #N (<PATH>... | --clear) --by LOGIN'),
        rank=160,
        help=(
            (80, """\
plan keeps the Plan's order in the product's datastore, never in git:
the epics in rank order, each with its issues in rank order, the edges
between issues, and policy. The store is <git common dir>/attavo/store/
(ATTAVO_STORE overrides it): shared by every worktree of the clone, never
staged, one file per version. get prints the plan and its version; put
writes the next version from a file and refuses a stale --base by naming
the latest version and its author; log lists versions; rank and move
change one position or one membership and write the next version; claims
sets (or --clear removes) the repository paths an issue will change before
it has a spec, with who set them and when, and refuses an issue the plan
does not place. A re-rank is a new version, not a commit: priority is
working material the attestation does not bind to."""),
        ),
    ),
)
