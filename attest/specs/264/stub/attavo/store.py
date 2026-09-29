"""The product's datastore: versioned documents keyed to the tenant.

The storage ruling (2026-09-25) splits what the product keeps in three:
git holds the facts the attestation binds to; this store holds working
material it does not bind to -- first the Plan's documents -- and never a
record; content-addressed bulk holds the bytes a record points at by hash.

A document is a folder <store>/<name>/ with one file per version,
<n>.json: {"name", "version", "base", "sha256", "by", "at", "body"}. A
write names the version it read (its base) and creates <base+1>.json
through a hard link from a finished temporary file, which fails when that
version already exists: two writers from the same base cannot both land,
the loser is refused by name, and a reader never sees a half-written file.
No database, no lock, and the same behaviour on a mounted folder.

The store lives under the clone's git common dir, <common>/attavo/store/,
so every worktree of a clone reads the same store and nothing in it is
ever staged; ATTAVO_STORE overrides the location. It is per clone and per
machine: sharing across people is the multi-user step, and a hosted engine
replaces this module then without changing a caller of get, put or log.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from attavo.common import now_iso

NAME = re.compile(r"^[a-z][a-z0-9-]*$")
VERSION_FILE = re.compile(r"^([0-9]+)\.json$")


class StoreRefusal(Exception):
    """A write or read the store refuses, with the reason as its message."""


def store_root(root: Path) -> Path:
    """<git common dir>/attavo/store for the clone holding root, or
    ATTAVO_STORE when it is set."""
    env = os.environ.get("ATTAVO_STORE")
    if env:
        return Path(env).expanduser().resolve()
    run = subprocess.run(["git", "-C", str(root), "rev-parse", "--git-common-dir"],
                         capture_output=True, text=True, check=False)
    common = run.stdout.strip()
    if run.returncode != 0 or not common:
        raise StoreRefusal(f"{root} is not in a git clone and ATTAVO_STORE is not set")
    return (root / common).resolve() / "attavo" / "store"


def _doc_dir(root: Path, name: str) -> Path:
    if not NAME.match(name):
        raise StoreRefusal(f"{name!r} is not a document name")
    return store_root(root) / name


def body_sha256(body: Any) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def versions(root: Path, name: str) -> list[int]:
    d = _doc_dir(root, name)
    if not d.is_dir():
        return []
    found = (VERSION_FILE.match(p.name) for p in d.iterdir())
    return sorted(int(m.group(1)) for m in found if m)


def latest(root: Path, name: str) -> int:
    """The latest version, 0 when the document has none."""
    vs = versions(root, name)
    return vs[-1] if vs else 0


def get(root: Path, name: str, version: int | None = None) -> dict[str, Any] | None:
    """The envelope of one version (the latest by default), None when absent."""
    v = latest(root, name) if version is None else version
    if v <= 0:
        return None
    path = _doc_dir(root, name) / f"{v}.json"
    if not path.is_file():
        return None
    envelope: dict[str, Any] = json.loads(path.read_text())
    return envelope


def log(root: Path, name: str) -> list[dict[str, Any]]:
    """Every version's envelope without its body, oldest first."""
    out = []
    for v in versions(root, name):
        env = get(root, name, v)
        if env is not None:
            out.append({k: env[k] for k in ("version", "base", "sha256", "by", "at")})
    return out


def put(root: Path, name: str, body: Any, base: int, by: str,
        check: Callable[[Any], list[str]] | None = None) -> dict[str, Any]:
    """Write version base+1 of name, or refuse: a stale base, an empty
    author, a body the check refuses, or a writer that landed first."""
    if not by.strip():
        raise StoreRefusal("a write names its author (--by)")
    if check is not None:
        errors = check(body)
        if errors:
            raise StoreRefusal(f"the {name} body is refused: " + "; ".join(errors))
    current = latest(root, name)
    if base != current:
        raise StoreRefusal(_moved(root, name, base, current))
    d = _doc_dir(root, name)
    d.mkdir(parents=True, exist_ok=True)
    envelope = {"name": name, "version": base + 1, "base": base, "sha256": body_sha256(body),
                "by": by, "at": now_iso(), "body": body}
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".put-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(envelope, indent=2, ensure_ascii=False) + "\n")
        try:
            os.link(tmp, d / f"{base + 1}.json")
        except FileExistsError:
            raise StoreRefusal(_moved(root, name, base, latest(root, name))) from None
    finally:
        os.unlink(tmp)
    return envelope


def _moved(root: Path, name: str, base: int, current: int) -> str:
    if current == 0:
        return f"the {name} document has no version yet; write it with base 0, not {base}"
    env = get(root, name, current) or {}
    return (f"the {name} document moved: base {base} is not the latest; the latest is "
            f"version {current}, written by {env.get('by')} at {env.get('at')}")
