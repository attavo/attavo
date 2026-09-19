from __future__ import annotations

import os
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

# --- the spec contract ---

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
    criteria = []
    for l in lines[idx_criteria + 1:]:
        s = l.strip()
        if not s.startswith("- ") or ":" not in s:
            continue
        name = s[2:].split(":", 1)[0].strip()
        criteria.append(name)
    return {"claims": claims, "criteria": criteria}, None


def run_criterion(exe: Path, cwd: Path) -> int:
    try:
        run = subprocess.run([str(exe)], cwd=str(cwd), capture_output=True,
                             timeout=CRITERION_TIMEOUT, check=False)
        return run.returncode
    except subprocess.TimeoutExpired:
        return 124
    except OSError:
        return 126


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


def run_criteria(root: Path, spec_dir: Path, names: list[str]) -> list[dict[str, Any]]:
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
        if built:
            refusal = stub_removed_refusal(root, spec_dir)
            if refusal is None:
                # Per criterion, not the set: passing on the tree is what
                # the build already satisfied; failing on the tree but
                # passing on the stub is a new obligation the next build
                # must meet -- an operator scripting a ruling the executor
                # skipped. Refused only when a criterion satisfies neither.
                results = run_criteria(root, spec_dir, parsed["criteria"])
                fail = next((c for c in results if c["on_tree"] != 0 and c["on_stub"] != 0), None)
                if fail:
                    refusal = f"criterion fails against the tree plus stub: {fail['name']}"
        elif not parsed["claims"]:
            refusal = "claims: empty; the build may touch nothing"
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
                    results = run_criteria(root, spec_dir, listed)
                    fail_tree = next((c for c in results if c["on_tree"] == 0), None)
                    if fail_tree:
                        refusal = f"criterion passes against the tree: {fail_tree['name']}"
                    else:
                        fail_stub = next((c for c in results if c["on_stub"] != 0), None)
                        if fail_stub:
                            refusal = f"criterion fails against the tree plus stub: {fail_stub['name']}"

    status = "refused" if refusal else ("built" if built else "pass")
    record = {"sha": git(root, "rev-parse", "HEAD").strip(),
              "spec_sha": spec_sha,
              "at": now_iso(), "status": status, "approval_sha": None,
              "criteria": results, "refusal": refusal}
    write_record(records_dir, "spec", record, m, item_id)

    if refusal:
        print(f"attavo spec-gate: refused: {refusal}")
        return 1
    print(f"attavo spec-gate: {status} ({len(results)} criterion/criteria)")
    return 0


CRITERION_TIMEOUT = 120
