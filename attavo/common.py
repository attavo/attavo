from __future__ import annotations

import os
import re
import subprocess
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MANIFEST = "attavo.toml"

STAGES = ("spec", "approval", "build", "verify")


PRODUCT_ROOT = Path(__file__).resolve().parent.parent


class Proof:
    def __init__(self, key: str, title: str) -> None:
        self.key = key
        self.title = title
        self.holds = False
        self.reason = ""
        self.extra: dict[str, Any] = {}

    def fail(self, reason: str, **extra: Any) -> "Proof":
        self.holds, self.reason = False, reason
        self.extra.update(extra)
        return self

    def hold(self, reason: str, **extra: Any) -> "Proof":
        self.holds, self.reason = True, reason
        self.extra.update(extra)
        return self

    def as_dict(self) -> dict[str, Any]:
        return {"proof": self.key, "title": self.title,
                "holds": self.holds, "reason": self.reason, **self.extra}


def read_manifest(root: Path) -> dict[str, Any] | None:
    path = root / MANIFEST
    if not path.is_file():
        return None
    return parse_manifest(path.read_text(encoding="utf-8"))


def parse_manifest(text: str) -> dict[str, Any]:
    """The manifest's text as read_manifest reads it, for a copy that is
    not on disk (the manifest as it stood at a commit)."""
    return tomllib.loads(text)


def git_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """The environment every git call the product makes runs in. Git takes
    an "optional" index lock even for a read (`git status` refreshes the
    index), and a pass's read in a checkout would then fail a worker's
    write there, after its push had succeeded (#343). With optional locks
    off, a read never holds the index."""
    return {**os.environ, "GIT_OPTIONAL_LOCKS": "0", **(extra or {})}


def git(root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    out = subprocess.run(["git", "-C", str(root), *args],
                         capture_output=True, text=True, check=False, env=git_env(env))
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "git failed")
    return out.stdout


def product_version() -> str:
    """What the running program is. Three fallbacks, in order, and never an
    exception -- this runs inside write_record, after the work it
    describes already happened, and a raise here would lose the record
    for an invocation that already ran:

      1. ATTAVO_PRODUCT_VERSION, when the environment sets it. The forge
         action sets this from its own ref (action.yml, not here -- this
         function stays forge-agnostic).
      2. git describe --tags --always --dirty against PRODUCT_ROOT: a
         tagged release reports its tag, a checkout reports its commit,
         a dirty tree says so.
      3. "unknown", when PRODUCT_ROOT is not a git repository at all --
         the case for a forge that extracts this action from a tarball
         rather than a git checkout, which is exactly when (1) is
         expected to already have supplied the answer.

    Distinct from the tenant's product_pin -- an operator running a
    checkout, which is how every build and verify have actually been run
    so far, bypasses the pin entirely (issue #49)."""
    env = os.environ.get("ATTAVO_PRODUCT_VERSION")
    if env:
        return env
    try:
        return git(PRODUCT_ROOT, "describe", "--tags", "--always", "--dirty").strip()
    except RuntimeError:
        return "unknown"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def derive_item_id(root: Path) -> str | None:
    """ATTAVO_ITEM first, not last: a rejection fixture's own item id must
    win over whatever attavo/N-* branch the enclosing tenant repository
    happens to be checked out on -- the common case is doctor running from
    a real item's own branch, where a fallback-only env var would never be
    consulted at all and every gate fixture would silently resolve to the
    real item instead of its own."""
    env_item = os.environ.get("ATTAVO_ITEM")
    if env_item:
        return env_item
    out = subprocess.run(["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
                         capture_output=True, text=True, check=False, env=git_env())
    branch = out.stdout.strip()
    m = re.match(r"^attavo/(\d+)-", branch)
    return m.group(1) if m else None


def fixture_item_id(fixture: Path) -> str | None:
    """The one item id under a rejection fixture's own paths.specs, so the
    doctor can export it as ATTAVO_ITEM: a fixture carries no attavo/N-*
    branch for a gate command with no argument to derive from otherwise."""
    fm = read_manifest(fixture)
    specs_rel = ((fm or {}).get("paths") or {}).get("specs")
    if not specs_rel:
        return None
    specs_dir = fixture / specs_rel
    if not specs_dir.is_dir():
        return None
    items = sorted(p.name for p in specs_dir.iterdir() if p.is_dir())
    return items[0] if len(items) == 1 else None


class ShallowCheckoutError(RuntimeError):
    pass


def last_commit_touching(root: Path, relpath: str) -> str | None:
    shallow = subprocess.run(["git", "-C", str(root), "rev-parse", "--is-shallow-repository"],
                             capture_output=True, text=True, check=False, env=git_env())
    if shallow.stdout.strip() == "true":
        raise ShallowCheckoutError(
            "shallow checkout: git log -1 -- <path> returns HEAD for every path on a "
            "shallow clone; check out with fetch-depth: 0")
    out = subprocess.run(["git", "-C", str(root), "log", "-1", "--format=%H", "--", relpath],
                         capture_output=True, text=True, check=False, env=git_env())
    sha = out.stdout.strip()
    return sha or None
