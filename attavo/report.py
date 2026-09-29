from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from attavo.common import STAGES, ShallowCheckoutError, git
from attavo.doctor import invocations_without_record, merge_introducing_records, stall_bound_s
from attavo.drive import InvocationReading, drive_state, read_invocation, retry_state, uncommitted_record
from attavo.records import next_attempt_number, parse_attempts
from attavo.types import CounselRecord, DriveReport
from attavo.verify import FORGE_OK_CONCLUSIONS, FORGE_UNCONCLUDED_STATUSES, read_forge_checks

# --- drive --report (reconciler R1, rulings A and G): what one item is
# waiting on, and for whom -- drive's own decision (drive_state, the
# invocation readers recovery uses, read_forge_checks) read without acting
# on it. It writes nothing, starts nothing, and reads the forge once and
# never polls it, so a refusal is a level anyone can read again rather than
# a line drive printed once. `says` speaks the operator's words: a stage is
# "build" or "review", never the command that runs it. ---

_WORD = {"build": "build", "verify": "review"}

_COUNSEL_SAYS = {"answer": "has an answer", "patch": "found a product defect",
                 "person": "says it is a person's call"}


def _head(root: Path) -> str | None:
    try:
        return git(root, "rev-parse", "HEAD").strip() or None
    except RuntimeError:
        return None


def _latest(records_dir: Path, stage: str) -> tuple[str, dict[str, Any]] | None:
    """(stem, record) of the highest-numbered <stage>-NN.json, or None."""
    attempts = parse_attempts(records_dir, stage)
    if not attempts:
        return None
    nn = max(attempts)
    return f"{stage}-{nn:02d}", attempts[nn]


def _filed(records_dir: Path, stem: str) -> str | None:
    marker = records_dir / f"{stem}.filed"
    try:
        text = marker.read_text().strip()
    except OSError:
        return None
    return text or None


def _counsel_of(records_dir: Path, stem: str) -> tuple[str, CounselRecord] | None:
    """The latest counsel record that read the stop named stem."""
    attempts = parse_attempts(records_dir, "counsel")
    for nn in sorted(attempts, reverse=True):
        if attempts[nn].get("record") == stem:
            return f"counsel-{nn:02d}", attempts[nn]
    return None


def _stopped(root: Path, m: dict[str, Any], r: DriveReport, records_dir: Path, stage: str,
            stem: str, record: dict[str, Any]) -> DriveReport:
    stop = record.get("stop") or {}
    stop_class = stop.get("class")
    attempt = int(stem.rsplit("-", 1)[1])
    r["state"], r["kind"], r["stage"] = "stopped", "yours", stage
    r["attempt"], r["record"], r["stop_class"] = attempt, stem, stop_class
    r["filed"] = _filed(records_dir, stem)

    # R3: every attempt of this stage, side by side -- the exhaustion
    # report's own comparison, printed whether or not the budget is
    # actually spent yet, since a person reading --report wants the same
    # picture drive itself would act on.
    attempts = parse_attempts(records_dir, stage)
    rows: list[dict[str, Any]] = [
        {"attempt": nn, "class": (rec.get("stop") or {}).get("class"),
         "signature": (rec.get("stop") or {}).get("signature"),
         "bound": (rec.get("stop") or {}).get("bound"),
         "wall_clock_s": rec.get("wall_clock_s"), "cost_usd": rec.get("cost_usd")}
        for nn, rec in sorted(attempts.items())]
    r["attempts"] = rows
    last_two = [row["signature"] for row in rows[-2:]]
    signatures_match = bool(len(last_two) == 2 and last_two[0] is not None and last_two[0] == last_two[1])
    r["signatures_match"] = signatures_match
    r["rulings_available"] = [
        "attavo drive N --again --by LOGIN: reset the budget and start the next attempt"
        + (" (unlikely to help: the last two attempts failed the same way)" if signatures_match else ""),
        "amend the spec (moves the approval)"]

    state = retry_state(root, m, records_dir, stage, stop_class, attempt)
    how = f"as {stop_class}" if stop_class else f"({record.get('status')})"
    if state.waiting_s is not None:
        r["kind"] = "waits"
        says = (f"{_WORD[stage].capitalize()} attempt {attempt:02d} stopped {how}; retrying "
                f"{_WORD[stage]} in {int(state.waiting_s)} s")
    elif state.eligible:
        r["kind"] = "acts"
        says = f"{_WORD[stage].capitalize()} attempt {attempt:02d} stopped {how}; the next pass retries it"
    else:
        says = f"Needs you: {_WORD[stage]} attempt {attempt:02d} stopped {how}"
    counsel = _counsel_of(records_dir, stem)
    if counsel:
        r["counsel"] = counsel[0]
        verdict = str(counsel[1].get("verdict"))
        verdict_says = _COUNSEL_SAYS.get(verdict)
        says += f"; {counsel[0]} {verdict_says or 'could not read it'}"
        if verdict == "patch":
            says += f"; waiting on {r['filed'] or 'the issue counsel filed'}"
    if r["filed"] and "waiting on" not in says:
        says += f"; filed as {r['filed']}"
    r["says"] = says
    return r


