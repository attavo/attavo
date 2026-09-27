"""Issue #210: `test` inherits a records-only commit's result only because
no test in the ordinary suite reads this repository's own records
directory, the one thing such a commit changes. The tests that do are
marked @pytest.mark.own_records and are the only ones `test` runs on the
inherit path, the only ones the doctor job runs, and the ones every
other run excludes (-m 'not own_records'). This guard is what keeps the
marker true: an unmarked test that names this repository as the tenant
a product reader is pointed at, or joins ROOT to the records directory,
fails here. Nothing is exempt: the doctor's own proofs carry the marker
since #229 build-04, when a hand-kept deselect list that stood in for
it was missing one test and the gate ran that test against the running
build's own marker.

The guard reads test source for the vocabulary by which a test in this
suite reaches the repository's own tree (ROOT handed to a product reader,
ROOT joined to "attest" or to the manifest's paths). It cannot follow a
helper that hides ROOT; a new way of reaching the records adds a pattern
here. The property behind the list was also checked directly when this
guard was written: the suite run on a copy of the repository with
attest/records removed, unmarked tests only, passed.

Run: python3 -m pytest tests -q
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
MARK = "pytest.mark.own_records"
READERS = (
    re.compile(r"\b(?:run_doctor|drive_report|read_manifest|run_attavo)\(\s*ROOT\b"),
    re.compile(r"\bROOT\s*/\s*[\"']attest[\"']"),
    re.compile(r"\bROOT\b[^\n]*\bpaths\b"),
)


def _is_marked(node: ast.AST) -> bool:
    decorators = getattr(node, "decorator_list", [])
    return any(ast.unparse(d) == MARK for d in decorators)


def unmarked_own_records_readers(tests_dir: Path, skip: str) -> list[str]:
    """`file::test` for every test in tests_dir that reads the repository's
    own records without the marker. `skip` is a file name not scanned: this
    file states the patterns as strings and would name itself."""
    found: list[str] = []
    for path in sorted(tests_dir.glob("test_*.py")):
        if path.name == skip:
            continue
        source = path.read_text()
        tree = ast.parse(source)
        if MARK in source and any(
                isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytestmark"
                                                  for t in n.targets) and MARK in ast.unparse(n.value)
                for n in tree.body):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
                continue
            if _is_marked(node):
                continue
            body = ast.get_source_segment(source, node) or ""
            name = f"{path.name}::{node.name}"
            if any(pattern.search(body) for pattern in READERS):
                found.append(name)
    return found


def test_no_unmarked_test_reads_this_repositorys_own_records() -> None:
    assert unmarked_own_records_readers(TESTS, skip=Path(__file__).name) == []


def test_every_doctor_proof_test_carries_the_marker() -> None:
    """The doctor's proofs read this repository's own records by
    construction (run_doctor(ROOT)); the guard above names any that lose
    the marker, and this names the set so a new proof's test is written
    marked (#229 build-04)."""
    source = (TESTS / "test_doctor.py").read_text()
    tree = ast.parse(source)
    proofs = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)
              and n.name.startswith("test_") and "run_doctor(ROOT)" in (ast.get_source_segment(source, n) or "")]
    assert len(proofs) >= 7, proofs
    unmarked = [n.name for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in proofs and not _is_marked(n)]
    assert unmarked == [], unmarked


@pytest.mark.parametrize("reader", [
    "run_doctor(ROOT)",
    "drive_report(ROOT, m, item)",
    "read_manifest(ROOT)",
    "run_attavo(ROOT, 'report')",
    "list((ROOT / 'attest').rglob('*.json'))",
    "sorted((ROOT / m['paths']['records']).iterdir())",
])
def test_the_guard_names_a_planted_reader_and_not_a_marked_one(tmp_path: Path, reader: str) -> None:
    (tmp_path / "test_planted.py").write_text(
        f"def test_reads() -> None:\n    {reader}\n")
    (tmp_path / "test_marked.py").write_text(
        f"import pytest\n\n\n@pytest.mark.own_records\ndef test_reads() -> None:\n    {reader}\n")
    (tmp_path / "test_module_marked.py").write_text(
        f"import pytest\n\npytestmark = pytest.mark.own_records\n\n\ndef test_reads() -> None:\n    {reader}\n")
    assert unmarked_own_records_readers(tmp_path, skip="") == ["test_planted.py::test_reads"]


def test_the_guard_leaves_a_test_that_builds_its_own_tenant_alone(tmp_path: Path) -> None:
    (tmp_path / "test_own.py").write_text(
        "def test_builds(tmp_path):\n    root = make_tenant(tmp_path, 'valid')\n    run_doctor(root)\n")
    assert unmarked_own_records_readers(tmp_path, skip="") == []
