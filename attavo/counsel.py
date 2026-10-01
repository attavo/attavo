from __future__ import annotations

import contextlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, cast

from attavo import store
from attavo.build import (
    adapter_reports,
    gate_checkout,
    merge_adapter_config,
    resolve_adapter,
    split_adapter_stdout,
)
from attavo.common import MANIFEST, PRODUCT_ROOT, derive_item_id, git, now_iso, read_manifest
from attavo.records import latest_attempt_path, latest_record, parse_attempts, write_record
from attavo.types import SpecRecord, StageRecord
from attavo.verify import diff_since_approval, read_forge_checks
from attavo.workcopy import item_events_file

# --- counsel: a stop is read inside the product first, before a person
# (issue #100). It never acts: it proposes a ruling, a patch, or names the
# one-line question a person must answer. The stop path writes
# paths.records/<N>/counsel-NN.json and commits nothing. A question (--ask)
# writes no record, no log and no commit: it runs in a detached snapshot
# of HEAD and its turn goes to the store document conversation-<N>, which
# --record-turn turns into a record, on purpose (issue #312). ---

COUNSEL_STAGES = ("build", "verify")

# How many of the stage's last event lines a follow-up is handed in place
# of the whole log.
EVENTS_TAIL_LINES = 200


def latest_non_pass_stage_record(records_dir: Path) -> tuple[str | None, StageRecord | None, Path | None]:
    """(stage, record, path) for the item's most recent non-pass build or
    verify record, by its own "at" timestamp -- or, when neither stage
    carries one, the item's latest refused spec-gate record (issue #133):
    a refused gate is the same shape of stop, earlier, and it is read only
    once a build or verify record exists to counsel first, since either
    one already implies the gate that produced it passed. (None, None,
    None) when none of the three exists. "Most recent" rather than
    "build, else verify" so a stop on the stage that ran later (typically
    verify, bound to a build that already passed) is the one counseled."""
    candidates: list[tuple[str, StageRecord, Path]] = []
    for stage in COUNSEL_STAGES:
        path = latest_attempt_path(records_dir, stage)
        if path is None:
            continue
        # A path exists, so the record read from the same files does too.
        rec = cast(StageRecord, latest_record(records_dir, stage))
        if rec.get("status") == "pass":
            continue
        candidates.append((stage, rec, path))
    if candidates:
        candidates.sort(key=lambda c: c[1].get("at") or "")
        return candidates[-1]
    spec_path = latest_attempt_path(records_dir, "spec")
    if spec_path is not None:
        spec_rec = cast(SpecRecord, latest_record(records_dir, "spec"))
        if spec_rec.get("status") == "refused":
            return "spec", spec_rec, spec_path
    return None, None, None


def latest_stage_record(records_dir: Path) -> tuple[str | None, StageRecord | None, Path | None]:
    """(stage, record, path) for the item's most recent build or verify
    record regardless of status, by its own "at" timestamp -- or, when
    neither stage carries one, the item's latest spec-gate record,
    regardless of status. The evidence set `attavo counsel --ask` reads
    over (issue #175): a question is not bound to a stop, so the pass
    filter latest_non_pass_stage_record applies does not apply here.
    (None, None, None) when the item carries no record of any of the
    three stages yet."""
    candidates: list[tuple[str, StageRecord, Path]] = []
    for stage in COUNSEL_STAGES:
        path = latest_attempt_path(records_dir, stage)
        if path is None:
            continue
        candidates.append((stage, cast(StageRecord, latest_record(records_dir, stage)), path))
    if candidates:
        candidates.sort(key=lambda c: c[1].get("at") or "")
        return candidates[-1]
    spec_path = latest_attempt_path(records_dir, "spec")
    if spec_path is not None:
        return "spec", cast(SpecRecord, latest_record(records_dir, "spec")), spec_path
    return None, None, None


