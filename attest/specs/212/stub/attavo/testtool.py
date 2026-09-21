from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from attavo.common import MANIFEST, derive_item_id, git, read_manifest

# --- attavo test, AS A STUB: not the implementation and not to be shipped.
# It exists so the spec's criteria can be shown to run. It sees committed
# changes only (not the working tree, not untracked files), it does not
# leave the records directory out, it ends a run past its bound without
# ending what that run started, and run_bounded is a plain timeout.
# tests/test_testtool.py fails on it by design. ---

TIMED_OUT_EXIT = 124


def run_bounded(command: str, root: Path, stdin: str, timeout_s: int | None
                ) -> tuple[int, str, str, bool]:
    try:
        run = subprocess.run(command, shell=True, cwd=str(root), input=stdin, capture_output=True,
                             text=True, check=False, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return TIMED_OUT_EXIT, "", "", True
    return run.returncode, run.stdout, run.stderr, False


def cmd_test(argv: list[str]) -> int:
    root = Path(".").resolve()
    item_id = argv[0] if argv else derive_item_id(root)
    m = read_manifest(root)
    if not item_id or m is None:
        print(f"attavo test: no item or no {MANIFEST}", file=sys.stderr)
        return 2
    tests_cfg: dict[str, Any] = m.get("tests") or {}
    default = git(root, "symbolic-ref", "-q", "refs/remotes/origin/HEAD").strip()
    base = git(root, "merge-base", "HEAD", default).strip()
    changed = git(root, "diff", "--name-only", base, "HEAD")
    selected = subprocess.run(tests_cfg["select"], shell=True, cwd=str(root), input=changed,
                              capture_output=True, text=True, check=False).stdout
    code, out, err, timed_out = run_bounded(tests_cfg["run"], root, selected,
                                            tests_cfg.get("call_timeout_s"))
    sys.stdout.write(out)
    sys.stderr.write(err)
    return code