def _invocation(r: DriveReport, reading: InvocationReading, info: dict[str, Any],
                bound: int | None) -> DriveReport:
    stage, nn, _log, _last_at, age, cls, crossed = reading
    r["stage"], r["attempt"], r["stop_class"] = stage, nn, None if cls == "running" else cls
    r["since"] = info.get("at") if isinstance(info.get("at"), str) else None
    r["last_event_age_s"] = None if age is None else int(age)
    word = _WORD[stage]
    if cls == "running":
        r["state"], r["kind"] = "running", "waits"
        r["says"] = (f"{word.capitalize()} running since {r['since'] or 'an unknown time'}; "
                     f"nothing to do until it finishes")
    else:
        r["state"], r["kind"] = "owes-recovery", "acts"
        if cls == "hung" and crossed == "ceiling":
            # R3: a stage past its own ceiling is hung by wall clock
            # alone -- never worded in terms of event age, which an
            # adapter that streams no events never had to begin with.
            r["says"] = (f"{word.capitalize()} attempt {nn:02d} is alive {r['last_event_age_s']} s "
                         f"after it began, past its ceiling; recovery would end it and record it "
                         f"as hung")
        elif cls == "hung":
            r["says"] = (f"{word.capitalize()} attempt {nn:02d} is alive but has had no event for "
                         f"{r['last_event_age_s']} s (bound {bound} s); recovery would end it and "
                         f"record it as hung")
        else:
            r["says"] = (f"{word.capitalize()} attempt {nn:02d} is gone and wrote no record; "
                         f"recovery would record it as died")
    return r


def _pending(root: Path, m: dict[str, Any], records_dir: Path, r: DriveReport) -> DriveReport | None:
    """The report for an invocation that began and wrote no record, or None
    when every invocation has its record. One that needs recovery reads
    before one still running: it is the one the next move is about. R2:
    a bound is no longer required to read this at all -- read_invocation
    reads alive from the heartbeat first, and needs a stall bound only to
    call a live, heartbeat-less invocation hung rather than running."""
    pending = invocations_without_record(records_dir)
    if not pending:
        return None
    bound = stall_bound_s(m)
    readings = [(read_invocation(root, bound, marker, info), info) for marker, info in pending]
    reading, info = next(((rd, i) for rd, i in readings if rd.cls != "running"), readings[0])
    if reading.stage not in _WORD:
        r["state"], r["kind"] = "refused", "yours"
        r["says"] = f"Found an invocation marker for an unknown stage, {reading.stage}"
        return r
    if reading.cls != "running" and next_attempt_number(records_dir, reading.stage) != reading.nn:
        r["state"], r["kind"], r["stage"], r["attempt"] = "refused", "yours", reading.stage, reading.nn
        r["says"] = (f"{_WORD[reading.stage].capitalize()} attempt {reading.nn:02d} wrote no record, but a "
                     f"later one exists; read the records before recovering")
        return r
    return _invocation(r, reading, info, bound)