def counsel_forge_evidence(root: Path, forge_cfg: dict[str, Any], sha: str | None, tmp: Path
                          ) -> tuple[dict[str, Any], Path]:
    """(forge, checks_path) -- issue #175. forge is the summary counsel's
    own record carries: {"read", "reason", "checks"}, "checks" a list of
    {"name", "conclusion"} pairs. checks_path is always written, under
    tmp, as ATTAVO_COUNSEL_CHECKS: a JSON {"read", "reason", "checks"}
    envelope whose own "checks" carry the richer {"name", "conclusion",
    "run_url", "log"} shape the adapter reads -- "log" naming a sibling
    file under tmp with that check's own failed-step log, fetched through
    [forge].check_log_command for every check not "success". Absent
    [forge].checks_command, absent [forge].check_log_command, or a
    counseled record with no sha of its own: read is false and reason
    names exactly which is missing -- never a checks list a reader could
    mistake for "all clear" (an empty list from a real, successful read
    is not this; that is read: true, checks: [])."""
    checks_command = forge_cfg.get("checks_command")
    check_log_command = forge_cfg.get("check_log_command")
    checks_path = tmp / "checks.json"

    if not checks_command:
        reason = "not read: no [forge].checks_command"
    elif not check_log_command:
        reason = "not read: no [forge].check_log_command"
    elif not sha:
        reason = "not read: the counseled record carries no sha"
    else:
        reason = None

    if reason is not None:
        envelope = {"read": False, "reason": reason, "checks": []}
        checks_path.write_text(json.dumps(envelope, indent=2) + "\n")
        return envelope, checks_path

    # reason is None only when checks_command, check_log_command, and sha are all set.
    checks_command, sha = cast(str, checks_command), cast(str, sha)
    raw_checks, _ok = read_forge_checks(root, checks_command, sha)
    payload_checks: list[dict[str, Any]] = []
    record_checks: list[dict[str, Any]] = []
    for i, c in enumerate(raw_checks["conclusions"]):
        name, conclusion = c["name"], c["conclusion"]
        log_path = None
        if conclusion != "success":
            log_run = subprocess.run(
                f"{check_log_command} {shlex.quote(sha)} {shlex.quote(name)}",
                shell=True, cwd=str(root), capture_output=True, text=True, check=False)
            if log_run.returncode == 0 and log_run.stdout.strip():
                log_file = tmp / f"check-{i:02d}.log"
                log_file.write_text(log_run.stdout)
                log_path = str(log_file)
        payload_checks.append({"name": name, "conclusion": conclusion,
                               "run_url": None, "log": log_path})
        record_checks.append({"name": name, "conclusion": conclusion})

    checks_path.write_text(json.dumps(
        {"read": True, "reason": None, "checks": payload_checks}, indent=2) + "\n")
    return {"read": True, "reason": None, "checks": record_checks}, checks_path


def counsel_graph_text(root: Path, m: dict[str, Any], item_id: str, sha: str | None,
                       checks_command: str | None) -> str:
    """A text report of the item's own place on the branch (issue #175):
    the default branch's first-parent log since the item's own branch
    point, this branch's own commits (sha, author, subject), the markers
    and records present at the counseled sha, and the check runs on the
    last three shas on this branch -- so a stop whose cause is "the
    checks are on the record commit, not the code commit" is readable
    without a terminal. Best-effort: a git read that fails (a shallow
    checkout, an unset refs/remotes/origin/HEAD) is noted in the text
    rather than raised, since the graph is evidence, not a gate."""
    records_rel = m["paths"]["records"]
    parts: list[str] = []

    symref = subprocess.run(
        ["git", "-C", str(root), "symbolic-ref", "-q", "refs/remotes/origin/HEAD"],
        capture_output=True, text=True, check=False)
    default_ref = symref.stdout.strip() if symref.returncode == 0 else None

    if default_ref:
        try:
            branch_point = git(root, "merge-base", default_ref, "HEAD").strip()
            parts.append(
                "## the default branch, first-parent, since this item's branch point\n\n" +
                (git(root, "log", "--first-parent", "--oneline",
                    f"{branch_point}..{default_ref}").strip() or "(nothing new)"))
            parts.append(
                "## this branch's own commits since the branch point\n\n" +
                (git(root, "log", "--first-parent", "--format=%H %an %s",
                    f"{branch_point}..HEAD").strip() or "(no commits yet)"))
        except RuntimeError as exc:
            parts.append(f"## the default branch\n\n(could not be read: {exc})")
    else:
        parts.append("## the default branch\n\n(refs/remotes/origin/HEAD is not set)")

    if sha:
        try:
            tree = git(root, "ls-tree", "-r", "--name-only", sha, "--",
                      f"{records_rel.rstrip('/')}/{item_id}/").strip()
            parts.append(f"## markers and records present at {sha}\n\n" + (tree or "(none)"))
        except RuntimeError as exc:
            parts.append(f"## markers and records present at {sha}\n\n(could not be read: {exc})")

    if checks_command:
        try:
            recent = git(root, "log", "--first-parent", "-3", "--format=%H", "HEAD").split()
        except RuntimeError:
            recent = []
        lines = []
        for s in recent:
            checks, _ = read_forge_checks(root, checks_command, s)
            line = ", ".join(f"{c['name']}={c['conclusion']}" for c in checks["conclusions"])
            lines.append(f"{s}: {line or '(no checks)'}")
        if lines:
            parts.append("## check runs for the last three shas on this branch\n\n" +
                         "\n".join(lines))

    return "\n\n".join(parts)


