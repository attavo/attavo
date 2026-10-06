"""The record kinds, typed. Each TypedDict here mirrors one JSON schema: its
keys are that schema's `properties` and its required keys are that schema's
`required`, and tests/test_types_mirror_schemas.py asserts both, so the type
cannot drift from the contract. The schema stays the contract; this is its
mirror (issue #189).

Every key each schema lists is required, so no class here is total=False; a
key the schema someday makes optional goes in a total=False base. A value the
schema declares as an object with fixed properties but this module does not
yet name is a dict[str, Any] (or a list of them), no tighter than the schema's
own `additionalProperties` allows for the adapter-owned ones.
"""
from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

# The stage names a record file's <stage>-NN.json prefix can carry, one per
# schema under schema/records/.
Stage = Literal["spec", "approval", "build", "verify", "counsel"]

# "class" is a Python keyword, so the record kinds that carry it are built
# with the functional syntax. signature and bound are R3's own additions,
# optional (a record older than record_version 8/7 carries neither).
_StopRequired = TypedDict("_StopRequired", {
    "class": str,
    "provenance": str,
    "detail": str | None,
})
_StopOptional = TypedDict("_StopOptional", {
    "signature": str | None,
    "bound": str | None,
}, total=False)


class Stop(_StopRequired, _StopOptional):
    """build-NN.json's and verify-NN.json's `stop`: the class of a stop and whose
    it is. The two schemas enumerate different classes; the shape is the same."""


class _SpecOptional(TypedDict, total=False):
    # Written from spec/3 on (ruled 2026-09-27); records before it carry neither.
    # "class" is a Python keyword, hence the functional form below.
    research: str


_SpecClass = TypedDict("_SpecClass", {"class": str | None}, total=False)


class SpecRecord(_SpecOptional, _SpecClass):
    record_version: int
    item: str
    sha: str
    spec_sha: str | None
    at: str
    status: str
    approval_sha: str | None
    criteria: list[dict[str, Any]]
    refusal: str | None
    product_version: str
    product_pin: str | None


class ApprovalRecord(TypedDict):
    record_version: int
    item: str
    sha: str
    spec_sha: str | None
    approved_by: str
    at: str
    product_version: str
    product_pin: str | None


class _BuildRequired(TypedDict):
    record_version: int
    item: str
    sha: str
    start_sha: str
    spec_sha: str | None
    approval_sha: str | None
    at: str
    adapter: str
    adapter_config: dict[str, Any]
    executor_identity: dict[str, str]
    reports: list[str]
    adapter_version: str | None
    model: str | None
    model_version: str | None
    iterations: int
    criteria: list[dict[str, Any]]
    selected_tests: dict[str, Any] | None
    checks: dict[str, Any] | None
    tokens: dict[str, int]
    cost_usd: float | None
    wall_clock_s: float
    status: str
    refusal: str | None
    halted: dict[str, Any] | None
    refusal_kind: str | None
    report_class: str | None
    stop: Stop | None
    product_version: str
    product_pin: str | None


class _BuildOptional(TypedDict, total=False):
    retry_of: str | None
    steer: str | None


class BuildRecord(_BuildRequired, _BuildOptional):
    """build-NN.json (issue #60): retry_of and steer (R3) are optional --
    absent on a record written before this release."""


class _VerifyRequired(TypedDict):
    record_version: int
    item: str
    sha: str
    build_sha: str
    build_record: str | None
    approval_sha: str | None
    at: str
    adapter: str | None
    adapter_config: dict[str, Any]
    adapter_version: str | None
    model: str | None
    model_version: str | None
    reports: list[str]
    forge_checks: dict[str, Any]
    findings: list[dict[str, Any]] | None
    tests_named: list[str]
    commits: list[str]
    stub_ratio: float | None
    tokens: dict[str, int]
    cost_usd: float | None
    wall_clock_s: float
    status: str
    report_class: str | None
    stop: Stop | None
    product_version: str
    product_pin: str | None


class _VerifyOptional(TypedDict, total=False):
    retry_of: str | None
    steer: str | None


class VerifyRecord(_VerifyRequired, _VerifyOptional):
    """verify-NN.json (issue #60): retry_of and steer (R3) are optional --
    absent on a record written before this release."""


