"""The doctor's five proofs, as tests written before the capability.

Each test asserts that one proof holds against this repository. All five
fail at the first commit and must fail: that is the definition of done,
stated before anything exists to satisfy it. A test here turns green only
when the capability it names has landed and been used for real.

Run: python3 -m pytest tests -q
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from helpers import FIXTURES, commit_all, git, init_repo, make_tenant
from test_drive import (
    GREEN_FORGE,
    add_drive_stall_bound,
    add_forge,
    approve_item,
    latest_record,
    run_attavo,
    write_dead_marker,
)

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"


def run_doctor(root: Path) -> dict[str, Any]:
    out = subprocess.run([sys.executable, str(ATTAVO), "doctor", str(root), "--json"],
                         capture_output=True, text=True, check=False)
    report = json.loads(out.stdout)
    assert out.returncode == (0 if report["done"] else 1)
    return {p["proof"]: p for p in report["proofs"]}


@pytest.mark.own_records
def test_every_gate_rejects() -> None:
    """Proof 1. Every gate in the manifest rejects its rejection fixture.

    Holds when: a manifest exists, [gates] names at least one gate, each gate
    has a fixture under paths.rejections, and each gate exits non-zero on it.
    """
    p = run_doctor(ROOT)["every-gate-rejects"]
    assert p["holds"], p["reason"]


@pytest.mark.own_records
def test_one_item_traversed_every_stage() -> None:
    """Proof 2. One work item carries a record for spec, approval, build,
    verify, and attestation, every one written by a script."""
    p = run_doctor(ROOT)["one-item-every-stage"]
    assert p["holds"], p["reason"]


@pytest.mark.own_records
def test_no_commit_lacks_a_record() -> None:
    """Proof 3. Every commit on the default branch after the manifest was
    introduced is named by a record's sha or commits list."""
    p = run_doctor(ROOT)["no-commit-lacks-record"]
    assert p["holds"], p["reason"]


@pytest.mark.own_records
def test_no_stop_lacks_cause() -> None:
    """Proof 4, issue #94. Every non-pass build or verify record at
    record_version 3 or later carries a stop naming its class and cause."""
    p = run_doctor(ROOT)["no-stop-lacks-cause"]
    assert p["holds"], p["reason"]


@pytest.mark.own_records
def test_no_invocation_lacks_record() -> None:
    """Proof 5, issue #151. Every <stage>-NN.started marker under
    paths.records has arrived at its own <stage>-NN.json, or is still a
    live process on this host."""
    p = run_doctor(ROOT)["no-invocation-lacks-record"]
    assert p["holds"], p["reason"]


@pytest.mark.own_records
def test_every_attested_item_is_sealed() -> None:
    """Proof 5, issue #158. Every merged, fully-staged item has a
    statement in the forge's attestation store for its merge commit --
    #106, #148, #153 predate [forge].sealed_since and are named before the
    window instead."""
    p = run_doctor(ROOT)["every-attested-item-is-sealed"]
    assert p["holds"], p["reason"]


def test_record_loader_uses_highest_attempt_number(tmp_path: Path) -> None:
    """Acceptance 2a. <stage>-NN.json files are one stage's attempts; the
    doctor treats the highest NN as latest, and STAGES is unchanged.

    spec-01.json names a commit that isn't on the branch at all; spec-02.json
    names the real code commit. Proof 3 holds only if the loader reads
    spec-02, not spec-01 -- i.e. only if it picks the highest attempt.
    """
    root = tmp_path / "attempts"
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    init_repo(root, "install")

    (root / "src.txt").write_text("code\n")
    commit_all(root, "code change")
    code_sha = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()

    records = root / "records" / "1"
    records.mkdir(parents=True)
    (records / "spec-01.json").write_text(json.dumps({"sha": "0" * 40}))
    (records / "spec-02.json").write_text(json.dumps({"sha": code_sha}))
    commit_all(root, "spec records")

    p = run_doctor(root)["no-commit-lacks-record"]
    assert p["holds"], p["reason"]


def test_doctor_proof_two_counts_three_of_four_stages_still_red(tmp_path: Path) -> None:
    """Acceptance 8. A work item carrying spec, approval, and build records
    is three of the four STAGES proof two names; verify doesn't exist
    yet, so the proof stays red and names exactly that one as lacking.
    """
    root = tmp_path / "three_stages"
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    init_repo(root, "install")

    records = root / "records" / "1"
    records.mkdir(parents=True)
    (records / "spec-01.json").write_text(json.dumps({"status": "pass"}))
    (records / "approval-01.json").write_text(json.dumps({"approved_by": "alice"}))
    (records / "build-01.json").write_text(json.dumps({"status": "pass"}))
    commit_all(root, "spec, approval, and build records")

    p = run_doctor(root)["one-item-every-stage"]
    assert not p["holds"]
    assert "verify" in p["reason"]
    assert "build" not in p["reason"]


def test_doctor_proof_two_holds_on_a_fixture_carrying_all_four_stages_merged(tmp_path: Path) -> None:
    """Acceptance row 4, issue #69: attestation is retired as a record
    type -- STAGES is four stages, not five -- and proof two now also
    requires the four records to have reached HEAD by an actual merge
    commit, whose first-parent diff introduces all four, the same
    derivation attest itself uses. A fixture with one record per stage,
    landed on a branch and merged --no-ff, is enough to prove the
    mechanism."""
    root = tmp_path / "four_stages_merged"
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    init_repo(root, "install")

    subprocess.run(["git", "-C", str(root), "checkout", "-b", "attavo/1-x"], check=True)
    records = root / "records" / "1"
    records.mkdir(parents=True)
    (records / "spec-01.json").write_text(json.dumps({"status": "pass"}))
    (records / "approval-01.json").write_text(json.dumps({"approved_by": "alice"}))
    (records / "build-01.json").write_text(json.dumps({"status": "pass"}))
    (records / "verify-01.json").write_text(json.dumps({"status": "pass"}))
    commit_all(root, "spec, approval, build, and verify records")
    subprocess.run(["git", "-C", str(root), "checkout", "main"], check=True)
    subprocess.run(["git", "-C", str(root), "merge", "--no-ff", "-m",
                    "Merge branch 'attavo/1-x'", "attavo/1-x"], check=True)

    p = run_doctor(root)["one-item-every-stage"]
    assert p["holds"], p["reason"]


def test_doctor_proof_two_fails_when_four_stage_records_are_never_merged(tmp_path: Path) -> None:
    """Acceptance row 4's other half, issue #69: the same four records,
    committed straight to main with no merge at all -- proof two fails,
    naming the missing merge rather than a lacking stage."""
    root = tmp_path / "four_stages_unmerged"
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    init_repo(root, "install")

    records = root / "records" / "1"
    records.mkdir(parents=True)
    (records / "spec-01.json").write_text(json.dumps({"status": "pass"}))
    (records / "approval-01.json").write_text(json.dumps({"approved_by": "alice"}))
    (records / "build-01.json").write_text(json.dumps({"status": "pass"}))
    (records / "verify-01.json").write_text(json.dumps({"status": "pass"}))
    commit_all(root, "spec, approval, build, and verify records, committed directly")

    p = run_doctor(root)["one-item-every-stage"]
    assert not p["holds"]
    assert "merge" in p["reason"]


def test_proof_two_fails_naming_the_item_and_refusal_text_on_approval_sha_mismatch(tmp_path: Path) -> None:
    """Acceptance row 1, issue #74. build-01's approval_sha does not name
    the latest approval's sha -- attavo attest would refuse exactly this
    chain, and from v0.11.0 the coherence gate refuses the merge group on
    it. Proof two must refuse it too, not just find the four stages
    present: fails naming the item and check_attest_chain's own refusal
    text, never a re-typed copy of it."""
    root = tmp_path / "approval_sha_mismatch"
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    init_repo(root, "install")

    subprocess.run(["git", "-C", str(root), "checkout", "-b", "attavo/1-x"], check=True)
    records = root / "records" / "1"
    records.mkdir(parents=True)
    (records / "spec-01.json").write_text(json.dumps({"status": "pass"}))
    (records / "approval-01.json").write_text(json.dumps({"approved_by": "alice", "sha": "1" * 40}))
    (records / "build-01.json").write_text(
        json.dumps({"status": "pass", "approval_sha": "2" * 40, "sha": "3" * 40}))
    (records / "verify-01.json").write_text(json.dumps({"status": "pass", "build_sha": "3" * 40}))
    commit_all(root, "spec, approval, build, and verify records with a stale approval_sha")
    subprocess.run(["git", "-C", str(root), "checkout", "main"], check=True)
    subprocess.run(["git", "-C", str(root), "merge", "--no-ff", "-m",
                    "Merge branch 'attavo/1-x'", "attavo/1-x"], check=True)

    p = run_doctor(root)["one-item-every-stage"]
    assert not p["holds"]
    assert "item 1" in p["reason"]
    assert "approval_sha does not name the latest approval" in p["reason"]


