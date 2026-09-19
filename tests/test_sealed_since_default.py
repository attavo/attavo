"""Proof six's window start defaults to proof three's (issue #192):
[forge].sealed_since is optional, and absent, the window is [doctor].from
if the tenant set it, otherwise the commit that introduced the manifest.
A window that holds a merge is named on the holding line, so a tenant that
declares a debt window reads it aloud on every run.

Run: python3 -m pytest tests/test_sealed_since_default.py -q
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
MANIFEST = '[paths]\nrecords = "records"\n\n[forge]\nattestation_command = "sh ./fake-attest"\n'


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def _init(root: Path) -> None:
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", "."], cwd=root, check=True)
    _git(root, "config", "user.email", "doctor@attavo.invalid")
    _git(root, "config", "user.name", "doctor fixture")
    (root / "fake-attest").write_text(FAKE_ATTEST)
    (root / "sealed.txt").write_text("")


def _merge_item(root: Path, item: str) -> str:
    """Merge one item's four stage records into main; the merge's sha."""
    _git(root, "checkout", "-q", "-b", f"attavo/{item}-x")
    (root / "records" / item).mkdir(parents=True)
    for stage in ("spec", "approval", "build", "verify"):
        (root / "records" / item / f"{stage}-01.json").write_text('{"status": "pass"}')
        _git(root, "add", "records")
        _git(root, "commit", "-q", "-m", f"{stage} record for item {item}")
    _git(root, "checkout", "-q", "main")
    _git(root, "merge", "-q", "--no-ff", "-m", f"Merge branch 'attavo/{item}-x'", f"attavo/{item}-x")
    return _git(root, "rev-parse", "HEAD")


def _install(root: Path, manifest: str = MANIFEST) -> str:
    (root / "attavo.toml").write_text(manifest)
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "install")
    return _git(root, "rev-parse", "HEAD")


def _publish_main(root: Path) -> None:
    _git(root, "update-ref", "refs/remotes/origin/main", _git(root, "rev-parse", "main"))
    _git(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")


def _doctor(root: Path, *flags: str) -> str:
    return subprocess.run(["python3", str(ATTAVO), "doctor", *flags, str(root)],
                          capture_output=True, text=True, check=False).stdout


def _proof_six(root: Path) -> dict[str, Any]:
    proofs = json.loads(_doctor(root, "--json"))["proofs"]
    return cast(dict[str, Any], [p for p in proofs if p["proof"] == "every-attested-item-is-sealed"][0])


def _proof_six_line(root: Path) -> str:
    return [line for line in _doctor(root).splitlines() if line.startswith("proof 6 ")][0]


def test_sealed_since_defaults_to_proof_threes_window_start(tmp_path: Path) -> None:
    root = tmp_path / "default"
    _init(root)
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "before the product")
    _merge_item(root, "1")
    _merge_item(root, "2")
    _install(root)
    m3 = _merge_item(root, "3")
    (root / "sealed.txt").write_text(m3 + "\n")
    _publish_main(root)
    p = _proof_six(root)
    assert p["holds"], p["reason"]
    assert p["before_window"] == ["1", "2"] and p["sealed"] == ["3"]
    assert p["unsealed"] == [] and p["not_merged"] == []


def test_sealed_since_defaults_to_doctor_from_when_set(tmp_path: Path) -> None:
    root = tmp_path / "from"
    _init(root)
    _install(root)
    _merge_item(root, "1")
    (root / "queue.txt").write_text("the queue arrives\n")
    _git(root, "add", "queue.txt")
    _git(root, "commit", "-q", "-m", "the queue arrives")
    window = _git(root, "rev-parse", "HEAD")
    m2 = _merge_item(root, "2")
    (root / "attavo.toml").write_text(MANIFEST + f'\n[doctor]\nfrom = "{window}"\n')
    (root / "sealed.txt").write_text(m2 + "\n")
    _publish_main(root)
    p = _proof_six(root)
    assert p["holds"], p["reason"]
    assert p["before_window"] == ["1"] and p["sealed"] == ["2"]


def test_holding_line_names_the_window_commit(tmp_path: Path) -> None:
    declared = tmp_path / "declared"
    _init(declared)
    _install(declared)
    m1 = _merge_item(declared, "1")
    m2 = _merge_item(declared, "2")
    (declared / "attavo.toml").write_text(MANIFEST + f'sealed_since = "{m1}"\n')
    (declared / "sealed.txt").write_text(m2 + "\n")
    _publish_main(declared)
    assert _proof_six_line(declared) == (
        f"proof 6 every-attested-item-is-sealed: HOLDS: 1 sealed, 1 before the window at "
        f"{m1[:12]}, 0 not merged")

    whole = tmp_path / "whole"
    _init(whole)
    _install(whole)
    w1 = _merge_item(whole, "1")
    (whole / "sealed.txt").write_text(w1 + "\n")
    _publish_main(whole)
    assert _proof_six_line(whole) == (
        "proof 6 every-attested-item-is-sealed: HOLDS: 1 sealed, 0 before the window, 0 not merged")