def turn_hash(turn: dict[str, Any]) -> str:
    """The sha256 a turn carries: its own content without this field."""
    return store.body_sha256({k: v for k, v in turn.items() if k != "sha256"})


def conversation_name(item_id: str) -> str:
    return f"conversation-{item_id}"


def read_conversation(root: Path, item_id: str) -> tuple[int, list[dict[str, Any]]]:
    """(version, turns) of the item's conversation; (0, []) when none."""
    env = store.get(root, conversation_name(item_id))
    if env is None:
        return 0, []
    return int(env["version"]), list(env["body"]["turns"])


def cmd_turns(root: Path, item_id: str, m: dict[str, Any]) -> int:
    """The conversation as JSON, each turn with the counsel record, if any,
    whose turn field cites its sha256. Writes nothing."""
    _, turns = read_conversation(root, item_id)
    cited: dict[str, str] = {}
    item_records_dir = root / m["paths"]["records"] / item_id
    for n, rec in sorted(parse_attempts(item_records_dir, "counsel").items()):
        turn = rec.get("turn")
        if turn:
            cited[turn["sha256"]] = f"counsel-{n:02d}"
    out = [{"n": t["n"], "at": t["at"], "by": t["by"], "question": t["question"],
            "record": t["record"], "sha": t["sha"], "answer": t["answer"], "sha256": t["sha256"],
            "recorded_as": cited.get(t["sha256"])} for t in turns]
    print(json.dumps({"item": item_id, "turns": out}))
    return 0


def cmd_record_turn(root: Path, item_id: str, m: dict[str, Any], k: int, by: str) -> int:
    """Write counsel-NN.json from turn k: the operator's explicit act, the
    only way a question becomes a record and so the only way it steers."""
    name = conversation_name(item_id)
    turns = read_conversation(root, item_id)[1]
    turn = next((t for t in turns if t["n"] == k), None)
    if turn is None:
        print(f"attavo counsel: refused: no turn {k} in {name}")
        return 1
    if turn.get("sha256") != turn_hash(turn):
        print(f"attavo counsel: refused: turn {k} of {name} does not match its sha256")
        return 1
    # The turn's own record, never the latest stage record at the time of
    # recording: a turn about build-01 recorded after build-02 stopped would
    # otherwise steer build-03 with an answer about build-01.
    read_stem = turn.get("record")
    if not read_stem:
        print(f"attavo counsel: refused: turn {k} was asked with no stage record; "
              "there is nothing to record it against")
        return 1
    item_records_dir = root / m["paths"]["records"] / item_id
    stage = read_stem.split("-")[0]
    executor = m.get("executor") or {}
    adapter_name = executor.get("counsel") or executor.get("verify")
    adapter_path = resolve_adapter(root, adapter_name) if adapter_name else None
    answer = turn["answer"]
    earlier = [f"counsel-{n:02d}" for n, rec in sorted(parse_attempts(item_records_dir, "counsel").items())
               if rec.get("record") == read_stem]
    record = {
        "sha": turn["sha"],
        "record_stage": stage,
        "record": read_stem,
        "at": now_iso(),
        "adapter": adapter_name,
        "adapter_config": merge_adapter_config(m, adapter_name, "counsel") if adapter_name else {},
        "adapter_version": None,
        "model": answer.get("model"),
        "model_version": None,
        "reports": adapter_reports(adapter_path) if adapter_path else [],
        "tokens": answer.get("tokens") or {"input": 0, "output": 0},
        "cost_usd": answer.get("cost_usd"),
        "wall_clock_s": answer.get("wall_clock_s") or 0,
        "status": "done",
        "class": answer.get("class"),
        "verdict": answer.get("verdict"),
        "citations": answer.get("citations") or [],
        "proposal": {"ruling_text": None, "patch": None, "question": None, "issue": None,
                     **(answer.get("proposal") or {})},
        "forge": {"read": False, "reason": f"not read: recorded from turn {k} of {name}",
                  "checks": []},
        "question": turn["question"],
        "answers": earlier[-1] if earlier else None,
        "turn": {"n": k, "sha256": turn["sha256"], "by": by},
    }
    out_path = write_record(item_records_dir, "counsel", record, m, item_id)
    print(f"attavo counsel: done ({out_path.name})")
    return 0


