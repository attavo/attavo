from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from attavo.common import MANIFEST, read_manifest
from attavo.records import parse_attempts, stage_schema
from attavo.types import CounselRecord, Stop

# --- stop: every non-pass build or verify record's class and cause (issue
# #89) -- derived here, at write time, from the record's own structured
# fields and what the caller knows structurally (issue #241). The
# transcript decides neither class nor provenance: the executor reads this
# repository's records and docs, which name every class. ---

# no-cause is the doctor's phrase for a record that predates `stop`; nothing
# derives it.
STOP_CLASSES = ("non-question", "outlived-invocation", "no-cause", "forge-unconcluded",
               "ceiling", "claims-boundary", "repeated-act", "unclassed", "question",
               "environment", "gate", "died", "hung", "transient", "returned", "empty",
               "foreign-commit")

# R3: the four families a pass retries with no person involved -- every
# other class is counsel's to read first, and a person's when counsel says
# so (or when counsel is not configured at all).
RETRYABLE_CLASSES = ("died", "hung", "transient", "returned")


def signature_of(text: str | None) -> str | None:
    """R3: a failure's signature, compared between attempts by plain string
    equality -- text with every run of digits removed, so two stops that
    differ only in a pid, a timestamp, or a retry-after count still match.
    None for no text at all (nothing to sign)."""
    if not text:
        return None
    stripped = re.sub(r"[0-9]+", "", text).strip()
    return stripped or None


STOP_PROVENANCES = ("product", "tenant", "executor", "spec")

# Issue #100's followups: an unclassed detail is the log's last line
# verbatim, and that line is sometimes an adapter's own event JSON or a
# tool's full error text -- thousands of characters wide. Capped here,
# at write time, so a filed issue's own summary of it (file-stops'
# log_tail already carries the full picture) stays short; the class
# itself, "unclassed", already says the full explanation lives in the
# log, not in this field.
UNCLASSED_DETAIL_MAX_CHARS = 200


def _log_last_line(log: str | None) -> str:
    lines = [l for l in (log or "").splitlines() if l.strip()]
    last = lines[-1] if lines else ""
    return last[:UNCLASSED_DETAIL_MAX_CHARS]


def _first_red(gate: dict[str, Any] | None) -> str | None:
    """The first red element of a build's gate, named: a criterion, then the
    selected tests, then the checks; None when the gate is absent or green."""
    if not gate:
        return None
    for c in gate.get("criteria") or []:
        if c["exit"] != 0:
            return f"criterion {c['name']}: exit {c['exit']}"
    tests = gate.get("selected_tests")
    if tests and tests["exit"] != 0:
        return f"selected tests: exit {tests['exit']}, {tests['count']} selected"
    checks = gate.get("checks")
    if checks and checks["exit"] != 0:
        return f"checks: exit {checks['exit']}"
    return None


def derive_stop(status: str, *, report_class: str | None = None,
                halted: dict[str, Any] | None = None, refusal_kind: str | None = None,
                refusal: str | None = None, gate: dict[str, Any] | None = None,
                log: str | None = None) -> Stop | None:
    """The stop field for a build or verify record: None for status "pass",
    else {class, provenance, detail} from the record's own structure and
    what the caller knows of it, in this order, and nothing else decides:

      - returned: forge-unconcluded/tenant (verify's own declaration)
      - report_class, the adapter's report-line class, when it names a
        STOP_CLASSES member: that class/executor
      - a halt carrying a non-empty question: question/spec, detail the
        question's first line
      - refused, by refusal_kind: claims -> claims-boundary/spec; protected
        (paths.records or paths.specs) -> claims-boundary/executor;
        foreign-commit -> foreign-commit/tenant; detail the refusal
      - failed with a red gate element: gate/executor, detail naming the
        first red one
      - anything else: unclassed/product, detail the log's last line
        (capped at UNCLASSED_DETAIL_MAX_CHARS)

    The log supplies the detail of unclassed and nothing more."""
    if status == "pass":
        return None
    if status == "returned":
        return {"class": "forge-unconcluded", "provenance": "tenant", "detail": None}
    if report_class in STOP_CLASSES:
        return {"class": report_class, "provenance": "executor", "detail": None}
    question = (halted or {}).get("question")
    if status == "halted" and question:
        return {"class": "question", "provenance": "spec",
               "detail": question.splitlines()[0]}
    if status == "refused":
        if refusal_kind == "claims":
            return {"class": "claims-boundary", "provenance": "spec", "detail": refusal}
        if refusal_kind == "protected":
            return {"class": "claims-boundary", "provenance": "executor", "detail": refusal}
        if refusal_kind == "foreign-commit":
            return {"class": "foreign-commit", "provenance": "tenant", "detail": refusal}
    if status == "failed":
        red = _first_red(gate)
        if red:
            return {"class": "gate", "provenance": "executor", "detail": red}
    return {"class": "unclassed", "provenance": "product", "detail": _log_last_line(log)}


