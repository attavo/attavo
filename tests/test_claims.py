"""attavo claims: work in flight read from git, and an item's collisions
with it (the Plan Map's L1, 2026-09-27). Written red first: on 3f1bd57
there is no claims subcommand and no attavo.claims module.

Run: python3 -m pytest tests -q
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from helpers import commit_all, git, make_tenant

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"


def run_attavo(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(ATTAVO), *args], cwd=str(root),
                          capture_output=True, text=True, check=False)


def push_branch(root: Path, branch: str, files: dict[str, str]) -> None:
    """A branch in flight: committed off main and published as
    refs/remotes/origin/<branch>, the way a fetched remote branch reads,
    then back on main."""
    git(root, "checkout", "-q", "-b", branch, "main")
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    commit_all(root, f"work on {branch}")
    git(root, "update-ref", f"refs/remotes/origin/{branch}", git(root, "rev-parse", "HEAD").strip())
    git(root, "checkout", "-q", "main")


def spec_text(claims: list[str]) -> str:
    return ("## Problem\n\nClass: fix\n\nsomething.\n\n## Claims\n\n"
            + "".join(f"- {c}\n" for c in claims)
            + "\n## Criteria\n\n- c.sh: exits 0\n")


def test_overlap_reads_paths_and_globs_both_ways() -> None:
    from attavo.claims import overlap
    assert overlap("attavo/spec.py", "attavo/spec.py")
    assert overlap("src/**", "src/a/b.py") and overlap("src/a/b.py", "src/**")
    assert overlap("tests/fixtures/*/specs/*/spec.md", "tests/fixtures/")
    assert not overlap("attavo/spec.py", "attavo/verify.py")
    assert not overlap("src/**", "docs/readme.md")


def test_claims_lists_unmerged_branches_with_their_spec_claims_and_skips_merged_ones(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "valid")
    push_branch(root, "attavo/2-x", {"specs/2/spec.md": spec_text(["attavo/spec.py"]),
                                     "records/2/spec-01.json": "{}\n"})
    push_branch(root, "lane/3-y", {"adapters/run": "x\n"})
    push_branch(root, "attavo/4-done", {"lib/z.py": "z\n"})
    git(root, "merge", "-q", "--no-ff", "-m", "merge 4", "attavo/4-done")
    git(root, "update-ref", "refs/remotes/origin/main", git(root, "rev-parse", "HEAD").strip())
    run = run_attavo(root, "claims", "--json")
    assert run.returncode == 0, run.stdout + run.stderr
    report: dict[str, Any] = json.loads(run.stdout)
    by_branch = {f["branch"]: f for f in report["in_flight"]}
    assert set(by_branch) == {"attavo/2-x", "lane/3-y"}, by_branch
    assert by_branch["attavo/2-x"]["item"] == "2"
    assert by_branch["attavo/2-x"]["declared"] == ["attavo/spec.py"]
    assert by_branch["attavo/2-x"]["changed"] == [], "specs and records are not changes"
    assert by_branch["lane/3-y"]["changed"] == ["adapters/run"] and by_branch["lane/3-y"]["item"] is None


def test_an_item_that_collides_with_work_in_flight_is_named_by_branch_and_path_and_exits_one(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "valid")
    push_branch(root, "lane/9-other", {"src/marker.txt": "someone else\n"})
    run = run_attavo(root, "claims", "1")
    assert run.returncode == 1, run.stdout + run.stderr
    assert "item 1 collides with 1 branch(es) in flight:" in run.stdout, run.stdout
    assert "lane/9-other: src/marker.txt" in run.stdout, run.stdout


def test_an_item_with_disjoint_claims_exits_zero_and_its_own_branch_is_not_a_collision(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "valid")
    push_branch(root, "attavo/1-self", {"src/marker.txt": "mine\n"})
    push_branch(root, "lane/9-other", {"docs/readme.md": "x\n"})
    run = run_attavo(root, "claims", "1", "--json")
    assert run.returncode == 0, run.stdout + run.stderr
    report = json.loads(run.stdout)
    assert report["item"] == "1" and report["claims"] == ["src/**"], report
    assert report["collisions"] == [], report


def test_claims_refuses_by_name_when_the_default_branch_is_not_set(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "valid")
    git(root, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
    run = run_attavo(root, "claims")
    assert run.returncode == 2
    assert "refs/remotes/origin/HEAD is not set" in run.stderr, run.stderr