def test_proof_two_fails_naming_the_item_and_the_superseded_verify(tmp_path: Path) -> None:
    """Acceptance row 5, issue #109. verify-01's build_sha does not name
    the latest build's sha, so verify-01 is superseded by that later
    build, not a mismatch -- proof two fails, naming it, with the new
    message, never the retired mismatch wording."""
    root = tmp_path / "build_sha_mismatch"
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    init_repo(root, "install")

    subprocess.run(["git", "-C", str(root), "checkout", "-b", "attavo/1-x"], check=True)
    records = root / "records" / "1"
    records.mkdir(parents=True)
    (records / "spec-01.json").write_text(json.dumps({"status": "pass"}))
    (records / "approval-01.json").write_text(json.dumps({"approved_by": "alice", "sha": "1" * 40}))
    (records / "build-01.json").write_text(
        json.dumps({"status": "pass", "approval_sha": "1" * 40, "sha": "3" * 40}))
    (records / "verify-01.json").write_text(json.dumps({"status": "pass", "build_sha": "9" * 40}))
    commit_all(root, "spec, approval, build, and verify records with a stale build_sha")
    subprocess.run(["git", "-C", str(root), "checkout", "main"], check=True)
    subprocess.run(["git", "-C", str(root), "merge", "--no-ff", "-m",
                    "Merge branch 'attavo/1-x'", "attavo/1-x"], check=True)

    p = run_doctor(root)["one-item-every-stage"]
    assert not p["holds"]
    assert "item 1" in p["reason"]
    assert "no verify names the latest build" in p["reason"]
    assert "verify-01 names" in p["reason"]
    assert "verify's build_sha does not name the latest build" not in p["reason"]


def test_proof_two_holds_naming_only_the_held_item_when_another_is_refused(tmp_path: Path) -> None:
    """Acceptance row 3, issue #74. Item 1 carries every stage but a
    stale approval_sha; item 2's chain holds cleanly. Proof two holds,
    naming only item 2 -- item 1's refusal never leaks into a passing
    reason."""
    root = tmp_path / "two_items_one_refused"
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    init_repo(root, "install")

    subprocess.run(["git", "-C", str(root), "checkout", "-b", "attavo/1-x"], check=True)
    records1 = root / "records" / "1"
    records1.mkdir(parents=True)
    (records1 / "spec-01.json").write_text(json.dumps({"status": "pass"}))
    (records1 / "approval-01.json").write_text(json.dumps({"approved_by": "alice", "sha": "1" * 40}))
    (records1 / "build-01.json").write_text(
        json.dumps({"status": "pass", "approval_sha": "2" * 40, "sha": "3" * 40}))
    (records1 / "verify-01.json").write_text(json.dumps({"status": "pass", "build_sha": "3" * 40}))
    commit_all(root, "spec, approval, build, and verify records for item 1, chain refused")
    subprocess.run(["git", "-C", str(root), "checkout", "main"], check=True)
    subprocess.run(["git", "-C", str(root), "merge", "--no-ff", "-m",
                    "Merge branch 'attavo/1-x'", "attavo/1-x"], check=True)

    subprocess.run(["git", "-C", str(root), "checkout", "-b", "attavo/2-x"], check=True)
    records2 = root / "records" / "2"
    records2.mkdir(parents=True)
    (records2 / "spec-01.json").write_text(json.dumps({"status": "pass"}))
    (records2 / "approval-01.json").write_text(json.dumps({"approved_by": "alice", "sha": "9" * 40}))
    (records2 / "build-01.json").write_text(
        json.dumps({"status": "pass", "approval_sha": "9" * 40, "sha": "8" * 40}))
    (records2 / "verify-01.json").write_text(json.dumps({"status": "pass", "build_sha": "8" * 40}))
    commit_all(root, "spec, approval, build, and verify records for item 2, chain holds")
    subprocess.run(["git", "-C", str(root), "checkout", "main"], check=True)
    subprocess.run(["git", "-C", str(root), "merge", "--no-ff", "-m",
                    "Merge branch 'attavo/2-x'", "attavo/2-x"], check=True)
    merge2_sha = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()

    p = run_doctor(root)["one-item-every-stage"]
    assert p["holds"], p["reason"]
    assert p["reason"] == (
        "item(s) 2 carry spec, approval, build, verify with the chain held, "
        f"merged at {merge2_sha[:12]}")


def test_proof_three_holds_on_an_item_merge_with_no_attestation_record(tmp_path: Path) -> None:
    """Acceptance row 5, issue #69: an item's merge commit carries its
    record paths in its own first-parent diff, so it is a record commit
    by the rule that already exists -- no merge_sha field is needed, and
    there is no attestation record at all now that attest never writes
    (replaces test_merge_sha_covers_the_merge_commit_for_proof_three,
    deleted alongside the merge_sha special case it exercised)."""
    root = tmp_path / "item_merge"
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    init_repo(root, "install")

    subprocess.run(["git", "-C", str(root), "checkout", "-b", "attavo/1-x"], check=True)
    records = root / "records" / "1"
    records.mkdir(parents=True)
    (records / "spec-01.json").write_text(json.dumps({"status": "pass"}))
    (records / "approval-01.json").write_text(json.dumps({"approved_by": "alice"}))
    (records / "build-01.json").write_text(json.dumps({"status": "pass"}))
    (records / "verify-01.json").write_text(json.dumps({"status": "pass"}))
    commit_all(root, "spec, approval, build, and verify records for item 1")
    subprocess.run(["git", "-C", str(root), "checkout", "main"], check=True)
    subprocess.run(["git", "-C", str(root), "merge", "--no-ff", "-m",
                    "Merge branch 'attavo/1-x'", "attavo/1-x"], check=True)

    p = run_doctor(root)["no-commit-lacks-record"]
    assert p["holds"], p["reason"]
    assert not (records / "attestation-01.json").exists()


def _three_way_fixture(tmp_path: Path, name: str, *, boundary_paths: list[str] | None,
                       ordinary_touches: str, include_direct_commit: bool) -> Path:
    """A tenant history carrying one commit of each of proof three's three
    kinds (issue #72): an item merge (recorded, by the records-path rule
    proof two's fixtures already exercise), a --no-ff merge touching only
    `ordinary_touches` (ordinary), and, when asked, one commit made
    straight to main naming no record (unrecorded -- on a protected main
    with a queue, the only way a change arrives without the forge).
    """
    root = tmp_path / name
    root.mkdir()
    manifest = '[paths]\nrecords = "records"\n'
    if boundary_paths is not None:
        patterns = ", ".join(f'"{path}"' for path in boundary_paths)
        manifest += f'\n[spec.boundary]\npaths = [{patterns}]\n'
    (root / "attavo.toml").write_text(manifest)
    init_repo(root, "install")

    subprocess.run(["git", "-C", str(root), "checkout", "-b", "attavo/1-x"], check=True)
    records = root / "records" / "1"
    records.mkdir(parents=True)
    (records / "spec-01.json").write_text(json.dumps({"status": "pass"}))
    commit_all(root, "spec record for item 1")
    subprocess.run(["git", "-C", str(root), "checkout", "main"], check=True)
    subprocess.run(["git", "-C", str(root), "merge", "--no-ff", "-m",
                    "Merge branch 'attavo/1-x'", "attavo/1-x"], check=True)

    subprocess.run(["git", "-C", str(root), "checkout", "-b", "ordinary-change"], check=True)
    touched = root / ordinary_touches
    touched.parent.mkdir(parents=True, exist_ok=True)
    touched.write_text("v2\n")
    commit_all(root, "an ordinary change")
    subprocess.run(["git", "-C", str(root), "checkout", "main"], check=True)
    subprocess.run(["git", "-C", str(root), "merge", "--no-ff", "-m",
                    "Merge branch 'ordinary-change'", "ordinary-change"], check=True)

    if include_direct_commit:
        (root / "direct.txt").write_text("direct\n")
        commit_all(root, "a change committed straight to main")

    return root


