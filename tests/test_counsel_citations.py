"""A counsel citation is a repository-relative path at the turn's sha (#328).

The specimen: the operator's first questions from Analyze (#277, turns 1
and 2). Every citation was an absolute path into the detached snapshot the
question ran in, removed when the answer returned; the second cited
prior.json, a temporary input file. Each was dead the moment it was read.

Every test here is written red against origin/main: there, the stored
citations are exactly what the adapter returned.

Run: python3 -m pytest tests/test_counsel_citations.py -q
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from helpers import commit_all, git, make_tenant
from test_counsel import (
    COUNSEL_SCHEMA,
    approve_and_halt,
    configure_counsel_adapter,
    records_dir,
    run_attavo,
    write_probe_adapter,
)
from test_counsel_ask import conversation, halted_and_committed, head, use_probe_adapter

# A probe adapter that cites what ATTAVO_PROBE_CITES names, with {cwd},
# {prior}, {events} and {log} replaced by the tree it runs in and the
# temporary inputs counsel handed it.
PROBE = (
    "#!/usr/bin/env python3\n"
    "import json, os\n"
    "subs = {'{cwd}': os.getcwd(), '{prior}': os.environ.get('ATTAVO_COUNSEL_PRIOR', ''),\n"
    "        '{events}': os.environ.get('ATTAVO_COUNSEL_EVENTS', ''),\n"
    "        '{log}': os.environ.get('ATTAVO_COUNSEL_LOG', '')}\n"
    "def fill(s):\n"
    "    for k, v in subs.items():\n"
    "        s = s.replace(k, v)\n"
    "    return s\n"
    "cites = [{**c, 'path': fill(c['path']) if c.get('path') else None}\n"
    "         for c in json.loads(os.environ['ATTAVO_PROBE_CITES'])]\n"
    "print(json.dumps({'model': 'probe', 'tokens': {'input': 1, 'output': 1}, 'cost_usd': 0.0,\n"
    "  'class': 'unclassed', 'verdict': 'answer', 'citations': cites,\n"
    "  'proposal': {'ruling_text': 'x', 'patch': None, 'question': None}}))\n"
)


def cite(path: str | None, symbol: str | None = None, kind: str = "instruction") -> dict[str, Any]:
    return {"kind": kind, "path": path, "symbol": symbol, "ruling_id": None}


def probe_env(cites: list[dict[str, Any]]) -> dict[str, str]:
    return {"ATTAVO_PROBE_CITES": json.dumps(cites)}


def probed_tenant(tmp_path: Path) -> Path:
    root = halted_and_committed(tmp_path)
    write_probe_adapter(root, "probe", PROBE)
    use_probe_adapter(root)
    return root


def ask_cites(root: Path, cites: list[dict[str, Any]], question: str = "why?") -> list[dict[str, Any]]:
    run = run_attavo(root, "counsel", "1", "--ask", question, "--by", "alice", env=probe_env(cites))
    assert run.returncode == 0, run.stdout + run.stderr
    out: list[dict[str, Any]] = json.loads(run.stdout)["answer"]["citations"]
    return out


def test_a_snapshot_path_is_stored_repository_relative_and_opens_at_the_turns_sha(tmp_path: Path) -> None:
    """The specimen: /private/var/.../attavo-gate-*/head/<path>. Stored as
    <path>, with its line kept, and `git show <sha>:<path>` opens it."""
    root = probed_tenant(tmp_path)

    stored = ask_cites(root, [cite("{cwd}/specs/1/spec.md:3")])

    turn = conversation(root)["turns"][0]
    assert turn["answer"]["citations"] == stored
    assert stored[0]["path"] == "specs/1/spec.md:3", stored
    assert "resolution" not in stored[0]
    shown = git(root, "show", f"{turn['sha']}:{stored[0]['path'].rsplit(':', 1)[0]}")
    assert shown == (root / "specs/1/spec.md").read_text()
    assert turn["sha"] == head(root)
    assert "/attavo-gate-" not in json.dumps(turn)


def test_a_path_outside_the_snapshot_is_kept_and_marked_not_in_repository(tmp_path: Path) -> None:
    """Kept as written, never dropped and never rewritten: the reader sees
    what counsel said and that it is not a file of the repository."""
    root = probed_tenant(tmp_path)

    stored = ask_cites(root, [cite("/etc/hosts"), cite("../elsewhere/notes.md")])

    assert [(c["path"], c["resolution"]) for c in stored] == [
        ("/etc/hosts", "not_in_repository"), ("../elsewhere/notes.md", "not_in_repository")]


def test_a_path_missing_at_the_turns_sha_is_kept_and_marked_not_found_at_that_sha(tmp_path: Path) -> None:
    """Inside the snapshot but not in the commit it is a snapshot of (a file
    the adapter wrote there): relative, kept, marked -- not silently dropped."""
    root = probed_tenant(tmp_path)

    stored = ask_cites(root, [cite("{cwd}/attavo/never_existed.py:9"), cite("docs/also-missing.md")])

    assert [(c["path"], c["resolution"]) for c in stored] == [
        ("attavo/never_existed.py:9", "not_found_at_sha"), ("docs/also-missing.md", "not_found_at_sha")]


def test_a_citation_with_no_path_is_left_exactly_as_it_was(tmp_path: Path) -> None:
    root = probed_tenant(tmp_path)
    ruling = {"kind": "guard", "path": None, "symbol": None, "ruling_id": "overlay-2026-09-01"}

    assert ask_cites(root, [ruling]) == [ruling]


def test_counsels_own_temporary_inputs_are_named_for_what_they_are(tmp_path: Path) -> None:
    """Issue #277's second answer cited .../tmpqb285u5h/prior.json turn-01.
    A temporary input is named, not linked: turn 1, the events tail."""
    root = probed_tenant(tmp_path)
    first = ask_cites(root, [cite("{cwd}/specs/1/spec.md")], question="first")
    assert first[0]["path"] == "specs/1/spec.md"

    stored = ask_cites(root, [cite("{prior}", symbol="turn-01"), cite("{events}")], question="second")

    assert [(c["path"], c["symbol"], c["resolution"]) for c in stored] == [
        ("turn 1", None, "input"), ("the events tail", None, "input")]
    assert not any("/tmp" in json.dumps(c) or "/var/" in json.dumps(c) for c in stored)


def test_a_prior_reading_is_named_by_its_record(tmp_path: Path) -> None:
    root = probed_tenant(tmp_path)
    assert run_attavo(root, "counsel", "1", env=probe_env([])).returncode == 0
    commit_all(root, "counsel-01")

    stored = ask_cites(root, [cite("{prior}", symbol="counsel-01")], question="after the reading")

    assert [(c["path"], c["resolution"]) for c in stored] == [("prior reading counsel-01", "input")]


def test_the_stop_path_normalises_its_citations_against_its_own_tree(tmp_path: Path) -> None:
    """`attavo counsel N` without --ask writes counsel-NN.json from the
    checkout itself, not a snapshot: its citations are relative to that
    tree and open at the record's sha, and the same two markers apply."""
    root = probed_tenant(tmp_path)

    run = run_attavo(root, "counsel", "1", env=probe_env([
        cite("{cwd}/specs/1/spec.md:2"), cite("/etc/hosts"), cite("{cwd}/attavo/nothing.py")]))
    assert run.returncode == 0, run.stdout + run.stderr

    record = json.loads((records_dir(root) / "counsel-01.json").read_text())
    jsonschema.validate(record, COUNSEL_SCHEMA)
    got = [(c["path"], c.get("resolution")) for c in record["citations"]]
    assert got == [("specs/1/spec.md:2", None), ("/etc/hosts", "not_in_repository"),
                   ("attavo/nothing.py", "not_found_at_sha")]
    assert git(root, "show", f"{record['sha']}:specs/1/spec.md")


