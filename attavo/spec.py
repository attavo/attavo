from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, cast

from attavo.approval import approval_status
from attavo.common import (
    MANIFEST,
    ShallowCheckoutError,
    derive_item_id,
    git,
    last_commit_touching,
    now_iso,
    read_manifest,
)
from attavo.records import latest_record, write_record
from attavo import Command

# --- the spec contract ---

ROOT_CAUSE_LINE = re.compile(r"^Root-cause:\s*([a-z0-9][a-z0-9-]*)\s*$")
ROOT_CAUSE_ENTRY = re.compile(r"^- ([a-z0-9][a-z0-9-]*):\s+\S")
CLASS_LINE = re.compile(r"^Class:\s*(\S+)\s*$")
CLASSES = ("core", "fix")
KINDS = ("behavioural", "structural")
# "- [kind] name.sh: ..." -- the label is parsed off before the name, which
# is everything between the dash (and label) and the first colon.
KIND_LABEL = re.compile(r"^\[([^\]]*)\]\s*")


def parse_spec(spec_md: Path) -> tuple[dict[str, Any] | None, str | None]:
    lines = spec_md.read_text().splitlines()
    heading_idx = [i for i, l in enumerate(lines) if l.startswith("## ")]
    headings = [lines[i].rstrip() for i in heading_idx]
    expected = ["## Problem", "## Claims", "## Criteria"]
    if headings != expected:
        bad = next((h for h in headings if h not in expected), None)
        if bad is None:
            missing = [h for h in expected if h not in headings]
            bad = missing[0] if missing else "heading order"
        return None, f"headings: found {headings or ['(none)']}, expected {expected} ({bad})"
    idx_problem, idx_claims, idx_criteria = heading_idx
    claims = [l.strip()[2:].strip() for l in lines[idx_claims + 1:idx_criteria]
              if l.strip().startswith("- ")]
    # A fix item names the root cause its change closes, as one line in
    # the problem: "Root-cause: <slug>". Optional; when present the gate
    # checks it against the tenant's closed list (root_cause_refusal).
    root_cause = None
    for l in lines[idx_problem + 1:idx_claims]:
        m = ROOT_CAUSE_LINE.match(l)
        if m:
            root_cause = m.group(1)
            break
    # The item's class, declared by the operator at filing as one line in
    # the problem: "Class: core" (the change touches the mechanism) or
    # "Class: fix". Optional to parse; the gate requires it (class_refusal).
    item_class = None
    for l in lines[idx_problem + 1:idx_claims]:
        cm = CLASS_LINE.match(l)
        if cm:
            item_class = cm.group(1)
            break
    criteria = []
    kinds: dict[str, str] = {}
    for l in lines[idx_criteria + 1:]:
        s = l.strip()
        if not s.startswith("- ") or ":" not in s:
            continue
        body = s[2:]
        label = KIND_LABEL.match(body)
        kind = "structural"
        if label:
            body = body[label.end():]
            kind = label.group(1).strip()
        name = body.split(":", 1)[0].strip()
        if kind not in KINDS:
            return None, f"criterion has an unknown kind [{kind}]: {name}"
        criteria.append(name)
        kinds[name] = kind
    return {"claims": claims, "criteria": criteria, "kinds": kinds,
            "root_cause": root_cause, "class": item_class}, None


def root_causes_path(root: Path, m: dict[str, Any]) -> Path:
    """The tenant's closed list of root causes: root-causes.md beside
    the specs directory (attest/root-causes.md when specs live in
    attest/specs). One root cause per line, "- <slug>: <one sentence>"."""
    return root / Path(m["paths"]["specs"]).parent / "root-causes.md"