def gate_stop(refusal: str | None) -> Stop:
    """The stop for a refused spec-gate record: class "gate", provenance
    "spec", the refusal string itself as cause (issue #137) -- not
    derive_stop's generic claims-boundary/spec fallback issue #133
    reused, which named a boundary the record never crossed: "criterion
    fails against the tree plus stub: doctor-template-absent.sh" is not
    a write outside claims. cmd_spec_gate is untouched by this lane, so
    a spec record still carries no stop field of its own; this is
    synthesized wherever a refused one is read (item_stops,
    cmd_file_stops, proof_no_stop_lacks_cause), never persisted."""
    return {"class": "gate", "provenance": "spec", "detail": refusal}


# --- stops: every non-pass record by class, provenance, and answerer
# (issue #129) ---

def item_stops(root: Path, m: dict[str, Any], item_id: str) -> dict[str, Any]:
    """{"item", "rows", "totals"}: one row per non-pass build or verify
    record (STOP_STAGES), plus every refused spec-gate record (issue
    #133), class "gate" (issue #137) -- read from status and refusal
    rather than a stop field spec-gate records never carry (gate_stop
    synthesizes it) -- under item_id, whatever the record's own
    record_version -- a record too old to carry stop at all still gets
    a row, its class printed as the doctor's own phrase for it.

    The answerer, a closed choice: "counsel:<verdict>" when
    counsel_record_for finds a counsel record naming this exact stage and
    number -- the product's own answer, which wins over whatever the stop
    itself says, since a counseled stop was already read once; failing
    that, "person" for class question (a person's to answer, not a
    guard's); failing that, "guard" for a class in GUARDED_STOP_CLASSES
    (a guard's own rejection needed no further reading); anything else
    -- no-cause, unclassed, or no stop at all -- is "none": nobody has
    answered it yet."""
    records_rel = m["paths"]["records"]
    item_records_dir = root / records_rel / item_id
    by_class = {c: 0 for c in stop_class_enum()}
    by_answerer: dict[str, int] = {}
    before_stop_was_recorded = 0
    rows: list[dict[str, Any]] = []

    def add_row(stage: str, n: int, stop: Stop | None) -> None:
        nonlocal before_stop_was_recorded
        name = f"{stage}-{n:02d}"
        marker = item_records_dir / f"{name}.filed"
        filed_issue = marker.read_text() if marker.is_file() else None
        counsel = counsel_record_for(item_records_dir, stage, n)

        if counsel is not None:
            answerer = f"counsel:{counsel.get('verdict')}"
        elif stop and stop.get("class") == "question":
            answerer = "person"
        elif stop and stop.get("class") in GUARDED_STOP_CLASSES:
            answerer = "guard"
        else:
            answerer = "none"

        if stop:
            cls, provenance, detail = (stop.get("class"), stop.get("provenance"),
                                      stop.get("detail"))
            if cls in by_class:
                by_class[cls] += 1
        else:
            cls, provenance, detail = "before stop was recorded", None, None
            before_stop_was_recorded += 1

        by_answerer[answerer] = by_answerer.get(answerer, 0) + 1
        rows.append({"item": item_id, "record": name, "class": cls,
                    "provenance": provenance, "detail": detail,
                    "answerer": answerer, "filed_issue": filed_issue})

    if item_records_dir.is_dir():
        for stage in STOP_STAGES:
            for n, rec in sorted(parse_attempts(item_records_dir, stage).items()):
                if rec.get("status") == "pass":
                    continue
                add_row(stage, n, rec.get("stop"))
        for n, rec in sorted(parse_attempts(item_records_dir, "spec").items()):
            if rec.get("status") != "refused":
                continue
            add_row("spec", n, gate_stop(rec.get("refusal")))

    totals = {"by_class": by_class, "by_answerer": by_answerer,
             "before_stop_was_recorded": before_stop_was_recorded}
    return {"item": item_id, "rows": rows, "totals": totals}


