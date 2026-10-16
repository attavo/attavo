"""The doctor's fifth proof: every attested item has its statement in the
store (issue #158). This repository's own #106, #148, #153 were merged
direct, with no merge_group run and so no statement, and the doctor said
done anyway -- proof two held on the four stage records with the chain
held, never asking whether the merge was sealed.

Run: python3 -m pytest tests -q
"""
import json
import subprocess
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"

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
    _git(root, "update-ref", "refs/remotes/origin/main", _git(root, "rev-parse", "main"))
    _git(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    return root, merges[0], merges[1]


def _tenant_with_one_merged_and_one_unmerged_item(tmp_path: Path) -> Path:
    root = tmp_path / "unmerged"
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
    _git(root, "checkout", "-q", "-b", "attavo/1-x")
    (root / "records" / "1").mkdir(parents=True)
    for stage in ("spec", "approval", "build", "verify"):
        (root / "records" / "1" / f"{stage}-01.json").write_text('{"status": "pass"}')
        _git(root, "add", "records")
        _git(root, "commit", "-q", "-m", f"{stage} record for item 1")
    _git(root, "checkout", "-q", "main")
    _git(root, "merge", "-q", "--no-ff", "-m", "Merge branch 'attavo/1-x'", "attavo/1-x")
    m1 = _git(root, "rev-parse", "HEAD")
    (root / "sealed.txt").write_text(m1 + "\n")
    _git(root, "add", "sealed.txt")
    _git(root, "commit", "-q", "-m", "seal item 1")
    _git(root, "checkout", "-q", "-b", "attavo/2-x")
    (root / "records" / "2").mkdir(parents=True)
    for stage in ("spec", "approval", "build", "verify"):
        (root / "records" / "2" / f"{stage}-01.json").write_text('{"status": "pass"}')
        _git(root, "add", "records")
        _git(root, "commit", "-q", "-m", f"{stage} record for item 2")
    _git(root, "checkout", "-q", "--detach")
    _git(root, "merge", "-q", "--no-ff", "-m", "synthetic test merge of attavo/2-x into main", "main")
    return root


def _proof(root: Path) -> dict[str, Any]:
    run = subprocess.run(["python3", str(ATTAVO), "doctor", "--json", str(root)],
                         capture_output=True, text=True, check=False)
    proofs = json.loads(run.stdout)["proofs"]
    return cast(dict[str, Any], [p for p in proofs if p["proof"] == "every-attested-item-is-sealed"][0])


def test_doctor_proof_six_fails_on_an_unsealed_merge(tmp_path: Path) -> None:
    root, m1, m2 = _tenant_with_two_merged_items(tmp_path)
    (root / "sealed.txt").write_text(m1 + "\n")
    p = _proof(root)
    assert not p["holds"]
    assert "item 2" in p["reason"] and m2[:12] in p["reason"]
    assert p["sealed"] == ["1"] and p["unsealed"] == ["2"]


def test_doctor_proof_six_holds_when_every_merge_is_sealed(tmp_path: Path) -> None:
    root, m1, m2 = _tenant_with_two_merged_items(tmp_path)
    (root / "sealed.txt").write_text(m1 + "\n" + m2 + "\n")
    p = _proof(root)
    assert p["holds"] and p["sealed"] == ["1", "2"]


def test_doctor_proof_six_names_a_merge_before_the_window_without_failing(tmp_path: Path) -> None:
    root, m1, m2 = _tenant_with_two_merged_items(tmp_path)
    (root / "sealed.txt").write_text(m2 + "\n")
    (root / "attavo.toml").write_text(
        '[paths]\nrecords = "records"\n\n[forge]\nattestation_command = "sh ./fake-attest"\n'
        f'sealed_since = "{m1}"\n')
    p = _proof(root)
    assert p["holds"] and p["before_window"] == ["1"] and p["sealed"] == ["2"]


def test_doctor_proof_six_fails_without_an_attestation_command(tmp_path: Path) -> None:
    root = tmp_path / "bare"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", "."], cwd=root, check=True)
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    (root / "records").mkdir()
    p = _proof(root)
    assert not p["holds"] and "attestation_command" in p["reason"]


def test_doctor_proof_six_says_not_read_when_the_store_cannot_be_read(tmp_path: Path) -> None:
    root = tmp_path / "unreadable"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", "."], cwd=root, check=True)
    _git(root, "config", "user.email", "doctor@attavo.invalid")
    _git(root, "config", "user.name", "doctor fixture")
    (root / "attavo.toml").write_text(
        '[paths]\nrecords = "records"\n\n[forge]\nattestation_command = "sh ./unreadable-store"\n')
    (root / "unreadable-store").write_text(
        '#!/bin/sh\nread sha\necho "gh: not logged in" >&2\nexit 2\n')
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "install")
    _git(root, "checkout", "-q", "-b", "attavo/1-x")
    (root / "records" / "1").mkdir(parents=True)
    for stage in ("spec", "approval", "build", "verify"):
        (root / "records" / "1" / f"{stage}-01.json").write_text('{"status": "pass"}')
        _git(root, "add", "records")
        _git(root, "commit", "-q", "-m", f"{stage} record for item 1")
    _git(root, "checkout", "-q", "main")
    _git(root, "merge", "-q", "--no-ff", "-m", "Merge branch 'attavo/1-x'", "attavo/1-x")
    _git(root, "update-ref", "refs/remotes/origin/main", _git(root, "rev-parse", "main"))
    _git(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    p = _proof(root)
    assert not p["holds"]
    assert "not read" in p["reason"] and "unreadable-store" in p["reason"] and "exited 2" in p["reason"]
    assert p["not_read"] == ["1"] and p["unsealed"] == [] and p["sealed"] == []


def test_doctor_proof_six_names_an_unmerged_item(tmp_path: Path) -> None:
    root = _tenant_with_one_merged_and_one_unmerged_item(tmp_path)
    noref = _proof(root)
    assert not noref["holds"] and "refs/remotes/origin/HEAD" in noref["reason"]
    _git(root, "update-ref", "refs/remotes/origin/main", "main")
    _git(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    ref = _proof(root)
    assert ref["holds"]
    assert ref["sealed"] == ["1"] and ref["not_merged"] == ["2"] and ref["unsealed"] == []


def test_proof_six_compares_interventions_never_acts(tmp_path: Path) -> None:
    """Specimen: main's doctor on 2026-10-16, which failed item 350's
    sealed `acts: []` against records carrying an approve and a merge.
    Proof six holds a v6 statement with `acts: []` and a v7 statement
    without `acts` alike, and fails either whose interventions differ from
    the records."""
    from helpers import commit_all, make_tenant
    from test_attest import configure_forge, run_attavo
    from test_attest_interventions import traverse_with_a_transport
    from test_attest_time import proof_six, seal

    root = make_tenant(tmp_path, "build_valid")
    configure_forge(root)
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text().replace(
        "[forge]\n", '[forge]\nattestation_command = "sh ./fake-store"\n'))
    (root / "fake-store").write_text(
        '#!/bin/sh\nread sha\ntest -f "statements/$sha.json" && cat "statements/$sha.json"\n')
    commit_all(root, "a store to read")
    transport = traverse_with_a_transport(root)
    merge = _git(root, "rev-parse", "HEAD")
    run = run_attavo(root, "attest", "1")
    assert run.returncode == 0, run.stdout + run.stderr
    statement = json.loads(run.stdout)
    assert transport in [i["sha"] for i in statement["interventions"]]
    statement.pop("acts", None)

    seal(root, merge, statement, "https://attavo.dev/attestation/v7")
    held = proof_six(root)
    assert held["holds"] is True, held

    v6: dict[str, Any] = dict(statement, acts=[])
    seal(root, merge, v6, "https://attavo.dev/attestation/v6")
    held = proof_six(root)
    assert held["holds"] is True, held

    for version in ("v6", "v7"):
        forged: dict[str, Any] = dict(statement, interventions=[])
        if version == "v6":
            forged["acts"] = []
        seal(root, merge, forged, f"https://attavo.dev/attestation/{version}")
        failed = proof_six(root)
        assert failed["holds"] is False, (version, failed)
        assert "item 1" in failed["reason"] and "interventions" in failed["reason"], failed
        assert "acts" not in failed["reason"].replace("interventions", ""), failed
