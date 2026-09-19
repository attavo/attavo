from __future__ import annotations

import json
from pathlib import Path

from attavo.common import git, now_iso

# --- events: the adapter contract's one optional output (issue #26) --
# an uncommitted <stage>-NN.events.jsonl the screen tails while a stage
# runs, never a record and never committed (commit_record names only
# .json, .log, and .filed; this suffix is never among them). Written
# under the checkout's own git directory, not the working tree (issue
# #140): tenant zero's own item #688 committed its events file for
# real -- the executor's own commit staged it, because it sat inside
# the tree the executor runs `git status` against. ---

EVENT_TEXT_MAX_CHARS = 400


def stage_events_dir(root: Path) -> Path:
    """<git-dir>/attavo, absolute -- <checkout>/.git/attavo for an
    ordinary checkout, <main-repo>/.git/worktrees/<name>/attavo for a
    linked worktree (issue #140). Outside the working tree either way,
    so a file written here never appears in `git status` in root and is
    never at risk of being staged and committed by the executor; the
    screen's seam finds it the same way, with the same command, in its
    own worktree."""
    git_dir = Path(git(root, "rev-parse", "--absolute-git-dir").strip())
    events_dir = git_dir / "attavo"
    events_dir.mkdir(parents=True, exist_ok=True)
    return events_dir


def append_event(events_path: Path, kind: str, text: str) -> None:
    """One {at, kind, text} JSON line appended to events_path and flushed
    at once (open-write-close, not a kept handle) -- the screen tails
    this file while the stage is still running, so a line held in a
    buffer is a line the feed does not move on. text is collapsed to one
    line and capped at EVENT_TEXT_MAX_CHARS: a tool result can be a whole
    file, and the feed renders lines, not files."""
    line = {"at": now_iso(), "kind": kind,
            "text": " ".join(text.split())[:EVENT_TEXT_MAX_CHARS]}
    with events_path.open("a") as fh:
        fh.write(json.dumps(line) + "\n")
