from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from attavo.common import (
    MANIFEST,
    ShallowCheckoutError,
    derive_item_id,
    git,
    last_commit_touching,
    now_iso,
    read_manifest,
)
from attavo.records import latest_attempt_path, latest_record, write_record

# --- approval, bound to a commit ---

def cmd_approve(argv: list[str]) -> int:
    root = Path(".").resolve()
    by = None
    positional: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "--by":
            by = argv[i + 1] if i + 1 < len(argv) else None
            i += 2
        else:
            positional.append(argv[i])
            i += 1
    item_id = positional[0] if positional else derive_item_id(root)
    if not item_id:
        print("attavo approve: cannot derive item id; pass N or run on an attavo/N-* branch",
              file=sys.stderr)
        return 2
    if not by:
        print("attavo approve: --by LOGIN is required", file=sys.stderr)
        return 2
    m = read_manifest(root)
    if m is None:
        print(f"attavo approve: no {MANIFEST} at {root}", file=sys.stderr)
        return 2

    specs_rel = m["paths"]["specs"]
    records_rel = m["paths"]["records"]
    spec_rel = str(Path(specs_rel) / item_id)
    try:
        spec_sha_now = last_commit_touching(root, spec_rel)
    except ShallowCheckoutError as exc:
        print(f"attavo approve: refused: {exc}", file=sys.stderr)
        return 2
    records_dir = root / records_rel / item_id
    latest_spec = latest_record(records_dir, "spec")
    approvers = ((m.get("operators") or {}).get("approvers")) or []

    refusal = None
    if latest_spec is None or latest_spec.get("status") not in ("pass", "built"):
        refusal = f"no passing spec-gate record for item {item_id}"
    elif latest_spec.get("spec_sha") != spec_sha_now:
        refusal = (f"spec-gate record is stale: recorded {latest_spec.get('spec_sha')}, "
                   f"spec now at {spec_sha_now}")
    elif by not in approvers:
        refusal = f"{by} is not in [operators].approvers"

    if refusal:
        print(f"attavo approve: refused: {refusal}")
        return 1

    record = {"sha": git(root, "rev-parse", "HEAD").strip(), "spec_sha": spec_sha_now,
              "approved_by": by, "at": now_iso()}
    write_record(records_dir, "approval", record, m, item_id)
    print(f"attavo approve: item {item_id} approved by {by}")
    return 0


def approval_status(root: Path, m: dict[str, Any], item_id: str) -> str:
    """"absent", "expired", or "valid": whether the latest approval record
    for item_id still binds to the spec's current commit. Raises
    ShallowCheckoutError on a shallow checkout, same as last_commit_touching.
    Shared by cmd_approval_status and cmd_build so build refuses on exactly
    what `attavo approval-status` would print.
    """
    specs_rel = m["paths"]["specs"]
    records_rel = m["paths"]["records"]
    spec_rel = str(Path(specs_rel) / item_id)
    spec_sha_now = last_commit_touching(root, spec_rel)
    records_dir = root / records_rel / item_id
    latest_approval = latest_record(records_dir, "approval")

    if latest_approval is None:
        return "absent"
    if not record_is_committed(root, latest_attempt_path(records_dir, "approval")):
        return "uncommitted"
    if latest_approval.get("spec_sha") == spec_sha_now:
        return "valid"
    return "expired"


def record_is_committed(root: Path, path: Path | None) -> bool:
    """Whether the record at path is in HEAD byte for byte. A record that
    exists only in the working tree -- written by hand, or by a script
    whose transport never ran -- is not the operator's act on the branch,
    and no stage may run on it (item 224: build and verify ran on an
    approval the merge queue's attest then refused as absent)."""
    if path is None:
        return False
    rel = path.resolve().relative_to(root.resolve()).as_posix()
    try:
        return git(root, "show", f"HEAD:{rel}") == path.read_text()
    except (RuntimeError, OSError):
        return False


def cmd_approval_status(argv: list[str]) -> int:
    root = Path(".").resolve()
    item_id = argv[0] if argv else derive_item_id(root)
    if not item_id:
        print("attavo approval-status: cannot derive item id; pass N or run on an attavo/N-* branch",
              file=sys.stderr)
        return 2
    m = read_manifest(root)
    if m is None:
        print(f"attavo approval-status: no {MANIFEST} at {root}", file=sys.stderr)
        return 2

    try:
        status = approval_status(root, m, item_id)
    except ShallowCheckoutError as exc:
        print(f"attavo approval-status: refused: {exc}", file=sys.stderr)
        return 2

    print(status)
    return 0 if status == "valid" else 1
