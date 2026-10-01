from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, cast

from attavo.build import (
    adapter_reports,
    merge_adapter_config,
    resolve_adapter,
    split_adapter_stdout,
)
from attavo.common import MANIFEST, PRODUCT_ROOT, derive_item_id, git, now_iso, read_manifest
from attavo.events import stage_events_dir
from attavo.records import latest_attempt_path, latest_record, parse_attempts, write_record
from attavo.types import SpecRecord, StageRecord
from attavo.verify import diff_since_approval, read_forge_checks

# --- counsel: a stop is read inside the product first, before a person
# (issue #100). Read-only -- it commits nothing and writes nothing outside
# paths.records/<N>/ -- and it never acts: it proposes a ruling, a patch,
# or names the one-line question a person must answer. ---

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


def cmd_counsel(argv: list[str]) -> int:
    root = Path(".").resolve()
    ask: str | None = None
    positional: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "--ask":
            ask = argv[i + 1] if i + 1 < len(argv) else None
            i += 2
        else:
            positional.append(argv[i])
            i += 1
    item_id = positional[0] if positional else derive_item_id(root)
    if not item_id:
        print("attavo counsel: cannot derive item id; pass N or run on an attavo/N-* branch",
              file=sys.stderr)
        return 2
    if ask is not None and not ask.strip():
        print("attavo counsel: --ask requires non-empty text", file=sys.stderr)
        return 2
    m = read_manifest(root)
    if m is None:
        print(f"attavo counsel: no {MANIFEST} at {root}", file=sys.stderr)
        return 2

    specs_rel = m["paths"]["specs"]
    records_rel = m["paths"]["records"]
    item_records_dir = root / records_rel / item_id

    # issue #175: --ask reads the item's latest records regardless of
    # status -- a question is not bound to a stop -- everything else
    # about the evidence set and the write is shared with the stop path.
    if ask is not None:
        stage, stop_record, record_path = latest_stage_record(item_records_dir)
        no_record_msg = f"no build, verify, or spec-gate record for item {item_id}"
    else:
        stage, stop_record, record_path = latest_non_pass_stage_record(item_records_dir)
        no_record_msg = f"no non-pass build, verify, or spec-gate record for item {item_id}"
    if stop_record is None:
        print(f"attavo counsel: refused: {no_record_msg}")
        return 1
    # latest_*_stage_record returns all three of (stage, record, path) or none.
    stage, record_path = cast(str, stage), cast(Path, record_path)

    executor = m.get("executor") or {}
    adapter_name = executor.get("counsel") or executor.get("verify")
    if not adapter_name:
        print("attavo counsel: refused: no [executor].counsel or [executor].verify "
              "in the manifest")
        return 1
    adapter_path = resolve_adapter(root, adapter_name)
    if adapter_path is None:
        print(f"attavo counsel: refused: no adapter named {adapter_name!r}")
        return 1

    adapter_config = merge_adapter_config(m, adapter_name, "counsel")
    reports = adapter_reports(adapter_path)
    spec_dir = root / specs_rel / item_id
    log_path = record_path.with_suffix(".log")

    # The corpus counsel reads before it answers ([counsel].reads): method
    # rules, briefs, rulings, in the repository under paths the manifest
    # names. A path that does not exist is refused, not skipped -- a
    # configured corpus nobody can read is prose.
    reads_rel = [str(x) for x in ((m.get("counsel") or {}).get("reads") or [])]
    reads_missing = [r for r in reads_rel if not (root / r).exists()]
    if reads_missing:
        print(f"attavo counsel: refused: [counsel].reads names {', '.join(reads_missing)}, "
              "which does not exist")
        return 1
    reads_abs = [str((root / r).resolve()) for r in reads_rel]

    # Counsel's own earlier readings of this item, oldest first: the first
    # input to a follow-up, so a question about a stop counsel has already
    # read is answered from that reading, not re-derived (ruling 1,
    # 2026-09-25). `answers` names the newest prior reading of the same
    # record, or null for a first reading.
    prior_records = parse_attempts(item_records_dir, "counsel")
    prior = [{"record": f"counsel-{n:02d}", "answers": rec.get("answers"),
              "record_stage": rec.get("record_stage"), "reads": rec.get("record"),
              "at": rec.get("at"), "status": rec.get("status"), "class": rec.get("class"),
              "verdict": rec.get("verdict"), "question": rec.get("question"),
              "ruling_text": (rec.get("proposal") or {}).get("ruling_text"),
              "person_question": (rec.get("proposal") or {}).get("question"),
              "citations": rec.get("citations") or []}
             for n, rec in sorted(prior_records.items())]
    same_stop = [p["record"] for p in prior if p["reads"] == record_path.stem]
    answers = same_stop[-1] if same_stop else None

    # The whole log is handed on the stop path, and on --ask only when the
    # question is about the log; otherwise the stage's last events stand
    # in for it (ruling: the record, the signature and the events tail,
    # not 4.8 MB of transcript, unless asked).
    hand_log = log_path.is_file() and (ask is None or "log" in ask.lower())
    events_file = stage_events_dir(root) / f"{record_path.stem}.events.jsonl"

    diff_text, diff_refusal = diff_since_approval(root, stop_record.get("approval_sha"))
    if diff_refusal:
        print(f"attavo counsel: refused: {diff_refusal}")
        return 1

    forge_cfg = m.get("forge") or {}
    stop_sha = stop_record.get("sha")

    started = time.monotonic()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        diff_path = tmp_path / "diff.patch"
        diff_path.write_text(diff_text)

        forge, checks_path = counsel_forge_evidence(root, forge_cfg, stop_sha, tmp_path)

        graph_path = tmp_path / "graph.txt"
        graph_path.write_text(counsel_graph_text(
            root, m, item_id, stop_sha, forge_cfg.get("checks_command")))

        prior_path = tmp_path / "prior.json"
        prior_path.write_text(json.dumps(prior, indent=2) + "\n")

        events_path = tmp_path / "events.jsonl"
        if events_file.is_file():
            events_path.write_text("".join(events_file.read_text().splitlines(keepends=True)[-EVENTS_TAIL_LINES:]))
        else:
            events_path.write_text("")

        env = dict(os.environ)
        env["ATTAVO_SPEC_DIR"] = str(spec_dir)
        env["ATTAVO_RECORDS_DIR"] = str(item_records_dir)
        env["ATTAVO_RULINGS"] = str(root / m["paths"]["rulings"])
        env["ATTAVO_STAGE"] = "counsel"
        env["ATTAVO_COUNSEL_RECORD"] = str(record_path)
        env["ATTAVO_COUNSEL_LOG"] = str(log_path) if hand_log else ""
        env["ATTAVO_COUNSEL_EVENTS"] = str(events_path) if events_file.is_file() else ""
        env["ATTAVO_COUNSEL_PRIOR"] = str(prior_path) if prior else ""
        env["ATTAVO_COUNSEL_READS"] = "\n".join(reads_abs)
        env["ATTAVO_COUNSEL_DIFF"] = str(diff_path)
        env["ATTAVO_COUNSEL_CHECKS"] = str(checks_path)
        env["ATTAVO_COUNSEL_GRAPH"] = str(graph_path)
        env["ATTAVO_MANIFEST"] = str(root / MANIFEST)
        env["ATTAVO_PRODUCT_ROOT"] = str(PRODUCT_ROOT)
        env["ATTAVO_ADAPTER_CONFIG"] = json.dumps(adapter_config)
        if ask is not None:
            env["ATTAVO_COUNSEL_QUESTION"] = ask

        run = subprocess.run([str(adapter_path)], cwd=str(root), env=env,
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
        "wall_clock_s": round(time.monotonic() - started, 3),
        "status": status_out,
        "class": cls,
        "verdict": verdict,
        "citations": citations,
        "proposal": proposal,
        "forge": forge,
        "question": ask,
        "answers": answers,
    }
    record_out_path = write_record(item_records_dir, "counsel", record, m, item_id, log=transcript)

    print(f"attavo counsel: {status_out} ({record_out_path.name})")
    return 0 if status_out == "done" else 1