def cmd_counsel(argv: list[str]) -> int:
    root = Path(".").resolve()
    ask: str | None = None
    by: str | None = None
    record_turn: str | None = None
    turns_mode = False
    json_flag = False
    positional: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "--ask":
            ask = argv[i + 1] if i + 1 < len(argv) else None
            i += 2
        elif argv[i] == "--by":
            by = argv[i + 1] if i + 1 < len(argv) else None
            i += 2
        elif argv[i] == "--record-turn":
            record_turn = argv[i + 1] if i + 1 < len(argv) else ""
            i += 2
        elif argv[i] == "--turns":
            turns_mode = True
            i += 1
        elif argv[i] == "--json":
            json_flag = True
            i += 1
        else:
            positional.append(argv[i])
            i += 1
    item_id = positional[0] if positional else derive_item_id(root)
    if not item_id:
        print("attavo counsel: cannot derive item id; pass N or run on an attavo/N-* branch",
              file=sys.stderr)
        return 2
    if json_flag and not turns_mode:
        print("attavo counsel: --json is valid only with --turns", file=sys.stderr)
        return 2
    if ask is not None and not ask.strip():
        print("attavo counsel: --ask requires non-empty text", file=sys.stderr)
        return 2
    if ask is not None and not (by or "").strip():
        print("attavo counsel: --ask requires --by <login>", file=sys.stderr)
        return 2
    if record_turn is not None and not (by or "").strip():
        print("attavo counsel: --record-turn requires --by <login>", file=sys.stderr)
        return 2
    if record_turn is not None and not record_turn.isdigit():
        print("attavo counsel: --record-turn requires a turn number", file=sys.stderr)
        return 2
    m = read_manifest(root)
    if m is None:
        print(f"attavo counsel: no {MANIFEST} at {root}", file=sys.stderr)
        return 2
    if turns_mode:
        return cmd_turns(root, item_id, m)
    if record_turn is not None:
        return cmd_record_turn(root, item_id, m, int(record_turn), cast(str, by))

    # issue #312: a question reads a detached snapshot of HEAD, never the
    # working copy, which is the executor's while a stage runs.
    with contextlib.ExitStack() as stack:
        ev = stack.enter_context(gate_checkout(root)) if ask is not None else root
        return _counsel(root, ev, m, item_id, ask, by)


