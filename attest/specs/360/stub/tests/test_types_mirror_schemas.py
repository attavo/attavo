"""Issue #189: attavo/types.py is the mirror of schema/records/*.schema.json
and schema/attestation.schema.json. The schema stays the contract; each
record TypedDict's keys equal the schema's `properties` and its required
keys equal the schema's `required`, so the type cannot drift from it.

Run: python3 -m pytest tests -q
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from attavo import types

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "schema"

# The TypedDict for each schema, by the schema's path under schema/.
RECORD_KINDS = {
    "SpecRecord": "records/spec.schema.json",
    "ApprovalRecord": "records/approval.schema.json",
    "BuildRecord": "records/build.schema.json",
    "VerifyRecord": "records/verify.schema.json",
    "CounselRecord": "records/counsel.schema.json",
    "AttestRecord": "attestation.schema.json",
    "DriveReport": "drive-report.schema.json",
}

# The Stop sub-shape appears in build and verify records, each with its own
# class enum; the type is one shape and the schemas carry the same three keys.
STOP_HOMES = ("records/build.schema.json", "records/verify.schema.json")


def load(path: str) -> dict[str, Any]:
    schema: dict[str, Any] = json.loads((SCHEMA / path).read_text())
    return schema


@pytest.mark.parametrize("kind", sorted(RECORD_KINDS))
def test_the_typeddict_keys_equal_the_schemas_properties(kind: str) -> None:
    schema = load(RECORD_KINDS[kind])
    td = getattr(types, kind)
    assert set(td.__annotations__) == set(schema["properties"])


@pytest.mark.parametrize("kind", sorted(RECORD_KINDS))
def test_the_typeddicts_required_keys_equal_the_schemas_required(kind: str) -> None:
    schema = load(RECORD_KINDS[kind])
    td = getattr(types, kind)
    assert set(td.__required_keys__) == set(schema["required"])
    assert set(td.__optional_keys__) == set(schema["properties"]) - set(schema["required"])


@pytest.mark.parametrize("path", STOP_HOMES)
def test_the_stop_shape_mirrors_the_stop_in_each_schema_that_carries_one(path: str) -> None:
    stop = load(path)["properties"]["stop"]
    assert set(types.Stop.__annotations__) == set(stop["properties"])
    assert set(types.Stop.__required_keys__) == set(stop["required"])


# Issue #360, finding 4 (verify-01 on #353): the intervention class type mirrors the schema's enum.

def test_the_intervention_class_type_mirrors_the_schemas_enum() -> None:
    import typing
    schema = json.loads((Path(__file__).resolve().parent.parent / "schema" / "attestation.schema.json").read_text())
    enum = set(schema["properties"]["interventions"]["items"]["properties"]["class"]["enum"])
    found = None
    for name in dir(types):
        obj = getattr(types, name)
        hints = getattr(obj, "__annotations__", None)
        if isinstance(hints, dict) and "class" in hints and "sha" in hints:
            found = typing.get_type_hints(obj)["class"]
    assert found is not None, "no TypedDict for an intervention in attavo.types"
    assert typing.get_origin(found) is typing.Literal and set(typing.get_args(found)) == enum
