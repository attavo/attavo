from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

from attavo.common import MANIFEST, derive_item_id, git, read_manifest

# --- test: the executor's one test command (issue #212) --
# The tests [tests].select names for every path the item has changed since
# it left the default branch, run through [tests].run, bounded by
# [tests].call_timeout_s. The executor is given this and not the tenant's
# whole-tree checks: given a checks command it ran it (#194's 590 s wait;
# #204 and #206 ran the suite twice inside one build), and the gate runs
# them after every commit whatever the executor does. ---

TIMED_OUT_EXIT = 124

# What an ended run is given to leave on its own after SIGTERM, before its
# whole process group is sent SIGKILL.
END_GRACE_S = 2


def _signal_group(proc: "subprocess.Popen[str]", sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        pass


def run_bounded(command: str, root: Path, stdin: str, timeout_s: int | None
                ) -> tuple[int, str, str, bool]:
    """(exit, stdout, stderr, timed_out) of `command` run through the shell
    from root. The command is its own process group's leader, so a run past
    timeout_s is ended with everything it started -- a test runner's
    workers and a shell's background jobs alike -- rather than only the
    shell, which would leave them holding the pipes and the machine. A run
    that outlasts its bound exits 124, as timeout(1) does."""
    proc = subprocess.Popen(command, shell=True, cwd=str(root), stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True)
    try:
        out, err = proc.communicate(stdin, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _signal_group(proc, signal.SIGTERM)
        try:
            out, err = proc.communicate(timeout=END_GRACE_S)
        except subprocess.TimeoutExpired:
            _signal_group(proc, signal.SIGKILL)
            try:
                out, err = proc.communicate(timeout=END_GRACE_S)
            except subprocess.TimeoutExpired:
                # something the run started left its group and holds the pipes
                out, err = "", ""
        _signal_group(proc, signal.SIGKILL)
        return TIMED_OUT_EXIT, out, err, True
    _signal_group(proc, signal.SIGKILL)
    return proc.returncode, out, err, False


def without_records(paths: list[str], records_rel: str) -> list[str]:
    """paths other than paths.records itself and what is under it -- the
    records directory is where the product writes, not the item's work."""
    prefix = records_rel.rstrip("/") + "/"
    return [p for p in paths if p and p != records_rel and not p.startswith(prefix)]


def changed_since_default_branch(root: Path, records_rel: str) -> list[str]:
    """Every path the item has changed since it left the default branch,
    committed or not: tracked paths whose content differs from the
    merge-base with what refs/remotes/origin/HEAD names -- deleted and
    renamed-from paths included, staged or not -- and untracked paths that
    are not ignored. Sorted; the records directory left aside.
    RuntimeError when the default branch cannot be named."""
    default = git(root, "symbolic-ref", "-q", "refs/remotes/origin/HEAD").strip()
    base = git(root, "merge-base", default, "HEAD").strip()
    tracked = git(root, "diff", "--name-only", "--no-renames", "-z", base).split("\0")
    untracked = git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0")
    return sorted(set(without_records(tracked + untracked, records_rel)))


def cmd_test(argv: list[str]) -> int:
    root = Path(".").resolve()
    item_id = argv[0] if argv else derive_item_id(root)
    if not item_id:
        print("attavo test: cannot derive item id; pass N or run on an attavo/N-* branch",
              file=sys.stderr)
        return 2
    m = read_manifest(root)
    if m is None:
        print(f"attavo test: no {MANIFEST} at {root}", file=sys.stderr)
        return 2
    tests_cfg: dict[str, Any] = m.get("tests") or {}
    if not tests_cfg.get("select") or not tests_cfg.get("run"):
        print("attavo test: no [tests] table in the manifest "
              "([tests].select and [tests].run are both required)", file=sys.stderr)
        return 2
    try:
        changed = changed_since_default_branch(root, m["paths"]["records"])
    except RuntimeError as exc:
        print(f"attavo test: cannot name what item {item_id} has changed: {exc}; "
              "run `git remote set-head origin -a`", file=sys.stderr)
        return 2

    select_run = subprocess.run(tests_cfg["select"], shell=True, cwd=str(root),
                                input="".join(p + "\n" for p in changed),
                                capture_output=True, text=True, check=False)
    if select_run.returncode != 0:
        # Nothing selected because the selector failed is not nothing to run.
        sys.stderr.write(select_run.stderr)
        print(f"attavo test: [tests].select exited {select_run.returncode}; nothing was run",
              file=sys.stderr)
        return 2
    identifiers = [line for line in select_run.stdout.splitlines() if line.strip()]
    if not identifiers:
        print(f"attavo test {item_id}: no test is selected for the {len(changed)} "
              "path(s) changed since the default branch")
        return 0

    bound = tests_cfg.get("call_timeout_s")
    print(f"attavo test {item_id}: {len(identifiers)} selected", flush=True)
    code, out, err, timed_out = run_bounded(tests_cfg["run"], root,
                                            "".join(i + "\n" for i in identifiers), bound)
    sys.stdout.write(out)
    sys.stderr.write(err)
    if timed_out:
        print(f"attavo test {item_id}: the run did not finish within {bound} s; "
              "it and everything it started were ended")
    return code
