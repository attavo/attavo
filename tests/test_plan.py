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