def test_proof_three_fails_naming_the_direct_commit_with_the_three_counts(tmp_path: Path) -> None:
    """Acceptance row 1, issue #72. The three-way fixture with its direct
    commit included: proof three fails, naming the direct commit and
    reporting all three counts in the reason."""
    root = _three_way_fixture(tmp_path, "direct_commit", boundary_paths=["boundary/**"],
                              ordinary_touches="pins/version.txt", include_direct_commit=True)
    direct_sha = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()

    p = run_doctor(root)["no-commit-lacks-record"]
    assert not p["holds"]
    assert "1 recorded, 1 ordinary, 1 unrecorded" in p["reason"]
    assert direct_sha[:12] in p["reason"]


def test_proof_three_holds_with_zero_unrecorded_once_the_direct_commit_is_gone(tmp_path: Path) -> None:
    """Acceptance row 2, issue #72. The same fixture with the direct commit
    removed: proof three holds, reporting `0 unrecorded`."""
    root = _three_way_fixture(tmp_path, "no_direct_commit", boundary_paths=["boundary/**"],
                              ordinary_touches="pins/version.txt", include_direct_commit=False)

    p = run_doctor(root)["no-commit-lacks-record"]
    assert p["holds"], p["reason"]
    assert "1 recorded, 1 ordinary, 0 unrecorded" in p["reason"]


def test_proof_three_reports_boundary_evasions_and_still_holds(tmp_path: Path) -> None:
    """Acceptance row 3, issue #72. The ordinary merge touches a path
    inside [spec.boundary].paths: the reason carries `boundary evasions:
    1`, and the proof still holds -- evasions are reported, never
    refused."""
    root = _three_way_fixture(tmp_path, "boundary_evasion", boundary_paths=["boundary/**"],
                              ordinary_touches="boundary/thing.txt", include_direct_commit=False)

    p = run_doctor(root)["no-commit-lacks-record"]
    assert p["holds"], p["reason"]
    assert "boundary evasions: 1" in p["reason"]


def test_proof_three_reports_boundary_evasions_as_not_available_with_no_boundary_paths(tmp_path: Path) -> None:
    """Acceptance row 4, issue #72. A tenant declaring no [spec.boundary]
    paths at all: evasions are reported as `n/a`, not `0` -- an absent
    read is not the same measurement as zero evasions found."""
    root = _three_way_fixture(tmp_path, "no_boundary", boundary_paths=None,
                              ordinary_touches="pins/version.txt", include_direct_commit=False)

    p = run_doctor(root)["no-commit-lacks-record"]
    assert p["holds"], p["reason"]
    assert "boundary evasions: n/a" in p["reason"]
    assert p["boundary_evasions"] == "n/a"


def test_proof_three_json_carries_the_path_ratio_fields(tmp_path: Path) -> None:
    """Acceptance row 5, issue #72. `attavo doctor --json` carries
    `recorded`, `ordinary`, `unrecorded`, and `boundary_evasions` on
    proof three's own entry -- the path-ratio primitive Phase 4 names,
    produced now because a decision depends on it."""
    root = _three_way_fixture(tmp_path, "json_fields", boundary_paths=["boundary/**"],
                              ordinary_touches="pins/version.txt", include_direct_commit=False)

    p = run_doctor(root)["no-commit-lacks-record"]
    assert p["recorded"] == 1
    assert p["ordinary"] == 1
    assert p["unrecorded"] == 0
    assert p["boundary_evasions"] == 0


def test_proof_three_json_lists_the_ordinary_and_unrecorded_commits(tmp_path: Path) -> None:
    """Issue #148. Proof three's JSON entry carries `ordinary_commits` and
    `unrecorded_commits`, each commit with sha and subject, so a reader
    can name the commits behind the counts without re-running the
    classification by hand. The ordinary merge that touches a boundary
    path is flagged `boundary_evasion: true`."""
    root = _three_way_fixture(tmp_path, "commit_lists", boundary_paths=["boundary/**"],
                              ordinary_touches="boundary/thing.txt", include_direct_commit=True)
    direct_sha = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()

    p = run_doctor(root)["no-commit-lacks-record"]
    ordinary_commits = p["ordinary_commits"]
    unrecorded_commits = p["unrecorded_commits"]
    assert [c["subject"] for c in ordinary_commits] == ["Merge branch 'ordinary-change'"]
    assert len(ordinary_commits[0]["sha"]) == 40
    assert ordinary_commits[0]["boundary_evasion"] is True
    assert [c["subject"] for c in unrecorded_commits] == ["a change committed straight to main"]
    assert unrecorded_commits[0]["sha"] == direct_sha


def _window_fixture(tmp_path: Path, name: str) -> tuple[Path, str, str]:
    """Two commits made straight to main, then a branch carrying item 1's
    spec record, merged --no-ff. Returns (root, merge_sha, branch_sha) --
    merge_sha is on main's first-parent line and is the only sha issue
    #74's [doctor] from tests declare as valid; branch_sha is the branch
    commit itself, reachable only as the merge's second parent and never
    on that line, the sha the invalid-from test declares instead."""
    root = tmp_path / name
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    init_repo(root, "install")

    (root / "pre1.txt").write_text("1\n")
    commit_all(root, "a change committed straight to main")
    (root / "pre2.txt").write_text("2\n")
    commit_all(root, "another change committed straight to main")

    subprocess.run(["git", "-C", str(root), "checkout", "-b", "attavo/1-x"], check=True)
    records = root / "records" / "1"
    records.mkdir(parents=True)
    (records / "spec-01.json").write_text(json.dumps({"status": "pass"}))
    commit_all(root, "spec record for item 1")
    branch_sha = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run(["git", "-C", str(root), "checkout", "main"], check=True)
    subprocess.run(["git", "-C", str(root), "merge", "--no-ff", "-m",
                    "Merge branch 'attavo/1-x'", "attavo/1-x"], check=True)
    merge_sha = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                               capture_output=True, text=True, check=True).stdout.strip()
    return root, merge_sha, branch_sha


def _declare_doctor_from(root: Path, from_sha: str) -> None:
    """Names [doctor] from via its own merge, not a direct commit to
    main -- declaring the window must never itself read as an unrecorded
    commit inside it."""
    subprocess.run(["git", "-C", str(root), "checkout", "-b", "declare-window"], check=True)
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text() + f'\n[doctor]\nfrom = "{from_sha}"\n')
    commit_all(root, "declare the doctor's window")
    subprocess.run(["git", "-C", str(root), "checkout", "main"], check=True)
    subprocess.run(["git", "-C", str(root), "merge", "--no-ff", "-m",
                    "Merge branch 'declare-window'", "declare-window"], check=True)


def test_proof_three_holds_reporting_before_the_window_with_doctor_from(tmp_path: Path) -> None:
    """Acceptance row 4, issue #74. [doctor] from names the item merge:
    the two commits made straight to main before it fall before the
    window, not into unrecorded -- proof three holds, `before the
    window: 2`, and the window's own counts, 1 recorded (the item merge)
    and 1 ordinary (the merge that declares the window itself)."""
    root, merge_sha, _ = _window_fixture(tmp_path, "window_holds")
    _declare_doctor_from(root, merge_sha)

    p = run_doctor(root)["no-commit-lacks-record"]
    assert p["holds"], p["reason"]
    assert "before the window: 2" in p["reason"]
    assert "1 recorded, 1 ordinary, 0 unrecorded" in p["reason"]
    assert p["before_window"] == 2


