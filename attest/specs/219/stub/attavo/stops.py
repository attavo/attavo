from __future__ import annotations

import re

import json
import sys
from pathlib import Path
from typing import Any

from attavo.common import MANIFEST, read_manifest
from attavo.records import parse_attempts, stage_schema
from attavo.types import CounselRecord, Stop

# --- stop: every non-pass build or verify record's class and cause (issue
# #89) -- derived here, at write time, from the refusal, halted, and log
# the caller already has. Never a fresh read of anything: the tenant zero
# specimens this retires (#576 build-11, #659 build-01 and verify-01) are
# exactly a stop the product wrote with nothing beside it to explain
# itself after the fact. ---

STOP_CLASSES = ("non-question", "outlived-invocation", "no-cause", "forge-unconcluded",
               "ceiling", "claims-boundary", "repeated-act", "unclassed", "question",
               "environment", "gate", "died", "hung", "transient", "returned")

# R3: the families a pass retries without a person. Everything else is
# counsel's to read, and a person's when counsel says so.
RETRYABLE_CLASSES = ("died", "hung", "transient", "returned")


def signature_of(text: str | None) -> str | None:
    """R3: a failure's signature, compared by string equality between
    attempts: the text with every run of digits removed, so two refusals
    that differ only in a retry-after, a pid, or a timestamp match."""
    if not text:
        return None
    return re.sub(r"[0-9]+", "", text).strip() or None

STOP_PROVENANCES = ("product", "tenant", "executor", "spec")

# Beyond the enum spelling itself, the prose issue #88 actually shipped
# (adapters/claude-code/run and adapters/codex/run, on main since
# v0.16.0) once put a class's own name, or a real specimen's wording of
# it, in the transcript for the generic match below to find. Issue #94
# moves non-question and outlived-invocation onto report_class instead
# (below): non-question's own halt line ("halted: class non-question")
# would otherwise still satisfy the bare-name match here with no adapter
# report-line field involved at all, and outlived-invocation's refusal
# never named its class in text to begin with, so both are disabled here
# -- the same pattern issue #93 established for ceiling, just below,
# rather than removed outright.
CLASS_TEXT_MARKERS: dict[str, tuple[str, ...]] = {
    "non-question": (),
    "outlived-invocation": (),
    # Issue #93: ceiling's own text-based check is CEILING_MARKERS, below,
    # with provenance "product" -- a fact about the tool, not an executor
    # judgment like non-question/outlived-invocation are. Left at the
    # default (the class's own name, "ceiling"), the generic match above
    # would instead catch adapters/codex/run's and
    # adapters/claude-code/run's own pre-emptive refusal -- which must
    # carry the word "ceiling" by the same issue's own brief -- as
    # provenance "executor", before CEILING_MARKERS is ever consulted.
    "ceiling": (),
    # R3: transient is read from the adapter's report line (report_class),
    # returned is written by the product itself; neither is scanned for,
    # since a retried attempt's feedback file names the prior stop's class
    # and that text reaches the transcript.
    "transient": (),
    "returned": (),
    # A genuine question (issue #100's own followups) is read structurally
    # -- status "halted" carrying a non-empty halted.question -- not by
    # scanning for the literal word "question", which the question's own
    # text very often contains regardless of what it is actually asking.
    # Left at the default, the generic match above would misclassify any
    # question that happens to use the word "question" as executor
    # provenance, before the structural check in derive_stop is reached.
    "question": (),
    # environment/tenant (issue #115) is set directly by cmd_build's own
    # preflight -- the one caller who knows a [checks].command exited 2
    # on the approved tree -- never derived from adapter text. Left at
    # the default, the generic match above would catch the word
    # "environment" anywhere in a log or refusal (e.g. codex's own
    # OPENAI_API_KEY auth-mismatch refusal, adapters/codex/run's
    # check_auth) as an environment/executor stop that never happened.
    "environment": (),
}

# Known tool error text for a declared ceiling -- issue #86's own specimen
# (codex's "Input exceeds..." line on a diff too large to judge) is
# "input_too_large"; a matching line anywhere in the log, not only its
# last line, since cmd_build and cmd_verify both append "[adapter exited
# N]" after whatever the tool itself printed. "ceiling exceeded" is issue
# #93's own addition: both adapters now refuse pre-emptively, before the
# tool is ever invoked, against a declared or fixed input limit, and
# their refusal text is not caught by any of the three markers above.
CEILING_MARKERS = ("input_too_large", "exceeds the maximum context length",
                  "context_length_exceeded", "ceiling exceeded")


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


