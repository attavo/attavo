from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from attavo.common import STAGES, ShallowCheckoutError, git
from attavo.doctor import (
    invocation_alive,
    invocations_without_record,
    merge_introducing_records,
    stall_bound_s,
)
from attavo.drive import _events_path_for, _events_tail, _seconds_since, drive_state
from attavo.records import latest_attempt_path, latest_record, parse_attempts
from attavo.types import DriveReport
from attavo.verify import forge_checks_unconcluded, read_forge_checks

# --- drive --report: what one item is waiting on, and for whom, as a pure
# function of its records, its markers, and one read of the forge. It
# writes nothing, starts nothing, and never waits: a refusal is not an
# event to record but the level a reader finds for as long as it is true
# (#171). The operator's words, not the product's: "review", never
# "verify"; nothing here says "drive". ---

STAGE_WORD = {"build": "build", "verify": "review"}


def _blank(item_id: str, head: str | None) -> DriveReport:
    return {"item": item_id, "state": "refused", "kind": "yours", "says": "", "stage": None,
            "attempt": None, "record": None, "stop_class": None, "counsel": None, "filed": None,
            "head": head, "checks": None, "since": None, "last_event_age_s": None}


def _set(r: DriveReport, **fields: Any) -> None:
    """Every key is one DriveReport declares; the schema test holds the two
    together, and the report's own tests read each field it sets."""
    cast(dict[str, Any], r).update(fields)


def _counsel_for(records_dir: Path, record: str) -> tuple[str | None, str | None]:
    """(the stem of the latest counsel record that read `record`, its
    verdict). The report names the record; a view opens it for the text."""
    stem: str | None = None
    verdict: str | None = None
    for n, c in sorted(parse_attempts(records_dir, "counsel").items()):
        if c.get("record") == record:
            stem = f"counsel-{n:02d}"
            v = c.get("verdict")
            verdict = v if isinstance(v, str) else None
    return stem, verdict


def _filed_for(records_dir: Path, record: str) -> str | None:
    marker = records_dir / f"{record}.filed"
    try:
        return marker.read_text().strip() or None
    except OSError:
        return None


def drive_report(root: Path, m: dict[str, Any], item_id: str) -> DriveReport:
    try:
        head: str | None = git(root, "rev-parse", "HEAD").strip()
    except RuntimeError:
        head = None
    r = _blank(item_id, head)
    records_dir = root / m["paths"]["records"] / item_id

    # Merged: a merge on the default branch's own first-parent line brought
    # the item's records in -- the derivation attest and the doctor use, so
    # a record merely sitting in a tree is not mistaken for a merge.
    merge_sha, _ = merge_introducing_records(root, m["paths"]["records"], item_id, STAGES)
    if merge_sha is not None:
        _set(r, state="merged", kind="done", says=f"Merged at {merge_sha[:7]}")
        return r

    try:
        stage, extra, _ = drive_state(root, m, item_id)
    except ShallowCheckoutError as exc:
        _set(r, says=f"Cannot read: {exc}")
        return r

    if stage == "no-approval":
        _set(r, state="waiting-on-approval", kind="yours",
                 says=("Waiting on you: approve the spec" if extra == "absent"
                       else "Waiting on you: the approval expired when the spec moved; approve again"))
        return r

    pending = invocations_without_record(records_dir)
    if pending:
        marker, info = pending[-1]
        st, nn = marker.stem.rsplit("-", 1)
        _, last_at = _events_tail(_events_path_for(root, st, int(nn)))
        age = _seconds_since(last_at or info.get("at"))
        bound = stall_bound_s(m)
        _set(r, stage=st, attempt=int(nn), since=info.get("at"),
                 last_event_age_s=None if age is None else int(age))
        word = STAGE_WORD.get(st, st).capitalize()
        if invocation_alive(info) and (bound is None or age is None or age <= bound):
            _set(r, state="running", kind="waits",
                     says=f"{word} running since {info.get('at')}, attempt {int(nn)}, last event "
                          f"{'unknown' if age is None else int(age)} s ago")
        else:
            cls = "hung" if invocation_alive(info) else "died"
            _set(r, state="owes-recovery", kind="acts", stop_class=cls,
                     says=f"{word} attempt {int(nn)} began at {info.get('at')} and wrote no record; "
                          f"recovery would record it as {cls}")
        return r

    if stage in ("build", "verify"):
        latest = latest_record(records_dir, stage)
        path = latest_attempt_path(records_dir, stage)
        stale = False
        if stage == "verify" and latest is not None:
            build = latest_record(records_dir, "build")
            stale = latest.get("build_sha") != (build.get("sha") if build else None)
        if stage == "build" and latest is not None:
            approval = latest_record(records_dir, "approval")
            stale = latest.get("approval_sha") != (approval.get("sha") if approval else None)
        if latest is not None and path is not None and latest.get("status") != "pass" and not stale:
            stop = latest.get("stop") or {}
            counsel, verdict = _counsel_for(records_dir, path.stem)
            _set(r, state="stopped", kind="yours", stage=stage, record=path.stem,
                 stop_class=stop.get("class"), counsel=counsel,
                 filed=_filed_for(records_dir, path.stem),
                 says=(f"Needs you: {STAGE_WORD[stage]} attempt {path.stem[-2:]} stopped as "
                       f"{stop.get('class') or latest.get('status')}"
                       + (f"; {counsel} reads it as {verdict}" if counsel else "")))
            return r

    if stage == "build":
        _set(r, state="next-build", kind="acts", stage="build", says="Spec approved; build is next")
        return r

    checks_command = (m.get("forge") or {}).get("checks_command")
    if not checks_command or head is None:
        _set(r, says="Cannot read GitHub's checks: no [forge].checks_command in the manifest")
        return r
    forge, green = read_forge_checks(root, checks_command, head)
    r["checks"] = forge["conclusions"]
    if not green:
        names = ", ".join(f"{c['name']} {c['conclusion']}" for c in forge["conclusions"]
                          if c["conclusion"] not in ("success", "neutral", "skipped"))
        if forge_checks_unconcluded(forge):
            _set(r, state="waiting-on-checks", kind="waits", stage=stage if stage != "ready" else None,
                     says=f"Waiting on GitHub: {names or 'no check reported yet'} on {head[:7]}")
        else:
            _set(r, state="blocked-on-checks", kind="waits", stage=stage if stage != "ready" else None,
                     says=f"Blocked: {names} on {head[:7]}; nothing runs until the head changes")
        return r
    if stage == "verify":
        _set(r, state="next-review", kind="acts", stage="verify", says="Build passed and checks are green; review is next")
        return r
    _set(r, state="ready-to-merge", kind="yours", says="Ready to merge")
    return r
