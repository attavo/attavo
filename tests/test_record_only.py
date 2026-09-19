"""attavo record-only: whether HEAD, against its first parent, touches only
paths.records. Issue #19 -- the decision moves out of
templates/workflows/attavo-spec-gate.yml's own shell and into the product,
so a second template does not grow a second copy of it (Lane 4, issue #54).

Regression tests, written and run red first: on `main` before this lane
there was no `record-only` subcommand, so every one of these failed with
"unknown subcommand".

Run: python3 -m pytest tests -q
"""
import os
import subprocess
import sys
from pathlib import Path

from helpers import commit_all, git, init_repo, make_tenant

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"


def run_attavo(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(ATTAVO), *args], cwd=str(root),
                          capture_output=True, text=True, check=False)


def test_record_only_true_when_the_head_commit_only_touches_records(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "valid")
    (root / "records" / "1").mkdir(parents=True)
    (root / "records" / "1" / "spec-01.json").write_text("{}\n")
    commit_all(root, "attavo: spec-gate record for #1")

    run = run_attavo(root, "record-only")
    assert run.returncode == 0, run.stderr


def test_record_only_false_when_the_head_commit_touches_specs(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "valid")
    (root / "specs" / "1" / "spec.md").write_text("# spec, amended\n")
    commit_all(root, "spec: item 1")

    run = run_attavo(root, "record-only")
    assert run.returncode == 1


def test_record_only_false_when_the_head_commit_touches_both_records_and_specs(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "valid")
    (root / "records" / "1").mkdir(parents=True)
    (root / "records" / "1" / "spec-01.json").write_text("{}\n")
    (root / "specs" / "1" / "spec.md").write_text("# spec, amended\n")
    commit_all(root, "spec + record")

    run = run_attavo(root, "record-only")
    assert run.returncode == 1


def test_record_only_false_on_a_merge_commit_from_main(tmp_path: Path) -> None:
    """Issue #17's clean-merge case, carried into the shared subcommand: a
    merge that brings in a real change from main must not read as
    record-only just because the merge commit's own diff against its
    parents is empty."""
    root = make_tenant(tmp_path, "valid")
    git(root, "checkout", "-b", "attavo/1-x")
    (root / "records" / "1").mkdir(parents=True)
    (root / "records" / "1" / "spec-01.json").write_text("{}\n")
    commit_all(root, "attavo: spec-gate record for #1")

    git(root, "checkout", "main")
    (root / "on-main.txt").write_text("main moved on\n")
    commit_all(root, "unrelated change on main")

    git(root, "checkout", "attavo/1-x")
    git(root, "merge", "--no-ff", "-m", "Merge branch 'main' into attavo/1-x", "main")

    run = run_attavo(root, "record-only")
    assert run.returncode == 1


def test_record_only_exits_2_with_no_manifest(tmp_path: Path) -> None:
    root = tmp_path / "no_manifest"
    root.mkdir()
    (root / "f.txt").write_text("x\n")
    init_repo(root, "initial")

    run = run_attavo(root, "record-only")
    assert run.returncode == 2
