from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from attavo.common import MANIFEST, derive_item_id, read_manifest
from attavo.records import latest_record, parse_attempts
from attavo.stops import STOP_STAGES, counsel_record_for, gate_stop

# --- file-findings: one tenant issue per finding, nothing filed silently ---

def cmd_file_findings(argv: list[str]) -> int:
    root = Path(".").resolve()
    item_id = argv[0] if argv else derive_item_id(root)
    if not item_id:
        print("attavo file-findings: cannot derive item id; pass N or run on an attavo/N-* branch",
              file=sys.stderr)
        return 2
    m = read_manifest(root)
    if m is None:
        print(f"attavo file-findings: no {MANIFEST} at {root}", file=sys.stderr)
        return 2

    records_rel = m["paths"]["records"]
    item_records_dir = root / records_rel / item_id

    forge_cfg = m.get("forge") or {}
    file_issue_command = forge_cfg.get("file_issue_command")
    if not file_issue_command:
        print("attavo file-findings: refused: no [forge].file_issue_command in the manifest")
        return 1

    latest_verify = latest_record(item_records_dir, "verify")
    if latest_verify is None:
        print(f"attavo file-findings: refused: no verify record for item {item_id}")
        return 1

    findings = latest_verify.get("findings") or []
    if not findings:
        print("attavo file-findings: no findings to file")
        return 0

    for finding in findings:
        if not finding.get("retires"):
            title = finding.get("title", "<untitled>")
            print(f"attavo file-findings: refused: finding {title!r} carries no retires; "
                  f"nothing filed")
            return 1

    for finding in findings:
        run = subprocess.run(file_issue_command, shell=True, cwd=str(root),
                             input=json.dumps(finding) + "\n", capture_output=True,
                             text=True, check=False)
        if run.returncode != 0:
            title = finding.get("title", "<untitled>")
            print(f"attavo file-findings: refused: filing {title!r} exited {run.returncode}: "
                  f"{run.stderr.strip()}")
            return 1

    print(f"attavo file-findings: filed {len(findings)} issue(s)")
    return 0


# --- file-stops: one tenant issue per stop record, nothing filed silently,
# nothing filed twice (issue #89) ---


FILE_STOPS_LOG_TAIL_LINES = 20


def compose_stop_title(item_id: str, stage: str, n: int, stop: dict) -> str:
    """`stop <class>/<provenance> on item <N>: <stage>-<NN>` -- the title
    the tenant's file-issue script would otherwise fall back to
    <untitled> for. An empty class or provenance is a record defect, not
    a filing: this returns "" rather than a title with a blank half, so
    the caller can refuse on it."""
    cls = stop.get("class")
    provenance = stop.get("provenance")
    if not cls or not provenance:
        return ""
    return f"stop {cls}/{provenance} on item {item_id}: {stage}-{n:02d}"


def cmd_file_stops(argv: list[str]) -> int:
    root = Path(".").resolve()
    item_id = argv[0] if argv else derive_item_id(root)
    if not item_id:
        print("attavo file-stops: cannot derive item id; pass N or run on an attavo/N-* branch",
              file=sys.stderr)
        return 2
    m = read_manifest(root)
    if m is None:
        print(f"attavo file-stops: no {MANIFEST} at {root}", file=sys.stderr)
        return 2

    records_rel = m["paths"]["records"]
    item_records_dir = root / records_rel / item_id

    forge_cfg = m.get("forge") or {}
    file_issue_command = forge_cfg.get("file_issue_command")
    if not file_issue_command:
        print("attavo file-stops: refused: no [forge].file_issue_command in the manifest")
        return 1

    # A stop record's own .filed marker beside it -- the same convention as
    # its .log sidecar -- is what makes a second run file nothing: no new
    # artifact beside the records (the gate output already lives in the
    # log), just a fact about this one record, in the one place a reader
    # already looks. It carries the filing command's own stdout (the
    # issue it opened), so that fact is kept, not just the fact of
    # filing. This command never commits it -- the same as build and
    # verify, which write their own records uncommitted -- a caller that
    # wants it on the ledger commits it itself; drive does, in the same
    # commit as the record and its log (issue #89).
    pending: list[tuple[str, int, dict, Path]] = []
    for stage in STOP_STAGES:
        attempts = parse_attempts(item_records_dir, stage)
        for n in sorted(attempts):
            stop = attempts[n].get("stop")
            if not stop:
                continue
            marker = item_records_dir / f"{stage}-{n:02d}.filed"
            if marker.exists():
                continue
            pending.append((stage, n, stop, marker))

    # A refused spec-gate record is the same shape of stop, earlier (issue
    # #133); its own class is "gate" (issue #137), never persisted -- it
    # never carries a stop field of its own (cmd_spec_gate is untouched
    # by this lane), so it is synthesized here from its refusal.
    for n, rec in sorted(parse_attempts(item_records_dir, "spec").items()):
        if rec.get("status") != "refused":
            continue
        marker = item_records_dir / f"spec-{n:02d}.filed"
        if marker.exists():
            continue
        pending.append(("spec", n, gate_stop(rec.get("refusal")), marker))

    if not pending:
        print("attavo file-stops: no stops to file")
        return 0

    for stage, n, stop, _marker in pending:
        if stop["class"] == "unclassed" and not stop.get("detail"):
            print(f"attavo file-stops: refused: {stage}-{n:02d} is unclassed with no detail; "
                  f"nothing filed")
            return 1

    for stage, n, stop, _marker in pending:
        if not compose_stop_title(item_id, stage, n, stop):
            print(f"attavo file-stops: refused: {stage}-{n:02d} composes an empty title; "
                  f"nothing filed")
            return 1

    for stage, n, stop, marker in pending:
        record_path = item_records_dir / f"{stage}-{n:02d}.json"
        log_path = record_path.with_suffix(".log")
        log_tail = ("\n".join(log_path.read_text().splitlines()[-FILE_STOPS_LOG_TAIL_LINES:])
                   if log_path.is_file() else "")
        counsel = counsel_record_for(item_records_dir, stage, n)
        body = {
            "title": compose_stop_title(item_id, stage, n, stop),
            "class": stop["class"],
            "provenance": stop["provenance"],
            "record": str(record_path.relative_to(root)),
            "log_tail": log_tail,
            "counsel_verdict": counsel.get("verdict") if counsel else None,
            "counsel_citations": counsel.get("citations") if counsel else [],
            "counsel_proposal": counsel.get("proposal") if counsel else None,
        }
        run = subprocess.run(file_issue_command, shell=True, cwd=str(root),
                             input=json.dumps(body) + "\n", capture_output=True,
                             text=True, check=False)
        if run.returncode != 0:
            print(f"attavo file-stops: refused: filing {stage}-{n:02d} exited {run.returncode}: "
                  f"{run.stderr.strip()}")
            return 1
        marker.write_text(run.stdout)

    print(f"attavo file-stops: filed {len(pending)} issue(s)")
    return 0
