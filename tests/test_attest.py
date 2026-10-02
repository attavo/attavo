"""attavo attest: refuse unless the latest spec, approval, build, and
verify records for an item all exist and chain, derive the item from the
merge commit's own record paths rather than a branch name, and print the
attestation statement to stdout. Never writes -- the four stage records
are the evidence; a forge-specific template signs the printed statement
on the merge group (issue #69, section "The lane").

Merges tests/test_attest_dry_run.py into this file (issue #69, change 5):
attest itself is now always a read, so there is nothing left for a
--dry-run mode to do differently.

Regression tests, written and run red first: on `main` before this lane
`attavo attest` wrote an attestation-NN.json record and accepted
--dry-run; every test here that expects nothing written, a printed
statement, or --dry-run refused fails against that shape.

Run: python3 -m pytest tests -q
"""
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from helpers import commit_all, git, init_repo, make_tenant

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"
STATEMENT_SCHEMA = json.loads((ROOT / "schema" / "attestation.schema.json").read_text())


def run_attavo(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
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


def records_snapshot(root: Path) -> list[Any]:
    records_dir = root / "records"
    return sorted(records_dir.rglob("*")) if records_dir.is_dir() else []


# --- row 1: a coherent chain prints a valid statement and writes nothing ---

def test_attest_prints_a_valid_statement_on_a_coherent_chain_and_writes_nothing(tmp_path: Path) -> None:
    """Acceptance row 1: the merge commit carries no attavo/N-* branch
    information at all -- 'improve-thing' names nothing about item 1 --
    proving the derivation reads record paths, not the branch name."""
    root = make_tenant(tmp_path, "build_valid")
    configure_forge(root)
    git(root, "checkout", "-b", "improve-thing")
    traverse_item(root, "1")
    merge_into_main(root, "improve-thing")

    before = records_snapshot(root)
    run = run_attavo(root, "attest")
    assert run.returncode == 0, run.stdout + run.stderr

    statement = json.loads(run.stdout)
    jsonschema.validate(statement, STATEMENT_SCHEMA)
    assert statement["item"] == "1"
    assert statement["sha"] == statement["merge_sha"]
    assert statement["tokens"].keys() >= {"build", "verify", "counsel", "input", "output"}

    assert records_snapshot(root) == before
    assert git(root, "status", "--porcelain") == ""


# --- row 2: every refusal and the --partial not-yet path, same as before ---

def test_attest_refuses_a_chain_with_a_missing_verify_record(tmp_path: Path) -> None:
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

    before = records_snapshot(root)
    run = run_attavo(root, "attest")
    assert run.returncode == 1
    assert "verify" in run.stderr
    assert records_snapshot(root) == before


def test_attest_refuses_a_build_whose_approval_sha_is_not_the_latest_approval(tmp_path: Path) -> None:
    """A re-approval after the build must force a re-run."""
    root = make_tenant(tmp_path, "build_valid")
    configure_forge(root)
    git(root, "checkout", "-b", "attavo/1-x")
    traverse_item(root, "1")

    assert run_attavo(root, "approve", "1", "--by", "alice").returncode == 0
    commit_all(root, "attavo: second approval record for #1")
    merge_into_main(root, "attavo/1-x")

    run = run_attavo(root, "attest")
    assert run.returncode == 1
    assert "approval_sha" in run.stderr


def test_attest_exits_0_and_writes_nothing_when_the_merge_commit_names_zero_items(tmp_path: Path) -> None:
    """Issue #58: not every merge is an item merge. The coherence
    template's signing steps rely on stdout not being JSON here -- a
    merge group can carry no item exactly as easily as a pull request
    can -- so this is asserted directly, not just inferred from the
    printed line's wording."""
    root = make_tenant(tmp_path, "build_valid")
    git(root, "checkout", "-b", "chore/unrelated")
    (root / "README-CHORE.md").write_text("nothing to do with any item\n")
    commit_all(root, "chore: unrelated change")
    merge_into_main(root, "chore/unrelated")

    before = records_snapshot(root)
    run = run_attavo(root, "attest")
    assert run.returncode == 0, run.stderr
    assert records_snapshot(root) == before
    with pytest.raises(json.JSONDecodeError):
        json.loads(run.stdout)


def test_attest_refuses_when_the_merge_commit_names_two_items(tmp_path: Path) -> None:
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


def test_attest_exits_2_with_no_manifest(tmp_path: Path) -> None:
    root = tmp_path / "no_manifest"
    root.mkdir()
    (root / "f.txt").write_text("x\n")
    init_repo(root, "initial")

    run = run_attavo(root, "attest")
    assert run.returncode == 2


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
    naming a sha the existing verify record does not: verify-01 names
    build-01, and build-02 then passes, so verify-01 is superseded."""
    git(root, "checkout", "-b", "attavo/1-x")
    traverse_item(root, "1")
    records_dir = root / "records" / "1"
    build_01 = json.loads((records_dir / "build-01.json").read_text())
    build_01["sha"] = "f" * 40
    (records_dir / "build-02.json").write_text(json.dumps(build_01))
    commit_all(root, "attavo: second build record for #1")
    merge_into_main(root, "attavo/1-x")


def build_second_verify_names_second_build(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A real re-approval, re-build, and re-verify: build-02 names the
    second approval, verify-02 names build-02, and the chain holds --
    the ordinary case a superseded verify must never be confused with.
    ATTAVO_FIXTURE_WRITE points the second build at a second file, its
    path added to the tenant's own boundary, since the fixture adapter's
    build is not idempotent against its own tree -- a second real build
    committing the same file as the first has nothing left to commit."""
    git(root, "checkout", "-b", "attavo/1-x")
    traverse_item(root, "1")
    assert run_attavo(root, "approve", "1", "--by", "alice").returncode == 0
    commit_all(root, "attavo: second approval record for #1")
    spec = root / "specs" / "1" / "spec.md"
    spec.write_text(spec.read_text().replace("- built.txt", "- built.txt\n- built2.txt"))
    monkeypatch.setenv("ATTAVO_FIXTURE_WRITE", "built2.txt")
    assert run_attavo(root, "build", "1").returncode == 0
    commit_all(root, "attavo: second build record for #1")
    assert run_attavo(root, "verify", "1").returncode == 0
    commit_all(root, "attavo: second verify record for #1")
    merge_into_main(root, "attavo/1-x")


def build_two_items(root: Path) -> None:
    git(root, "checkout", "-b", "multi")
    (root / "records" / "1").mkdir(parents=True)
    (root / "records" / "1" / "spec-01.json").write_text("{}\n")
    (root / "records" / "2").mkdir(parents=True)
    (root / "records" / "2" / "spec-01.json").write_text("{}\n")
    commit_all(root, "two items' records in one commit")
    merge_into_main(root, "multi")


def build_missing_verify_after_reapproval(root: Path) -> None:
    """Spec, approval, build present, then a second approval after the
    build -- the shape that broke the chain in issue #62 -- verify
    absent."""
    git(root, "checkout", "-b", "attavo/1-x")
    assert run_attavo(root, "spec-gate", "1").returncode == 0
    commit_all(root, "attavo: spec-gate record for #1")
    assert run_attavo(root, "approve", "1", "--by", "alice").returncode == 0
    commit_all(root, "attavo: approval record for #1")
    assert run_attavo(root, "build", "1").returncode == 0
    commit_all(root, "attavo: build record for #1")
    assert run_attavo(root, "approve", "1", "--by", "alice").returncode == 0
    commit_all(root, "attavo: second approval record for #1")
    merge_into_main(root, "attavo/1-x")


OTHER_REFUSALS = [
    pytest.param(build_two_items, None, id="two-items"),
]


@pytest.mark.parametrize("build, needle", OTHER_REFUSALS)
def test_attest_refusals_are_unaffected_by_partial(
        tmp_path: Path, build: Callable[[Path], object], needle: str | None) -> None:
    """--partial only forgives a missing (or superseded-verify) record;
    every other refusal -- a stale approval_sha, or two items in the
    merge diff -- is identical with or without it."""
    root = make_tenant(tmp_path, "build_valid")
    configure_forge(root)
    build(root)

    plain = run_attavo(root, "attest")
    partial = run_attavo(root, "attest", "--partial")

    assert plain.returncode == partial.returncode
    assert plain.stderr == partial.stderr
    if needle:
        assert needle in plain.stderr


def test_partial_exits_0_naming_verify_absent_and_writes_nothing(tmp_path: Path) -> None:
    """spec, approval, build present, verify absent, on a no-fast-forward
    merge -- attest --partial exits 0, names the item and the stages
    present and absent, and writes nothing."""
    root = make_tenant(tmp_path, "build_valid")
    configure_forge(root)
    build_missing_verify_record(root)

    before = records_snapshot(root)
    run = run_attavo(root, "attest", "--partial")

    assert run.returncode == 0, run.stdout + run.stderr
    assert "1" in run.stdout
    assert "verify" in run.stdout
    assert records_snapshot(root) == before
    assert git(root, "status", "--porcelain") == ""


def test_partial_still_refuses_a_stale_approval_sha(tmp_path: Path) -> None:
    """The same fixture with a re-approval after the build -- --partial
    does not bypass the approval_sha chain assertion."""
    root = make_tenant(tmp_path, "build_valid")
    configure_forge(root)
    build_missing_verify_after_reapproval(root)

    run = run_attavo(root, "attest", "--partial")

    assert run.returncode == 1
    assert "approval_sha" in run.stderr


def test_without_partial_the_missing_verify_fixture_still_refuses(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "build_valid")
    configure_forge(root)
    build_missing_verify_record(root)

    run = run_attavo(root, "attest")

    assert run.returncode == 1
    assert "no verify record" in run.stderr


def test_partial_exits_0_naming_the_superseded_verify_and_the_build_it_named(tmp_path: Path) -> None:
    """Acceptance row 1, issue #109: spec, approval, build-01 pass,
    verify-01 (naming build-01), build-02 pass. A verify record can only
    name a build that existed when it was written, so verify-01 is
    superseded by build-02, not a mismatch -- --partial exits 0 with the
    not-yet line naming verify-01 and build-02, and writes nothing.

    Regression: on main before this lane, this fixture makes --partial
    refuse with 'verify's build_sha does not name the latest build'."""
    root = make_tenant(tmp_path, "build_valid")
    configure_forge(root)
    build_stale_verify_build_sha(root)

    before = records_snapshot(root)
    run = run_attavo(root, "attest", "--partial")

    assert run.returncode == 0, run.stdout + run.stderr
    assert "item 1 not yet" in run.stdout
    assert "verify-01 names build-01, superseded by build-02" in run.stdout
    assert records_snapshot(root) == before
    assert git(root, "status", "--porcelain") == ""


def test_attest_full_refuses_the_same_records_naming_the_superseded_verify(tmp_path: Path) -> None:
    """Acceptance row 2, issue #109: the same records without --partial
    refuse -- a superseded verify is still not a verify for the latest
    build -- with the new message, never the retired mismatch wording.

    Regression: on main before this lane, this exits 1 with
    'verify's build_sha does not name the latest build' instead."""
    root = make_tenant(tmp_path, "build_valid")
    configure_forge(root)
    build_stale_verify_build_sha(root)

    run = run_attavo(root, "attest")

    assert run.returncode == 1
    assert "no verify names the latest build" in run.stderr
    assert "verify-01 names" in run.stderr
    assert "verify's build_sha does not name the latest build" not in run.stderr


def test_attest_and_partial_agree_when_verify_two_names_build_two(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Acceptance row 3, issue #109: verify-02 names build-02 and passes
    -- the chain holds, so attest and attest --partial behave exactly as
    they did before this lane, both printing the same statement."""
    root = make_tenant(tmp_path, "build_valid")
    configure_forge(root)
    build_second_verify_names_second_build(root, monkeypatch)

    plain = run_attavo(root, "attest")
    partial = run_attavo(root, "attest", "--partial")

    assert plain.returncode == 0, plain.stdout + plain.stderr
    assert partial.returncode == 0, partial.stdout + partial.stderr
    plain_statement = json.loads(plain.stdout)
    partial_statement = json.loads(partial.stdout)
    # "at" is generated fresh on each call, so it alone may differ between
    # the two invocations; every other field must still agree.
    assert {**plain_statement, "at": None} == {**partial_statement, "at": None}
    jsonschema.validate(plain_statement, STATEMENT_SCHEMA)


def test_statement_carries_no_ci_runs_key(tmp_path: Path) -> None:
    """The product does not count a forge's own CI runs; a forge-read
    producer for the number is Phase 4's, if a decision ever needs it."""
    root = make_tenant(tmp_path, "build_valid")
    configure_forge(root)
    git(root, "checkout", "-b", "improve-thing")
    traverse_item(root, "1")
    merge_into_main(root, "improve-thing")

    run = run_attavo(root, "attest")
    assert run.returncode == 0, run.stdout + run.stderr

    statement = json.loads(run.stdout)
    assert "ci_runs" not in statement


# --- row 3: --dry-run no longer exists ---

def test_dash_dash_dry_run_is_refused_as_an_unknown_argument(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "build_valid")
    configure_forge(root)
    git(root, "checkout", "-b", "attavo/1-x")
    traverse_item(root, "1")
    merge_into_main(root, "attavo/1-x")

    before = records_snapshot(root)
    run = run_attavo(root, "attest", "--dry-run")

    assert run.returncode == 2
    assert "--dry-run" in run.stderr
    assert records_snapshot(root) == before