def cmd_stops(argv: list[str]) -> int:
    as_json = "--json" in argv
    positional = [a for a in argv if a != "--json"]
    root = Path(".").resolve()
    m = read_manifest(root)
    if m is None:
        print(f"attavo stops: no {MANIFEST} at {root}", file=sys.stderr)
        return 2

    records_rel = m["paths"]["records"]
    if positional:
        item_ids = [positional[0]]
    else:
        records_root = root / records_rel
        item_ids = (sorted((p.name for p in records_root.iterdir() if p.is_dir()))
                   if records_root.is_dir() else [])

    reports = [item_stops(root, m, item_id) for item_id in item_ids]

    if as_json:
        if positional:
            print(json.dumps(reports[0], indent=2))
        else:
            print(json.dumps({"items": reports}, indent=2))
        return 0

    for entry in reports:
        print(f"attavo stops: item {entry['item']}")
        for row in entry["rows"]:
            filed = f"  filed: {row['filed_issue'].strip()}" if row["filed_issue"] else ""
            print(f"  {row['record']:<10} {row['class']:<24} {row['provenance'] or '-':<10} "
                 f"{row['answerer']:<16}{filed}")
        t = entry["totals"]
        print(f"  by class: {t['by_class']}")
        print(f"  by answerer: {t['by_answerer']}")
        print(f"  before stop was recorded: {t['before_stop_was_recorded']}")
    if not reports:
        print(f"attavo stops: no item found under {records_rel}")
    return 0


# The stop classes issue #89 gave no code of their own beyond the guard
# that raised them -- non-question and outlived-invocation from an
# adapter's own pre-emptive refusal, forge-unconcluded from a returned
# verify, ceiling from a declared-input-limit error, claims-boundary from
# the write-outside-claims refusal, repeated-act from... the same shape,
# environment from cmd_build's own [checks].command preflight (issue
# #115). question (a person's to answer) and the residual (no-cause,
# unclassed -- nobody has looked yet) are not in this tuple; attavo stops
# (issue #129) is the one reader, so it lives beside driver_identity,
# not beside derive_stop, which never needs to tell a guard from any
# other class once it has already named one.
GUARDED_STOP_CLASSES = ("non-question", "outlived-invocation", "forge-unconcluded", "ceiling",
                        "claims-boundary", "repeated-act", "environment")


def stop_class_enum() -> tuple[str, ...]:
    """Every class a stop can name, read from the build record schema's own
    enum (issue #129) rather than retyped -- STOP_CLASSES already drifted
    once (environment arrived in the schema before anything but derive_stop
    read it); a second copy is a second place to forget. build's schema is
    read, not verify's: verify's own enum is a strict subset (it never
    raises environment, cmd_build's own preflight alone)."""
    schema = stage_schema("build")
    return tuple(schema["properties"]["stop"]["properties"]["class"]["enum"])


STOP_STAGES = ("build", "verify")


def counsel_record_for(item_records_dir: Path, stage: str, n: int) -> CounselRecord | None:
    """The latest counsel-NN.json (issue #100) whose own "record" field
    names f"{stage}-{n:02d}" -- the counsel invocation, if any, that read
    this exact stage record -- or None when counsel never ran against it
    (e.g. file-stops run standalone, outside drive)."""
    target = f"{stage}-{n:02d}"
    attempts = parse_attempts(item_records_dir, "counsel")
    matches = [rec for _, rec in sorted(attempts.items()) if rec.get("record") == target]
    return matches[-1] if matches else None
