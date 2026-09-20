from __future__ import annotations

from pathlib import Path
from typing import Any

from attavo.common import STAGES, ShallowCheckoutError, git
from attavo.doctor import invocations_without_record, merge_introducing_records, stall_bound_s
from attavo.drive import InvocationReading, drive_state, read_invocation
from attavo.records import next_attempt_number, parse_attempts
from attavo.types import DriveReport
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


def _counsel_of(records_dir: Path, stem: str) -> tuple[str, dict[str, Any]] | None:
    """The latest counsel record that read the stop named stem."""
    attempts = parse_attempts(records_dir, "counsel")
    for nn in sorted(attempts, reverse=True):
        if attempts[nn].get("record") == stem:
            return f"counsel-{nn:02d}", attempts[nn]
    return None


def _stopped(r: DriveReport, records_dir: Path, stage: str, stem: str, record: dict[str, Any]) -> DriveReport:
    stop = record.get("stop") or {}
    stop_class = stop.get("class")
    r["state"], r["kind"], r["stage"] = "stopped", "yours", stage
    r["attempt"], r["record"], r["stop_class"] = int(stem.rsplit("-", 1)[1]), stem, stop_class
    r["filed"] = _filed(records_dir, stem)
    how = f"as {stop_class}" if stop_class else f"({record.get('status')})"
    says = f"Needs you: {_WORD[stage]} attempt {stem.rsplit('-', 1)[1]} stopped {how}"
    counsel = _counsel_of(records_dir, stem)
    if counsel:
        r["counsel"] = counsel[0]
        verdict = _COUNSEL_SAYS.get(str(counsel[1].get("verdict")))
        says += f"; {counsel[0]} {verdict or 'could not read it'}"
    if r["filed"]:
        says += f"; filed as {r['filed']}"
    r["says"] = says
    return r


def _invocation(r: DriveReport, reading: InvocationReading, info: dict[str, Any],
                bound: int) -> DriveReport:
    stage, nn, _log, _last_at, age, cls = reading
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
        if cls == "hung":
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
    before one still running: it is the one the next move is about."""
    pending = invocations_without_record(records_dir)
    if not pending:
        return None
    bound = stall_bound_s(m)
    if bound is None:
        r["state"], r["kind"] = "refused", "yours"
        r["says"] = ("An invocation wrote no record, and the manifest sets no stall bound, so a "
                     "running one cannot be told from a hung one")
        return r
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
                      "last_event_age_s": None}

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

    pending = _pending(root, m, records_dir, r)
    if pending:
        return pending

    build = _latest(records_dir, "build")
    if step == "build":
        approval = _latest(records_dir, "approval")
        if (build and build[1].get("status") != "pass"
                and build[1].get("approval_sha") == (approval[1].get("sha") if approval else None)):
            return _stopped(r, records_dir, "build", build[0], build[1])
        r["state"], r["kind"], r["stage"] = "next-build", "acts", "build"
        r["says"] = "Build is next"
        return r

    review = _latest(records_dir, "verify")
    if (step == "verify" and review and review[1].get("status") != "pass"
            and build and review[1].get("build_sha") == build[1].get("sha")):
        return _stopped(r, records_dir, "verify", review[0], review[1])

    checks_command = (m.get("forge") or {}).get("checks_command")
    if not checks_command:
        r["says"] = "No checks command is set in the manifest, so nothing can say whether the checks are green"
        return r
    forge_checks, ok = read_forge_checks(root, checks_command, r["head"] or "")
    return _checks(r, forge_checks, ok, ready=step == "ready")
