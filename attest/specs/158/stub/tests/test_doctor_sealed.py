"""attavo doctor, proof six: every attested item has its statement in
the store (the self-tenant's #106, #148, #153 were merged with no
merge_group run and no statement, and the doctor said done)."""
import json
import subprocess
from pathlib import Path

ATTAVO = Path(__file__).resolve().parents[1] / "bin" / "attavo"

FAKE_ATTEST = (
    '#!/bin/sh\nread sha\ngrep -qx "$sha" sealed.txt && printf %s '
    '"{\\"_type\\": \\"https://in-toto.io/Statement/v1\\"}"\n'
)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def _tenant_with_two_merged_items(tmp_path: Path) -> tuple[Path, str, str]:
    root = tmp_path / "sealed"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", "."], cwd=root, check=True)
    _git(root, "config", "user.email", "doctor@attavo.invalid")
    _git(root, "config", "user.name", "doctor fixture")
    (root / "attavo.toml").write_text(
        '[paths]\nrecords = "records"\n\n[forge]\nattestation_command = "sh ./fake-attest"\n')
    (root / "fake-attest").write_text(FAKE_ATTEST)
    (root / "sealed.txt").write_text("")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "install")
    merges = []
    for item in ("1", "2"):
        _git(root, "checkout", "-q", "-b", f"attavo/{item}-x")
        (root / "records" / item).mkdir(parents=True)
        for stage in ("spec", "approval", "build", "verify"):
            (root / "records" / item / f"{stage}-01.json").write_text('{"status": "pass"}')
            _git(root, "add", "records")
            _git(root, "commit", "-q", "-m", f"{stage} record for item {item}")
        _git(root, "checkout", "-q", "main")
        _git(root, "merge", "-q", "--no-ff", "-m", f"Merge branch 'attavo/{item}-x'", f"attavo/{item}-x")
        merges.append(_git(root, "rev-parse", "HEAD"))
    _set_default_branch_ref(root)
    return root, merges[0], merges[1]


def _set_default_branch_ref(root: Path) -> None:
    """The walk behind proof six starts at the default branch as
    refs/remotes/origin/HEAD names it and refuses by name without it
    (issue #169); a fixture with no remote sets the ref by hand."""
    _git(root, "update-ref", "refs/remotes/origin/main", _git(root, "rev-parse", "main"))
    _git(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")


def _proof(root: Path) -> dict:
    run = subprocess.run(["python3", str(ATTAVO), "doctor", "--json", str(root)],
                         capture_output=True, text=True, check=False)
    proofs = json.loads(run.stdout)["proofs"]
    return [p for p in proofs if p["proof"] == "every-attested-item-is-sealed"][0]


def test_doctor_proof_six_fails_on_an_unsealed_merge(tmp_path):
    root, m1, m2 = _tenant_with_two_merged_items(tmp_path)
    (root / "sealed.txt").write_text(m1 + "\n")
    p = _proof(root)
    assert not p["holds"]
    assert "item 2" in p["reason"] and m2[:12] in p["reason"]
    assert p["sealed"] == ["1"] and p["unsealed"] == ["2"]


def test_doctor_proof_six_holds_when_every_merge_is_sealed(tmp_path):
    root, m1, m2 = _tenant_with_two_merged_items(tmp_path)
    (root / "sealed.txt").write_text(m1 + "\n" + m2 + "\n")
    p = _proof(root)
    assert p["holds"] and p["sealed"] == ["1", "2"]


def test_doctor_proof_six_names_a_merge_before_the_window_without_failing(tmp_path):
    root, m1, m2 = _tenant_with_two_merged_items(tmp_path)
    (root / "sealed.txt").write_text(m2 + "\n")
    (root / "attavo.toml").write_text(
        '[paths]\nrecords = "records"\n\n[forge]\nattestation_command = "sh ./fake-attest"\n'
        f'sealed_since = "{m1}"\n')
    p = _proof(root)
    assert p["holds"] and p["before_window"] == ["1"] and p["sealed"] == ["2"]


def test_doctor_proof_six_names_an_unmerged_item(tmp_path):
    """An item whose records sit on a branch, or on a pull_request
    checkout's synthetic merge, has no merge on the default branch's
    first-parent line: it is named in not_merged, never failed."""
    root, m1, m2 = _tenant_with_two_merged_items(tmp_path)
    (root / "sealed.txt").write_text(m1 + "\n" + m2 + "\n")
    _git(root, "checkout", "-q", "-b", "attavo/3-x")
    (root / "records" / "3").mkdir(parents=True)
    for stage in ("spec", "approval", "build", "verify"):
        (root / "records" / "3" / f"{stage}-01.json").write_text('{"status": "pass"}')
        _git(root, "add", "records")
        _git(root, "commit", "-q", "-m", f"{stage} record for item 3")
    _git(root, "checkout", "-q", "--detach")
    _git(root, "merge", "-q", "--no-ff", "-m", "synthetic test merge", "main")
    p = _proof(root)
    assert p["holds"] and p["sealed"] == ["1", "2"] and p["not_merged"] == ["3"]
    assert p["unsealed"] == []


def test_doctor_proof_six_refuses_without_a_default_branch_ref(tmp_path):
    root, m1, m2 = _tenant_with_two_merged_items(tmp_path)
    (root / "sealed.txt").write_text(m1 + "\n" + m2 + "\n")
    _git(root, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
    p = _proof(root)
    assert not p["holds"] and "refs/remotes/origin/HEAD" in p["reason"]


def test_doctor_proof_six_says_not_read_when_the_store_cannot_be_read(tmp_path):
    """Exit 2 from the attestation command (gh unauthenticated on a
    runner, the command missing) is "the store could not be read", never
    "unsealed": the item lands in not_read and the reason names the
    command and its exit."""
    root, m1, m2 = _tenant_with_two_merged_items(tmp_path)
    (root / "fake-attest").write_text('#!/bin/sh\nread sha\necho "gh: not logged in" >&2\nexit 2\n')
    p = _proof(root)
    assert not p["holds"]
    assert "not read" in p["reason"] and "exited 2" in p["reason"]
    assert p["not_read"] == ["1", "2"] and p["unsealed"] == [] and p["sealed"] == []


def test_doctor_proof_six_fails_without_an_attestation_command(tmp_path):
    root = tmp_path / "bare"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", "."], cwd=root, check=True)
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    (root / "records").mkdir()
    _git(root, "config", "user.email", "doctor@attavo.invalid")
    _git(root, "config", "user.name", "doctor fixture")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "install")
    _set_default_branch_ref(root)
    p = _proof(root)
    assert not p["holds"] and "attestation_command" in p["reason"]
