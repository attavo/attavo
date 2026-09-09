"""attavo attest: refuse unless the latest spec, approval, build, and verify
records for an item all exist and chain, derive the item from the merge
commit's own record paths rather than a branch name, and write
attestation-NN.json. Issue #54, Lane 4 section 1.

Regression tests, written and run red first: on `main` before this lane
there was no `attest` subcommand, so every one of these failed with
"unknown subcommand".

Run: python3 -m pytest tests -q
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from helpers import commit_all, git, init_repo, make_tenant

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"


def run_attavo(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ATTAVO), *args], cwd=str(root),
                          capture_output=True, text=True, check=False)


def configure_forge(root: Path) -> None:
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text()
                    + '\n[executor.fixture]\ny = 2\n'
                    + '\n[checks]\ncommand = "true"\n'
                    + """\n[forge]\nchecks_command = "printf 'ci success\\n'"\n""")
    commit_all(root, "configure the fixture adapter, the tenant's checks, and the forge read")


def traverse_item(root: Path, item: str = "1") -> None:
    """spec-gate, approve, build, verify, each committed -- the shape a
    real workflow template leaves on the item's branch before it merges."""
    assert run_attavo(root, "spec-gate", item).returncode == 0
    commit_all(root, f"attavo: spec-gate record for #{item}")
    assert run_attavo(root, "approve", item, "--by", "alice").returncode == 0
    commit_all(root, f"attavo: approval record for #{item}")
    assert run_attavo(root, "build", item).returncode == 0
    commit_all(root, f"attavo: build record for #{item}")
    assert run_attavo(root, "verify", item).returncode == 0
    commit_all(root, f"attavo: verify record for #{item}")


def merge_into_main(root: Path, branch: str) -> None:
    git(root, "checkout", "main")
    git(root, "merge", "--no-ff", "-m", f"Merge branch '{branch}' into main", branch)


def latest_attestation(root: Path, item: str = "1") -> dict:
    files = sorted((root / "records" / item).glob("attestation-*.json"))
    assert files, f"no attestation record for item {item}"
    return json.loads(files[-1].read_text())


def test_attest_succeeds_on_a_full_chain_derived_from_a_branch_named_nothing_like_the_item(tmp_path):
    """Acceptance 4: the merge commit carries no attavo/N-* branch
    information at all -- 'improve-thing' names nothing about item 1 --
    proving the derivation reads record paths, not the branch name."""
    root = make_tenant(tmp_path, "build_valid")
    configure_forge(root)
    git(root, "checkout", "-b", "improve-thing")
    traverse_item(root, "1")
    merge_into_main(root, "improve-thing")

    run = run_attavo(root, "attest")
    assert run.returncode == 0, run.stdout + run.stderr

    rec = latest_attestation(root)
    assert rec["item"] == "1"
    assert rec["sha"] == rec["merge_sha"]
    assert rec["signed"] is False
    assert rec["signature"] is None
    assert rec["cost_by_stage_usd"].keys() == {"build", "verify"}


def test_attest_refuses_a_chain_with_a_missing_verify_record(tmp_path):
    """Acceptance 1."""
    root = make_tenant(tmp_path, "build_valid")
    configure_forge(root)
    git(root, "checkout", "-b", "attavo/1-x")
    assert run_attavo(root, "spec-gate", "1").returncode == 0
    commit_all(root, "attavo: spec-gate record for #1")
    assert run_attavo(root, "approve", "1", "--by", "alice").returncode == 0
    commit_all(root, "attavo: approval record for #1")
    assert run_attavo(root, "build", "1").returncode == 0
    commit_all(root, "attavo: build record for #1")
    merge_into_main(root, "attavo/1-x")

    run = run_attavo(root, "attest")
    assert run.returncode == 1
    assert "verify" in run.stderr
    assert not list((root / "records" / "1").glob("attestation-*.json"))


def test_attest_refuses_a_build_whose_approval_sha_is_not_the_latest_approval(tmp_path):
    """Acceptance 2: a re-approval after the build must force a re-run."""
    root = make_tenant(tmp_path, "build_valid")
    configure_forge(root)
    git(root, "checkout", "-b", "attavo/1-x")
    traverse_item(root, "1")

    # a second approval, with nothing rebuilt after it
    assert run_attavo(root, "approve", "1", "--by", "alice").returncode == 0
    commit_all(root, "attavo: second approval record for #1")
    merge_into_main(root, "attavo/1-x")

    run = run_attavo(root, "attest")
    assert run.returncode == 1
    assert "approval_sha" in run.stderr


def test_attest_exits_0_and_writes_nothing_when_the_merge_commit_names_zero_items(tmp_path):
    """Issue #58: not every merge is an item merge -- tenant zero's own
    installing merge touches .github/workflows/, attavo.toml, and bin/,
    none of it under paths.records, and that is an ordinary Tuesday, not
    a defect. attest exits 0 and writes no record rather than refusing."""
    root = make_tenant(tmp_path, "build_valid")
    git(root, "checkout", "-b", "chore/unrelated")
    (root / "README-CHORE.md").write_text("nothing to do with any item\n")
    commit_all(root, "chore: unrelated change")
    merge_into_main(root, "chore/unrelated")

    records_dir = root / "records"
    before = sorted(records_dir.rglob("*")) if records_dir.is_dir() else []

    run = run_attavo(root, "attest")
    assert run.returncode == 0, run.stderr

    after = sorted(records_dir.rglob("*")) if records_dir.is_dir() else []
    assert after == before, "the records directory changed"


def test_attest_refuses_when_the_merge_commit_names_two_items(tmp_path):
    """Acceptance 3b."""
    root = make_tenant(tmp_path, "build_valid")
    git(root, "checkout", "-b", "multi")
    (root / "records" / "1").mkdir(parents=True)
    (root / "records" / "1" / "spec-01.json").write_text("{}\n")
    (root / "records" / "2").mkdir(parents=True)
    (root / "records" / "2" / "spec-01.json").write_text("{}\n")
    commit_all(root, "two items' records in one commit")
    merge_into_main(root, "multi")

    run = run_attavo(root, "attest")
    assert run.returncode == 1
    assert "1" in run.stderr and "2" in run.stderr


def test_attest_exits_2_with_no_manifest(tmp_path):
    root = tmp_path / "no_manifest"
    root.mkdir()
    (root / "f.txt").write_text("x\n")
    init_repo(root, "initial")

    run = run_attavo(root, "attest")
    assert run.returncode == 2