def test_proof_three_holds_when_doctor_from_names_a_seven_character_prefix(tmp_path: Path) -> None:
    """Acceptance row 5 (amendment), issue #74. [doctor] from need not be
    a full sha -- any spelling git rev-parse --verify resolves is
    accepted, resolved before the first-parent check runs. A
    seven-character prefix of the item merge holds with the same counts
    as the full-sha case."""
    root, merge_sha, _ = _window_fixture(tmp_path, "window_holds_short_sha")
    _declare_doctor_from(root, merge_sha[:7])

    p = run_doctor(root)["no-commit-lacks-record"]
    assert p["holds"], p["reason"]
    assert "before the window: 2" in p["reason"]
    assert "1 recorded, 1 ordinary, 0 unrecorded" in p["reason"]
    assert p["before_window"] == 2


def test_proof_three_fails_naming_the_key_when_doctor_from_is_off_the_first_parent_line(tmp_path: Path) -> None:
    """Acceptance row 5, issue #74. [doctor] from names the item branch's
    own commit -- reachable only as a merge's second parent, never on
    main's first-parent line -- proof three fails naming the key rather
    than silently falling back to the install commit."""
    root, _, branch_sha = _window_fixture(tmp_path, "window_off_line")
    _declare_doctor_from(root, branch_sha)

    p = run_doctor(root)["no-commit-lacks-record"]
    assert not p["holds"]
    assert "[doctor] from" in p["reason"]
    assert branch_sha[:12] in p["reason"]


def test_proof_three_defaults_to_the_install_commit_when_doctor_key_is_absent(tmp_path: Path) -> None:
    """Acceptance row 6, issue #74. No [doctor] table at all: the window
    defaults to the install commit exactly as before this issue -- both
    commits made straight to main are still read as unrecorded, and
    before_window is 0 -- the read every existing proof-three test
    exercises is unaffected by the key's addition."""
    root, _merge_sha, _branch_sha = _window_fixture(tmp_path, "window_absent")

    p = run_doctor(root)["no-commit-lacks-record"]
    assert not p["holds"]
    assert "1 recorded, 0 ordinary, 2 unrecorded" in p["reason"]
    assert "before the window: 0" in p["reason"]
    assert p["before_window"] == 0


def test_doctor_proof_one_refuses_a_gate_with_empty_retires(tmp_path: Path) -> None:
    """Residual, issue #54 -- proposed, not yet ruled on: a gate that
    retires nothing only adds. The manifest schema has required
    [gates.<name>].retires to be non-empty since Phase 1, but read_manifest
    never validates against that schema, so nothing enforced it at
    runtime until this proof read it directly."""
    root = tmp_path / "empty_retires"
    root.mkdir()
    (root / "attavo.toml").write_text(
        '[paths]\nrejections = "rejections"\n\n'
        '[gates.spec]\ncommand = "false"\nretires = []\n')
    (root / "rejections" / "spec").mkdir(parents=True)
    init_repo(root, "install")

    p = run_doctor(root)["every-gate-rejects"]
    assert not p["holds"]
    assert "retires" in p["reason"] and "spec" in p["reason"]


def test_doctor_proof_one_runs_the_gate_without_an_item_argument(tmp_path: Path) -> None:
    """Issue #7, and its regression test. [gates.spec].command is a bare
    `attavo spec-gate`, no item argument, and its rejections/spec/ fixture
    carries no attavo/N-* branch at all (init_repo leaves it on `main`) --
    so before the doctor exported the fixture's item as ATTAVO_ITEM, the
    gate command would exit 2 (cannot derive item id) without ever writing
    a record, which still counts as "rejected" but proves nothing about the
    prose criterion this fixture means to test.

    Red at the merge base: no ATTAVO_ITEM export existed, so no
    spec-01.json was ever written and reading it raised FileNotFoundError.
    Green once the doctor exports it: the record exists and names the
    prose criterion by name, which is the actual thing proof one asserts.
    """
    outer = tmp_path / "gate_rejection"
    shutil.copytree(FIXTURES / "gate_rejection", outer)
    cmd = f"{sys.executable} {ATTAVO}"
    toml = outer / "attavo.toml"
    toml.write_text(toml.read_text().replace("ATTAVO_CMD", cmd))
    init_repo(outer / "rejections" / "spec")

    p = run_doctor(outer)["every-gate-rejects"]
    assert p["holds"], p["reason"]

    record = json.loads(
        (outer / "rejections" / "spec" / "records" / "1" / "spec-01.json").read_text())
    assert record["refusal"] == "criterion is prose: missing.sh"


def test_doctor_proof_four_fails_on_a_failed_record_with_no_stop(tmp_path: Path) -> None:
    """Acceptance row 1, issue #94: a failed build record at
    record_version 3 with a two-line log and no stop at all -- a stop
    the product forgot to write, the way tenant zero's #576 build-11
    (before record_version 3 existed) was diagnosed only by a person
    rerunning it by hand. Fails today: no proof four exists."""
    root = make_tenant(tmp_path, "no_stop_lacks_cause")

    p = run_doctor(root)["no-stop-lacks-cause"]
    assert not p["holds"]
    assert "build-01" in p["reason"]
    assert "no stop" in p["reason"]


def test_doctor_proof_four_holds_on_a_fixture_with_stop_and_gates_heading(tmp_path: Path) -> None:
    """Acceptance row 2, issue #94: a failed build record whose stop is
    present and valid, and whose log carries the gates heading since
    gates actually ran (selected_tests is set) -- proof four holds."""
    root = tmp_path / "stop_holds"
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    init_repo(root, "install")

    records = root / "records" / "1"
    records.mkdir(parents=True)
    build = {
        "status": "failed", "record_version": 3,
        "selected_tests": {"select": "cat", "run": "cat", "count": 1, "exit": 1},
        "stop": {"class": "unclassed", "provenance": "product", "detail": "1 failed, 0 passed"},
    }
    (records / "build-01.json").write_text(json.dumps(build))
    (records / "build-01.log").write_text(
        "--- attavo gates, iteration 1 ---\ncriterion x: exit 1\n1 failed, 0 passed")
    commit_all(root, "a failed build record with a proper stop and gates heading")

    p = run_doctor(root)["no-stop-lacks-cause"]
    assert p["holds"], p["reason"]


def test_doctor_proof_four_reports_older_records_mixing_versions_2_and_3(tmp_path: Path) -> None:
    """Acceptance row 3, issue #94: a record_version 2 record predates
    stop by construction (proof three's own window lesson) and is
    counted, never refused; a record_version 3 record alongside it is
    checked as usual. Proof four holds, reporting the older count."""
    root = tmp_path / "mixed_versions"
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    init_repo(root, "install")

    records = root / "records" / "1"
    records.mkdir(parents=True)
    (records / "build-01.json").write_text(json.dumps({"status": "failed", "record_version": 2}))
    (records / "verify-01.json").write_text(json.dumps({
        "status": "returned", "record_version": 3,
        "stop": {"class": "forge-unconcluded", "provenance": "tenant", "detail": None}}))
    commit_all(root, "one older record before stop, one current record with a stop")

    p = run_doctor(root)["no-stop-lacks-cause"]
    assert p["holds"], p["reason"]
    assert "1 record(s) before stop was recorded" in p["reason"]
    assert p["older_records"] == 1


def test_doctor_proof_four_holds_with_no_non_pass_records_to_check(tmp_path: Path) -> None:
    """A tenant found on a real v0.16.3 pin: no non-pass build or verify
    record at record_version 3 or later exists to check -- every item
    passed. The clause is "every failed or refused record carries its
    cause", which holds vacuously on an empty set, the way proof three
    holds when nothing is unrecorded. Proof four must hold, reporting
    0 checked and the older-record count, not fail the tenant for having
    nothing to check."""
    root = tmp_path / "all_pass"
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    init_repo(root, "install")

    records = root / "records" / "1"
    records.mkdir(parents=True)
    (records / "build-01.json").write_text(json.dumps({"status": "failed", "record_version": 2}))
    (records / "verify-01.json").write_text(json.dumps({"status": "pass", "record_version": 3}))
    commit_all(root, "one older record before stop, one current record that passed")

    p = run_doctor(root)["no-stop-lacks-cause"]
    assert p["holds"], p["reason"]
    assert "0 checked" in p["reason"]
    assert "1 record(s) before stop was recorded" in p["reason"]
    assert p["older_records"] == 1


