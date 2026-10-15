"""The product's datastore (attavo/store.py): versioned documents keyed to
the tenant, outside git. Written red first: on d4c0bc1 there is no
attavo.store module.

Run: python3 -m pytest tests/test_store.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from attavo import store
from helpers import git, make_tenant

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def no_store_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ATTAVO_STORE", raising=False)


def test_a_put_from_the_latest_base_writes_the_next_version_and_get_and_log_read_it(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "valid")
    env = store.put(root, "plan", {"a": 1}, 0, "alice")
    assert (env["version"], env["base"], env["by"]) == (1, 0, "alice")
    assert env["sha256"] == store.body_sha256({"a": 1}) and env["at"]
    env2 = store.put(root, "plan", {"a": 2}, 1, "bob")
    assert env2["version"] == 2
    got = store.get(root, "plan")
    assert got is not None and got["body"] == {"a": 2}
    first = store.get(root, "plan", 1)
    assert first is not None and first["body"] == {"a": 1}
    assert [(e["version"], e["by"]) for e in store.log(root, "plan")] == [(1, "alice"), (2, "bob")]
    assert "body" not in store.log(root, "plan")[0]


def test_a_stale_base_is_refused_by_name_with_the_latest_version_and_its_author(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "valid")
    store.put(root, "plan", {"a": 1}, 0, "alice")
    store.put(root, "plan", {"a": 2}, 1, "bob")
    with pytest.raises(store.StoreRefusal) as r:
        store.put(root, "plan", {"a": 3}, 1, "carol")
    assert "moved" in str(r.value) and "version 2" in str(r.value) and "bob" in str(r.value)
    assert store.latest(root, "plan") == 2


def test_two_writers_from_one_base_leave_one_version_and_one_refusal(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "valid")
    store.put(root, "plan", {"a": 0}, 0, "alice")
    script = ("import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path; from attavo import store\n"
              "try:\n    store.put(Path(sys.argv[2]), 'plan', {'w': sys.argv[3]}, 1, sys.argv[3]); print('landed')\n"
              "except store.StoreRefusal as r:\n    print('refused', r)\n")
    procs = [subprocess.Popen([sys.executable, "-c", script, str(ROOT), str(root), f"w{i}"],
                              stdout=subprocess.PIPE, text=True) for i in range(6)]
    outs = [p.communicate()[0] for p in procs]
    assert sum(o.startswith("landed") for o in outs) == 1, outs
    assert sum(o.startswith("refused") for o in outs) == 5, outs
    assert store.versions(root, "plan") == [1, 2]


def test_a_writer_that_loses_the_link_race_is_refused_naming_the_winner(tmp_path: Path,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_tenant(tmp_path, "valid")
    store.put(root, "plan", {"a": 1}, 0, "alice")
    real_latest = store.latest
    calls = {"n": 0}

    def stale_once(r: Path, name: str) -> int:
        calls["n"] += 1
        return 0 if calls["n"] == 1 else real_latest(r, name)

    monkeypatch.setattr(store, "latest", stale_once)
    with pytest.raises(store.StoreRefusal) as r:
        store.put(root, "plan", {"a": 9}, 0, "bob")
    assert "version 1" in str(r.value) and "alice" in str(r.value)
    monkeypatch.setattr(store, "latest", real_latest)
    got = store.get(root, "plan")
    assert got is not None and got["body"] == {"a": 1}
    assert not [p for p in (store.store_root(root) / "plan").iterdir() if p.name.startswith(".put-")]


def test_a_body_the_check_refuses_and_a_missing_author_write_nothing(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "valid")

    def check(body: Any) -> list[str]:
        return [] if isinstance(body, dict) and "ok" in body else ["no ok key"]

    with pytest.raises(store.StoreRefusal, match="no ok key"):
        store.put(root, "plan", {"bad": 1}, 0, "alice", check=check)
    with pytest.raises(store.StoreRefusal, match="author"):
        store.put(root, "plan", {"ok": 1}, 0, "  ", check=check)
    assert store.latest(root, "plan") == 0


def test_the_store_is_under_the_git_common_dir_shared_by_a_worktree_and_never_staged(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "valid")
    common = Path(git(root, "rev-parse", "--git-common-dir").strip())
    common = (root / common).resolve()
    assert store.store_root(root) == common / "attavo" / "store"
    wt = tmp_path / "wt"
    git(root, "worktree", "add", "-q", "-b", "lane/x", str(wt))
    assert store.store_root(wt) == store.store_root(root)
    store.put(wt, "plan", {"from": "worktree"}, 0, "alice")
    got = store.get(root, "plan")
    assert got is not None and got["body"] == {"from": "worktree"}
    assert git(root, "status", "--porcelain") == ""
    assert git(wt, "status", "--porcelain") == ""


def test_attavo_store_overrides_the_location_and_a_directory_outside_git_is_refused(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(store.StoreRefusal, match="not in a git clone"):
        store.store_root(plain)
    monkeypatch.setenv("ATTAVO_STORE", str(tmp_path / "elsewhere"))
    assert store.store_root(plain) == (tmp_path / "elsewhere").resolve()
    store.put(plain, "plan", {"x": 1}, 0, "alice")
    written = json.loads((tmp_path / "elsewhere" / "plan" / "1.json").read_text())
    assert written["body"] == {"x": 1} and written["version"] == 1


def test_a_document_name_that_is_not_a_name_is_refused(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "valid")
    for bad in ("../x", "Plan", "", "a/b"):
        with pytest.raises(store.StoreRefusal):
            store.put(root, bad, {}, 0, "alice")
    assert not os.path.exists(tmp_path / "x")


# Issue #360, finding 2 (verify-01 on #339): put stamps its own at when the caller passes none.

def test_put_stamps_its_own_at_when_the_caller_passes_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "now_iso", lambda: "2026-02-02T02:02:02Z", raising=False)
    root = make_tenant(tmp_path, "valid")
    store.put(root, "plan", {"schema": "plan/1", "epics": [], "edges": [], "policy": {}}, 0, "alice")
    doc = store.get(root, "plan")
    assert doc is not None and doc["at"] == "2026-02-02T02:02:02Z"
    store.put(root, "plan", {"schema": "plan/1", "epics": [], "edges": [], "policy": {}}, 1, "bob",
              at="2026-03-03T03:03:03Z")
    doc = store.get(root, "plan")
    assert doc is not None and doc["at"] == "2026-03-03T03:03:03Z"