def _counsel(root: Path, ev: Path, m: dict[str, Any], item_id: str, ask: str | None,
             by: str | None) -> int:
    """The reading itself. root is the checkout it was called in; ev is the
    tree it reads from -- root for the stop path, a snapshot for --ask."""
    specs_rel = m["paths"]["specs"]
    records_rel = m["paths"]["records"]
    item_records_dir = ev / records_rel / item_id

    stage: str | None
    stop_record: Any
    record_path: Path | None
    if ask is not None:
        # issue #312: a question is answerable in every state; only an item
        # with no spec is refused.
        if not (ev / specs_rel / item_id / "spec.md").is_file():
            print(f"attavo counsel: refused: no spec for item {item_id}")
            return 1
        stage, stop_record, record_path = latest_stage_record(item_records_dir)
    else:
        stage, stop_record, record_path = latest_non_pass_stage_record(item_records_dir)
        if stop_record is None:
            print(f"attavo counsel: refused: no non-pass build, verify, or spec-gate record "
                  f"for item {item_id}")
            return 1

    executor = m.get("executor") or {}
    adapter_name = executor.get("counsel") or executor.get("verify")
    if not adapter_name:
        print("attavo counsel: refused: no [executor].counsel or [executor].verify "
              "in the manifest")
        return 1
    adapter_path = resolve_adapter(ev, adapter_name)
    if adapter_path is None:
        print(f"attavo counsel: refused: no adapter named {adapter_name!r}")
        return 1

    adapter_config = merge_adapter_config(m, adapter_name, "counsel")
    reports = adapter_reports(adapter_path)
    spec_dir = ev / specs_rel / item_id
    log_path = record_path.with_suffix(".log") if record_path else None

    # The corpus counsel reads before it answers ([counsel].reads): method
    # rules, briefs, rulings, in the repository under paths the manifest
    # names. A path that does not exist is refused, not skipped -- a
    # configured corpus nobody can read is prose.
    reads_rel = [str(x) for x in ((m.get("counsel") or {}).get("reads") or [])]
    reads_missing = [r for r in reads_rel if not (ev / r).exists()]
    if reads_missing:
        print(f"attavo counsel: refused: [counsel].reads names {', '.join(reads_missing)}, "
              "which does not exist")
        return 1
    reads_abs = [str((ev / r).resolve()) for r in reads_rel]

    # Counsel's own earlier readings of this item, oldest first: the first
    # input to a follow-up, so a question about a stop counsel has already
    # read is answered from that reading, not re-derived (ruling 1,
    # 2026-09-25). `answers` names the newest prior reading of the same
    # record, or null for a first reading. A follow-up is also handed the
    # earlier turns of the conversation (issue #312), after the records.
    prior_records = parse_attempts(item_records_dir, "counsel")
    prior = [{"record": f"counsel-{n:02d}", "answers": rec.get("answers"),
              "record_stage": rec.get("record_stage"), "reads": rec.get("record"),
              "at": rec.get("at"), "status": rec.get("status"), "class": rec.get("class"),
              "verdict": rec.get("verdict"), "question": rec.get("question"),
              "ruling_text": (rec.get("proposal") or {}).get("ruling_text"),
              "person_question": (rec.get("proposal") or {}).get("question"),
              "citations": rec.get("citations") or []}
             for n, rec in sorted(prior_records.items())]
    answers = None
    if record_path is not None:
        same_stop = [p["record"] for p in prior if p["reads"] == record_path.stem]
        answers = same_stop[-1] if same_stop else None
    if ask is not None:
        for t in read_conversation(root, item_id)[1]:
            a = t["answer"]
            prior.append({"record": f"turn-{t['n']:02d}", "answers": None, "record_stage": None,
                          "reads": None, "at": t["at"], "status": "done", "class": a.get("class"),
                          "verdict": a.get("verdict"), "question": t["question"],
                          "ruling_text": (a.get("proposal") or {}).get("ruling_text"),
                          "person_question": (a.get("proposal") or {}).get("question"),
                          "citations": a.get("citations") or []})

    # The whole log is handed on the stop path, and on --ask only when the
    # question is about the log; otherwise the stage's last events stand
    # in for it (ruling: the record, the signature and the events tail,
    # not 4.8 MB of transcript, unless asked).
    hand_log = (log_path is not None and log_path.is_file()
                and (ask is None or "log" in ask.lower()))
    events_file = item_events_file(root, item_id, record_path.stem) if record_path else None

    diff_text, diff_refusal = diff_since_approval(
        ev, stop_record.get("approval_sha") if stop_record else None)
    if diff_refusal:
        print(f"attavo counsel: refused: {diff_refusal}")
        return 1

    forge_cfg = m.get("forge") or {}
    stop_sha = stop_record.get("sha") if stop_record else None

    started = time.monotonic()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        diff_path = tmp_path / "diff.patch"
        diff_path.write_text(diff_text)

        forge, checks_path = counsel_forge_evidence(ev, forge_cfg, stop_sha, tmp_path)

        graph_path = tmp_path / "graph.txt"
        graph_path.write_text(counsel_graph_text(
            ev, m, item_id, stop_sha, forge_cfg.get("checks_command")))

        prior_path = tmp_path / "prior.json"
        prior_path.write_text(json.dumps(prior, indent=2) + "\n")

        events_path = tmp_path / "events.jsonl"
        has_events = events_file is not None and events_file.is_file()
        if events_file is not None and has_events:
            events_path.write_text("".join(events_file.read_text().splitlines(keepends=True)[-EVENTS_TAIL_LINES:]))
        else:
            events_path.write_text("")

        env = dict(os.environ)
        env["ATTAVO_SPEC_DIR"] = str(spec_dir)
        env["ATTAVO_RECORDS_DIR"] = str(item_records_dir)
        env["ATTAVO_RULINGS"] = str(ev / m["paths"]["rulings"])
        env["ATTAVO_STAGE"] = "counsel"
        env["ATTAVO_COUNSEL_RECORD"] = str(record_path) if record_path else ""
        env["ATTAVO_COUNSEL_LOG"] = str(log_path) if hand_log else ""
        env["ATTAVO_COUNSEL_EVENTS"] = str(events_path) if has_events else ""
        env["ATTAVO_COUNSEL_PRIOR"] = str(prior_path) if prior else ""
        env["ATTAVO_COUNSEL_READS"] = "\n".join(reads_abs)
        env["ATTAVO_COUNSEL_DIFF"] = str(diff_path)
        env["ATTAVO_COUNSEL_CHECKS"] = str(checks_path)
        env["ATTAVO_COUNSEL_GRAPH"] = str(graph_path)
        env["ATTAVO_MANIFEST"] = str(ev / MANIFEST)
        env["ATTAVO_PRODUCT_ROOT"] = str(PRODUCT_ROOT)
        env["ATTAVO_ADAPTER_CONFIG"] = json.dumps(adapter_config)
        if ask is not None:
            env["ATTAVO_COUNSEL_QUESTION"] = ask

        run = subprocess.run([str(adapter_path)], cwd=str(ev), env=env,
                             capture_output=True, text=True, check=False)
        transcript, payload = split_adapter_stdout(run.stdout)
        if run.returncode != 0:
            detail = f"[adapter exited {run.returncode}]"
            if run.stderr.strip():
                detail += "\n" + run.stderr.rstrip("\n")
            transcript = (transcript + "\n" if transcript else "") + detail

    status_out = "failed"
    model = model_version = adapter_version_out = None
    tokens = {"input": 0, "output": 0}
    cost_usd = None
    cls = verdict = None
    citations: list[Any] = []
    proposal = {"ruling_text": None, "patch": None, "question": None, "issue": None}

    if (run.returncode == 0 and payload is not None
            and payload.get("verdict") in ("answer", "patch", "person")):
        status_out = "done"
        model = payload.get("model")
        model_version = payload.get("model_version")
        adapter_version_out = payload.get("adapter_version")
        tok = payload.get("tokens") or {}
        tokens = {"input": tok.get("input", 0) or 0, "output": tok.get("output", 0) or 0}
        cost_usd = payload.get("cost_usd")
        cls = payload.get("class")
        verdict = payload.get("verdict")
        citations = payload.get("citations") or []
        proposal = {**proposal, **(payload.get("proposal") or {})}

    wall_clock_s = round(time.monotonic() - started, 3)

    if ask is not None:
        if status_out != "done":
            print("attavo counsel: failed: the adapter returned no usable answer")
            return 1
        return _record_turn_in_store(root, item_id, ask, cast(str, by),
                                     record_path.stem if record_path else None, {
            "class": cls, "verdict": verdict, "citations": citations, "proposal": proposal,
            "model": model, "tokens": tokens, "cost_usd": cost_usd, "wall_clock_s": wall_clock_s})

    assert stage is not None and record_path is not None
    record = {
        "sha": git(root, "rev-parse", "HEAD").strip(),
        "record_stage": stage,
        "record": record_path.stem,
        "at": now_iso(),
        "adapter": adapter_name,
        "adapter_config": adapter_config,
        "adapter_version": adapter_version_out,
        "model": model,
        "model_version": model_version,
        "reports": reports,
        "tokens": tokens,
        "cost_usd": cost_usd,
        "wall_clock_s": wall_clock_s,
        "status": status_out,
        "class": cls,
        "verdict": verdict,
        "citations": citations,
        "proposal": proposal,
        "forge": forge,
        "question": None,
        "answers": answers,
        "turn": None,
    }
    record_out_path = write_record(item_records_dir, "counsel", record, m, item_id, log=transcript)

    print(f"attavo counsel: {status_out} ({record_out_path.name})")
    return 0 if status_out == "done" else 1


def _record_turn_in_store(root: Path, item_id: str, question: str, by: str,
                          record: str | None, answer: dict[str, Any]) -> int:
    """Append one turn to conversation-<N>: written against the version it
    read, retried once when another writer landed first. Prints the turn's
    number, hash and answer as one JSON object, for the screen."""
    name = conversation_name(item_id)
    snapshot = git(root, "rev-parse", "HEAD").strip()
    refusal = ""
    for _ in range(2):
        version, turns = read_conversation(root, item_id)
        turn: dict[str, Any] = {"n": len(turns) + 1, "at": now_iso(), "by": by,
                                "question": question, "record": record, "sha": snapshot,
                                "answer": answer}
        turn["sha256"] = turn_hash(turn)
        try:
            store.put(root, name, {"item": item_id, "turns": [*turns, turn]}, version, by)
        except store.StoreRefusal as exc:
            refusal = str(exc)
            continue
        print(json.dumps({"turn": turn["n"], "sha256": turn["sha256"], "answer": answer}))
        return 0
    print(f"attavo counsel: refused: {refusal}")
    return 1
