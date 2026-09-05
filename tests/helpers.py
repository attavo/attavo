"""Shared helpers for building tenant fixtures at test time.

Fixtures under tests/fixtures/ are plain file trees, not git repositories;
each test copies the one it needs into a tmp_path and initializes it as its
own repository, so every test gets a clean, independent commit history.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def git(root: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                         text=True, check=True)
    return out.stdout


def init_repo(root: Path, message: str = "initial") -> None:
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Attest Test")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)


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