def listed_root_causes(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [mm.group(1) for mm in (ROOT_CAUSE_ENTRY.match(l) for l in path.read_text().splitlines()) if mm]


def root_cause_refusal(root: Path, m: dict[str, Any], root_cause: str | None) -> str | None:
    """A spec that names a root cause must name one the tenant lists.
    Symptom counts do not converge; root-cause counts can, and only if
    every root cause a fix item names is one the list already carries or
    an item added on purpose. No root-cause line, no check: a capability
    item has no root cause."""
    if root_cause is None:
        return None
    path = root_causes_path(root, m)
    if not path.is_file():
        return f"root cause: {root_cause} named but {path.relative_to(root)} does not exist"
    if root_cause not in listed_root_causes(path):
        return f"root cause: {root_cause} is not listed in {path.relative_to(root)}"
    return None


def research_policy(m: dict[str, Any]) -> str:
    """[spec].research: "required" makes a core spec owe a research record
    before its gate passes; absent, it owes none. Ruled 2026-09-27: every
    core item owes research in the end state, behind this flag until the
    Plan area hosts research. The gate record says which policy applied."""
    return "required" if (m.get("spec") or {}).get("research") == "required" else "not required by policy"


def class_refusal(m: dict[str, Any], parsed: dict[str, Any], records_dir: Path) -> str | None:
    """A spec declares its class; a spec that names a root cause is core
    (ruled 2026-09-27: a root cause lies behind several issues, and closing
    it changes the mechanism); and, when the tenant requires research, a
    core spec has a research record in the item's records before its gate
    passes."""
    item_class = parsed.get("class")
    if item_class is None:
        return "class: none declared; add a line 'Class: core' or 'Class: fix' to the problem"
    if item_class not in CLASSES:
        return f"class: {item_class} is not core or fix"
    if parsed.get("root_cause") and item_class != "core":
        return f"class: {item_class} with root cause {parsed['root_cause']}; a root-cause item is core"
    if (item_class == "core" and research_policy(m) == "required"
            and latest_record(records_dir, "research") is None):
        return "research: [spec].research is required and the item has no research record"
    return None


def run_criterion(exe: Path, cwd: Path) -> int:
    try:
        run = subprocess.run([str(exe)], cwd=str(cwd), capture_output=True,
                             timeout=CRITERION_TIMEOUT, check=False)
        return run.returncode
    except subprocess.TimeoutExpired:
        return 124
    except OSError:
        return 126


def stub_outside_claims(spec_dir: Path, claims: list[str]) -> str | None:
    """The first path under stub/ (or named in stub/.removed) that no claim
    matches, or None. The stub is the change's floor: a path it touches is
    a path the build will touch, so a Claims list that does not name it is
    wrong before any build runs (item 229: a claim missed at filing cost
    an attempt to find). Matched the way cmd_build matches (fnmatch), with
    a directory claim covering everything under it."""
    stub_dir = spec_dir / "stub"
    if not stub_dir.is_dir():
        return None
    paths = sorted(p.relative_to(stub_dir).as_posix() for p in stub_dir.rglob("*")
                   if p.is_file() and p.name != ".removed")
    paths += parse_removed(stub_dir)
    patterns = [c + "**" if c.endswith("/") else c for c in claims]
    for path in paths:
        if not any(fnmatch.fnmatch(path, pat) or path.startswith(pat.rstrip("*")) and pat.endswith("**")
                   for pat in patterns):
            return path
    return None


def parse_removed(stub_dir: Path) -> list[str]:
    """The paths listed in stub/.removed, one per line, comments (lines
    starting with #) and blank lines ignored. [] when the stub carries no
    .removed at all.
    """
    removed_file = stub_dir / ".removed"
    if not removed_file.is_file():
        return []
    paths = []
    for line in removed_file.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        paths.append(s)
    return paths


def removed_path_refusal(root: Path, stub_dir: Path, rel: str) -> str | None:
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts:
        return f"stub/.removed names a path outside the tree: {rel}"
    if not (root / p).exists():
        return f"stub/.removed names a path not in the tree: {rel}"
    if (stub_dir / p).exists():
        return f"stub/.removed names a path the stub also supplies: {rel}"
    return None


def stub_removed_refusal(root: Path, spec_dir: Path) -> str | None:
    """Validated by name, before any criterion runs -- a stub whose
    .removed cannot be applied has no meaning to gate against.
    """
    stub_dir = spec_dir / "stub"
    for rel in parse_removed(stub_dir):
        refusal = removed_path_refusal(root, stub_dir, rel)
        if refusal:
            return refusal
    return None


def run_criteria(root: Path, spec_dir: Path, names: list[str],
                 kinds: dict[str, str] | None = None) -> list[dict[str, Any]]:
    criteria_dir = spec_dir / "criteria"
    stub_dir = spec_dir / "stub"
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "tree"
        stubbed = Path(tmp) / "stubbed"
        shutil.copytree(root, tree, ignore=shutil.ignore_patterns(".git"))
        shutil.copytree(root, stubbed, ignore=shutil.ignore_patterns(".git"))
        if stub_dir.is_dir():
            shutil.copytree(stub_dir, stubbed, dirs_exist_ok=True)
            for rel in parse_removed(stub_dir):
                target = stubbed / rel
                if target.is_dir():
                    shutil.rmtree(target)
                elif target.exists():
                    target.unlink()
            removed_marker = stubbed / ".removed"
            if removed_marker.exists():
                removed_marker.unlink()
        results = []
        for name in names:
            exe = criteria_dir / name
            results.append({"name": name,
                            "kind": (kinds or {}).get(name, "structural"),
                            "on_tree": run_criterion(exe, tree),
                            "on_stub": run_criterion(exe, stubbed)})
        return results


def commit_postdates(root: Path, earlier: str, later: str) -> bool:
    """Whether later is a strict descendant of earlier on this branch."""
    if earlier == later:
        return False
    run = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", earlier, later],
                         capture_output=True, check=False)
    return run.returncode == 0


