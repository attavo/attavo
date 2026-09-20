from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Any

from attavo.records import latest_record
from attavo.types import DriveReport

# --- drive --report, AS A STUB. This file exists so the spec's criteria can
# be shown to run and to be satisfiable; it is not the implementation and
# must not be shipped. It guesses the item's state from which files exist
# under its records directory. It does not use drive_state, so it knows
# nothing of an approval that expired, a build bound to an earlier approval,
# or a review of an earlier build; it treats every marker without a record
# as died, so it never reads running or hung; it never reads merged,
# refused, or counsel. tests/test_report.py fails on it by design. The
# build writes the report from the product's own functions (drive_state,
# invocations_without_record, invocation_alive, stall_bound_s,
# read_forge_checks, merge_introducing_records, parse_attempts). ---


def drive_report(root: Path, m: dict[str, Any], item_id: str) -> DriveReport:
    d = root / m["paths"]["records"] / item_id
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=False).stdout.strip() or None
    r: DriveReport = {"item": item_id, "state": "refused", "kind": "yours", "says": "", "stage": None,
                      "attempt": None, "record": None, "stop_class": None, "counsel": None,
                      "filed": None, "head": head, "checks": None, "since": None,
                      "last_event_age_s": None}

    def have(stem: str) -> list[Path]:
        return sorted(d.glob(f"{stem}-[0-9][0-9].json"))

    if not have("approval"):
        r["state"], r["says"] = "waiting-on-approval", "Waiting on you: approve the spec"
        return r
    for marker in sorted(d.glob("*.started")):
        if not (d / f"{marker.stem}.json").exists():
            stage, nn = marker.stem.rsplit("-", 1)
            r["state"], r["kind"], r["stage"], r["attempt"], r["stop_class"] = (
                "owes-recovery", "acts", stage, int(nn), "died")
            r["says"] = f"Attempt {int(nn)} wrote no record; recovery would record it as died"
            return r
    builds = have("build")
    if not builds:
        r["state"], r["kind"], r["stage"], r["says"] = "next-build", "acts", "build", "Build is next"
        return r
    latest: dict[str, Any] = dict(latest_record(d, "build") or {})
    if latest.get("status") != "pass":
        filed = d / f"{builds[-1].stem}.filed"
        r["state"], r["stage"], r["record"] = "stopped", "build", builds[-1].stem
        r["stop_class"] = (latest.get("stop") or {}).get("class")
        r["filed"] = filed.read_text().strip() if filed.exists() else None
        r["says"] = f"Needs you: build stopped as {r['stop_class']}"
        return r
    if have("verify"):
        r["state"], r["says"] = "ready-to-merge", "Ready to merge"
        return r
    command = (m.get("forge") or {}).get("checks_command", "true")
    out = subprocess.run(f"{command} {shlex.quote(head or '')}", shell=True, cwd=str(root),
                         capture_output=True, text=True, check=False).stdout
    checks = [dict(zip(("name", "conclusion"), line.rsplit(" ", 1))) for line in out.splitlines() if " " in line]
    r["checks"], r["stage"] = checks, "verify"
    named = ", ".join(f"{c['name']} {c['conclusion']}" for c in checks if c["conclusion"] != "success")
    if any(c["conclusion"] == "in_progress" for c in checks):
        r["state"], r["kind"], r["says"] = "waiting-on-checks", "waits", f"Waiting on GitHub: {named}"
    elif named:
        r["state"], r["kind"] = "blocked-on-checks", "waits"
        r["says"] = f"Blocked: {named} on {(head or '')[:7]}; nothing runs until the head changes"
    else:
        r["state"], r["kind"], r["says"] = "next-review", "acts", "Review is next"
    return r
