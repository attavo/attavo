from __future__ import annotations

import contextlib
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

from attavo.build import driver_identity
from attavo.common import git
from attavo.events import stage_events_dir

# --- the item's working copy (the reconciler's R5, ruling F): the product,
# not the caller, makes `../attavo-worktree-N` on the item's branch,
# brings it to origin's tip, and removes it once the item is merged. The
# location is a convention, not a manifest key. Nothing here forces,
# resets, or deletes: a state these rules do not cover is refused and
# reported, and the copy stays. ---

ITEM_BRANCH_RE = re.compile(r"^attavo/(\d+)-")
ORIGIN_ITEM_REFS = "refs/remotes/origin/attavo/"


class Ensured(NamedTuple):
    """path None with a refusal: stopped, and why. path None with none:
    the item is merged on origin and has no copy, so there is nothing to make."""
    path: Path | None
    refusal: str | None


def copy_path(root: Path, item_id: str) -> Path:
    return root.parent / f"attavo-worktree-{item_id}"


def current_branch(root: Path) -> str:
    out = subprocess.run(["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
                         capture_output=True, text=True, check=False)
    return out.stdout.strip()


def on_item_branch(root: Path) -> bool:
    """True inside an item's own working copy: the branch is attavo/N-*.
    Anywhere else is the tenant's checkout, and a pass ensures the copy."""
    return ITEM_BRANCH_RE.match(current_branch(root)) is not None


def fetch_origin_items(root: Path) -> dict[str, list[str]]:
    """{item id: [branch, ...]} for origin's attavo/* branches, read at
    one `git fetch` of that namespace and one `git for-each-ref`. Pruned,
    so a branch origin no longer has is no longer named."""
    if not has_origin(root):
        raise RuntimeError("this checkout has no origin")
    default = subprocess.run(["git", "-C", str(root), "symbolic-ref", "-q", "refs/remotes/origin/HEAD"],
                             capture_output=True, text=True, check=False).stdout.strip()
    specs = [f"+refs/heads/attavo/*:{ORIGIN_ITEM_REFS}*"]
    if default.startswith("refs/remotes/origin/"):
        specs.append(f"+refs/heads/{default.removeprefix('refs/remotes/origin/')}:{default}")
    git(root, "fetch", "-q", "--prune", "origin", *specs)
    out = git(root, "for-each-ref", "--format=%(refname)", ORIGIN_ITEM_REFS).splitlines()
    items: dict[str, list[str]] = {}
    for ref in out:
        branch = ref.removeprefix("refs/remotes/origin/")
        m = ITEM_BRANCH_RE.match(branch)
        if m:
            items.setdefault(m.group(1), []).append(branch)
    return items


def default_ref(root: Path, fetch: bool = True) -> str:
    """origin's default branch as a remote-tracking ref, freshly fetched
    when origin answers."""
    out = subprocess.run(["git", "-C", str(root), "symbolic-ref", "-q", "refs/remotes/origin/HEAD"],
                         capture_output=True, text=True, check=False)
    ref = out.stdout.strip() or "refs/remotes/origin/main"
    name = ref.removeprefix("refs/remotes/origin/")
    if fetch:
        subprocess.run(["git", "-C", str(root), "fetch", "-q", "origin", f"+refs/heads/{name}:{ref}"],
                       capture_output=True, text=True, check=False)
    return ref


def registered_copies(root: Path) -> dict[Path, str | None]:
    """Every worktree git lists for this repository: resolved path -> the
    branch it is on (None when detached)."""
    listed: dict[Path, str | None] = {}
    path: Path | None = None
    for line in git(root, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            path = Path(line[len("worktree "):]).resolve()
            listed[path] = None
        elif line.startswith("branch ") and path is not None:
            listed[path] = line[len("branch "):].removeprefix("refs/heads/")
    return listed


def _allowed_author(email: str) -> bool:
    return (email == driver_identity()["email"]
            or (email.startswith("executor+") and email.endswith("@attavo.invalid")))


def _held(copy: Path, item_id: str, records_rel: str) -> bool:
    from attavo.drive import worker_holds_copy
    return worker_holds_copy(copy, copy / records_rel / item_id)


def _tracked_changes(copy: Path) -> list[str]:
    return git(copy, "status", "--porcelain", "--untracked-files=no").splitlines()


def _conform(root: Path, branch: str, tip: str) -> str | None:
    """Bring the local branch to origin's tip by the only two moves the
    product makes: push the commits the executor or the driver authored
    that origin lacks (a worker that died between its commit and its
    push) as a fast-forward, or fast-forward to origin's tip. A commit
    origin lacks by anyone else is a refusal naming sha and author."""
    exists = subprocess.run(["git", "-C", str(root), "rev-parse", "-q", "--verify",
                             f"refs/heads/{branch}"], capture_output=True, text=True, check=False)
    if exists.returncode != 0:
        return None
    ours = git(root, "log", "--format=%h\t%an\t%ae", f"{tip}..refs/heads/{branch}").splitlines()
    if not ours:
        return None
    foreign = [line.split("\t") for line in ours if not _allowed_author(line.split("\t")[2])]
    if foreign:
        named = "; ".join(f"{sha} by {name} <{email}>" for sha, name, email in foreign)
        return (f"{branch} carries commit(s) origin's {branch} lacks that neither the executor nor "
                f"the driver authored: {named}. Push them, or move them, and run the pass again")
    behind = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", tip,
                             f"refs/heads/{branch}"], capture_output=True, text=True, check=False)
    if behind.returncode != 0:
        return (f"{branch} and origin's {branch} have diverged; the pass neither merges nor rebases. "
                f"Local commits: {'; '.join(ours)}")
    run = subprocess.run(["git", "-C", str(root), "push", "-q", "origin",
                          f"refs/heads/{branch}:refs/heads/{branch}"],
                         capture_output=True, text=True, check=False)
    if run.returncode != 0:
        return f"could not push {branch}'s unpushed commits to origin: {run.stderr.strip()}"
    return None