def cmd_spec_gate(argv: list[str]) -> int:
    root = Path(".").resolve()
    item_id = argv[0] if argv else derive_item_id(root)
    if not item_id:
        print("attavo spec-gate: cannot derive item id; pass N or run on an attavo/N-* branch",
              file=sys.stderr)
        return 2
    m = read_manifest(root)
    if m is None:
        print(f"attavo spec-gate: no {MANIFEST} at {root}", file=sys.stderr)
        return 2
    specs_rel = m["paths"]["specs"]
    records_rel = m["paths"]["records"]
    spec_dir = root / specs_rel / item_id
    spec_md = spec_dir / "spec.md"
    if not spec_md.is_file():
        print(f"attavo spec-gate: no spec at {spec_dir}", file=sys.stderr)
        return 2
    spec_rel = str(Path(specs_rel) / item_id)
    try:
        spec_sha = last_commit_touching(root, spec_rel)
    except ShallowCheckoutError as exc:
        print(f"attavo spec-gate: refused: {exc}", file=sys.stderr)
        return 2

    records_dir = root / records_rel / item_id
    try:
        approved = approval_status(root, m, item_id) == "valid"
    except ShallowCheckoutError as exc:
        print(f"attavo spec-gate: refused: {exc}", file=sys.stderr)
        return 2
    latest_approval = latest_record(records_dir, "approval")
    if approved:
        record: dict[str, Any] = {"sha": git(root, "rev-parse", "HEAD").strip(), "spec_sha": spec_sha,
                  "at": now_iso(), "status": "approved",
                  "approval_sha": latest_approval.get("sha") if latest_approval else None,
                  "criteria": [], "refusal": None}
        write_record(records_dir, "spec", record, m, item_id)
        print("attavo spec-gate: approved (no criteria run)")
        return 0

    # Third verdict, beside "approved" and Phase 1's "pass": the approval
    # is not valid, but a build already ran after it -- the amendment that
    # expired the approval landed above the line, not below it, and the
    # tree the build produced is what must satisfy the criteria now, not
    # the stub that only ever proved they were reachable before approval.
    # Issue #98: whatever the build's status, including a halt -- the
    # executor commits then asks, so a halted build's sha postdating the
    # approval means it did the work. commit_postdates already excludes a
    # halt that committed nothing, since such a build's sha equals its
    # start_sha and so postdates nothing.
    latest_build = latest_record(records_dir, "build")
    built = bool(
        latest_approval and latest_build
        and commit_postdates(root, latest_approval["sha"], latest_build["sha"]))

    parsed, refusal = parse_spec(spec_md)
    results: list[dict[str, Any]] = []
    if refusal is None:
        # parse_spec returns a refusal exactly when it returns no spec.
        parsed = cast(dict[str, Any], parsed)
        refusal = root_cause_refusal(root, m, parsed.get("root_cause"))
    if refusal is None and not built:
        # Required before approval only: an item already approved and
        # built re-gates as it did, so no item in flight is stranded.
        refusal = class_refusal(m, cast(dict[str, Any], parsed), records_dir)
    if refusal is None:
        parsed = cast(dict[str, Any], parsed)
        if built:
            refusal = stub_removed_refusal(root, spec_dir)
            if refusal is None:
                # Per criterion, not the set: passing on the tree is what
                # the build already satisfied; failing on the tree but
                # passing on the stub is a new obligation the next build
                # must meet -- an operator scripting a ruling the executor
                # skipped. Refused only when a criterion satisfies neither.
                results = run_criteria(root, spec_dir, parsed["criteria"], parsed["kinds"])
                # A behavioural criterion fails on the stub by design, so
                # after a build it is judged on the tree alone.
                fail_behav = next((c for c in results
                                   if c["kind"] == "behavioural" and c["on_tree"] != 0), None)
                fail = next((c for c in results if c["kind"] == "structural"
                             and c["on_tree"] != 0 and c["on_stub"] != 0), None)
                if fail_behav:
                    refusal = f"criterion labelled behavioural fails on the tree: {fail_behav['name']}"
                elif fail:
                    refusal = f"criterion fails against the tree plus stub: {fail['name']}"
        elif not parsed["claims"]:
            refusal = "claims: empty; the build may touch nothing"
        elif not parsed["criteria"]:
            # Zero criteria match zero files in criteria/, so the prose check
            # below would pass a spec that asserts nothing, and approve it.
            refusal = "criteria: empty; the spec asserts nothing"
        elif stub_outside_claims(spec_dir, parsed["claims"]) is not None:
            refusal = ("stub touches a path the claims do not name: "
                       f"{stub_outside_claims(spec_dir, parsed['claims'])}")
        elif parsed["criteria"] and "structural" not in parsed["kinds"].values():
            # A behavioural criterion only has to fail on the stub, so a
            # spec of nothing else proves nothing reachable before approval.
            refusal = ("no structural criterion: nothing shows the stub reaches "
                       "what the behavioural criteria test")
        else:
            criteria_dir = spec_dir / "criteria"
            listed = parsed["criteria"]
            existing = sorted(p.name for p in criteria_dir.iterdir()) if criteria_dir.is_dir() else []
            prose = None
            for name in listed:
                if name not in existing:
                    prose = name
                    break
            if prose is None:
                for name in existing:
                    if name not in listed:
                        prose = name
                        break
            if prose is None:
                for name in listed:
                    if not os.access(criteria_dir / name, os.X_OK):
                        prose = name
                        break
            if prose:
                refusal = f"criterion is prose: {prose}"
            else:
                refusal = stub_removed_refusal(root, spec_dir)
                if refusal is None:
                    results = run_criteria(root, spec_dir, listed, parsed["kinds"])
                    fail_tree = next((c for c in results if c["on_tree"] == 0), None)
                    if fail_tree:
                        refusal = f"criterion passes against the tree: {fail_tree['name']}"
                    else:
                        # The label is checked against the measured exits.
                        fail_stub = next((c for c in results if c["kind"] == "structural"
                                          and c["on_stub"] != 0), None)
                        pass_stub = next((c for c in results if c["kind"] == "behavioural"
                                          and c["on_stub"] == 0), None)
                        if fail_stub:
                            refusal = ("criterion labelled structural fails on the tree plus stub: "
                                       f"{fail_stub['name']}")
                        elif pass_stub:
                            refusal = ("criterion labelled behavioural passes on the tree plus stub: "
                                       f"{pass_stub['name']}")

    status = "refused" if refusal else ("built" if built else "pass")
    record = {"sha": git(root, "rev-parse", "HEAD").strip(),
              "spec_sha": spec_sha,
              "at": now_iso(), "status": status, "approval_sha": None,
              "criteria": results, "refusal": refusal,
              "class": (parsed or {}).get("class"), "research": research_policy(m)}
    write_record(records_dir, "spec", record, m, item_id)

    if refusal:
        print(f"attavo spec-gate: refused: {refusal}")
        return 1
    print(f"attavo spec-gate: {status} ({len(results)} criterion/criteria)")
    return 0