def _carries_a_known_class(text: str | None) -> str | None:
    """Whichever of STOP_CLASSES this text names -- its own spelling, or
    one of CLASS_TEXT_MARKERS' real-world phrasings of it, when that
    class is not disabled there -- or None. Issue #88 is where an
    adapter's own halt or refusal first named its class this way; issue
    #93 added ceiling's own pre-emptive refusal to that set and disabled
    its bare-name match (CEILING_MARKERS covers it instead); issue #94
    disables non-question and outlived-invocation the same way, reading
    both from the adapter's own report line (derive_stop's report_class)
    now instead."""
    text = text or ""
    for name in STOP_CLASSES:
        markers = CLASS_TEXT_MARKERS.get(name, (name,))
        if any(marker in text for marker in markers):
            return name
    return None


def derive_stop(status: str, *, refusal: str | None = None, halted: dict[str, Any] | None = None,
                log: str | None = None, report_class: str | None = None,
                refusal_provenance: str | None = None) -> Stop | None:
    """The stop field for a build or verify record: None for status "pass",
    else {class, provenance, detail} from what the caller already knows.

      - report_class -- payload.get("class") off the adapter's own report
        line (issue #88, #94), when it names a STOP_CLASSES member -- is
        that class, provenance "executor". build.schema.json and
        verify.schema.json stay untouched by issue #88 on purpose, so the
        class lives only on the report line, never a structured field
        cmd_build or cmd_verify forwards on its own account; the caller
        reads payload itself and passes this in, since only it still has
        payload once an iteration ends. This is non-question's and
        outlived-invocation's only path to a record now (issue #94):
        CLASS_TEXT_MARKERS disables both for the text scan below, the
        same way issue #93 disabled ceiling's bare-name match there. A
        class an adapter does not yet put on its report line --
        outlived-invocation today, since that refusal path prints no
        report line at all -- falls to unclassed below, same as any
        other unnamed cause, until the adapter that raises it puts a
        class on the line the way non-question already does.
      - a halt carrying a non-empty halted.question, once it is not a
        non-question caught above, is a genuine question -- question/spec,
        with the question's own first line as detail (issue #100's
        followups). This is the specimen issue #100 itself was built to
        retire: tenant zero's #663 build-01, a proper question, classed
        unclassed/product and filed as a product defect with the wrong
        provenance -- a question is about the definition of done, which is
        a person's or a ruling's to answer, never the product's own
        mechanism.
      - failing that, a refusal or the log itself that already names its
        class in text (issue #88, #93 for ceiling's own pre-emptive
        refusal) is that class, provenance "executor" -- the log is
        checked because neither adapter's refusal *field* carries the
        class today; the class-naming line each adapter prints reaches
        only the transcript
      - a refusal that is not that -- the write-outside-claims path
        itself, not the "commit ... not authored by the executor" shape
        -- is claims-boundary/spec, unless the caller already knows this
        refusal touched paths.records or paths.specs (issue #140:
        protected_path_touched, checked ahead of the ordinary claims
        match in cmd_build) and passes refusal_provenance="executor" --
        the product's own directories are not a claim a spec makes, so a
        refusal there is not the spec's boundary to answer for
      - a returned verify (the forge was not green; no adapter ran) is
        forge-unconcluded/tenant
      - an adapter exit whose log carries a declared-ceiling error line is
        ceiling/product
      - anything else is unclassed/product, with the log's last line
        (capped at UNCLASSED_DETAIL_MAX_CHARS) as its detail -- the other
        class that carries a detail, since every remaining class's
        evidence already lives in refusal, halted, forge_checks, or the
        log itself.
    """
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
    known = _carries_a_known_class(refusal) or _carries_a_known_class(log)
    if known:
        return {"class": known, "provenance": "executor", "detail": None}
    if status == "refused" and refusal is not None and not refusal.startswith("commit "):
        return {"class": "claims-boundary", "provenance": refusal_provenance or "spec",
               "detail": None}
    if log and any(marker in log.lower() for marker in CEILING_MARKERS):
        return {"class": "ceiling", "provenance": "product", "detail": None}
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
