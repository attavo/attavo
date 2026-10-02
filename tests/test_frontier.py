"""attavo frontier: the waves, built by the product (attavo/frontier.py).
Written red first: on 7a5cbfb there is no frontier subcommand and no
attavo.frontier module.

Run: python3 -m pytest tests/test_frontier.py -q
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from attavo import frontier, store
from attavo.issues import Issue
from helpers import commit_all, git, make_tenant

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"


@pytest.fixture(autouse=True)
def no_store_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ATTAVO_STORE", raising=False)


def issue(n: int, state: str = "open", cls: str | None = "core") -> Issue:
    return {"number": n, "title": f"issue {n}", "state": state, "closed_at": None, "labels": [],
            "item_class": cls}


def plan(epics: list[tuple[str, list[int]]], edges: list[dict[str, Any]] | None = None,
         claims: dict[int, list[str]] | None = None) -> dict[str, Any]:
    """A plan body; claims maps an issue to the draft claims the plan holds for it."""
    body: dict[str, Any] = {"schema": "plan/1",
                            "epics": [{"id": e, "title": e.title(), "issues": ns} for e, ns in epics],
                            "edges": edges or [], "policy": {}}
    if claims is not None:
        body["claims"] = {str(n): {"paths": p, "by": "op", "at": "2026-10-01T09:00:00Z"} for n, p in claims.items()}
    return body


def wave_of(out: dict[str, Any], n: int) -> int | None:
    return next((k for k, w in enumerate(out["waves"]) for x in w if x["issue"] == n), None)


def why_of(out: dict[str, Any], n: int) -> list[str]:
    return next(x["why"] for w in out["waves"] for x in w if x["issue"] == n)


def test_order_is_epic_rank_then_issue_rank_and_an_unplaced_open_issue_is_backlog_last() -> None:
    body = plan([("e1", [3, 1]), ("backlog", [2])],
                claims={1: ["a/x.py"], 2: ["b/x.py"], 3: ["c/x.py"], 9: ["d/x.py"]})
    issues = [issue(1), issue(2), issue(3), issue(9)]
    out = frontier.compute(body, issues, [])
    assert [x["issue"] for x in out["waves"][1]] == [3, 1, 2, 9]
    assert out["order"] == frontier.ORDER and "Backlog, last, by number" in out["order"]
    assert next(x["epic"] for x in out["waves"][1] if x["issue"] == 9) == "backlog"


def test_an_issue_with_an_open_predecessor_waits_and_a_closed_one_does_not() -> None:
    body = plan([("e1", [1, 2, 3]), ("backlog", [])],
                [{"type": "depends", "from": 1, "to": 2}, {"type": "ruled", "from": 4, "to": 3,
                                                           "by": "op", "at": "2026-09-29"}],
                claims={1: ["a.py"], 2: ["b.py"], 3: ["c.py"], 4: ["d.py"]})
    issues = [issue(1), issue(2), issue(3), issue(4, state="closed")]
    out = frontier.compute(body, issues, [])
    assert (wave_of(out, 1), wave_of(out, 2), wave_of(out, 3)) == (1, 2, 1)
    assert why_of(out, 2) == ["waits on #1 (depends)"]
    assert why_of(out, 3) == []


def test_two_issues_whose_store_claims_overlap_never_share_a_wave_and_the_lower_ranked_one_waits() -> None:
    """Ruled 2026-10-01 (#313): the plan's claims are what the frontier
    arbitrates on, so two issues with overlapping store claims never share a wave."""
    body = plan([("e1", [1, 2]), ("backlog", [])], claims={1: ["attavo/cli.py"], 2: ["attavo/*.py"]})
    out = frontier.compute(body, [issue(1), issue(2)], [])
    assert (wave_of(out, 1), wave_of(out, 2)) == (1, 2)
    assert why_of(out, 2) == ["shares attavo/*.py, attavo/cli.py with #1 in wave 1"]


def test_strict_opportunism_a_lower_ranked_issue_never_takes_a_path_a_higher_ranked_one_wants() -> None:
    body = plan([("e1", [1, 2]), ("backlog", [3, 4])], [{"type": "depends", "from": 1, "to": 2}],
                {1: ["x/one.py"], 2: ["shared.py"], 3: ["shared.py"], 4: ["y/four.py"]})
    issues = [issue(1), issue(2), issue(3), issue(4)]
    out = frontier.compute(body, issues, [])
    assert wave_of(out, 4) == 1, "a disjoint lower-ranked issue is placed ahead of a blocked higher one"
    assert wave_of(out, 2) == 2
    assert wave_of(out, 3) == 3
    assert "yields shared.py to #2, ranked above" in why_of(out, 3)


def test_a_predecessor_never_yields_to_its_own_dependents() -> None:
    """The first live read (plan version 1, 2026-09-30): R4 (#276) is
    ranked above CN (#277) and waits on it by a ruled edge; both claim
    attavo/cli.py. CN must not yield the path to the issue that waits on
    it, or neither is ever placed and everything claiming the path behind
    R4 freezes. Transitively too: #10 waits on #11, which waits on #12."""
    body = plan([("e1", [276, 10, 11]), ("e2", [277, 12]), ("backlog", [300])],
                [{"type": "ruled", "from": 277, "to": 276, "by": "op", "at": "2026-09-26"},
                 {"type": "depends", "from": 11, "to": 10}, {"type": "depends", "from": 12, "to": 11}],
                {276: ["attavo/cli.py"], 277: ["attavo/cli.py", "docs/**"], 10: ["x/shared.py"],
                 11: ["y/b.py"], 12: ["x/shared.py"], 300: ["attavo/cli.py"]})
    issues = [issue(n) for n in (276, 277, 10, 11, 12, 300)]
    out = frontier.compute(body, issues, [])
    assert (wave_of(out, 277), wave_of(out, 276)) == (1, 2)
    assert wave_of(out, 12) == 1 and wave_of(out, 11) == 2 and wave_of(out, 10) == 3
    assert wave_of(out, 300) == 3, "a lower-ranked issue still yields to the dependent above it"
    assert out["not_placed"] == []


def test_an_issue_overlapping_a_branch_in_flight_is_not_in_wave_one_and_says_which_branch() -> None:
    body = plan([("e1", [1, 5]), ("backlog", [])], claims={1: ["src/**"], 5: ["draft/only.py"]})
    flights: list[dict[str, Any]] = [{"branch": "lane/9-other", "item": None, "declared": [], "changed": ["src/marker.txt"]},
               {"branch": "attavo/5-x", "item": "5", "declared": ["lib/**"], "changed": ["lib/a.py"]}]
    out = frontier.compute(body, [issue(1), issue(5)], flights, {"attavo/5-x": "awaiting approval"})
    zero = {x["branch"]: x for x in out["waves"][0]}
    assert zero["attavo/5-x"]["issue"] == 5 and zero["attavo/5-x"]["spec_state"] == "awaiting approval"
    assert zero["attavo/5-x"]["claims"] == ["lib/**", "lib/a.py"] and zero["attavo/5-x"]["claims_from"] == "branch", \
        "once the branch exists its own claims govern; the draft claims are not copied or consulted"
    assert zero["lane/9-other"]["claims_from"] == "branch"
    assert zero["lane/9-other"]["issue"] is None and zero["lane/9-other"]["spec_state"] == "lane"
    assert wave_of(out, 1) == 2
    assert why_of(out, 1) == ["waits on branch lane/9-other: src/**, src/marker.txt"]


def test_an_issue_with_no_store_claims_is_not_placed_and_its_dependents_say_why() -> None:
    """Ruled 2026-10-01 (#313): the not-placed reason points at the command that sets them."""
    body = plan([("e1", [1, 2]), ("backlog", [])], [{"type": "depends", "from": 1, "to": 2}], {2: ["b.py"]})
    out = frontier.compute(body, [issue(1), issue(2)], [])
    by = {x["issue"]: x["why"] for x in out["not_placed"]}
    assert by[1] == ["no claims: set them with attavo plan claims '#1' <paths>"]
    assert by[2] == ["waits on #1 (depends)"]
    assert len(out["waves"]) == 1


def test_an_issue_whose_claims_are_only_in_the_store_is_placed_and_says_where_they_came_from() -> None:
    """Ruled 2026-10-01 (#313): an issue with no spec takes its claims from the plan."""
    body = plan([("e1", [7]), ("backlog", [])], claims={7: ["src/**", "docs/x.md"]})
    out = frontier.compute(body, [issue(7)], [])
    (placed,) = out["waves"][1]
    assert placed["issue"] == 7 and placed["claims"] == ["src/**", "docs/x.md"] and placed["claims_from"] == "store"
    assert out["not_placed"] == []


def test_epic_progress_is_closed_over_total_from_the_forge() -> None:
    body = plan([("e1", [1, 2, 3]), ("backlog", [4])])
    issues = [issue(1, state="closed"), issue(2, state="closed"), issue(3), issue(4), issue(8)]
    out = frontier.compute(body, issues, [])
    assert [(e["id"], e["closed"], e["total"]) for e in out["epics"]] == [("e1", 2, 3), ("backlog", 0, 2)]
    assert not re.search(r"\d{4}-\d{2}-\d{2}", json.dumps(out)), "waves are an order, not a forecast"


def tenant_with_issues(tmp_path: Path, payload: list[dict[str, Any]]) -> Path:
    root = make_tenant(tmp_path, "valid")
    cmd = tmp_path / "issues.py"
    cmd.write_text(f"import sys\nsys.stdout.write({json.dumps(json.dumps(payload))})\n")
    with (root / "attavo.toml").open("a") as f:
        f.write(f'\n[forge]\nissues_command = "{sys.executable} {cmd}"\n')
    commit_all(root, "the issues command")
    git(root, "update-ref", "refs/remotes/origin/main", git(root, "rev-parse", "HEAD").strip())
    return root


def run_attavo(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(ATTAVO), *args], cwd=str(root),
                          capture_output=True, text=True, check=False)


def test_the_command_reads_the_store_the_issues_and_git_and_reports_the_waves(tmp_path: Path) -> None:
    payload = [{"number": 7, "title": "seven", "state": "open", "closed_at": None, "labels": [],
                "body": "x\n\nClass: core\n"},
               {"number": 8, "title": "eight", "state": "open", "closed_at": None, "labels": [],
                "body": "Class: fix\n"},
               {"number": 9, "title": "nine", "state": "open", "closed_at": None, "labels": [], "body": ""}]
    root = tenant_with_issues(tmp_path, payload)
    store.put(root, "plan", plan([("e1", [7, 8]), ("backlog", [9])],
                                 claims={7: ["draft/only.py"], 8: ["src/other.txt"], 9: ["docs/x.md"]}), 0, "alice")
    git(root, "checkout", "-q", "-b", "attavo/7-seven")
    (root / "specs" / "7").mkdir(parents=True)
    (root / "specs" / "7" / "spec.md").write_text(
        "## Problem\n\nClass: core\n\nseven.\n\n## Claims\n\n- src/**\n\n## Criteria\n\n- c.sh: exits 0\n")
    (root / "records" / "7").mkdir(parents=True)
    (root / "records" / "7" / "spec-01.json").write_text(json.dumps({"status": "pass", "item": "7"}))
    commit_all(root, "spec for 7")
    git(root, "update-ref", "refs/remotes/origin/attavo/7-seven", git(root, "rev-parse", "HEAD").strip())
    git(root, "checkout", "-q", "main")

    run = run_attavo(root, "frontier", "--json")
    assert run.returncode == 0, run.stdout + run.stderr
    out = json.loads(run.stdout)
    assert out["plan_version"] == 1 and out["default"] == "refs/remotes/origin/main"
    assert [(x["issue"], x["spec_state"]) for x in out["waves"][0]] == [(7, "awaiting approval")]
    assert [(x["issue"], x["claims"], x["claims_from"]) for x in out["waves"][1]] == [(9, ["docs/x.md"], "store")]
    assert out["waves"][0][0]["claims_from"] == "branch" and out["waves"][0][0]["claims"] == ["src/**"], \
        "#7's draft claims in the store are replaced by its spec's, nothing copied"
    assert out["waves"][2][0]["claims_from"] == "store"
    assert [(x["issue"], x["class"]) for x in out["waves"][2]] == [(8, "fix")]
    assert "yields src/other.txt" not in " ".join(out["waves"][2][0]["why"])
    assert any(line.startswith("waits on branch attavo/7-seven") for line in out["waves"][2][0]["why"])
    text = run_attavo(root, "frontier")
    assert text.returncode == 0 and "wave 0 (in flight): 1" in text.stdout and "plan version 1" in text.stdout
    assert git(root, "status", "--porcelain") == ""


def test_the_command_is_not_read_without_a_plan_or_an_issues_command_and_refuses_without_origin_head(
        tmp_path: Path) -> None:
    root = tenant_with_issues(tmp_path, [])
    r = run_attavo(root, "frontier", "--json")
    assert r.returncode == 2 and "not read: no plan in the store" in r.stderr
    bare = make_tenant(tmp_path, "valid", name="bare")
    store.put(bare, "plan", plan([("backlog", [])]), 0, "alice")
    r = run_attavo(bare, "frontier")
    assert r.returncode == 2 and "not read: no [forge].issues_command" in r.stderr
    git(bare, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
    r = run_attavo(bare, "frontier")
    assert r.returncode == 2 and "refs/remotes/origin/HEAD is not set" in r.stderr


def test_a_claims_section_in_an_issue_body_is_not_read_and_the_reason_says_how_to_set_claims(tmp_path: Path) -> None:
    """Ruled 2026-09-30 and 2026-10-01 (#313): metadata about an issue lives
    in tool-kept structured data, never as prose in the body. Plan version 8
    left ten issues unplaced for want of body claims; the body is no longer read."""
    payload = [{"number": 7, "title": "seven", "state": "open", "closed_at": None, "labels": [],
                "body": "x\n\nClass: core\n\n## Claims\n\n- src/**\n"},
               {"number": 8, "title": "eight", "state": "open", "closed_at": None, "labels": [], "body": ""}]
    root = tenant_with_issues(tmp_path, payload)
    store.put(root, "plan", plan([("e1", [7, 8]), ("backlog", [])], claims={8: ["lib/**"]}), 0, "alice")
    run = run_attavo(root, "frontier", "--json")
    assert run.returncode == 0, run.stdout + run.stderr
    out = json.loads(run.stdout)
    assert [(x["issue"], x["why"]) for x in out["not_placed"]] == [
        (7, ["no claims: set them with attavo plan claims '#7' <paths>"])]
    assert [(x["issue"], x["claims_from"]) for x in out["waves"][1]] == [(8, "store")]
    text = run_attavo(root, "frontier")
    assert "no claims: set them with attavo plan claims '#7' <paths>" in text.stdout


def test_the_claims_a_plan_claims_command_writes_are_the_ones_the_frontier_places_on(tmp_path: Path) -> None:
    """Ruled 2026-10-01 (#313): set through the command, read by the frontier, in a fixture tenant's store."""
    payload = [{"number": 7, "title": "seven", "state": "open", "closed_at": None, "labels": [], "body": ""},
               {"number": 8, "title": "eight", "state": "open", "closed_at": None, "labels": [], "body": ""}]
    root = tenant_with_issues(tmp_path, payload)
    store.put(root, "plan", plan([("e1", [7, 8]), ("backlog", [])]), 0, "alice")
    assert run_attavo(root, "frontier", "--json").returncode == 0
    for n, path in ((7, "src/a.py"), (8, "src/**")):
        r = run_attavo(root, "plan", "claims", f"#{n}", path, "--by", "bob")
        assert r.returncode == 0, r.stderr
    out = json.loads(run_attavo(root, "frontier", "--json").stdout)
    assert [[(x["issue"], x["claims_from"]) for x in w] for w in out["waves"]] == [[], [(7, "store")], [(8, "store")]]
    assert out["waves"][2][0]["why"] == ["shares src/**, src/a.py with #7 in wave 1"]
