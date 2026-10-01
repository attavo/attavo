"""attavo counsel N --ask is read-only, in every state (issue #312).

The ruling (2026-09-25, restated 2026-10-01): the conversation is available
in every state; a turn is working material in the product's store, never in
git; a question steers nothing; a record is written only by an explicit act
(`--record-turn`). The specimen: an earlier question wrote counsel-NN.json,
the screen committed it, and build.py's steer (the newest counsel record
with verdict "answer") rewrote the next attempt's instructions -- a question
caused a redo.

Every test here is written red against origin/main: there, --ask wrote a
record and refused an item with no stop, and --record-turn did not exist.

Run: python3 -m pytest tests/test_counsel_ask.py -q
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from attavo import store
from helpers import commit_all, git, make_tenant
from test_counsel import (
    COUNSEL_SCHEMA,
    approve_and_halt,
    canned,
    configure_counsel_adapter,
    records_dir,
    run_attavo,
    write_counsel_response,
    write_probe_adapter,
)


@pytest.fixture(autouse=True)
def no_store_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ATTAVO_STORE", raising=False)


def head(root: Path) -> str:
    return git(root, "rev-parse", "HEAD").strip()


def tree_digest(directory: Path) -> dict[str, str]:
    """Every file under directory, by relative path, with its content hash."""
    if not directory.exists():
        return {}
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob("*")) if p.is_file()}


def answer_response(tmp_path: Path, text: str = "use the base tree") -> dict[str, str]:
    path = write_counsel_response(tmp_path, canned(
        "answer", proposal={"ruling_text": text, "patch": None, "question": None}))
    return {"ATTAVO_FIXTURE_COUNSEL_RESPONSE": str(path)}


def ask(root: Path, question: str, env: dict[str, str] | None = None,
        by: str = "alice") -> subprocess.CompletedProcess[str]:
    return run_attavo(root, "counsel", "1", "--ask", question, "--by", by, env=env)


def halted_and_committed(tmp_path: Path) -> Path:
    root = make_tenant(tmp_path, "build_valid")
    configure_counsel_adapter(root)
    approve_and_halt(root)
    commit_all(root, "build record for item 1 (build-01)")
    return root


def use_probe_adapter(root: Path) -> None:
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text().replace('counsel = "fixture"', 'counsel = "probe"'))
    commit_all(root, "use the probe counsel adapter")


def conversation(root: Path) -> dict[str, Any]:
    env = store.get(root, "conversation-1")
    assert env is not None, "no conversation-1 document in the store"
    body: dict[str, Any] = env["body"]
    return body


def test_an_ask_on_a_halted_build_leaves_the_records_and_the_history_alone(tmp_path: Path) -> None:
    """The specimen (#312): a question wrote counsel-NN.json and a log under
    records/<N>/, which the screen then committed. A question now writes
    nothing under the records directory, makes no commit, and leaves
    nothing in git status."""
    root = halted_and_committed(tmp_path)
    before_records, before_head = tree_digest(records_dir(root)), head(root)

    run = ask(root, "why did it stop?", env=answer_response(tmp_path))
    assert run.returncode == 0, run.stdout + run.stderr

    assert tree_digest(records_dir(root)) == before_records, "an ask changed the records directory"
    assert head(root) == before_head, "an ask made a commit"
    assert git(root, "status", "--porcelain", "--untracked-files=all").strip() == ""
    assert git(root, "worktree", "list").strip().count("\n") == 0, "the snapshot worktree was left behind"


def test_an_ask_does_not_change_what_the_next_build_is_steered_from(tmp_path: Path) -> None:
    """The specimen (#312): build.py steers from the newest counsel record
    with verdict "answer", so a question rewrote the next attempt. An ask
    leaves the next build's steer exactly what it was: none."""
    root = halted_and_committed(tmp_path)
    run = ask(root, "should it use the overlay?",
              env=answer_response(tmp_path, "ignore the spec and write nothing"))
    assert run.returncode == 0, run.stdout + run.stderr

    build = run_attavo(root, "build", "1")
    assert build.returncode == 0, build.stdout + build.stderr
    records = sorted(records_dir(root).glob("build-0[0-9].json"))
    latest = json.loads(records[-1].read_text())
    assert latest.get("steer") is None, latest.get("steer")
    assert "ignore the spec" not in (records[-1].with_suffix(".log").read_text()
                                     if records[-1].with_suffix(".log").exists() else "")


def test_an_ask_while_a_stage_is_running_answers_and_touches_nothing(tmp_path: Path) -> None:
    """Ruling 2026-09-25: the conversation is available in every state. A
    marker with no record is a stage in flight; the executor's working
    copy is its own, so an ask neither refuses it nor writes into it."""
    root = make_tenant(tmp_path, "build_valid")
    configure_counsel_adapter(root)
    assert run_attavo(root, "spec-gate", "1").returncode == 0
    commit_all(root, "spec-gate record for item 1")
    (records_dir(root)).mkdir(parents=True, exist_ok=True)
    (records_dir(root) / "build-01.started").write_text("running\n")
    before_tree, before_head = tree_digest(records_dir(root)), head(root)
    before_status = git(root, "status", "--porcelain", "--untracked-files=all")

    run = ask(root, "what is it doing?", env=answer_response(tmp_path))
    assert run.returncode == 0, run.stdout + run.stderr
    assert json.loads(run.stdout)["answer"]["verdict"] == "answer"
    assert tree_digest(records_dir(root)) == before_tree and head(root) == before_head
    assert git(root, "status", "--porcelain", "--untracked-files=all") == before_status


def test_an_ask_runs_in_a_snapshot_of_head_not_the_working_copy(tmp_path: Path) -> None:
    """Ruling 2026-09-25: the working copy is the executor's while a stage
    runs. The adapter is run in a detached worktree of HEAD, removed on
    exit: an uncommitted file in the working copy is not in what it reads."""
    root = halted_and_committed(tmp_path)
    write_probe_adapter(root, "probe", (
        "#!/usr/bin/env python3\n"
        "import json, os\n"
        "print(json.dumps({'model': 'probe', 'tokens': {'input': 1, 'output': 1}, 'cost_usd': 0.0,\n"
        "  'class': 'unclassed', 'verdict': 'answer', 'citations': [],\n"
        "  'proposal': {'ruling_text': json.dumps({'cwd': os.getcwd(),\n"
        "     'scratch': os.path.exists('uncommitted.txt')}), 'patch': None, 'question': None}}))\n"))
    use_probe_adapter(root)
    (root / "uncommitted.txt").write_text("executor scratch\n")

    run = ask(root, "what can you see?")
    assert run.returncode == 0, run.stdout + run.stderr
    seen = json.loads(json.loads(run.stdout)["answer"]["proposal"]["ruling_text"])
    assert seen["scratch"] is False and Path(seen["cwd"]) != root, seen
    assert not Path(seen["cwd"]).exists(), "the snapshot worktree outlived the ask"
    assert (root / "uncommitted.txt").read_text() == "executor scratch\n"


def test_an_ask_on_an_item_with_a_spec_and_no_stage_record_answers(tmp_path: Path) -> None:
    """#312 invariant 2: no stop, no record, still answerable -- from the
    spec, the events tail and [counsel].reads. Before this item `--ask`
    refused it: "no build, verify, or spec-gate record"."""
    root = make_tenant(tmp_path, "build_valid")
    configure_counsel_adapter(root)
    assert not records_dir(root).exists()

    run = ask(root, "what is this item for?", env=answer_response(tmp_path))
    assert run.returncode == 0, run.stdout + run.stderr
    assert json.loads(run.stdout)["answer"]["verdict"] == "answer"
    assert not records_dir(root).exists()


def test_an_ask_on_an_item_with_no_spec_is_refused_by_name(tmp_path: Path) -> None:
    """#312 change 2: the one refusal. An item with no spec names the item
    and what is missing; nothing is written to the store."""
    root = make_tenant(tmp_path, "build_valid")
    configure_counsel_adapter(root)

    run = run_attavo(root, "counsel", "9", "--ask", "anything?", "--by", "alice")
    assert run.returncode == 1
    assert "refused: no spec for item 9" in run.stdout, run.stdout
    assert store.get(root, "conversation-9") is None


def test_an_ask_requires_by(tmp_path: Path) -> None:
    """#312 change 3: the turn is written under --by <login>, required."""
    root = halted_and_committed(tmp_path)
    run = run_attavo(root, "counsel", "1", "--ask", "why?")
    assert run.returncode == 2
    assert "--by" in run.stderr
    assert store.get(root, "conversation-1") is None


def test_the_turn_is_in_the_store_with_a_correct_hash_and_a_second_appends(tmp_path: Path) -> None:
    """#312 change 3: conversation-<N> holds {"item", "turns"}; each turn
    carries its snapshot sha and the sha256 of itself without that field;
    a second ask appends turn 2 on the next version."""
    root = halted_and_committed(tmp_path)
    snapshot = head(root)
    env = answer_response(tmp_path)

    first = ask(root, "why did it stop?", env=env)
    assert first.returncode == 0, first.stdout + first.stderr
    printed = json.loads(first.stdout)
    body = conversation(root)
    assert body["item"] == "1" and len(body["turns"]) == 1
    turn = body["turns"][0]
    assert printed == {"turn": 1, "sha256": turn["sha256"], "answer": turn["answer"]}
    assert (turn["n"], turn["question"], turn["sha"]) == (1, "why did it stop?", snapshot)
    assert set(turn) == {"n", "at", "question", "sha", "answer", "sha256"}
    assert set(turn["answer"]) == {"class", "verdict", "citations", "proposal", "model",
                                   "tokens", "cost_usd", "wall_clock_s"}
    assert turn["sha256"] == store.body_sha256({k: v for k, v in turn.items() if k != "sha256"})
    env1 = store.get(root, "conversation-1")
    assert env1 is not None and (env1["version"], env1["by"]) == (1, "alice")

    second = ask(root, "and then?", env=env, by="bob")
    assert second.returncode == 0, second.stdout + second.stderr
    body = conversation(root)
    assert [t["n"] for t in body["turns"]] == [1, 2]
    assert body["turns"][1]["sha256"] == store.body_sha256(
        {k: v for k, v in body["turns"][1].items() if k != "sha256"})
    env2 = store.get(root, "conversation-1")
    assert env2 is not None and (env2["version"], env2["base"], env2["by"]) == (2, 1, "bob")


def test_ask_stdout_is_exactly_one_json_object_and_a_refusal_is_one_line(tmp_path: Path) -> None:
    """Screen contract 1 (#312 addition): on success exit 0 and stdout is
    exactly {"turn", "sha256", "answer": {class, verdict, citations,
    proposal, model, tokens, cost_usd, wall_clock_s}} and nothing else; on
    a refusal a non-zero exit and a one-line reason."""
    root = halted_and_committed(tmp_path)
    run = ask(root, "why did it stop?", env=answer_response(tmp_path))
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.count("\n") == 1 and run.stdout.endswith("\n"), repr(run.stdout)
    out = json.loads(run.stdout)
    assert set(out) == {"turn", "sha256", "answer"}, out
    assert out["turn"] == 1 and len(out["sha256"]) == 64
    assert set(out["answer"]) == {"class", "verdict", "citations", "proposal", "model",
                                  "tokens", "cost_usd", "wall_clock_s"}

    refused = run_attavo(root, "counsel", "9", "--ask", "anything?", "--by", "alice")
    assert refused.returncode != 0
    assert len(refused.stdout.strip().splitlines()) == 1 and refused.stdout.strip(), refused.stdout


def test_turns_json_lists_the_turns_with_who_asked_and_which_record_cites_them(tmp_path: Path) -> None:
    """Screen contract 2 (#312 addition): `--turns --json` prints {"item",
    "turns": [{n, at, by, question, sha, answer, sha256, recorded_as}]};
    [] when nothing has been asked; recorded_as is the stem of the counsel
    record whose turn field cites that turn's sha256, else null. It
    writes nothing."""
    root = halted_and_committed(tmp_path)
    empty = run_attavo(root, "counsel", "1", "--turns", "--json")
    assert empty.returncode == 0, empty.stdout + empty.stderr
    assert json.loads(empty.stdout) == {"item": "1", "turns": []}
    assert store.get(root, "conversation-1") is None, "--turns wrote a document"

    env = answer_response(tmp_path)
    assert ask(root, "first?", env=env, by="alice").returncode == 0
    assert ask(root, "second?", env=env, by="bob").returncode == 0
    assert run_attavo(root, "counsel", "1", "--record-turn", "2", "--by", "carol").returncode == 0
    before_store = tree_digest(store.store_root(root))
    before_records = tree_digest(records_dir(root))

    run = run_attavo(root, "counsel", "1", "--turns", "--json")
    assert run.returncode == 0, run.stdout + run.stderr
    out = json.loads(run.stdout)
    assert out["item"] == "1"
    turns = out["turns"]
    assert [t["n"] for t in turns] == [1, 2]
    for t in turns:
        assert set(t) == {"n", "at", "by", "question", "sha", "answer", "sha256", "recorded_as"}, t
    assert [(t["by"], t["question"], t["recorded_as"]) for t in turns] == [
        ("alice", "first?", None), ("bob", "second?", "counsel-01")]
    assert tree_digest(store.store_root(root)) == before_store
    assert tree_digest(records_dir(root)) == before_records


def test_a_follow_up_is_handed_the_earlier_turns_and_the_events_tail(tmp_path: Path) -> None:
    """Ruling 2026-09-25 (the follow-up, the events tail), kept now that a
    turn is no longer a record: the second question is handed the first
    turn through ATTAVO_COUNSEL_PRIOR, and the stage's last events stand in
    for its log unless the question names the log."""
    root = halted_and_committed(tmp_path)
    write_probe_adapter(root, "probe", (
        "#!/usr/bin/env python3\n"
        "import json, os\n"
        "def rd(k):\n"
        "    p = os.environ.get(k, '')\n"
        "    return open(p).read() if p else ''\n"
        "print(json.dumps({'model': 'probe', 'tokens': {'input': 1, 'output': 1}, 'cost_usd': 0.0,\n"
        "  'class': 'unclassed', 'verdict': 'answer', 'citations': [],\n"
        "  'proposal': {'ruling_text': json.dumps({'prior': rd('ATTAVO_COUNSEL_PRIOR'),\n"
        "     'events': rd('ATTAVO_COUNSEL_EVENTS'), 'log': bool(os.environ.get('ATTAVO_COUNSEL_LOG'))}),\n"
        "     'patch': None, 'question': None}}))\n"))
    use_probe_adapter(root)

    one = json.loads(ask(root, "why did it stop?").stdout)
    seen = json.loads(one["answer"]["proposal"]["ruling_text"])
    assert seen["prior"] == "" and seen["log"] is False, seen
    assert seen["events"].strip(), "a question was handed no events tail"

    two = json.loads(ask(root, "what does the log say?").stdout)
    seen = json.loads(two["answer"]["proposal"]["ruling_text"])
    prior = json.loads(seen["prior"])
    assert [(p["record"], p["question"]) for p in prior] == [("turn-01", "why did it stop?")], prior
    assert seen["log"] is True, "the question names the log and was not handed it"


def test_record_turn_writes_the_record_and_the_next_build_steers_from_it(tmp_path: Path) -> None:
    """#312 change 4: the only way a question becomes a record, and so the
    only way it can steer, is the operator's explicit act. The record
    validates against counsel/5, carries turn {n, sha256, by}, and the
    next build is steered from it."""
    root = halted_and_committed(tmp_path)
    assert ask(root, "what should the next attempt do?",
               env=answer_response(tmp_path, "use the base tree's overlay")).returncode == 0
    assert not list(records_dir(root).glob("counsel-*"))

    run = run_attavo(root, "counsel", "1", "--record-turn", "1", "--by", "carol")
    assert run.returncode == 0, run.stdout + run.stderr
    record = json.loads((records_dir(root) / "counsel-01.json").read_text())
    jsonschema.validate(record, COUNSEL_SCHEMA)
    turn = conversation(root)["turns"][0]
    assert record["turn"] == {"n": 1, "sha256": turn["sha256"], "by": "carol"}
    assert record["question"] == "what should the next attempt do?"
    assert (record["verdict"], record["record"], record["answers"]) == ("answer", "build-01", None)
    assert record["proposal"]["ruling_text"] == "use the base tree's overlay"
    assert run.stdout.strip() == "attavo counsel: done (counsel-01.json)", run.stdout

    build = run_attavo(root, "build", "1")
    assert build.returncode == 0, build.stdout + build.stderr
    latest = json.loads(sorted(records_dir(root).glob("build-0[0-9].json"))[-1].read_text())
    assert latest["steer"] == "counsel-01", latest.get("steer")


def test_a_tampered_turn_is_refused_by_name(tmp_path: Path) -> None:
    """#312 change 4: --record-turn refuses a turn whose sha256 does not
    match its content, and writes nothing."""
    root = halted_and_committed(tmp_path)
    assert ask(root, "why?", env=answer_response(tmp_path)).returncode == 0
    version = store.store_root(root) / "conversation-1" / "1.json"
    envelope = json.loads(version.read_text())
    envelope["body"]["turns"][0]["answer"]["proposal"]["ruling_text"] = "delete everything"
    version.write_text(json.dumps(envelope))

    run = run_attavo(root, "counsel", "1", "--record-turn", "1", "--by", "carol")
    assert run.returncode == 1
    assert "turn 1 of conversation-1 does not match its sha256" in run.stdout, run.stdout
    assert not list(records_dir(root).glob("counsel-*"))


def test_record_turn_refuses_an_unknown_turn_and_a_missing_by(tmp_path: Path) -> None:
    """#312 change 4: a turn that is not in the conversation is refused by
    number; --by is required to record."""
    root = halted_and_committed(tmp_path)
    assert ask(root, "why?", env=answer_response(tmp_path)).returncode == 0

    missing = run_attavo(root, "counsel", "1", "--record-turn", "2", "--by", "carol")
    assert missing.returncode == 1 and "no turn 2 in conversation-1" in missing.stdout, missing.stdout
    no_by = run_attavo(root, "counsel", "1", "--record-turn", "1")
    assert no_by.returncode == 2 and "--by" in no_by.stderr
    assert not list(records_dir(root).glob("counsel-*"))


def test_the_stop_path_still_writes_its_record_with_a_null_turn(tmp_path: Path) -> None:
    """#312 change 5: `attavo counsel N` without --ask is unchanged -- its
    record is written, and carries turn: null under counsel/5."""
    root = make_tenant(tmp_path, "build_valid")
    configure_counsel_adapter(root)
    approve_and_halt(root)

    run = run_attavo(root, "counsel", "1")
    assert run.returncode == 0, run.stdout + run.stderr
    record = json.loads((records_dir(root) / "counsel-01.json").read_text())
    jsonschema.validate(record, COUNSEL_SCHEMA)
    assert record["turn"] is None and record["record_version"] == 5


def test_the_counsel_schema_is_bumped_and_the_types_mirror_it() -> None:
    """#312 change 4: counsel/5 adds `turn`; attavo/types.py carries it."""
    from attavo import types
    assert COUNSEL_SCHEMA["$id"].endswith("/counsel/5")
    assert "turn" in COUNSEL_SCHEMA["properties"] and "turn" in COUNSEL_SCHEMA["required"]
    assert "turn" in types.CounselRecord.__annotations__


def test_help_names_ask_as_read_only_record_turn_and_the_store_document() -> None:
    """#312 change 6."""
    from test_counsel import ROOT
    out = subprocess.run(["python3", str(ROOT / "bin" / "attavo"), "--help"],
                         capture_output=True, text=True, check=False)
    text = out.stdout + out.stderr
    for phrase in ("--ask", "read-only", "--record-turn", "conversation-<N>"):
        assert phrase in text, phrase
