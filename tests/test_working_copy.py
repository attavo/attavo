"""The reconciler's R5 (ruling F): the product owns the item's working
copy. A pass from the tenant's checkout ensures ../attavo-worktree-N on
origin's attavo/N-* at its tip, refuses what it may not touch, reads
without a copy for --report, and removes the copy on merge.

Run: python3 -m pytest tests -q
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from helpers import git, make_tenant
from test_drive import (
    GREEN_FORGE,
    ITEM_BRANCH,
    add_bare_remote,
    add_drive_stall_bound,
    add_forge,
    approve_item,
    head_of,
    publish_approval,
    record_files,
    remote_head,
    run_attavo,
)
from test_pass import FAST, git_dir, slow_adapter, wait_for


def tenant_on_main(tmp_path: Path) -> tuple[Path, Path]:
    """An approved item 1 on origin's attavo/1-x, and the tenant's own
    checkout on main with no local branch for it."""
    root = make_tenant(tmp_path, "build_valid", name="tenant")
    add_forge(root, GREEN_FORGE)
    add_drive_stall_bound(root)
    slow_adapter(root, 0)
    bare = add_bare_remote(root, tmp_path)
    approve_item(root)
    publish_approval(root)
    git(root, "checkout", "-q", "main")
    git(root, "branch", "-q", "-D", ITEM_BRANCH)
    return root, bare


def copy_of(tmp_path: Path, item: str = "1") -> Path:
    return tmp_path / f"attavo-worktree-{item}"


def drive(root: Path, *args: str) -> tuple[int, str]:
    run = run_attavo(root, "drive", *args, env=FAST)
    return run.returncode, run.stdout + run.stderr


def settle(copy: Path, bare: Path, stage: str = "build") -> None:
    """The worker's record is committed, pushed, and the copy at origin."""
    wait_for(lambda: record_files(copy, stage) != [])
    wait_for(lambda: remote_head(bare) == head_of(copy))


def listed(root: Path) -> list[str]:
    return [l.split()[1] for l in git(root, "worktree", "list", "--porcelain").splitlines()
            if l.startswith("worktree ")]


def add_merged_item(root: Path, bare: Path, item: str) -> None:
    """Item `item`'s four stage records, introduced by a merge on main's
    first-parent line, its branch left on origin the way a merge leaves it."""
    git(root, "checkout", "-q", "-b", f"attavo/{item}-y", "main")
    rec = root / "records" / item
    rec.mkdir(parents=True)
    for stage in ("spec", "approval", "build", "verify"):
        (rec / f"{stage}-01.json").write_text(json.dumps({"status": "pass", "sha": stage}))
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", f"records for item {item}")
    git(root, "push", "-q", "origin", f"attavo/{item}-y")
    git(root, "checkout", "-q", "main")
    git(root, "merge", "-q", "--no-ff", "-m", f"Merge attavo/{item}-y", f"attavo/{item}-y")
    git(root, "push", "-q", "origin", "main")


def test_a_pass_from_the_checkout_on_main_creates_the_copy_at_origins_tip_and_builds_there(tmp_path: Path) -> None:
    root, bare = tenant_on_main(tmp_path)
    tip = remote_head(bare)
    rc, out = drive(root, "1")
    assert rc == 0, out
    assert "Build started" in out, out
    copy = copy_of(tmp_path)
    assert copy.resolve().as_posix() in [Path(p).resolve().as_posix() for p in listed(root)]
    assert git(copy, "rev-parse", "--abbrev-ref", "HEAD").strip() == ITEM_BRANCH
    assert git(copy, "merge-base", tip, "HEAD").strip() == tip, "the copy is not at origin's tip"
    settle(copy, bare)
    assert not (root / "records" / "1" / "build-01.json").exists(), "the tenant's checkout was written to"


def test_zero_or_two_item_branches_on_origin_are_refused_by_name_and_nothing_is_created(tmp_path: Path) -> None:
    root, bare = tenant_on_main(tmp_path)
    git(root, "push", "-q", "origin", f"{remote_head(bare)}:refs/heads/attavo/1-other")
    rc, out = drive(root, "1")
    assert rc == 1 and "attavo/1-other" in out and "attavo/1-x" in out, out
    assert not copy_of(tmp_path).exists()
    git(root, "push", "-q", "origin", "--delete", "attavo/1-other", ITEM_BRANCH)
    rc, out = drive(root, "1")
    assert rc == 1 and "attavo/1-*" in out, out
    assert not copy_of(tmp_path).exists() and len(listed(root)) == 1


