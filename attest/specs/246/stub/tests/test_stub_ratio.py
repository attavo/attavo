"""verify's stub_ratio: whose code the review judged. On #194 and #204 the
build shipped the spec author's stub almost line for line and every record
said weak_stub false, stub_ratio null: three fields no release computed.

Run: python3 -m pytest tests -q
"""
from __future__ import annotations

import json
from pathlib import Path

from attavo.verify import stub_ratio, stub_test_paths
from helpers import commit_all, git, make_tenant
from test_drive import GREEN_FORGE, RED_FORGE, add_bare_remote, add_forge, approve_item, drive, latest_record, run_attavo

ROOT = Path(__file__).resolve().parent.parent
EXCLUDED = ("specs", "records")
SELECT_TESTS_DIR = "select = \"grep '^tests/' || true\""


def diff(path: str, *added: str) -> str:
    body = "".join(f"+{line}\n" for line in added)
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -0,0 +1,{len(added)} @@\n{body}"


def select_only_tests_dir(root: Path) -> None:
    """The fixture's selector is `cat`, which declares every path a test of
    itself, so every stub file would be left out of the ratio. A selector
    that selects only tests/ keeps built.txt counted, as it was."""
    manifest = root / "attavo.toml"
    manifest.write_text(manifest.read_text().replace('select = "cat"', SELECT_TESTS_DIR))
    commit_all(root, "a selector that selects tests/ only")


def stub(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "stub"
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    root.mkdir(exist_ok=True)
    return root


def test_stub_ratio_is_one_when_every_added_line_is_the_stubs(tmp_path: Path) -> None:
    s = stub(tmp_path, {"pkg/a.py": "def f():\n    return 1\n"})
    assert stub_ratio(diff("pkg/a.py", "def f():", "    return 1"), s, EXCLUDED) == 1.0


def test_stub_ratio_is_the_share_across_files_and_ignores_blank_lines(tmp_path: Path) -> None:
    s = stub(tmp_path, {"pkg/a.py": "def f():\n    return 1\n"})
    text = diff("pkg/a.py", "def f():", "", "    return 2") + diff("pkg/b.py", "x = 1", "y = 2")
    assert stub_ratio(text, s, EXCLUDED) == 0.25


def test_stub_ratio_matches_a_line_only_against_the_stubs_file_at_the_same_path(tmp_path: Path) -> None:
    s = stub(tmp_path, {"pkg/a.py": "x = 1\n"})
    assert stub_ratio(diff("pkg/b.py", "x = 1"), s, EXCLUDED) == 0.0


def test_stub_ratio_does_not_count_the_specs_or_the_records_directory(tmp_path: Path) -> None:
    s = stub(tmp_path, {"pkg/a.py": "x = 1\n"})
    text = diff("specs/1/stub/pkg/a.py", "x = 1") + diff("records/1/build-01.json", "{}") + diff("pkg/a.py", "x = 1")
    assert stub_ratio(text, s, EXCLUDED) == 1.0


def test_stub_ratio_is_null_with_no_stub_or_no_added_line_and_never_zero(tmp_path: Path) -> None:
    assert stub_ratio(diff("pkg/a.py", "x = 1"), tmp_path / "absent", EXCLUDED) is None
    s = stub(tmp_path, {"pkg/a.py": "x = 1\n"})
    assert stub_ratio("", s, EXCLUDED) is None
    assert stub_ratio(diff("records/1/build-01.json", "{}"), s, EXCLUDED) is None


def test_verify_records_the_ratio_and_no_longer_carries_the_two_fields_nothing_computed(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "build_valid")
    select_only_tests_dir(root)
    add_forge(root, GREEN_FORGE)
    add_bare_remote(root, tmp_path)
    # on the item's own branch, as a real item is: the diff under review is
    # the branch's work against the default branch, and drive pushes as it goes
    git(root, "checkout", "-q", "-b", "attavo/1-x")
    approve_item(root)
    assert drive(root, "1").returncode == 0
    assert drive(root, "1").returncode == 0
    record = latest_record(root, "verify")
    assert record["stub_ratio"] == 0.0, record["stub_ratio"]
    assert "weak_stub" not in record and "mutation" not in record, sorted(record)
    schema = json.loads((ROOT / "schema" / "records" / "verify.schema.json").read_text())
    assert schema["$id"].endswith("/verify/9"), schema["$id"]
    assert "weak_stub" not in schema["properties"] and "mutation" not in schema["properties"]


def test_a_returned_review_carries_the_ratio_too(tmp_path: Path) -> None:
    """#208's review: the forge-not-green write site recorded a constant
    null, though the ratio depends on the diff and the stub, not the forge."""
    root = make_tenant(tmp_path, "build_valid")
    select_only_tests_dir(root)
    add_forge(root, RED_FORGE)
    add_bare_remote(root, tmp_path)
    git(root, "checkout", "-q", "-b", "attavo/1-x")
    # the stub's one file says what the fixture executor will write, so the
    # share is 1.0 and a constant 0.0 cannot pass for it
    (root / "specs" / "1" / "stub" / "built.txt").write_text("built\n")
    commit_all(root, "a stub that is the change")
    approve_item(root)
    assert drive(root, "1").returncode == 0
    run = run_attavo(root, "verify", "1")
    assert run.returncode == 0 and "returned" in run.stdout, run.stdout + run.stderr
    record = latest_record(root, "verify")
    assert record["status"] == "returned", record["status"]
    assert record["stub_ratio"] == 1.0, record["stub_ratio"]


def test_stub_ratio_leaves_out_the_stubs_test_files_on_both_sides(tmp_path: Path) -> None:
    # #214 read 0.75 with its test file counted as code the build copied
    s = stub(tmp_path, {"pkg/a.py": "x = 1\n", "tests/test_a.py": "def test_a():\n    assert True\n"})
    text = diff("pkg/a.py", "x = 1", "y = 2") + diff("tests/test_a.py", "def test_a():", "    assert True")
    assert stub_ratio(text, s, EXCLUDED) == 0.75
    assert stub_ratio(text, s, EXCLUDED, frozenset({"tests/test_a.py"})) == 0.5
    assert stub_ratio(diff("tests/test_a.py", "def test_a():"), s, EXCLUDED, frozenset({"tests/test_a.py"})) is None


def test_stub_ratio_is_null_when_the_stubs_tests_cannot_be_told_apart(tmp_path: Path) -> None:
    s = stub(tmp_path, {"pkg/a.py": "x = 1\n"})
    assert stub_ratio(diff("pkg/a.py", "x = 1"), s, EXCLUDED, None) is None


def test_stub_test_paths_are_the_stub_files_the_tenants_selector_selects_for_themselves(tmp_path: Path) -> None:
    s = stub(tmp_path, {"pkg/a.py": "x = 1\n", "tests/test_a.py": "def test_a(): pass\n",
                        "tests/helpers.py": "", ".removed": "pkg/gone.py\n"})
    select = "while read p; do case $p in tests/test_*) echo \"$p::test_a\";; *) echo tests;; esac; done"
    assert stub_test_paths(tmp_path, s, select) == frozenset({"tests/test_a.py"})
    assert stub_test_paths(tmp_path, tmp_path / "absent", select) == frozenset()


def test_stub_test_paths_are_unknown_without_a_selector_or_when_it_fails(tmp_path: Path) -> None:
    s = stub(tmp_path, {"tests/test_a.py": "def test_a(): pass\n"})
    assert stub_test_paths(tmp_path, s, None) is None
    assert stub_test_paths(tmp_path, s, "exit 3") is None