def test_a_recorded_turn_carries_the_normalised_citations_into_its_record(tmp_path: Path) -> None:
    """--record-turn copies a turn's citations; they are the stored ones."""
    root = probed_tenant(tmp_path)
    ask_cites(root, [cite("{cwd}/specs/1/spec.md:3"), cite("/etc/hosts")])

    run = run_attavo(root, "counsel", "1", "--record-turn", "1", "--by", "alice")
    assert run.returncode == 0, run.stdout + run.stderr

    record = json.loads((records_dir(root) / "counsel-01.json").read_text())
    jsonschema.validate(record, COUNSEL_SCHEMA)
    assert [(c["path"], c.get("resolution")) for c in record["citations"]] == [
        ("specs/1/spec.md:3", None), ("/etc/hosts", "not_in_repository")]


def test_the_counsel_schema_is_bumped_for_the_citation_resolution() -> None:
    """counsel/6: a citation may carry `resolution`, optional, so every
    earlier record still validates."""
    from attavo import types
    assert COUNSEL_SCHEMA["$id"].endswith("/counsel/6")
    item = COUNSEL_SCHEMA["properties"]["citations"]["items"]
    assert item["properties"]["resolution"]["enum"] == ["input", "not_in_repository", "not_found_at_sha"]
    assert "resolution" not in item["required"]
    assert "resolution" in types.CounselCitation.__annotations__


@pytest.mark.parametrize("path", ["specs/1/spec.md", "specs/1/spec.md:12", "specs/1/spec.md:12-20"])
def test_an_already_relative_path_that_exists_is_left_alone(tmp_path: Path, path: str) -> None:
    root = make_tenant(tmp_path, "build_valid")
    configure_counsel_adapter(root)
    approve_and_halt(root)
    commit_all(root, "build record")
    write_probe_adapter(root, "probe", PROBE)
    use_probe_adapter(root)

    assert ask_cites(root, [cite(path)]) == [cite(path)]
