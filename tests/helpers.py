"""Shared helpers for building tenant fixtures at test time.

Fixtures under tests/fixtures/ are plain file trees, not git repositories;
each test copies the one it needs into a tmp_path and initializes it as its
own repository, so every test gets a clean, independent commit history.
"""
from __future__ import annotations

import inspect
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# A pause between two looks at a fact: an Event nobody sets, so the wait is
# a pace and never a judgment. No test reads the wall clock to decide it has
# waited long enough (#332).
_PACE = threading.Event()

# A bound on a fact that never comes true counts looks, not seconds, so a
# loaded machine (the suite at -n auto, with other work beside it) is given
# as many looks as an idle one: 900 looks 0.2 s apart is at least 180 s.
LOOKS = 900


def wait_for(fact: Callable[[], Any], what: str | None = None, looks: int = LOOKS,
             pause_s: float = 0.2) -> None:
    """Look at `fact` until it holds, pausing between looks. Gives up after
    `looks` looks, naming what it was waiting for (by default the fact's own
    source), so a hang is a sentence, not a ten-minute silence."""
    for _ in range(looks):
        if fact():
            return
        _PACE.wait(pause_s)
    if what is None:
        try:
            what = inspect.getsource(fact).strip()
        except (OSError, TypeError):
            what = repr(fact)
    raise AssertionError(f"gave up after {looks} looks waiting for: {what}")


def git(root: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                         text=True, check=True)
    return out.stdout


def init_repo(root: Path, message: str = "initial") -> None:
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Attavo Test")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)
    # attavo verify reads refs/remotes/origin/HEAD to diff the item's own
    # work against (issue #52); a real checkout always has this once
    # cloned, so fixtures fake it here rather than making every test set
    # up its own origin remote.
    sha = git(root, "rev-parse", "HEAD").strip()
    git(root, "update-ref", "refs/remotes/origin/main", sha)
    git(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")


def commit_all(root: Path, message: str) -> None:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)


def clone_shallow(root: Path, dest: Path) -> None:
    """A --depth 1 clone of root, the same history-truncated checkout
    actions/checkout@v4 leaves behind with no fetch-depth set.
    """
    subprocess.run(["git", "clone", "--quiet", "--depth", "1", "--branch",
                    git(root, "rev-parse", "--abbrev-ref", "HEAD").strip(),
                    f"file://{root}", str(dest)],
                   capture_output=True, text=True, check=True)


def make_tenant(tmp_path: Path, fixture: str, name: str | None = None) -> Path:
    """Copy tests/fixtures/<fixture> to a fresh dir and git-init + commit it."""
    dest = tmp_path / (name or fixture)
    shutil.copytree(FIXTURES / fixture, dest)
    init_repo(dest)
    return dest


def job_if_condition(yaml_text: str, job_name: str) -> str | None:
    """Pull a workflow job's top-level `if:` condition out of template YAML
    text, or None if the job has no such condition. Reads the literal file
    rather than reimplementing YAML parsing, same as extract_step_run.
    """
    lines = yaml_text.splitlines()
    marker = f"{job_name}:"
    start = next(i for i, l in enumerate(lines) if l.strip() == marker)
    job_indent = len(lines[start]) - len(lines[start].lstrip(" "))
    body_indent = job_indent + 2
    for line in lines[start + 1:]:
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent < body_indent:
            break
        if indent == body_indent and line.strip().startswith("if:"):
            return line.strip()[len("if:"):].strip()
    return None


def step_block(yaml_text: str, marker: str) -> str:
    """The raw lines of one workflow step, from the line matching `marker`
    exactly (its stripped text -- typically "- name: X" or "- uses: X") to
    the line before the next step at the same indent, or the file's end.
    Reads the literal file rather than reimplementing YAML parsing, same
    as job_if_condition and extract_step_run.
    """
    lines = yaml_text.splitlines()
    start = next(i for i, l in enumerate(lines) if l.strip() == marker)
    step_indent = len(lines[start]) - len(lines[start].lstrip(" "))
    end = len(lines)
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent <= step_indent and line.lstrip().startswith("- "):
            end = i
            break
    return "\n".join(lines[start:end])


def with_keys(step_text: str) -> list[str]:
    """The top-level keys of a step's `with:` mapping, from a step_block's
    text. Reads the literal file rather than reimplementing YAML parsing,
    same as job_if_condition, step_block, and extract_step_run.
    """
    lines = step_text.splitlines()
    start = next(i for i, l in enumerate(lines) if l.strip() == "with:")
    with_indent = len(lines[start]) - len(lines[start].lstrip(" "))
    keys = []
    for line in lines[start + 1:]:
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent <= with_indent:
            break
        if ":" in line.strip():
            keys.append(line.strip().split(":", 1)[0].strip())
    return keys


def extract_step_run(yaml_text: str, step_name: str) -> str:
    """Pull a workflow step's `run:` command out of template YAML text as-is,
    so a test exercises the literal shell the step will run rather than a
    reimplementation of it. Handles both `run: cmd` and `run: |` block forms.
    """
    lines = yaml_text.splitlines()
    marker = f"- name: {step_name}"
    start = next(i for i, l in enumerate(lines) if l.strip() == marker)
    step_indent = len(lines[start]) - len(lines[start].lstrip(" "))
    body_indent = step_indent + 2
    run_idx = next(
        i for i in range(start + 1, len(lines))
        if lines[i].strip().startswith("run:")
        and (len(lines[i]) - len(lines[i].lstrip(" "))) == body_indent)
    run_line = lines[run_idx].strip()[len("run:"):].strip()
    if run_line and not run_line.startswith("|"):
        return run_line
    block: list[str] = []
    block_indent = None
    for line in lines[run_idx + 1:]:
        if not line.strip():
            block.append("")
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent <= body_indent:
            break
        if block_indent is None:
            block_indent = indent
        block.append(line[block_indent:])
    return "\n".join(block).rstrip("\n")