def _uncommitted(root: Path, m: dict[str, Any], records_dir: Path, r: DriveReport,
                 stage: str) -> DriveReport | None:
    """R2: a record the working tree carries but git has no commit for yet
    -- the moment between a worker's write and its own commit -- reads as
    running for as long as the invocation it belongs to still touches its
    heartbeat; one a stale heartbeat left behind is a refusal naming the
    file, not a silent read of a record that was never actually
    transported. None when the latest record for stage is already
    committed (the ordinary case)."""
    path = uncommitted_record(root, records_dir, stage)
    if path is None:
        return None
    nn = int(path.stem.rsplit("-", 1)[1])
    marker = records_dir / f"{stage}-{nn:02d}.started"
    info: dict[str, Any] = {}
    if marker.is_file():
        try:
            with marker.open() as fh:
                loaded = json.load(fh)
            info = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            info = {}
    bound = stall_bound_s(m)
    reading = read_invocation(root, bound, marker, info) if marker.is_file() else None
    word = _WORD[stage]
    if reading is not None and reading.cls == "running":
        r["state"], r["kind"], r["stage"], r["attempt"] = "running", "waits", stage, nn
        r["since"] = info.get("at") if isinstance(info.get("at"), str) else None
        r["says"] = (f"{word.capitalize()} running; its {stage}-{nn:02d}.json is written but "
                     f"not yet committed")
        return r
    r["state"], r["kind"] = "refused", "yours"
    r["says"] = f"{path.name} exists but was never committed; its worker's heartbeat went stale first"
    return r


def _staged_at_origin(root: Path, m: dict[str, Any], records_dir: Path, r: DriveReport) -> DriveReport | None:
    """The stuck state a refused fast-forward leaves: a stage record this
    copy holds uncommitted that origin's branch already carries, its worker
    gone (a live heartbeat is the ordinary window, and reads running),
    and no marker lacking its record. Reported, never repaired: the copy
    is not moved while it holds the record, so no pass would ever say so."""
    for stage in ("build", "verify"):
        path = uncommitted_record(root, records_dir, stage)
        if path is None:
            continue
        nn = int(path.stem.rsplit("-", 1)[1])
        marker = records_dir / f"{stage}-{nn:02d}.started"
        if marker.is_file():
            try:
                with marker.open() as fh:
                    loaded = json.load(fh)
            except (OSError, ValueError):
                loaded = {}
            info = loaded if isinstance(loaded, dict) else {}
            if read_invocation(root, stall_bound_s(m), marker, info).cls == "running":
                continue
        carried = _carried_at_origin(root, records_dir, path)
        if carried:
            r["state"], r["kind"], r["stage"], r["attempt"] = "refused", "yours", stage, nn
            r["says"] = (f"{carried} is staged in this copy and origin's branch already carries it: the "
                         f"copy's fast-forward was refused, and no pass moves the copy while it holds "
                         f"the record. Move it aside, then run the pass again")
            return r
    return None


def _carried_at_origin(root: Path, records_dir: Path, path: Path) -> str | None:
    """The record's path when it is uncommitted here, no marker lacks its
    record, and origin's branch tip already carries the same bytes: the
    copy a worker's fast-forward left behind. None otherwise."""
    if invocations_without_record(records_dir):
        return None
    rel = str(path.relative_to(root))
    try:
        git(root, "fetch", "-q", "origin", git(root, "rev-parse", "--abbrev-ref", "HEAD").strip())
        theirs = git(root, "rev-parse", f"FETCH_HEAD:{rel}").strip()
        ours = git(root, "hash-object", "--", rel).strip()
    except RuntimeError:
        return None
    return rel if ours == theirs else None


