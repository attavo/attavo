"""attavo attest --dry-run: the same derivation and the same three
assertions -- presence, build's approval_sha, verify's build_sha -- as
`attavo attest`, on the same code path, writing nothing. Run on a pull
request or merge group, before the merge `attavo attest` itself demands,
so an incoherent chain refuses while a re-run is still possible (issue #62).

Rejection tests, written and run red first: on `main` before this lane
`attavo attest` accepted no `--dry-run` flag at all, so every dry-run
invocation here failed as an unrecognized item id or subcommand argument.

Run: python3 -m pytest tests -q
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from helpers import commit_all, git, make_tenant

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


def records_snapshot(root: Path) -> list:
    records_dir = root / "records"
    return sorted(records_dir.rglob("*")) if records_dir.is_dir() else []


def test_dry_run_refuses_a_build_whose_approval_sha_is_not_the_latest_approval(tmp_path):
    """Acceptance 1. Same fixture, same refusal, as the real attestation --
    and dry-run leaves the worktree exactly as it found it."""
    root = make_tenant(tmp_path, "build_valid")
    configure_forge(root)
    git(root, "checkout", "-b", "attavo/1-x")
    traverse_item(root, "1")

    assert run_attavo(root, "approve", "1", "--by", "alice").returncode == 0
    commit_all(root, "attavo: second approval record for #1")
    merge_into_main(root, "attavo/1-x")

    before = records_snapshot(root)
    real = run_attavo(root, "attest")
    dry = run_attavo(root, "attest", "--dry-run")

    assert real.returncode == dry.returncode == 1
    assert "approval_sha" in real.stderr
    assert real.stderr == dry.stderr
    assert records_snapshot(root) == before
    assert git(root, "status", "--porcelain") == ""


def test_dry_run_succeeds_on_a_coherent_chain_and_writes_nothing(tmp_path):
    """Acceptance 2."""
    root = make_tenant(tmp_path, "build_valid")
    configure_forge(root)
    git(root, "checkout", "-b", "attavo/1-x")
    traverse_item(root, "1")
    merge_into_main(root, "attavo/1-x")

    before = records_snapshot(root)
    run = run_attavo(root, "attest", "--dry-run")

    assert run.returncode == 0, run.stdout + run.stderr
    assert "1" in run.stdout
    for stage in ("spec-01", "approval-01", "build-01", "verify-01"):
        assert stage in run.stdout
    assert records_snapshot(root) == before
    assert git(root, "status", "--porcelain") == ""


def build_missing_verify_record(root: Path) -> None:
    git(root, "checkout", "-b", "attavo/1-x")
    assert run_attavo(root, "spec-gate", "1").returncode == 0
    commit_all(root, "attavo: spec-gate record for #1")
    assert run_attavo(root, "approve", "1", "--by", "alice").returncode == 0
    commit_all(root, "attavo: approval record for #1")
    assert run_attavo(root, "build", "1").returncode == 0
    commit_all(root, "attavo: build record for #1")
    merge_into_main(root, "attavo/1-x")


def build_stale_verify_build_sha(root: Path) -> None:
    """A second build record, minted by hand rather than a real re-run --
    the fixture adapter's build is not idempotent against its own tree --
    naming a sha the existing verify record does not."""
    git(root, "checkout", "-b", "attavo/1-x")
    traverse_item(root, "1")
    records_dir = root / "records" / "1"
    build_01 = json.loads((records_dir / "build-01.json").read_text())
    build_01["sha"] = "f" * 40
    (records_dir / "build-02.json").write_text(json.dumps(build_01))
    commit_all(root, "attavo: second build record for #1")
    merge_into_main(root, "attavo/1-x")


def build_two_items(root: Path) -> None:
    git(root, "checkout", "-b", "multi")
    (root / "records" / "1").mkdir(parents=True)
    (root / "records" / "1" / "spec-01.json").write_text("{}\n")
    (root / "records" / "2").mkdir(parents=True)
    (root / "records" / "2" / "spec-01.json").write_text("{}\n")
    commit_all(root, "two items' records in one commit")
    merge_into_main(root, "multi")


OTHER_REFUSALS = [
    pytest.param(build_missing_verify_record, "verify", id="missing-verify-record"),
    pytest.param(build_stale_verify_build_sha, "build_sha", id="verify-build-sha-stale"),
    pytest.param(build_two_items, None, id="two-items"),
]


@pytest.mark.parametrize("build, needle", OTHER_REFUSALS)
def test_dry_run_matches_the_real_attestation_on_every_other_refusal(tmp_path, build, needle):
    """Acceptance 3: for each of the other refusals -- a missing record,
    verify's build_sha not naming the latest build, two items in the merge
    diff -- dry-run and the real attestation produce the same stderr line
    and exit code on the same fixture."""
    root = make_tenant(tmp_path, "build_valid")
    configure_forge(root)
    build(root)

    dry = run_attavo(root, "attest", "--dry-run")
    real = run_attavo(root, "attest")

    assert dry.returncode == real.returncode
    assert dry.stderr == real.stderr
    if needle:
        assert needle in real.stderr


def test_dry_run_exits_0_and_writes_nothing_when_the_merge_commit_names_zero_items(tmp_path):
    """Acceptance 4, same shape as issue #58 for the real attestation."""
    root = make_tenant(tmp_path, "build_valid")
    git(root, "checkout", "-b", "chore/unrelated")
    (root / "README-CHORE.md").write_text("nothing to do with any item\n")
    commit_all(root, "chore: unrelated change")
    merge_into_main(root, "chore/unrelated")

    before = records_snapshot(root)
    run = run_attavo(root, "attest", "--dry-run")

    assert run.returncode == 0, run.stderr
    assert records_snapshot(root) == before


def test_dry_run_does_not_read_ci_runs(tmp_path, monkeypatch):
    """attest --dry-run never populates a record, so ATTAVO_CI_RUNS -- read
    only while building the attestation record -- must not affect it."""
    root = make_tenant(tmp_path, "build_valid")
    configure_forge(root)
    git(root, "checkout", "-b", "attavo/1-x")
    traverse_item(root, "1")
    merge_into_main(root, "attavo/1-x")

    before = records_snapshot(root)
    monkeypatch.setenv("ATTAVO_CI_RUNS", "7")
    run = run_attavo(root, "attest", "--dry-run")
    assert run.returncode == 0, run.stderr
    assert records_snapshot(root) == before
