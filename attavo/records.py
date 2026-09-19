from __future__ import annotations

import json
import re
from pathlib import Path

from attavo.common import PRODUCT_ROOT, product_version

# --- record files: <stage>-NN.json, one per attempt, never overwritten ---

SCHEMA_RECORDS_DIR = PRODUCT_ROOT / "schema" / "records"


def stage_schema(stage: str) -> dict:
    """schema/records/<stage>.schema.json -- this repository's own
    declaration of that stage's record shape (issue #60). The only
    artifact the outbound contract needs; there is no accessor for it
    beyond reading the file."""
    return json.loads((SCHEMA_RECORDS_DIR / f"{stage}.schema.json").read_text())


def schema_id_version(schema: dict) -> int:
    """The trailing integer of a schema's $id (".../records/build/2" -> 2)
    -- the single place a record shape's version lives. write_record reads
    this at call time rather than carrying its own copy, so the version it
    stamps cannot drift from what the schema declares (issue #60)."""
    return int(schema["$id"].rsplit("/", 1)[-1])


class RecordItemMismatch(RuntimeError):
    """A record's item field disagrees with the directory it was read
    from (issue #60, acceptance 5) -- a misfiled or copied record, refused
    rather than trusted."""


def _load_record(path: Path, item_id: str) -> dict:
    """The one place in this repository that turns a record file's bytes
    into a dict (issue #60, acceptance 6). Defaults record_version to 1
    when absent -- version 1 is every record written before this release
    and is never migrated -- and fills item from the directory when
    absent; refuses when a present item disagrees with it."""
    data = json.loads(path.read_text())
    data.setdefault("record_version", 1)
    item = data.get("item")
    if item is None:
        data["item"] = item_id
    elif item != item_id:
        raise RecordItemMismatch(
            f"{path}: item {item!r} disagrees with its directory {item_id!r}")
    return data


def parse_attempts(records_dir: Path, stage: str) -> dict[int, dict]:
    attempts: dict[int, dict] = {}
    if not records_dir.is_dir():
        return attempts
    for f in records_dir.glob(f"{stage}-*.json"):
        m = RECORD_RE.match(f.name)
        if not m or m.group(1) != stage:
            continue
        try:
            attempts[int(m.group(2))] = _load_record(f, records_dir.name)
        except json.JSONDecodeError:
            continue
    return attempts


def latest_record(records_dir: Path, stage: str) -> dict | None:
    attempts = parse_attempts(records_dir, stage)
    return attempts[max(attempts)] if attempts else None


def next_attempt_number(records_dir: Path, stage: str) -> int:
    attempts = parse_attempts(records_dir, stage)
    return (max(attempts) + 1) if attempts else 1


def latest_attempt_path(records_dir: Path, stage: str) -> Path | None:
    """The path of the highest-numbered <stage>-NN.json, or None."""
    attempts = parse_attempts(records_dir, stage)
    if not attempts:
        return None
    return records_dir / f"{stage}-{max(attempts):02d}.json"


def write_record(records_dir: Path, stage: str, record: dict, m: dict, item: str,
                 log: str | None = None) -> Path:
    """Every record gains record_version (this stage schema's own $id
    version -- issue #60) and item (verbatim from the caller, matching the
    record's directory segment), plus product_version (what ran, derived
    here) and product_pin (what the tenant declared, copied verbatim from
    the manifest, or null when the manifest carries no [attavo] table) --
    the latter two recorded, never enforced (issue #49). A manifest
    missing the table is an absent fact, not a crash: this is the one
    caller of write_record that must not raise on a shape the manifest
    schema forbids but tomllib.load happily returns.

    Refuses (raises ValueError) a record carrying a key this stage's
    schema does not declare -- the declared key set enforced with the
    standard library, not a jsonschema import: bin/attavo is a single
    script a tenant pins, and a runtime dependency changes installation.

    Also refuses a `status: "failed"` record that carries no trail at all
    -- log the empty or missing text it will sit beside, and refusal and
    halted (where the stage's schema has them) both null (issue #53).
    `returned`, `pass`, `halted`, and `refused` owe no such trail. log is
    None for a stage that invoked no adapter (verify's `returned`), which
    write_record then writes no log file for; otherwise the log is
    written before the record, so a refusal leaves no half-pair."""
    records_dir.mkdir(parents=True, exist_ok=True)
    nn = next_attempt_number(records_dir, stage)
    schema = stage_schema(stage)
    record = {**record, "record_version": schema_id_version(schema), "item": item,
              "product_version": product_version(),
              "product_pin": (m.get("attavo") or {}).get("version")}
    undeclared = set(record) - set(schema.get("properties", {}))
    if undeclared:
        raise ValueError(f"write_record: {stage} record carries key(s) its schema does not "
                         f"declare: {', '.join(sorted(undeclared))}")
    if (record.get("status") == "failed" and not log
            and record.get("refusal") is None and record.get("halted") is None):
        raise ValueError(f"write_record: {stage} record status is 'failed' with no log, "
                         f"refusal, or halted to say why")
    path = records_dir / f"{stage}-{nn:02d}.json"
    if log is not None:
        path.with_suffix(".log").write_text(log)
    path.write_text(json.dumps(record, indent=2) + "\n")
    return path


RECORD_RE = re.compile(r"^([a-z]+)-([0-9]{2,})\.json$")


def load_records(records_dir: Path) -> dict[str, dict[str, dict]]:
    """{item_id: {stage: latest_record}} for every <records>/<item>/<stage>-NN.json."""
    items: dict[str, dict[str, dict]] = {}
    if not records_dir.is_dir():
        return items
    for item_dir in sorted(p for p in records_dir.iterdir() if p.is_dir()):
        by_stage: dict[str, dict[int, dict]] = {}
        for rec in sorted(item_dir.glob("*.json")):
            m = RECORD_RE.match(rec.name)
            if not m:
                continue
            stage, n = m.group(1), int(m.group(2))
            try:
                data = _load_record(rec, item_dir.name)
            except json.JSONDecodeError:
                continue
            by_stage.setdefault(stage, {})[n] = data
        items[item_dir.name] = {stage: attempts[max(attempts)] for stage, attempts in by_stage.items()}
    return items