def test_doctor_proof_four_fails_when_gates_ran_but_the_log_lacks_their_heading(tmp_path: Path) -> None:
    """Issue #94's own proposed fix: a failed build whose selected_tests
    is set -- gates definitely ran that iteration -- but whose log
    carries no gates heading at all. Tenant zero's #659 build-01 was
    exactly this before issue #89's own fix landed: the gate output that
    failed it lived only in a temporary directory the product deleted."""
    root = tmp_path / "missing_gates_heading"
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    init_repo(root, "install")

    records = root / "records" / "1"
    records.mkdir(parents=True)
    build = {
        "status": "failed", "record_version": 3,
        "selected_tests": {"select": "cat", "run": "cat", "count": 1, "exit": 1},
        "stop": {"class": "unclassed", "provenance": "product", "detail": "1 failed, 0 passed"},
    }
    (records / "build-01.json").write_text(json.dumps(build))
    (records / "build-01.log").write_text("1 failed, 0 passed")
    commit_all(root, "a failed build with gates run but no gates heading in the log")

    p = run_doctor(root)["no-stop-lacks-cause"]
    assert not p["holds"]
    assert "gates heading" in p["reason"]


def test_doctor_proof_four_fails_when_a_log_ends_in_an_adapter_exit_after_only_event_lines(
        tmp_path: Path) -> None:
    """Issue #94's own proposed fix, tenant zero's #659 verify-01
    specimen: a log whose last line is a bare adapter-exit marker with
    nothing before it but the adapter's own JSON event stream -- the
    tool's real error line never reached it (issue #86's own
    split_adapter_stdout bug, now guarded against at read time too)."""
    root = tmp_path / "owed_tool_error"
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    init_repo(root, "install")

    records = root / "records" / "1"
    records.mkdir(parents=True)
    verify = {
        "status": "failed", "record_version": 3,
        "stop": {"class": "unclassed", "provenance": "product", "detail": "[adapter exited 1]"},
    }
    (records / "verify-01.json").write_text(json.dumps(verify))
    (records / "verify-01.log").write_text(
        '{"type":"thread.started","thread_id":"x"}\n[adapter exited 1]')
    commit_all(root, "a failed verify whose tool error line was dropped")

    p = run_doctor(root)["no-stop-lacks-cause"]
    assert not p["holds"]
    assert "tool's own error line is owed" in p["reason"]