CRITERION_TIMEOUT = 120


COMMANDS = (
    Command(
        name='spec-gate',
        run=cmd_spec_gate,
        usage=('attavo spec-gate [N]',),
        rank=20,
        help=(
            (50, """\
spec-gate, approve, and approval-status are gates and writers. They run
from the tenant root (the current working directory); N, the work item id,
is read from the ATTAVO_ITEM environment variable when set, else from the
branch (attavo/N-*). The doctor's first proof sets ATTAVO_ITEM for a
rejection fixture's own item, so a gate's own command needs no argument
even while the enclosing repository sits on a real item's attavo/N-*
branch. Every run of spec-gate or approve writes the next numbered attempt
file under paths.records/N/ and never overwrites one already written. A
fix item's spec may name the root cause its change closes as one
"Root-cause: <slug>" line in the problem; spec-gate refuses a slug the
tenant's root-causes.md (beside paths.specs) does not list, or any slug
when that file is absent, before any criterion runs -- a spec with no
root-cause line is untouched. spec-gate also refuses, before any
criterion runs, "stub touches a path the claims do not name: <path>" for
the first path under stub/ (or in stub/.removed) that no claim matches.
A criterion line may carry a kind after its dash, "- [behavioural] name.sh: ..."
or "- [structural] name.sh: ..."; unlabelled is structural. Structural must
fail on the tree and pass on the tree plus stub; behavioural must fail on
both, so the stub can be thin; before approval a spec needs at least one
structural criterion. A label the measured exits contradict is
refused by name, as is an unknown one. After a build a behavioural
criterion must pass on the tree.
See the spec contract in the project's Phase 1 issue for the full rules
each one enforces."""),
        ),
    ),
)
