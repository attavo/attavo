"""attavo plan: the Plan's order in the product's datastore (attavo/plan.py).
Written red first: on d4c0bc1 there is no plan subcommand and no
attavo.plan module.

Run: python3 -m pytest tests/test_plan.py -q
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from attavo import plan, store
from helpers import git, make_tenant

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"
SCHEMA = json.loads((ROOT / "schema" / "plan.schema.json").read_text())

PLAN: dict[str, Any] = {
    "schema": "plan/1",
    "epics": [
        {"id": "E4", "title": "Plan on the screen", "outcome": "waves on the screen", "issues": [262, 263]},
        {"id": "backlog", "title": "Backlog", "issues": [231, 232]},
        {"id": "E5", "title": "Claims arbitration", "issues": [264]},
    ],
    "edges": [
        {"type": "depends", "from": 262, "to": 263, "evidence": "reads its JSON"},
        {"type": "ruled", "from": 231, "to": 264, "by": "dbean10", "at": "2026-09-29", "why": "order"},
    ],
    "policy": {"research": "not required"},
}


@pytest.fixture(autouse=True)
def no_store_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ATTAVO_STORE", raising=False)


def run_attavo(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(ATTAVO), *args], cwd=str(root),
                          capture_output=True, text=True, check=False)


def broken(**change: Any) -> dict[str, Any]:
    body = copy.deepcopy(PLAN)
    body.update(change)
    return body


def test_a_plan_passes_its_own_check_and_its_schema() -> None:
    assert plan.shape_errors(PLAN) == []
    jsonschema.validate(PLAN, SCHEMA)


@pytest.mark.parametrize("body,needle", [
    (broken(epics=[e for e in PLAN["epics"] if e["id"] != "backlog"]), "no backlog epic"),
    (broken(extra=1), "unknown key"),
    (broken(schema="plan/0"), "schema"),
    (broken(edges=[{"type": "ruled", "from": 1, "to": 2}]), "names who ruled it"),
    (broken(edges=[{"type": "blocks", "from": 1, "to": 2}]), "type is not"),
    (broken(epics=[{"id": "backlog", "title": "Backlog", "issues": ["#1"]}]), "not an issue number"),
])
def test_the_check_and_the_schema_refuse_the_same_bodies(body: dict[str, Any], needle: str) -> None:
    assert any(needle in e for e in plan.shape_errors(body)), plan.shape_errors(body)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(body, SCHEMA)


def test_the_check_refuses_what_a_schema_cannot_say() -> None:
    twice = broken(epics=[{"id": "backlog", "title": "Backlog", "issues": [5]},
                          {"id": "E1", "title": "One", "issues": [5]}])
    assert any("#5 is in both backlog and E1" in e for e in plan.shape_errors(twice))
    loop = broken(edges=[{"type": "depends", "from": 7, "to": 7}])
    assert any("depends on itself" in e for e in plan.shape_errors(loop))


def test_the_global_order_is_epic_rank_then_issue_rank() -> None:
    assert plan.global_order(PLAN) == [("E4", 262), ("E4", 263), ("backlog", 231),
                                       ("backlog", 232), ("E5", 264)]


def test_rank_moves_one_epic_or_one_issue_within_its_epic_and_nothing_else() -> None:
    by_epic = plan.rank(PLAN, "backlog", "E4")
    assert [e["id"] for e in by_epic["epics"]] == ["backlog", "E4", "E5"]
    assert [e["id"] for e in plan.rank(PLAN, "E4", None)["epics"]] == ["backlog", "E5", "E4"]
    by_issue = plan.rank(PLAN, "#263", "#262")
    assert by_issue["epics"][0]["issues"] == [263, 262]
    assert by_issue["epics"][1:] == PLAN["epics"][1:] and by_issue["edges"] == PLAN["edges"]
    assert plan.rank(PLAN, "231", None)["epics"][1]["issues"] == [232, 231]
    with pytest.raises(store.StoreRefusal, match="ranks only within its epic"):
        plan.rank(PLAN, "#264", "#262")
    with pytest.raises(store.StoreRefusal, match="not in the plan"):
        plan.rank(PLAN, "#999", None)
    assert PLAN["epics"][0]["issues"] == [262, 263], "rank returns a copy"


def test_move_places_an_issue_last_in_an_epic_and_takes_it_out_of_its_old_one() -> None:
    moved = plan.move(PLAN, "#262", "backlog")
    assert moved["epics"][0]["issues"] == [263]
    assert moved["epics"][1]["issues"] == [231, 232, 262]
    placed = plan.move(PLAN, "#300", "E5")
    assert placed["epics"][2]["issues"] == [264, 300]
    assert plan.shape_errors(moved) == [] and plan.shape_errors(placed) == []
    with pytest.raises(store.StoreRefusal, match="no epic E9"):
        plan.move(PLAN, "#262", "E9")


def test_the_command_puts_gets_ranks_and_refuses_a_stale_base_with_nothing_in_git(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "valid")
    f = tmp_path / "plan.json"
    f.write_text(json.dumps(PLAN))
    put = run_attavo(root, "plan", "put", str(f), "--base", "0", "--by", "alice")
    assert put.returncode == 0 and "plan version 1 written by alice" in put.stdout, put.stdout + put.stderr
    got = run_attavo(root, "plan", "get")
    assert got.returncode == 0 and json.loads(got.stdout) == PLAN and "plan version 1 by alice" in got.stderr
    ranked = run_attavo(root, "plan", "rank", "#263", "--before", "#262", "--by", "bob")
    assert ranked.returncode == 0 and "plan version 2 written by bob" in ranked.stdout, ranked.stderr
    assert json.loads(run_attavo(root, "plan", "get").stdout)["epics"][0]["issues"] == [263, 262]
    moved = run_attavo(root, "plan", "move", "#232", "--epic", "E5", "--by", "bob")
    assert moved.returncode == 0, moved.stderr
    stale = run_attavo(root, "plan", "put", str(f), "--base", "1", "--by", "carol")
    assert stale.returncode == 2
    assert "moved" in stale.stderr and "version 3" in stale.stderr and "bob" in stale.stderr
    log = run_attavo(root, "plan", "log")
    assert [line.split("\t")[0] for line in log.stdout.splitlines()] == ["1", "2", "3"]
    first = run_attavo(root, "plan", "get", "--version", "1")
    assert json.loads(first.stdout) == PLAN
    assert git(root, "status", "--porcelain") == ""


def test_the_command_refuses_a_bad_body_a_missing_author_and_an_empty_store(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "valid")
    assert "no plan in the store" in run_attavo(root, "plan", "get").stderr
    assert run_attavo(root, "plan", "rank", "E4", "--last", "--by", "x").returncode == 2
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(broken(epics=[])))
    r = run_attavo(root, "plan", "put", str(bad), "--base", "0", "--by", "alice")
    assert r.returncode == 2 and "no backlog epic" in r.stderr, r.stderr
    good = tmp_path / "good.json"
    good.write_text(json.dumps(PLAN))
    r = run_attavo(root, "plan", "put", str(good), "--base", "0")
    assert r.returncode == 2 and "--by" in r.stderr
    assert store.latest(root, "plan") == 0


CLAIMED: dict[str, Any] = {
    **PLAN,
    "claims": {"262": {"paths": ["attavo/frontier.py", "docs/**"], "by": "dbean10", "at": "2026-10-01T09:00:00Z"}},
}


def claims_in(root: Path) -> dict[str, Any]:
    env = store.get(root, "plan")
    assert env is not None
    claims: dict[str, Any] = env["body"].get("claims", {})
    return claims


def tenant_with_plan(tmp_path: Path) -> Path:
    root = make_tenant(tmp_path, "valid")
    store.put(root, "plan", PLAN, 0, "alice")
    return root


@pytest.mark.parametrize("entry,needle", [
    ({"paths": [], "by": "a", "at": "t"}, "no paths"),
    ({"paths": "a.py", "by": "a", "at": "t"}, "paths is not a list"),
    ({"paths": ["a.py"], "at": "t"}, "by and at"),
    ({"paths": ["a.py"], "by": "a"}, "by and at"),
    ({"paths": ["a.py"], "by": "a", "at": "t", "why": "x"}, "claims 262: unknown key"),
    ({"paths": ["/etc/passwd"], "by": "a", "at": "t"}, "repository-relative"),
    ({"paths": [""], "by": "a", "at": "t"}, "repository-relative"),
])
def test_the_check_and_the_schema_refuse_the_same_malformed_claims_entry(entry: dict[str, Any], needle: str) -> None:
    """Ruled 2026-10-01 (#313): claims for an issue with no spec are
    tool-kept structured data in the plan, so the plan's shape refuses a
    malformed entry, in the schema and in the check alike."""
    body = {**PLAN, "claims": {"262": entry}}
    assert any(needle in e for e in plan.shape_errors(body)), plan.shape_errors(body)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(body, SCHEMA)


def test_the_schema_and_the_check_accept_the_claims_map_and_the_check_refuses_what_a_schema_cannot_say() -> None:
    """Ruled 2026-10-01 (#313): the plan's optional `claims` map."""
    assert plan.shape_errors(CLAIMED) == []
    jsonschema.validate(CLAIMED, SCHEMA)
    jsonschema.validate(PLAN, SCHEMA)
    for body, needle in [({**PLAN, "claims": {"x": CLAIMED["claims"]["262"]}}, "not an issue number"),
                         ({**PLAN, "claims": []}, "claims is not an object"),
                         ({**PLAN, "claims": {"262": {**CLAIMED["claims"]["262"], "paths": ["a/../b.py"]}}},
                          "repository-relative")]:
        assert any(needle in e for e in plan.shape_errors(body)), (needle, plan.shape_errors(body))


def test_plan_claims_writes_the_draft_claims_beside_the_order_and_clear_removes_them(tmp_path: Path) -> None:
    """Ruled 2026-10-01 (#313): `attavo plan claims '#N' <path>... --by`
    writes {paths, by, at} through the store's base-checked write."""
    root = tenant_with_plan(tmp_path)
    r = run_attavo(root, "plan", "claims", "#262", "attavo/frontier.py", "docs/**", "--by", "bob")
    assert r.returncode == 0 and "plan version 2 written by bob" in r.stdout, r.stdout + r.stderr
    entry = claims_in(root)["262"]
    assert entry["paths"] == ["attavo/frontier.py", "docs/**"] and entry["by"] == "bob"
    assert entry["at"] == store.get(root, "plan")["at"]  # type: ignore[index]
    again = run_attavo(root, "plan", "claims", "#262", "attavo/other.py", "--by", "carol")
    assert again.returncode == 0, again.stderr
    assert claims_in(root)["262"]["paths"] == ["attavo/other.py"] and claims_in(root)["262"]["by"] == "carol"
    got = run_attavo(root, "plan", "get")
    assert json.loads(got.stdout)["claims"]["262"]["paths"] == ["attavo/other.py"], "plan get shows the claims"
    cleared = run_attavo(root, "plan", "claims", "#262", "--clear", "--by", "bob")
    assert cleared.returncode == 0 and "262" not in claims_in(root), cleared.stderr
    assert [line.split("\t")[0] for line in run_attavo(root, "plan", "log").stdout.splitlines()] == ["1", "2", "3", "4"]
    assert plan.shape_errors(store.get(root, "plan")["body"]) == []  # type: ignore[index]
    assert git(root, "status", "--porcelain") == ""


def test_plan_claims_refuses_an_unplaced_issue_by_name_and_changes_nothing(tmp_path: Path) -> None:
    """Ruled 2026-10-01 (#313): refuses an issue the plan does not place,
    by name, the way rank does."""
    root = tenant_with_plan(tmp_path)
    r = run_attavo(root, "plan", "claims", "#999", "a.py", "--by", "bob")
    assert r.returncode == 2 and "#999 is not in the plan" in r.stderr, r.stderr
    assert store.latest(root, "plan") == 1
    r = run_attavo(root, "plan", "claims", "#999", "--clear", "--by", "bob")
    assert r.returncode == 2 and "#999 is not in the plan" in r.stderr
    r = run_attavo(root, "plan", "claims", "#262", "--clear", "--by", "bob")
    assert r.returncode == 2 and "#262 has no claims to clear" in r.stderr
    assert store.latest(root, "plan") == 1


def test_plan_claims_requires_by_and_paths_that_are_repository_relative(tmp_path: Path) -> None:
    """Ruled 2026-10-01 (#313): a write names its author; paths are
    repository-relative."""
    root = tenant_with_plan(tmp_path)
    r = run_attavo(root, "plan", "claims", "#262", "a.py")
    assert r.returncode == 2 and "--by" in r.stderr
    for argv, needle in [(["#262"], "plan claims '#N' <path>..."),
                         (["#262", "a.py", "--clear"], "plan claims '#N' <path>..."),
                         (["#262", "/abs/a.py"], "repository-relative"),
                         (["#262", "../out.py"], "repository-relative"),
                         (["#262", ""], "repository-relative")]:
        r = run_attavo(root, "plan", "claims", *argv, "--by", "bob")
        assert r.returncode == 2 and needle in r.stderr, (argv, r.stderr)
    assert store.latest(root, "plan") == 1


def test_plan_claims_refuses_a_stale_base_naming_the_writer_that_landed_first(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Ruled 2026-10-01 (#313): a claims write names its base, like every
    store write. The read is made stale by a version landing after it."""
    root = tenant_with_plan(tmp_path)
    read = store.get(root, "plan")
    store.put(root, "plan", plan.move(PLAN, "#262", "backlog"), 1, "bob")
    real, reads = store.get, iter([read])
    monkeypatch.setattr(store, "get", lambda *a, **k: next(reads, None) or real(*a, **k))
    monkeypatch.chdir(root)
    assert plan.cmd_plan(["claims", "#262", "a.py", "--by", "carol"]) == 2
    err = capsys.readouterr().err
    assert "moved" in err and "version 2" in err and "bob" in err
    assert "claims" not in store.get(root, "plan")["body"]  # type: ignore[index]
