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


def make_tenant(tmp_path: Path, fixture: str, name: str | None = None) -> Path:
    """Copy tests/fixtures/<fixture> to a fresh dir and git-init + commit it."""
    dest = tmp_path / (name or fixture)
    shutil.copytree(FIXTURES / fixture, dest)
    init_repo(dest)
    return dest