def has_origin(root: Path) -> bool:
    return subprocess.run(["git", "-C", str(root), "remote", "get-url", "origin"],
                          capture_output=True, text=True, check=False).returncode == 0


def ensure(root: Path, item_id: str, records_rel: str) -> Ensured:
    """The item's working copy at ../attavo-worktree-N, on origin's one
    attavo/N-* branch at its tip; or the reason it cannot be. Created
    when absent; a registered copy whose directory is gone is pruned and
    created again; a directory git does not list, tracked changes, or a
    commit origin lacks by another author are refused, and nothing is
    deleted or reset. While a worker holds the copy it is returned
    as it is."""
    copy = copy_path(root, item_id)
    listed = registered_copies(root)
    if not has_origin(root) and copy.resolve() not in listed:
        return Ensured(None, f"this checkout has no origin, so there is no attavo/{item_id}-* to work on; "
                             f"run from inside the item's working copy")
    try:
        items = fetch_origin_items(root)
    except RuntimeError as exc:
        return Ensured(None, f"could not read origin's attavo/* branches: {exc}")
    if copy.resolve() in listed and not copy.is_dir():
        git(root, "worktree", "prune")
        listed = registered_copies(root)
    present = copy.resolve() in listed
    if copy.exists() and not present:
        return Ensured(None, f"{copy} exists and git does not list it as a worktree; the pass "
                             f"does not delete it. Move it, and run the pass again")
    branches = items.get(item_id, [])
    if len(branches) > 1:
        return Ensured(None, f"origin has {len(branches)} branches for item {item_id}: "
                             f"{', '.join(sorted(branches))}; delete all but one")
    if not branches:
        if present:
            return Ensured(copy, None)
        return Ensured(None, f"origin has no attavo/{item_id}-* branch")
    branch = branches[0]
    if not present and item_id in merged_items(root, records_rel, [item_id]):
        return Ensured(None, None)
    tip = f"{ORIGIN_ITEM_REFS}{branch.removeprefix('attavo/')}"
    if present:
        on = listed[copy.resolve()]
        if on != branch:
            return Ensured(None, f"{copy} is on {on or 'a detached HEAD'}, not {branch}")
        if _held(copy, item_id, records_rel):
            return Ensured(copy, None)
        changed = _tracked_changes(copy)
        if changed:
            return Ensured(None, f"{copy} has tracked changes ({len(changed)} path(s), "
                                 f"first: {changed[0].strip()}); the pass does not touch them")
    refusal = _conform(root, branch, tip)
    if refusal:
        return Ensured(None, refusal)
    if not present:
        have = subprocess.run(["git", "-C", str(root), "rev-parse", "-q", "--verify",
                               f"refs/heads/{branch}"], capture_output=True, text=True, check=False)
        try:
            if have.returncode == 0:
                git(root, "worktree", "add", "-q", str(copy), branch)
            else:
                git(root, "worktree", "add", "-q", "--track", "-b", branch, str(copy), tip)
        except RuntimeError as exc:
            return Ensured(None, f"could not add {copy} on {branch}: {exc}")
    try:
        git(copy, "merge", "--ff-only", "-q", tip)
    except RuntimeError as exc:
        return Ensured(None, f"{copy} could not fast-forward to origin's {branch}: {exc}")
    return Ensured(copy, None)


