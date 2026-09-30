"""The tenant's issues, read through [forge].issues_command
(attavo/issues.py). Written red first: on 5df6926 there is no
attavo.issues module and no issues_command in the manifest schema.

Run: python3 -m pytest tests/test_issues.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from attavo import issues

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "schema" / "manifest.schema.json").read_text())

BODY = """The Plan store and `attavo plan`.

Class: core

## Claims

- attavo/store.py
- `attavo/plan.py`
-   tests/test_plan.py

## Acceptance

- not a claim
"""


def command_printing(tmp_path: Path, payload: Any, code: int = 0, err: str = "") -> str:
    script = tmp_path / "issues-cmd.py"
    script.write_text("import sys\n"
                      f"sys.stdout.write({json.dumps(payload) if not isinstance(payload, str) else payload!r})\n"
                      f"sys.stderr.write({err!r})\n"
                      f"sys.exit({code})\n")
    return f"{sys.executable} {script}"


def test_claims_are_read_from_the_bodys_claims_section_and_nowhere_else() -> None:
    assert issues.parse_claims(BODY) == ["attavo/store.py", "attavo/plan.py", "tests/test_plan.py"]
    assert issues.parse_claims("no section here\n- attavo/x.py\n") is None
    assert issues.parse_claims("## Claims\n\n## Acceptance\n- a\n") == []


def test_the_command_is_read_into_issues_with_their_claims_in_number_order(tmp_path: Path) -> None:
    payload = [
        {"number": 264, "title": "the Plan store", "state": "closed", "closed_at": "2026-09-29T20:00:00Z",
         "labels": ["class:core"], "body": BODY},
        {"number": 231, "title": "a fix", "state": "open", "closed_at": None, "labels": [], "body": "no claims"},
    ]
    m = {"forge": {"issues_command": command_printing(tmp_path, payload)}}
    got, why = issues.read_issues(tmp_path, m)
    assert why is None and got is not None
    assert [i["number"] for i in got] == [231, 264]
    assert got[0]["claims"] is None and got[0]["state"] == "open"
    assert got[1]["claims"] == ["attavo/store.py", "attavo/plan.py", "tests/test_plan.py"]
    assert got[1]["labels"] == ["class:core"] and got[1]["closed_at"] == "2026-09-29T20:00:00Z"


def test_no_command_is_not_read_and_never_an_empty_list(tmp_path: Path) -> None:
    assert issues.read_issues(tmp_path, {}) == (None, "not read: no [forge].issues_command")
    assert issues.read_issues(tmp_path, {"forge": {"checks_command": "x"}})[0] is None


def test_a_failing_command_or_output_of_the_wrong_shape_is_not_read_and_says_why(tmp_path: Path) -> None:
    failing = {"forge": {"issues_command": command_printing(tmp_path, "", code=3, err="gh: not logged in\n")}}
    got, why = issues.read_issues(tmp_path, failing)
    assert got is None and why == "not read: [forge].issues_command exited 3: gh: not logged in"
    for payload, needle in [
        ("not json", "printed no JSON"),
        ({"number": 1}, "not a list"),
        ([{"number": 1, "title": "t", "state": "OPEN", "labels": [], "body": ""}], "state 'OPEN'"),
        ([{"title": "t", "state": "open"}], "no issue number"),
        ([{"number": 2, "title": "t", "state": "open", "labels": [{"name": "x"}]}], "labels"),
    ]:
        (tmp_path / "issues-cmd.py").unlink(missing_ok=True)
        m = {"forge": {"issues_command": command_printing(tmp_path, payload)}}
        got, why = issues.read_issues(tmp_path, m)
        assert got is None and why is not None and needle in why, (payload, why)


def test_a_command_that_runs_past_its_bound_is_not_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(issues, "TIMEOUT_S", 1)
    got, why = issues.read_issues(tmp_path, {"forge": {"issues_command": "sleep 10"}})
    assert got is None and why == "not read: [forge].issues_command ran past 1s"


def test_the_manifest_schema_takes_issues_command_and_this_repository_declares_it() -> None:
    manifest = tomllib.loads((ROOT / "attavo.toml").read_text())
    jsonschema.validate(manifest, SCHEMA)
    command = manifest["forge"]["issues_command"]
    assert command == "python3 .attavo/bin/forge-issues"
    assert (ROOT / ".attavo" / "bin" / "forge-issues").is_file()
    bad = {**manifest, "forge": {**manifest["forge"], "issues_command": ""}}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, SCHEMA)


def test_this_repositorys_command_translates_the_forges_words_into_the_shape(tmp_path: Path) -> None:
    fake = tmp_path / "bin"
    fake.mkdir()
    gh_output = [{"number": 264, "title": "the Plan store", "state": "CLOSED", "closedAt": "2026-09-29T20:00:00Z",
                  "labels": [{"name": "class:core", "color": "000000"}], "body": BODY},
                 {"number": 263, "title": "R5", "state": "OPEN", "closedAt": None, "labels": [], "body": ""}]
    (fake / "gh").write_text(f"#!/bin/sh\ncat <<'JSON'\n{json.dumps(gh_output)}\nJSON\n")
    (fake / "gh").chmod(0o755)
    env = {**os.environ, "PATH": f"{fake}{os.pathsep}{os.environ['PATH']}"}
    run = subprocess.run([sys.executable, str(ROOT / ".attavo" / "bin" / "forge-issues")],
                         capture_output=True, text=True, env=env, check=False)
    assert run.returncode == 0, run.stderr
    m = {"forge": {"issues_command": command_printing(tmp_path, run.stdout)}}
    got, why = issues.read_issues(tmp_path, m)
    assert why is None and got is not None
    assert [(i["number"], i["state"], i["labels"]) for i in got] == [(263, "open", []),
                                                                     (264, "closed", ["class:core"])]
    assert got[1]["claims"] == ["attavo/store.py", "attavo/plan.py", "tests/test_plan.py"]
    (fake / "gh").write_text("#!/bin/sh\necho 'gh: offline' >&2\nexit 1\n")
    failed = subprocess.run([sys.executable, str(ROOT / ".attavo" / "bin" / "forge-issues")],
                            capture_output=True, text=True, env=env, check=False)
    assert failed.returncode != 0 and failed.stdout == ""