class CounselCitation(TypedDict):
    """One counsel citation (#328): `resolution` is absent for a repository
    file at the record's sha."""
    kind: str
    path: str | None
    symbol: str | None
    ruling_id: str | None
    resolution: NotRequired[Literal["input", "not_in_repository", "not_found_at_sha"]]


CounselRecord = TypedDict("CounselRecord", {
    "record_version": int,
    "item": str,
    "sha": str,
    "record_stage": str,
    "record": str,
    "at": str,
    "adapter": str | None,
    "adapter_config": dict[str, Any],
    "adapter_version": str | None,
    "model": str | None,
    "model_version": str | None,
    "reports": list[str],
    "tokens": dict[str, int],
    "cost_usd": float | None,
    "wall_clock_s": float,
    "status": str,
    "class": str | None,
    "verdict": str | None,
    "citations": list[CounselCitation],
    "proposal": dict[str, Any],
    "forge": dict[str, Any],
    "question": str | None,
    "answers": str | None,
    "turn": dict[str, Any] | None,
    "product_version": str,
    "product_pin": str | None,
})


class StageTime(TypedDict):
    """One stage's invocations summed (the statement's `time` block)."""
    attempts: int
    invocation_s: float
    partial: bool


class ItemTime(TypedDict):
    """Every invocation's wall_clock_s, per stage and for the item, beside
    the item's lead time -- summed from the records by `attavo attest`, so
    a view renders it and never derives it."""
    build: StageTime
    verify: StageTime
    counsel: StageTime
    invocation_s: float
    lead_s: float | None
    waiting_s: float | None
    partial: bool
    not_read: list[str]


class CostRate(TypedDict):
    """A tenant's published price for one model ([cost.rates."<model>"])."""
    input_usd_per_mtok: float
    output_usd_per_mtok: float
    as_of: str
    source: str


class ItemCost(TypedDict):
    """Every record's cost summed: the item's total across stages and
    executors, with a subtotal per stage and per adapter. A reported
    cost_usd is taken as it stands; a null one whose model has a tenant
    rate is priced as tokens times that rate (issue #307), and the basis
    says which. A record neither reported nor priceable is named in
    not_read and makes every sum it belongs to partial -- absent is never
    zero."""
    usd: float
    reported_usd: float
    estimated_usd: float
    by_stage_usd: dict[str, float]
    basis_by_stage: dict[str, str]
    by_adapter_usd: dict[str, float]
    rates: dict[str, CostRate]
    estimate_note: str
    partial: bool
    not_read: list[str]


class StageTokens(TypedDict):
    """One stage's tokens summed over every attempt (the statement's `tokens` block)."""
    input: int
    output: int
    partial: bool


class ItemTokens(TypedDict):
    """Every record's tokens summed, per stage and for the item. A record
    that carries no tokens is named in not_read and makes every sum it
    belongs to partial -- absent is never zero."""
    build: StageTokens
    verify: StageTokens
    counsel: StageTokens
    input: int
    output: int
    partial: bool
    not_read: list[str]


class AttestRecord(TypedDict):
    """The statement `attavo attest` prints (schema/attestation.schema.json)."""
    sha: str
    merge_sha: str
    item: str
    spec: str | None
    spec_sha: str | None
    approval: str | None
    approval_sha: str | None
    build: str | None
    build_sha: str | None
    verify: str | None
    verify_sha: str | None
    at: str
    interventions: list[dict[str, str]]
    acts: list[dict[str, str]]
    time: ItemTime
    cost: ItemCost
    tokens: ItemTokens
    product_version: str
    product_pin: str | None


# What counsel reads a stop from: a build or verify record, or a refused
# spec-gate record (issue #133).
StageRecord = SpecRecord | BuildRecord | VerifyRecord


class DriveReport(TypedDict):
    """What `attavo drive N --report` prints (schema/drive-report.schema.json):
    the item's level, read from its records, its markers, and the forge."""
    item: str
    state: str
    kind: str
    says: str
    stage: str | None
    attempt: int | None
    record: str | None
    stop_class: str | None
    counsel: str | None
    filed: str | None
    head: str | None
    checks: list[dict[str, str]] | None
    since: str | None
    last_event_age_s: int | None
    attempts: list[dict[str, Any]] | None
    signatures_match: bool | None
    rulings_available: list[str] | None
