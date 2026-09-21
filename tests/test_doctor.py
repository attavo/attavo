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
from pathlib import Path
from typing import Any

import pytest
from helpers import FIXTURES, commit_all, init_repo, make_tenant

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"


def run_doctor(root: Path) -> dict[str, Any]:
    out = subprocess.run([sys.executable, str(ATTAVO), "doctor", str(root), "--json"],
                         capture_output=True, text=True, check=False)
    report = json.loads(out.stdout)
    assert out.returncode == (0 if report["done"] else 1)
    return {p["proof"]: p for p in report["proofs"]}


def test_every_gate_rejects() -> None:
    """Proof 1. Every gate in the manifest rejects its rejection fixture.

    Holds when: a manifest exists, [gates] names at least one gate, each gate
    has a fixture under paths.rejections, and each gate exits non-zero on it.
    """
    p = run_doctor(ROOT)["every-gate-rejects"]
    assert p["holds"], p["reason"]


def test_one_item_traversed_every_stage() -> None:
    """Proof 2. One work item carries a record for spec, approval, build,
    verify, and attestation, every one written by a script."""
    p = run_doctor(ROOT)["one-item-every-stage"]
    assert p["holds"], p["reason"]


def test_no_commit_lacks_a_record() -> None:
    """Proof 3. Every commit on the default branch after the manifest was
    introduced is named by a record's sha or commits list."""
    p = run_doctor(ROOT)["no-commit-lacks-record"]
    assert p["holds"], p["reason"]


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
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n\n'
                                      '[drive]\nstall_after_s = 60\n')
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
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n\n'
                                      '[drive]\nstall_after_s = 60\n')
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


def test_doctor_proof_five_reports_a_live_invocation_as_running_not_failed(tmp_path: Path) -> None:
    """A marker naming this test's own pid, on this host, with no
    record: alive, so proof five holds and names it running rather than
    failing the tenant for a drive that has not had the chance to finish
    yet."""
    root = tmp_path / "live_invocation"
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n\n'
                                      '[drive]\nstall_after_s = 60\n')
    init_repo(root, "install")

    records = root / "records" / "1"
    records.mkdir(parents=True)
    (records / "build-01.started").write_text(json.dumps({
        "at": "2026-09-17T13:16:08Z", "host": socket.gethostname(), "pid": os.getpid(),
        "sha": "0" * 40, "product_version": "fixture"}))
    commit_all(root, "invocation marker for a still-running invocation")

    p = run_doctor(root)["no-invocation-lacks-record"]
    assert p["holds"], p["reason"]
    assert len(p["running_invocations"]) == 1
    assert "build-01" in p["running_invocations"][0]


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
