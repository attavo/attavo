from __future__ import annotations

import sys
from pathlib import Path

from attavo.common import MANIFEST, git, read_manifest
from attavo import Command

# --- record-only: whether HEAD, against its first parent, touches only
# paths.records. Core logic about a git checkout, not a forge's shell
# (issue #19); a workflow template's push-and-retrigger guard is one line
# calling this instead of carrying its own copy. ---

def cmd_record_only(argv: list[str]) -> int:
    root = Path(".").resolve()
    m = read_manifest(root)
    if m is None:
        print(f"attavo record-only: no {MANIFEST} at {root}", file=sys.stderr)
        return 2
    records_rel = m["paths"]["records"]
    try:
        changed = git(root, "diff", "--name-only", "HEAD^1", "HEAD").splitlines()
    except RuntimeError as exc:
        print(f"attavo record-only: refused: {exc}", file=sys.stderr)
        return 2
    prefix = records_rel.rstrip("/") + "/"
    only_records = all(p == records_rel or p.startswith(prefix) for p in changed if p)
    print("attavo record-only: yes" if only_records else "attavo record-only: no")
    return 0 if only_records else 1


COMMANDS = (
    Command(
        name='record-only',
        run=cmd_record_only,
        usage=('attavo record-only',),
        rank=100,
        help=(
            (150, """\
record-only exits 0 when HEAD, against its first parent, touches only
paths.records; 1 otherwise; 2 with no manifest. Core logic about a git
checkout, not a forge's shell -- a workflow template's push-and-retrigger
guard is one line reading this exit code instead of carrying its own
copy of the check."""),
        ),
    ),
)