@contextlib.contextmanager
def read_only_copy(root: Path, item_id: str) -> Iterator[tuple[Path | None, str | None]]:
    """(tree, refusal): origin's item branch at its tip in a detached
    worktree under the git dir, removed on exit with a plain remove; for
    a --report on an item with no working copy. Nothing outlives it."""
    try:
        items = fetch_origin_items(root)
    except RuntimeError as exc:
        yield None, f"could not read origin's attavo/{item_id}-*: {exc}"
        return
    branches = items.get(item_id, [])
    if len(branches) != 1:
        yield None, (f"origin has {len(branches)} branches for item {item_id}"
                     + (f": {', '.join(sorted(branches))}" if branches else f"; no attavo/{item_id}-*"))
        return
    parent = stage_events_dir(root) / "read"
    parent.mkdir(parents=True, exist_ok=True)
    holder = Path(tempfile.mkdtemp(prefix="rd-", dir=parent))
    tree = holder / "head"
    try:
        git(root, "worktree", "add", "--detach", "-q", str(tree),
            f"{ORIGIN_ITEM_REFS}{branches[0].removeprefix('attavo/')}")
    except RuntimeError as exc:
        with contextlib.suppress(OSError):
            holder.rmdir()
        yield None, f"could not read origin's {branches[0]}: {exc}"
        return
    try:
        yield tree, None
    finally:
        removed = subprocess.run(["git", "-C", str(root), "worktree", "remove", str(tree)],
                                 capture_output=True, text=True, check=False)
        if removed.returncode != 0:
            print(f"attavo drive: note: could not remove {tree}: {removed.stderr.strip()}",
                  file=sys.stderr)
        git(root, "worktree", "prune")
        for leftover in (holder, parent):
            with contextlib.suppress(OSError):
                leftover.rmdir()


def tenant_root(copy: Path) -> Path:
    """The tenant's own checkout: the first worktree git lists."""
    return next(iter(registered_copies(copy)))


def remove_when_merged(copy: Path, item_id: str, records_rel: str) -> str | None:
    """A merged item's working copy, removed with a plain `git worktree
    remove` -- only when it is tracked-clean, no marker lacks its record,
    and its HEAD is an ancestor of origin's default branch. First its
    <git-dir>/attavo moves to <common git dir>/attavo/archive/<N>/, so
    the events a stage wrote outlive the copy. Returns what it did, or
    why the copy stays; None when this is not the convention's copy."""
    from attavo.doctor import invocations_without_record
    tenant = tenant_root(copy)
    if copy.resolve() != copy_path(tenant, item_id).resolve():
        return None
    if _tracked_changes(copy):
        return f"merged, and {copy.name} stays: it has tracked changes"
    if invocations_without_record(copy / records_rel / item_id):
        return f"merged, and {copy.name} stays: an invocation marker has no record"
    default = default_ref(tenant)
    contained = subprocess.run(["git", "-C", str(copy), "merge-base", "--is-ancestor", "HEAD", default],
                               capture_output=True, text=True, check=False)
    if contained.returncode != 0:
        return f"merged, and {copy.name} stays: its HEAD is not in origin's default branch"
    events = Path(git(copy, "rev-parse", "--absolute-git-dir").strip()) / "attavo"
    common = Path(git(copy, "rev-parse", "--path-format=absolute", "--git-common-dir").strip())
    archive = common / "attavo" / "archive" / item_id
    moved: Path | None = None
    if events.is_dir():
        archive.parent.mkdir(parents=True, exist_ok=True)
        moved = archive
        n = 1
        while moved.exists():
            n += 1
            moved = archive.with_name(f"{item_id}.{n}")
        shutil.move(str(events), str(moved))
    removed = subprocess.run(["git", "-C", str(tenant), "worktree", "remove", str(copy)],
                             capture_output=True, text=True, check=False)
    if removed.returncode != 0:
        if moved is not None:
            shutil.move(str(moved), str(events))
        return f"merged, and {copy.name} stays: {removed.stderr.strip()}"
    where = f"; its events are in {moved}" if moved is not None else ""
    return f"merged: removed {copy.name}{where}"


def merged_items(root: Path, records_rel: str, items: list[str]) -> set[str]:
    """Which of `items` are merged, judged on origin's default branch as the
    fetch the pass already made left it -- never this checkout's HEAD."""
    from attavo.common import STAGES
    from attavo.doctor import _merges_reachable, merge_introducing_records
    ref = default_ref(root, fetch=False)
    merges, refusal = _merges_reachable(root, ref)
    if refusal:
        return set()
    return {n for n in items if merge_introducing_records(root, records_rel, n, STAGES, merges)[0]}


def existing_copy_items(root: Path) -> list[str]:
    found = []
    for path in sorted(root.parent.glob("attavo-worktree-*")):
        m = re.fullmatch(r"attavo-worktree-(\d+)", path.name)
        if m and path.is_dir():
            found.append(m.group(1))
    return found
