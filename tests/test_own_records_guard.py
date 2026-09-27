"""Issue #210: `test` inherits a records-only commit's result only because
no test in the ordinary suite reads this repository's own records
directory, the one thing such a commit changes. The tests that do are
marked @pytest.mark.own_records and are the only ones `test` runs on the
inherit path. This guard is what keeps that list true: an unmarked test
that names this repository as the tenant a product reader is pointed at,
or joins ROOT to the records directory, fails here. The doctor tests the
`test` job deselects are exempt: they never run in `test`, they run for
real in the `doctor` job, which does not inherit.

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
CI_YML = TESTS.parent / ".github" / "workflows" / "ci.yml"
MARK = "pytest.mark.own_records"
DESELECT = re.compile(r"--deselect\s+tests/(test_\w+\.py::test_\w+)")
READERS = (
    re.compile(r"\b(?:run_doctor|drive_report|read_manifest|run_attavo)\(\s*ROOT\b"),
    re.compile(r"\bROOT\s*/\s*[\"']attest[\"']"),
    re.compile(r"\bROOT\b[^\n]*\bpaths\b"),
)


def _is_marked(node: ast.AST) -> bool:
    decorators = getattr(node, "decorator_list", [])
    return any(ast.unparse(d) == MARK for d in decorators)


def deselected_by_the_test_job() -> frozenset[str]:
    """`file::test` for each test ci.yml's `test` job deselects."""
    lines = CI_YML.read_text().splitlines()
    start = lines.index("  test:")
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith("  ") and not lines[i].startswith("    ")
                and lines[i].strip().endswith(":") and lines[i].strip() != "test:"), len(lines))
    return frozenset(DESELECT.findall("\n".join(lines[start:end])))


def unmarked_own_records_readers(tests_dir: Path, skip: str,
                                 exempt: frozenset[str] = frozenset()) -> list[str]:
    """`file::test` for every test in tests_dir that reads the repository's
    own records without the marker. `skip` is a file name not scanned: this
    file states the patterns as strings and would name itself. `exempt`
    names tests, as `file::test`, that never run where the marker matters."""
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
            if name not in exempt and any(pattern.search(body) for pattern in READERS):
                found.append(name)
    return found


def test_the_test_job_deselects_the_doctor_tests_the_doctor_job_runs() -> None:
    assert len(deselected_by_the_test_job()) == 6, deselected_by_the_test_job()


def test_no_unmarked_test_reads_this_repositorys_own_records() -> None:
    assert unmarked_own_records_readers(
        TESTS, skip=Path(__file__).name, exempt=deselected_by_the_test_job()) == []


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