def test_a_copy_whose_directory_is_gone_is_pruned_and_made_again_and_a_directory_git_does_not_list_stays(
        tmp_path: Path) -> None:
    root, bare = tenant_on_main(tmp_path)
    assert drive(root, "1")[0] == 0
    copy = copy_of(tmp_path)
    settle(copy, bare)
    shutil.rmtree(copy)
    rc, out = drive(root, "1")
    assert rc == 0, out
    assert (copy / "attavo.toml").is_file() and len(listed(root)) == 2, out
    settle(copy, bare, "verify")

    git(root, "worktree", "remove", "--force", str(copy))
    copy.mkdir()
    (copy / "precious").write_text("mine\n")
    rc, out = drive(root, "1")
    assert rc == 1 and "does not delete" in out, out
    assert (copy / "precious").read_text() == "mine\n"
    assert str(copy.resolve()) not in [str(Path(p).resolve()) for p in listed(root)]


def test_tracked_changes_and_a_foreign_commit_are_refused_and_an_executor_commit_is_pushed(tmp_path: Path) -> None:
    root, bare = tenant_on_main(tmp_path)
    assert drive(root, "1")[0] == 0
    copy = copy_of(tmp_path)
    settle(copy, bare)
    toml = copy / "attavo.toml"
    before = toml.read_text()
    toml.write_text(before + "# edited\n")
    rc, out = drive(root, "1")
    assert rc == 1 and "tracked changes" in out, out
    assert toml.read_text() == before + "# edited\n"
    toml.write_text(before)

    git(copy, "commit", "-q", "--allow-empty", "-m", "someone's", "--author", "Some One <some@example.com>")
    stray = git(copy, "rev-parse", "--short", "HEAD").strip()
    rc, out = drive(root, "1")
    assert rc == 1 and stray in out and "Some One" in out, out
    assert head_of(copy) != remote_head(bare) and git(copy, "log", "-1", "--format=%s").strip() == "someone's"

    git(copy, "reset", "-q", "--hard", "HEAD^")
    git(copy, "commit", "-q", "--allow-empty", "-m", "executor's", "--author",
        "attavo executor (fixture) <executor+fixture@attavo.invalid>")
    mine = head_of(copy)
    rc, out = drive(root, "1")
    assert "stopped:" not in out, out
    git(bare, "merge-base", "--is-ancestor", mine, ITEM_BRANCH)
    settle(copy, bare, "verify")


def test_report_without_a_copy_reads_origins_branch_and_leaves_nothing_behind(tmp_path: Path) -> None:
    root, _ = tenant_on_main(tmp_path)
    rc, out = drive(root, "1", "--report")
    assert rc == 0, out
    assert json.loads(out)["state"] == "next-build", out
    assert not copy_of(tmp_path).exists() and len(listed(root)) == 1
    assert list((git_dir(root) / "attavo").rglob("head")) == [], "a scratch worktree is left behind"
    assert not (git_dir(root) / "worktrees").exists() or list((git_dir(root) / "worktrees").iterdir()) == []


def test_a_merged_items_clean_copy_is_removed_plainly_with_its_events_archived(tmp_path: Path) -> None:
    root, bare = tenant_on_main(tmp_path)
    add_merged_item(root, bare, "2")
    copy = copy_of(tmp_path, "2")
    git(root, "worktree", "add", "-q", str(copy), "attavo/2-y")
    events = Path(git(copy, "rev-parse", "--absolute-git-dir").strip()) / "attavo"
    events.mkdir(parents=True, exist_ok=True)
    (events / "build-01.events.jsonl").write_text('{"kind":"x"}\n')
    rc, out = drive(root, "2")
    assert rc == 0 and "removed attavo-worktree-2" in out, out
    assert not copy.exists() and len(listed(root)) == 1
    archived = git_dir(root) / "attavo" / "archive" / "2" / "build-01.events.jsonl"
    assert archived.read_text() == '{"kind":"x"}\n'


def test_a_merged_copy_that_is_dirty_or_not_in_the_default_branch_stays_and_is_reported(tmp_path: Path) -> None:
    root, bare = tenant_on_main(tmp_path)
    add_merged_item(root, bare, "2")
    copy = copy_of(tmp_path, "2")
    git(root, "worktree", "add", "-q", str(copy), "attavo/2-y")
    (copy / "attavo.toml").write_text((copy / "attavo.toml").read_text() + "# dirty\n")
    rc, out = drive(root, "2")
    assert "stays" in out or "tracked changes" in out, out
    assert copy.is_dir()
    git(copy, "checkout", "-q", "--", "attavo.toml")
    git(copy, "commit", "-q", "--allow-empty", "-m", "after the merge", "--author",
        "attavo executor (fixture) <executor+fixture@attavo.invalid>")
    rc, out = drive(root, "2")
    assert "stays" in out and "not in origin's default branch" in out, out
    assert copy.is_dir()
