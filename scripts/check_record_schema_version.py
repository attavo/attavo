#!/usr/bin/env python3
"""Refuses a schema/records/*.schema.json shape change that lands with no
$id version bump (issue #60, acceptance 3) -- a version nobody bumps is
worse than none, because readers trust it.

schema_version_drift is the actual check; tests/test_record_schemas.py
exercises it directly against a mutated copy of a real schema (its own
rejection test, and it stays there). This script is what proves the real
schemas -- not a fixture -- obey it: it reads each schema/records/*.schema.json
at the merge base and at HEAD and calls schema_version_drift on the pair.
It lives outside the test suite because a merge-base read behaves
differently inside pytest (working tree state, shallow clones, local vs
CI checkouts) than in the workflow, which always has the full history
this needs.

Run: python3 scripts/check_record_schema_version.py <base-ref>
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = ROOT / "schema" / "records"


def _schema_shape(schema: dict) -> str:
    props = {k: v for k, v in schema.get("properties", {}).items() if k != "record_version"}
    required = sorted(k for k in schema.get("required", []) if k != "record_version")
    return json.dumps({"properties": props, "required": required}, sort_keys=True)


def schema_version_drift(old: dict, new: dict) -> str | None:
    """None when the shape is unchanged, or changed alongside a bumped
    $id. A message when the shape changed but $id's trailing integer
    didn't."""
    old_version = old["$id"].rsplit("/", 1)[-1]
    new_version = new["$id"].rsplit("/", 1)[-1]
    if _schema_shape(old) != _schema_shape(new) and old_version == new_version:
        return f"shape changed but $id version is still {new_version}"
    return None


def schema_at(ref: str, relpath: str) -> dict | None:
    """A schema file's content at git ref `ref`, or None when it did not
    exist there -- a schema file introduced since the merge base has no
    prior shape to drift from."""
    run = subprocess.run(["git", "-C", str(ROOT), "show", f"{ref}:{relpath}"],
                         capture_output=True, text=True, check=False)
    if run.returncode != 0:
        return None
    return json.loads(run.stdout)


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: check_record_schema_version.py <base-ref>", file=sys.stderr)
        return 2
    base_ref = argv[0]
    merge_base = subprocess.run(["git", "-C", str(ROOT), "merge-base", "HEAD", base_ref],
                                capture_output=True, text=True, check=True).stdout.strip()

    schema_paths = sorted(SCHEMAS_DIR.glob("*.schema.json"))
    failures = []
    for path in schema_paths:
        relpath = str(path.relative_to(ROOT))
        old = schema_at(merge_base, relpath)
        if old is None:
            continue
        new = json.loads(path.read_text())
        message = schema_version_drift(old, new)
        if message:
            failures.append(f"{relpath}: {message}")

    if failures:
        for failure in failures:
            print(f"check-record-schema-version: refused: {failure}", file=sys.stderr)
        return 1
    print(f"check-record-schema-version: {len(schema_paths)} schema(s) checked against "
         f"{merge_base[:12]}, no unversioned shape change")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