def test_proof_four_reads_an_error_event_as_the_tools_own_words(tmp_path: Path) -> None:
    """Issue #200: a review the vendor refused ("Selected model is at
    capacity") logs the tool's own error as an `error` event, then the
    exit marker. The cause is in the log, in the tool's words, so proof
    four does not owe a line. An `error` event with an empty or missing
    message says nothing and still owes it."""
    event_lines = ('{"type":"thread.started","thread_id":"x"}\n'
                   '{"type":"turn.started"}\n')
    exit_line = "[adapter exited 1]"
    verify = {
        "status": "failed", "record_version": 3,
        "stop": {"class": "unclassed", "provenance": "product", "detail": exit_line},
    }
    cases = {
        "with_message": (
            '{"type":"error","message":"Selected model is at capacity. '
            'Please try a different model."}\n', True),
        "empty_message": ('{"type":"error","message":""}\n', False),
        "missing_message": ('{"type":"error"}\n', False),
    }
    for name, (error_line, holds) in cases.items():
        root = tmp_path / name
        root.mkdir()
        (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
        init_repo(root, "install")

        records = root / "records" / "1"
        records.mkdir(parents=True)
        (records / "verify-01.json").write_text(json.dumps(verify))
        (records / "verify-01.log").write_text(event_lines + error_line + exit_line)
        commit_all(root, f"a failed verify whose log has an error event: {name}")

        p = run_doctor(root)["no-stop-lacks-cause"]
        assert p["holds"] is holds, (name, p["reason"])
        if not holds:
            assert "tool's own error line is owed" in p["reason"], name


def test_doctor_proof_four_fails_on_an_unclassed_stop_with_no_detail(tmp_path: Path) -> None:
    """An unclassed stop's whole contract is its detail (issue #89):
    with none, the record is exactly as unexplained as no stop at all."""
    root = tmp_path / "unclassed_no_detail"
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    init_repo(root, "install")

    records = root / "records" / "1"
    records.mkdir(parents=True)
    build = {"status": "failed", "record_version": 3,
            "stop": {"class": "unclassed", "provenance": "product", "detail": None}}
    (records / "build-01.json").write_text(json.dumps(build))
    commit_all(root, "an unclassed stop with no detail")

    p = run_doctor(root)["no-stop-lacks-cause"]
    assert not p["holds"]
    assert "unclassed with no detail" in p["reason"]


# Issue #254: the first record_version whose build / verify record carries
# what its stop was derived from (refusal_kind, report_class).
REDERIVABLE = {"build": 11, "verify": 10}


def _rederive_tenant(tmp_path: Path, name: str, records: dict[str, dict[str, Any]]) -> Path:
    """A tenant whose item 1 holds exactly these records, each with the
    fields a record at its stage's re-derivable version carries (an
    override wins) and no log, so only the re-derivation can fail."""
    root = tmp_path / name
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    init_repo(root, "install")
    item_dir = root / "records" / "1"
    item_dir.mkdir(parents=True)
    for stem, fields in records.items():
        stage = stem.split("-")[0]
        base: dict[str, Any] = {"record_version": REDERIVABLE[stage], "status": "failed",
                                "report_class": None}
        if stage == "build":
            base |= {"refusal": None, "refusal_kind": None, "halted": None, "criteria": [],
                     "selected_tests": None, "checks": None}
        else:
            base |= {"forge_checks": {"command": "c", "sha": "s",
                                      "conclusions": [{"name": "t", "conclusion": "success"}]},
                     "findings": None}
        (item_dir / f"{stem}.json").write_text(json.dumps({**base, **fields}))
    commit_all(root, "records for the re-derivation")
    return root


def _stop(cls: str, provenance: str, detail: str | None = None,
          bound: str | None = None) -> dict[str, Any]:
    return {"class": cls, "provenance": provenance, "detail": detail, "signature": None,
            "bound": bound}


def test_proof_four_fails_a_build_record_whose_class_disagrees_with_its_fields(tmp_path: Path) -> None:
    """Issue #254 row 1: the fields say foreign-commit, the stop says gate --
    the doctor passes it today because gate is an enumerated class."""
    root = _rederive_tenant(tmp_path, "build_disagrees", {"build-01": {
        "status": "refused", "refusal": "commit 3f2a not authored by the executor",
        "refusal_kind": "foreign-commit",
        "stop": _stop("gate", "executor", "criterion a.sh: exit 1")}})

    p = run_doctor(root)["no-stop-lacks-cause"]
    assert not p["holds"]
    for named in ("item 1", "build-01", "gate/executor", "foreign-commit/tenant"):
        assert named in p["reason"], (named, p["reason"])


def test_proof_four_fails_a_verify_record_whose_report_class_says_transient(tmp_path: Path) -> None:
    """Issue #254 row 2: the adapter's report line said transient, the
    stop says unclassed -- the report-line class was read only at write time."""
    root = _rederive_tenant(tmp_path, "verify_disagrees", {"verify-01": {
        "report_class": "transient",
        "stop": _stop("unclassed", "product", "boom")}})

    p = run_doctor(root)["no-stop-lacks-cause"]
    assert not p["holds"]
    for named in ("item 1", "verify-01", "unclassed/product", "transient/executor"):
        assert named in p["reason"], (named, p["reason"])


def test_proof_four_fails_a_build_record_whose_environment_refusal_disagrees_with_its_stop(
        tmp_path: Path) -> None:
    """One of three writers that reach a class without derive_stop (issue
    #254): the [checks].command preflight refuses with refusal_kind
    "environment" but a stored stop of unclassed/product -- rederive_stop
    reads refusal_kind back, the same field the writer set it from, and
    the doctor fails the disagreement rather than trusting the stop."""
    root = _rederive_tenant(tmp_path, "environment_disagrees", {"build-01": {
        "status": "refused", "refusal": "no jsonschema", "refusal_kind": "environment",
        "stop": _stop("unclassed", "product", "no jsonschema")}})

    p = run_doctor(root)["no-stop-lacks-cause"]
    assert not p["holds"]
    for named in ("item 1", "build-01", "unclassed/product", "environment/tenant"):
        assert named in p["reason"], (named, p["reason"])


def test_proof_four_fails_a_verify_record_whose_forge_returned_disagrees_with_its_stop(
        tmp_path: Path) -> None:
    """A second writer without derive_stop: a review the forge returned
    (a concluded red check, read from forge_checks) but a stored stop of
    forge-unconcluded/tenant -- the disagreement between what the forge
    concluded and what the stop claims is caught the same way."""
    root = _rederive_tenant(tmp_path, "forge_returned_disagrees", {"verify-01": {
        "status": "returned",
        "forge_checks": {"command": "c", "sha": "s",
                         "conclusions": [{"name": "ci", "conclusion": "failure"}]},
        "stop": _stop("forge-unconcluded", "tenant")}})

    p = run_doctor(root)["no-stop-lacks-cause"]
    assert not p["holds"]
    for named in ("item 1", "verify-01", "forge-unconcluded/tenant", "returned/tenant"):
        assert named in p["reason"], (named, p["reason"])


def test_proof_four_fails_a_verify_record_whose_blocking_finding_disagrees_with_its_stop(
        tmp_path: Path) -> None:
    """The third: a review with a blocking finding is class returned by
    R3's own rule, read back from findings -- a stored stop of
    unclassed/product disagrees and fails."""
    root = _rederive_tenant(tmp_path, "blocking_finding_disagrees", {"verify-01": {
        "status": "failed",
        "findings": [{"title": "b", "specimen": "s", "retires": "r",
                     "rejection_test": "t", "severity": "blocking"}],
        "stop": _stop("unclassed", "product", "boom")}})

    p = run_doctor(root)["no-stop-lacks-cause"]
    assert not p["holds"]
    for named in ("item 1", "verify-01", "unclassed/product", "returned/tenant"):
        assert named in p["reason"], (named, p["reason"])


def test_proof_four_holds_when_every_stop_is_what_its_fields_derive(tmp_path: Path) -> None:
    """The passing counterpart of rows 1-3: each stop agrees with its own record."""
    root = _rederive_tenant(tmp_path, "agrees", {
        "build-01": {"status": "refused", "refusal": "commit 3f2a not authored",
                     "refusal_kind": "foreign-commit", "stop": _stop("foreign-commit", "tenant")},
        "build-02": {"criteria": [{"name": "a.sh", "exit": 0}, {"name": "b.sh", "exit": 3}],
                     "stop": _stop("gate", "executor", "criterion b.sh: exit 3")},
        "build-03": {"stop": _stop("hung", "product", "no event for 90 s", bound="stall")},
        "build-04": {"stop": _stop("died", "product", "no live heartbeat")},
        "verify-01": {"report_class": "transient", "stop": _stop("transient", "executor")},
        "verify-02": {"status": "returned", "findings": None,
                      "stop": _stop("forge-unconcluded", "tenant")},
    })

    p = run_doctor(root)["no-stop-lacks-cause"]
    assert p["holds"], p["reason"]
    assert "6 non-pass record(s)" in p["reason"]


def test_proof_four_holds_a_recovery_record_by_its_own_rule(tmp_path: Path) -> None:
    """Issue #254 row 3: hung owes a bound and died owes none."""
    cases = {
        "hung_without_bound": ({"build-01": {"stop": _stop("hung", "product", "d")}},
                               "build-01", "hung"),
        "died_with_bound": ({"verify-01": {"stop": _stop("died", "product", "d", bound="stall")}},
                            "verify-01", "died"),
    }
    for name, (records, stem, cls) in cases.items():
        root = _rederive_tenant(tmp_path, name, records)
        p = run_doctor(root)["no-stop-lacks-cause"]
        assert not p["holds"], name
        assert stem in p["reason"] and cls in p["reason"], (name, p["reason"])


def test_proof_four_counts_and_does_not_check_records_before_the_new_version(tmp_path: Path) -> None:
    """Issue #254 row 5: a disagreeing record one version short of the
    re-derivable one is counted, in the proof's line, never checked."""
    root = _rederive_tenant(tmp_path, "before_window", {"build-01": {
        "record_version": REDERIVABLE["build"] - 1, "status": "refused",
        "refusal": "commit 3f2a not authored", "refusal_kind": None,
        "stop": _stop("gate", "executor", "criterion a.sh: exit 1")}})

    p = run_doctor(root)["no-stop-lacks-cause"]
    assert p["holds"], p["reason"]
    assert "1 non-pass record(s) before their stop could be re-derived" in p["reason"]
    assert p["before_rederivable"] == 1


def test_a_recovery_record_the_pass_writes_carries_null_for_the_new_keys(tmp_path: Path) -> None:
    """Issue #254: drive.py builds its record from the schema's required
    keys, so the new nullable keys arrive as null with no change to it --
    and proof four checks that record by the recovery rule."""
    root = make_tenant(tmp_path, "build_valid")
    git(root, "checkout", "-q", "-b", "attavo/1-x")
    add_forge(root, GREEN_FORGE)
    add_drive_stall_bound(root)
    approve_item(root)
    write_dead_marker(root, "build", 1)
    run = run_attavo(root, "drive", "1", env={"ATTAVO_HEARTBEAT_S": "0.2",
                                              "ATTAVO_DRIVE_POLL_INTERVAL_S": "0.5"})
    assert run.returncode == 0, run.stdout + run.stderr

    rec = latest_record(root, "build")
    assert rec["stop"]["class"] == "died"
    assert rec["refusal_kind"] is None and rec["report_class"] is None
    assert rec["record_version"] >= REDERIVABLE["build"]
    p = run_doctor(root)["no-stop-lacks-cause"]
    assert p["holds"], p["reason"]


FULL_DRIVE_TABLE = ('[drive]\nstall_after_s = 60\nattempts = 3\n\n'
                    '[drive.build]\nceiling_s = 600\n\n[drive.verify]\nceiling_s = 600\n')


def _dead_pid() -> int:
    """A pid guaranteed not to be running: a real child, spawned and
    reaped, on this host."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_doctor_proof_five_fails_on_a_dead_invocation_with_no_record(tmp_path: Path) -> None:
    """Issue #151: a build-01.started marker naming a dead pid on this
    host, with no build-01.json to retire it, is exactly the silence two
    build attempts of item #148 left behind -- proof five names it."""
    root = tmp_path / "dead_invocation"
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n\n' + FULL_DRIVE_TABLE)
    init_repo(root, "install")

    records = root / "records" / "1"
    records.mkdir(parents=True)
    (records / "build-01.started").write_text(json.dumps({
        "at": "2026-09-17T13:16:08Z", "host": socket.gethostname(), "pid": _dead_pid(),
        "sha": "0" * 40, "product_version": "fixture"}))
    commit_all(root, "invocation marker for item 1")

    p = run_doctor(root)["no-invocation-lacks-record"]
    assert not p["holds"]
    assert "build-01" in p["reason"] and "never wrote its record" in p["reason"]


def test_doctor_proof_five_holds_once_the_record_arrives(tmp_path: Path) -> None:
    """The same marker, retired by its own build-01.json: proof five
    holds -- the record's arrival is what retires a marker, not its
    removal."""
    root = tmp_path / "recorded_invocation"
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n\n' + FULL_DRIVE_TABLE)
    init_repo(root, "install")

    records = root / "records" / "1"
    records.mkdir(parents=True)
    (records / "build-01.started").write_text(json.dumps({
        "at": "2026-09-17T13:16:08Z", "host": socket.gethostname(), "pid": _dead_pid(),
        "sha": "0" * 40, "product_version": "fixture"}))
    (records / "build-01.json").write_text(json.dumps({"status": "pass"}))
    commit_all(root, "invocation marker and its record for item 1")

    p = run_doctor(root)["no-invocation-lacks-record"]
    assert p["holds"], p["reason"]


def _heartbeat(root: Path, stage: str, nn: int, age_s: float, content: str = "") -> Path:
    """The worker's heartbeat file under the tenant's git dir, its mtime
    age_s seconds ago -- the fact a holder writes, the only one proof
    five reads."""
    beat_dir = Path(subprocess.run(["git", "-C", str(root), "rev-parse", "--absolute-git-dir"],
                                   capture_output=True, text=True, check=True).stdout.strip()) / "attavo"
    beat_dir.mkdir(parents=True, exist_ok=True)
    beat = beat_dir / f"{stage}-{nn:02d}.heartbeat"
    beat.write_text(content)
    stamp = time.time() - age_s
    os.utime(beat, (stamp, stamp))
    return beat


def _pidless_marker_tenant(root: Path) -> Path:
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n\n' + FULL_DRIVE_TABLE)
    init_repo(root, "install")
    records = root / "records" / "1"
    records.mkdir(parents=True)
    (records / "build-01.started").write_text(json.dumps({
        "at": "2026-09-17T13:16:08Z", "host": socket.gethostname(),
        "sha": "0" * 40, "product_version": "fixture"}))
    commit_all(root, "invocation marker for item 1")
    return root


def test_doctor_proof_five_reads_a_fresh_heartbeat_as_running_though_the_marker_has_no_pid(
        tmp_path: Path) -> None:
    """#229 build-04, build-05 and #237 build-01: the marker names no live
    process (here, none at all), the worker's heartbeat is fresh -- the
    build is running and proof five holds and says so."""
    root = _pidless_marker_tenant(tmp_path / "fresh_heartbeat")
    _heartbeat(root, "build", 1, age_s=1, content=f"{os.getpid()}\n")

    p = run_doctor(root)["no-invocation-lacks-record"]
    assert p["holds"], p["reason"]
    assert len(p["running_invocations"]) == 1
    assert "build-01" in p["running_invocations"][0]


def test_doctor_proof_five_reads_a_stale_heartbeat_as_died_though_a_pid_lives(tmp_path: Path) -> None:
    """A heartbeat older than three beats is a holder that stopped
    touching it: died, whatever process the marker or the file names."""
    root = _pidless_marker_tenant(tmp_path / "stale_heartbeat")
    _heartbeat(root, "build", 1, age_s=600, content=f"{os.getpid()}\n")

    p = run_doctor(root)["no-invocation-lacks-record"]
    assert not p["holds"]
    assert "build-01" in p["reason"] and "never wrote its record" in p["reason"]
    assert p["running_invocations"] == []


def test_doctor_proof_five_reads_no_heartbeat_on_this_host_as_died(tmp_path: Path) -> None:
    root = _pidless_marker_tenant(tmp_path / "no_heartbeat")

    p = run_doctor(root)["no-invocation-lacks-record"]
    assert not p["holds"]
    assert "build-01" in p["reason"] and "never wrote its record" in p["reason"]
    assert p["running_invocations"] == []


def test_doctor_proof_five_fails_without_stall_after_s_in_the_manifest(tmp_path: Path) -> None:
    """A tenant with no [drive].stall_after_s carries no bound past which
    a live invocation is hung -- proof five cannot tell a hung drive from
    a running one, and refuses to guess a default."""
    root = tmp_path / "no_stall_bound"
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    init_repo(root, "install")

    records = root / "records" / "1"
    records.mkdir(parents=True)
    (records / "build-01.json").write_text(json.dumps({"status": "pass"}))
    commit_all(root, "a record with no [drive].stall_after_s declared")

    p = run_doctor(root)["no-invocation-lacks-record"]
    assert not p["holds"]
    assert "stall_after_s" in p["reason"]


def test_doctor_refuses_a_drive_table_without_ceilings(tmp_path: Path) -> None:
    """R3: a [drive] table that declares a stall bound and no
    [drive.build].ceiling_s or [drive.verify].ceiling_s carries no bound
    past which a live stage is hung by wall clock -- proof five refuses
    to guess one, as it refuses to guess the stall bound."""
    root = tmp_path / "no_ceilings"
    root.mkdir()
    (root / "attavo.toml").write_text(
        '[paths]\nrecords = "records"\n\n[drive]\nstall_after_s = 60\nattempts = 3\n')
    init_repo(root, "install")

    records = root / "records" / "1"
    records.mkdir(parents=True)
    (records / "build-01.json").write_text(json.dumps({"status": "pass"}))
    commit_all(root, "a drive table with no ceilings")

    p = run_doctor(root)["no-invocation-lacks-record"]
    assert not p["holds"]
    assert "ceiling" in p["reason"]


def test_doctor_refuses_a_drive_table_without_attempts(tmp_path: Path) -> None:
    """R3: a [drive] table with both ceilings and a stall bound but no
    [drive].attempts carries no bound on how many attempts a retryable
    stop may spend -- proof five refuses to guess a default here too."""
    root = tmp_path / "no_attempts"
    root.mkdir()
    (root / "attavo.toml").write_text(
        '[paths]\nrecords = "records"\n\n[drive]\nstall_after_s = 60\n\n'
        '[drive.build]\nceiling_s = 600\n\n[drive.verify]\nceiling_s = 600\n')
    init_repo(root, "install")

    records = root / "records" / "1"
    records.mkdir(parents=True)
    (records / "build-01.json").write_text(json.dumps({"status": "pass"}))
    commit_all(root, "a drive table with no attempts budget")

    p = run_doctor(root)["no-invocation-lacks-record"]
    assert not p["holds"]
    assert "attempts" in p["reason"]


# --- proof seven: no attested item is empty (item 227). Rejection tests,
# written red first against v0.29.0, whose doctor had six proofs and held
# over an attested item whose build committed nothing. ---


def _head(root: Path) -> str:
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()


def _four_stage_item(root: Path, approval_sha: str, *, change: bool) -> tuple[str, str]:
    """One item, four records, merged --no-ff; returns (merge_sha, build_sha).
    change=True lands a real content commit before the records, so the
    tree genuinely differs from approval_sha outside records/; change=False
    leaves the tree exactly as approval_sha left it -- item 227's own
    build, and item 229's own fix for a retried attempt that adds no
    commit of its own but already carries one."""
    subprocess.run(["git", "-C", str(root), "checkout", "-q", "-b", "attavo/1-x"], check=True)
    if change:
        (root / "changed.txt").write_text("the item's own change\n")
        commit_all(root, "the item's own change")
    build_sha = _head(root)
    records = root / "records" / "1"
    records.mkdir(parents=True)
    (records / "spec-01.json").write_text(json.dumps({"status": "pass"}))
    (records / "approval-01.json").write_text(json.dumps({"approved_by": "alice"}))
    (records / "build-01.json").write_text(json.dumps(
        {"status": "pass", "sha": build_sha, "start_sha": approval_sha,
         "approval_sha": approval_sha}))
    (records / "verify-01.json").write_text(json.dumps({"status": "pass"}))
    commit_all(root, "spec, approval, build, and verify records")
    subprocess.run(["git", "-C", str(root), "checkout", "-q", "main"], check=True)
    subprocess.run(["git", "-C", str(root), "merge", "-q", "--no-ff", "-m",
                    "Merge branch 'attavo/1-x'", "attavo/1-x"], check=True)
    return _head(root), build_sha


def test_doctor_proof_seven_fails_on_an_attested_item_whose_build_committed_nothing(tmp_path: Path) -> None:
    root = tmp_path / "empty_item"
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    init_repo(root, "install")
    approval_sha = _head(root)
    merge, _ = _four_stage_item(root, approval_sha, change=False)
    report = run_doctor(root)
    assert "no-attested-item-is-empty" in report, sorted(report)
    p = report["no-attested-item-is-empty"]
    assert not p["holds"], p
    assert "item 1" in p["reason"] and "nothing was built" in p["reason"] and merge[:12] in p["reason"], p["reason"]


def test_doctor_proof_seven_holds_on_a_build_that_names_a_change_and_names_the_specimen_before_its_window(tmp_path: Path) -> None:
    built = tmp_path / "built_item"
    built.mkdir()
    (built / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    init_repo(built, "install")
    _four_stage_item(built, _head(built), change=True)
    p = run_doctor(built)["no-attested-item-is-empty"]
    assert p["holds"], p["reason"]
    assert p["built"] == ["1"], p

    windowed = tmp_path / "windowed_item"
    windowed.mkdir()
    (windowed / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    init_repo(windowed, "install")
    approval_sha = _head(windowed)
    merge, _ = _four_stage_item(windowed, approval_sha, change=False)
    toml = windowed / "attavo.toml"
    toml.write_text(toml.read_text() + f'\n[doctor]\nbuilt_since = "{merge[:12]}"\n')
    commit_all(windowed, "declare the window after the specimen")
    p = run_doctor(windowed)["no-attested-item-is-empty"]
    assert p["holds"], p["reason"]
    assert p["before_window"] == ["1"] and "before the window" in p["reason"], p


def test_doctor_proof_seven_treats_a_retried_attempt_that_adopts_a_prior_commit_as_not_empty(
        tmp_path: Path) -> None:
    """Item 229: a build record whose sha equals its own start_sha is not
    empty when that tree already differs from the approval -- a retried
    attempt whose head already carries a prior attempt's valid commit
    owes no commit of its own (item 227's own counsel question)."""
    root = tmp_path / "adopted_item"
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    init_repo(root, "install")
    approval_sha = _head(root)
    subprocess.run(["git", "-C", str(root), "checkout", "-q", "-b", "attavo/1-x"], check=True)
    (root / "changed.txt").write_text("a prior attempt's own valid commit\n")
    commit_all(root, "a prior attempt's own valid commit")
    retry_start_sha = _head(root)
    records = root / "records" / "1"
    records.mkdir(parents=True)
    (records / "spec-01.json").write_text(json.dumps({"status": "pass"}))
    (records / "approval-01.json").write_text(json.dumps({"approved_by": "alice"}))
    (records / "build-02.json").write_text(json.dumps(
        {"status": "pass", "sha": retry_start_sha, "start_sha": retry_start_sha,
         "approval_sha": approval_sha}))
    (records / "verify-01.json").write_text(json.dumps({"status": "pass"}))
    commit_all(root, "spec, approval, build, and verify records")
    subprocess.run(["git", "-C", str(root), "checkout", "-q", "main"], check=True)
    subprocess.run(["git", "-C", str(root), "merge", "-q", "--no-ff", "-m",
                    "Merge branch 'attavo/1-x'", "attavo/1-x"], check=True)
    p = run_doctor(root)["no-attested-item-is-empty"]
    assert p["holds"], p["reason"]
    assert p["built"] == ["1"], p


def test_doctor_proof_seven_names_a_build_with_no_readable_approval_as_not_read(tmp_path: Path) -> None:
    """Item 229: a build record whose approval_sha does not resolve in this
    repository is counted as not readable, not failed -- an unprovable
    build is not asserted empty."""
    root = tmp_path / "unreadable_item"
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    init_repo(root, "install")
    subprocess.run(["git", "-C", str(root), "checkout", "-q", "-b", "attavo/1-x"], check=True)
    build_sha = _head(root)
    records = root / "records" / "1"
    records.mkdir(parents=True)
    (records / "spec-01.json").write_text(json.dumps({"status": "pass"}))
    (records / "approval-01.json").write_text(json.dumps({"approved_by": "alice"}))
    (records / "build-01.json").write_text(json.dumps(
        {"status": "pass", "sha": build_sha, "start_sha": build_sha, "approval_sha": "f" * 40}))
    (records / "verify-01.json").write_text(json.dumps({"status": "pass"}))
    commit_all(root, "spec, approval, build, and verify records")
    subprocess.run(["git", "-C", str(root), "checkout", "-q", "main"], check=True)
    subprocess.run(["git", "-C", str(root), "merge", "-q", "--no-ff", "-m",
                    "Merge branch 'attavo/1-x'", "attavo/1-x"], check=True)
    p = run_doctor(root)["no-attested-item-is-empty"]
    assert p["holds"], p["reason"]
    assert p["not_read"] == ["1"], p


@pytest.mark.own_records
def test_no_attested_item_is_empty() -> None:
    """Proof 7, item 227. On this repository the specimen, #227, is named
    before the window ([doctor].built_since) and every later attested
    item's build names a change."""
    p = run_doctor(ROOT)["no-attested-item-is-empty"]
    assert p["holds"], p["reason"]


DRIVER_ENV = {"GIT_AUTHOR_NAME": "attavo driver", "GIT_AUTHOR_EMAIL": "driver@attavo.invalid",
              "GIT_COMMITTER_NAME": "attavo driver", "GIT_COMMITTER_EMAIL": "driver@attavo.invalid"}


def _driver_git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                   env={**os.environ, **DRIVER_ENV})


def _driver_history(tmp_path: Path, name: str) -> Path:
    """An item branch merged to main, whose records the doctor's proof
    three already accepts, and to which each test adds one driver commit."""
    root = tmp_path / name
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    init_repo(root, "install")
    subprocess.run(["git", "-C", str(root), "checkout", "-b", "attavo/1-x"], check=True)
    records = root / "records" / "1"
    records.mkdir(parents=True)
    (records / "spec-01.json").write_text(json.dumps({"status": "pass"}))
    commit_all(root, "spec record for item 1")
    return root


def _merge_to_main(root: Path) -> None:
    subprocess.run(["git", "-C", str(root), "checkout", "main"], check=True)
    subprocess.run(["git", "-C", str(root), "merge", "--no-ff", "-m", "Merge branch 'attavo/1-x'",
                    "attavo/1-x"], check=True, capture_output=True)


def test_proof_three_holds_on_a_driver_commit_that_carries_only_its_items_records(tmp_path: Path) -> None:
    root = _driver_history(tmp_path, "driver_ok")
    (root / "records" / "1" / "build-01.json").write_text(json.dumps({"status": "pass"}))
    _driver_git(root, "add", "records/1/build-01.json")
    _driver_git(root, "commit", "-q", "-m", "attavo: build record for item 1 (build-01)")
    _merge_to_main(root)
    p = run_doctor(root)["no-commit-lacks-record"]
    assert p["holds"], p["reason"]


def test_proof_three_fails_on_a_driver_authored_merge_commit(tmp_path: Path) -> None:
    """A merge authored as the driver -- the merge retry of the record push,
    authored as whoever's git config the machine had -- is not a records
    transport."""
    root = _driver_history(tmp_path, "driver_merge")
    subprocess.run(["git", "-C", str(root), "checkout", "-q", "-b", "elsewhere", "main"], check=True)
    (root / "elsewhere.txt").write_text("from elsewhere\n")
    commit_all(root, "a commit from elsewhere")
    subprocess.run(["git", "-C", str(root), "checkout", "-q", "attavo/1-x"], check=True)
    _driver_git(root, "merge", "--no-ff", "-m", "Merge branch 'elsewhere' into attavo/1-x", "elsewhere")
    _merge_to_main(root)
    p = run_doctor(root)["no-commit-lacks-record"]
    assert not p["holds"] and "not a records transport" in p["reason"] and "is a merge" in p["reason"], p["reason"]


def test_proof_three_fails_on_a_driver_commit_touching_a_file_outside_its_records(tmp_path: Path) -> None:
    root = _driver_history(tmp_path, "driver_outside")
    (root / "records" / "1" / "build-01.json").write_text(json.dumps({"status": "pass"}))
    (root / "stray.txt").write_text("not a record\n")
    _driver_git(root, "add", "-A")
    _driver_git(root, "commit", "-q", "-m", "attavo: build record for item 1 (build-01)")
    _merge_to_main(root)
    p = run_doctor(root)["no-commit-lacks-record"]
    assert not p["holds"], p["reason"]
    assert "not a records transport" in p["reason"] and "stray.txt" in p["reason"], p["reason"]
