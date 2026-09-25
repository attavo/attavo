"""verify's stub_ratio: whose code the review judged. On #194 and #204 the
build shipped the spec author's stub almost line for line and every record
said weak_stub false, stub_ratio null: three fields no release computed.

Run: python3 -m pytest tests -q
"""
from __future__ import annotations

import json
from pathlib import Path

from attavo.verify import stub_ratio
from helpers import commit_all, git, make_tenant
from test_drive import GREEN_FORGE, add_bare_remote, add_forge, approve_item, latest_record, run_attavo

ROOT = Path(__file__).resolve().parent.parent
EXCLUDED = ("specs", "records")


def diff(path: str, *added: str) -> str:
    body = "".join(f"+{line}\n" for line in added)
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -0,0 +1,{len(added)} @@\n{body}"


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
    add_forge(root, GREEN_FORGE)
    add_bare_remote(root, tmp_path)
    # on the item's own branch, as a real item is: the diff under review is
    # the branch's work against the default branch, and drive pushes as it goes
    git(root, "checkout", "-q", "-b", "attavo/1-x")
    approve_item(root)
    assert run_attavo(root, "drive", "1").returncode == 0
    assert run_attavo(root, "drive", "1").returncode == 0
    record = latest_record(root, "verify")
    assert record["stub_ratio"] == 0.0, record["stub_ratio"]
    assert "weak_stub" not in record and "mutation" not in record, sorted(record)
    schema = json.loads((ROOT / "schema" / "records" / "verify.schema.json").read_text())
    assert schema["$id"].endswith("/verify/6"), schema["$id"]
    assert "weak_stub" not in schema["properties"] and "mutation" not in schema["properties"]


def test_a_returned_review_carries_the_ratio_too(tmp_path: Path) -> None:
    """#208's review: the forge-not-green write site recorded a constant
    null, though the ratio depends on the diff and the stub, not the forge."""
    from test_drive import RED_FORGE
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, RED_FORGE)
    add_bare_remote(root, tmp_path)
    git(root, "checkout", "-q", "-b", "attavo/1-x")
    # the stub's one file says what the fixture executor will write, so the
    # share is 1.0 and a constant 0.0 cannot pass for it
    (root / "specs" / "1" / "stub" / "built.txt").write_text("built\n")
    commit_all(root, "a stub that is the change")
    approve_item(root)
    assert run_attavo(root, "drive", "1").returncode == 0
    run = run_attavo(root, "verify", "1")
    assert run.returncode == 0 and "returned" in run.stdout, run.stdout + run.stderr
    record = latest_record(root, "verify")
    assert record["status"] == "returned", record["status"]
    assert record["stub_ratio"] == 1.0, record["stub_ratio"]