def _checks(r: DriveReport, forge_checks: dict[str, Any], ok: bool, ready: bool) -> DriveReport:
    """Read the forge's answer on the head as a level: green, still
    running, or done and not green."""
    conclusions: list[dict[str, str]] = forge_checks["conclusions"]
    r["checks"] = [{"name": c["name"], "conclusion": c["conclusion"]} for c in conclusions]
    head7 = (r["head"] or "")[:7]
    if ok:
        if ready:
            r["state"], r["kind"], r["says"] = "ready-to-merge", "yours", "Ready to merge"
        else:
            r["state"], r["kind"], r["stage"] = "next-review", "acts", "verify"
            r["says"] = f"Review is next; the checks are green on {head7}"
        return r
    unconcluded = [c for c in conclusions if c["conclusion"] in FORGE_UNCONCLUDED_STATUSES]
    if unconcluded or not conclusions:
        named = ", ".join(f"{c['name']} {c['conclusion']}" for c in unconcluded) or "no checks reported"
        r["state"], r["kind"], r["stage"] = "waiting-on-checks", "waits", None if ready else "verify"
        r["says"] = f"Waiting on the checks: {named} on {head7}"
        return r
    named = (", ".join(f"{c['name']} {c['conclusion']}" for c in conclusions
                       if c["conclusion"] not in FORGE_OK_CONCLUSIONS)
             or "the checks command did not succeed")
    r["state"], r["kind"], r["stage"] = "blocked-on-checks", "waits", None if ready else "verify"
    after = "the item is ready to merge" if ready else "review starts"
    r["says"] = f"Blocked: {named} on {head7}; {after} once the checks are green"
    return r


def drive_report(root: Path, m: dict[str, Any], item_id: str) -> DriveReport:
    """The item's level, from the same reads `attavo drive` decides on --
    drive_state over its records, the markers recovery reads, one read of
    the forge -- and never an act: no record written, nothing started, no
    poll. A state for every item the records directory carries."""
    records_dir = root / m["paths"]["records"] / item_id
    r: DriveReport = {"item": item_id, "state": "refused", "kind": "yours", "says": "", "stage": None,
                      "attempt": None, "record": None, "stop_class": None, "counsel": None,
                      "filed": None, "head": _head(root), "checks": None, "since": None,
                      "last_event_age_s": None, "attempts": None, "signatures_match": None,
                      "rulings_available": None}

    merge, _ = merge_introducing_records(root, m["paths"]["records"], item_id, STAGES)
    if merge:
        r["state"], r["kind"], r["says"] = "merged", "done", f"Merged in {merge[:7]}"
        return r

    try:
        step, why, _ = drive_state(root, m, item_id)
    except ShallowCheckoutError as exc:
        r["says"] = f"Cannot read this item: {exc}"
        return r
    if step == "no-approval":
        r["state"], r["kind"] = "waiting-on-approval", "yours"
        r["says"] = ("Waiting on you: approve the spec" if why == "absent" else
                     "Waiting on you: the approval expired because the spec changed after it; "
                     "approve the spec again")
        return r

    stuck = _staged_at_origin(root, m, records_dir, r)
    if stuck:
        return stuck

    pending = _pending(root, m, records_dir, r)
    if pending:
        return pending

    if step in ("build", "verify"):
        held = _uncommitted(root, m, records_dir, r, step)
        if held:
            return held

    build = _latest(records_dir, "build")
    if step == "build":
        approval = _latest(records_dir, "approval")
        if (build and build[1].get("status") != "pass"
                and build[1].get("approval_sha") == (approval[1].get("sha") if approval else None)):
            return _stopped(root, m, r, records_dir, "build", build[0], build[1])
        r["state"], r["kind"], r["stage"] = "next-build", "acts", "build"
        r["says"] = "Build is next"
        return r

    review = _latest(records_dir, "verify")
    if (step == "verify" and review and review[1].get("status") != "pass"
            and build and review[1].get("build_sha") == build[1].get("sha")):
        return _stopped(root, m, r, records_dir, "verify", review[0], review[1])

    checks_command = (m.get("forge") or {}).get("checks_command")
    if not checks_command:
        r["says"] = "No checks command is set in the manifest, so nothing can say whether the checks are green"
        return r
    forge_checks, ok = read_forge_checks(root, checks_command, r["head"] or "")
    return _checks(r, forge_checks, ok, ready=step == "ready")
