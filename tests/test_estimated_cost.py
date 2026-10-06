"""The statement's estimated cost (issue #307): a record whose adapter
reported no price is priced at attest time as tokens times the tenant's
published rate, never written into a record, and doctor's sixth proof
holding the statement to the same function.

Specimen: #277's verify-01, gpt-5.6-sol, 184,076 input and 1,884 output
tokens, `cost_usd` null -- read as permanently "not read" until now.

Regression, run red first on origin/main at ffd1208: every test below
failed there (no `[cost.rates]` in the manifest schema, no basis, no
estimate, predicate v4), except the one that asserts no record is written,
which holds on both sides and guards the ruling.

Run: python3 -m pytest tests -q
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from attavo.doctor import _accounting_disagrees
from helpers import git
from test_attest_time import (INVOCATIONS, V5_COST_FIELDS, attest, make_item, proof_six, seal)

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_SCHEMA = json.loads((ROOT / "schema" / "manifest.schema.json").read_text())
ATTESTATION_SCHEMA = json.loads((ROOT / "schema" / "attestation.schema.json").read_text())

CODEX = "gpt-5.6-sol"
CLAUDE = "claude-opus-5-5"
RATE_TABLE = f"""
[cost.rates."{CODEX}"]
input_usd_per_mtok = 1.25
output_usd_per_mtok = 10.0
as_of = "2026-10-05"
source = "https://example.invalid/pricing"

[cost.rates."{CLAUDE}"]
input_usd_per_mtok = 15.0
output_usd_per_mtok = 75.0
as_of = "2026-10-05"
source = "https://example.invalid/pricing"
"""
CODEX_RATE = {"input_usd_per_mtok": 1.25, "output_usd_per_mtok": 10.0,
              "as_of": "2026-10-05", "source": "https://example.invalid/pricing"}
# 184076 * 1.25 / 1e6 + 1884 * 10 / 1e6
VERIFY_ESTIMATE = 0.248935
SPECIMEN_TOKENS = {"verify-01": {"input": 184076, "output": 1884}}

# A Claude Code build that reported its cost; a Codex verify that did not.
SPECIMEN: list[tuple[str, int, float | None, float | None, str]] = [
    ("build", 1, 100.0, 0.5, "claude-code"),
    ("verify", 1, 50.0, None, "codex"),
]
MODELS = {"build-01": CLAUDE, "verify-01": CODEX}


def specimen(tmp_path: Path, manifest_extra: str,
             invocations: list[tuple[str, int, float | None, float | None, str]] = SPECIMEN,
             models: dict[str, str] = MODELS) -> tuple[Path, str]:
    return make_item(tmp_path, invocations, manifest_extra=manifest_extra, models=models,
                     tokens=SPECIMEN_TOKENS)


def test_a_codex_record_is_priced_from_the_tenants_rate_and_the_totals_add_up(
        tmp_path: Path) -> None:
    """#277's verify-01 (gpt-5.6-sol, 184,076 input, 1,884 output, cost_usd
    null) reads as tokens times the manifest's rate, basis `estimated`; the
    Claude Code build keeps what it reported; usd is the two together."""
    root, _ = specimen(tmp_path, RATE_TABLE)
    statement = attest(root)
    jsonschema.validate(statement, ATTESTATION_SCHEMA)
    cost = statement["cost"]
    assert cost["basis_by_stage"] == {"build": "reported", "verify": "estimated"}
    assert cost["by_stage_usd"] == {"build": 0.5, "verify": VERIFY_ESTIMATE}
    assert cost["by_adapter_usd"] == {"claude-code": 0.5, "codex": VERIFY_ESTIMATE}
    assert cost["reported_usd"] == 0.5 and cost["estimated_usd"] == VERIFY_ESTIMATE
    assert cost["usd"] == round(0.5 + VERIFY_ESTIMATE, 6)
    assert cost["partial"] is False and cost["not_read"] == []
    assert cost["rates"] == {CODEX: CODEX_RATE}, "only the rate a record was priced by"


def test_the_estimate_is_computed_in_the_statement_and_written_into_no_record(
        tmp_path: Path) -> None:
    """The 2026-10-05 ruling: verify-01 keeps cost_usd null on disk."""
    root, _ = specimen(tmp_path, RATE_TABLE)
    attest(root)
    record = json.loads((root / "records" / "1" / "verify-01.json").read_text())
    assert record["cost_usd"] is None


def test_the_statement_says_what_an_estimate_is(tmp_path: Path) -> None:
    """Estimated amounts are tokens times the tenant's published rates, a
    meter at API prices, not a bill."""
    root, _ = specimen(tmp_path, RATE_TABLE)
    note = attest(root)["cost"]["estimate_note"]
    assert note == ("Estimated amounts are tokens times the tenant's published rates: "
                    "a meter at API prices, not a bill.")


def test_without_a_rate_the_record_stays_not_read_and_names_the_missing_rate(
        tmp_path: Path) -> None:
    """#277's verify-01 with no [cost.rates] table: not priced, named, partial."""
    root, _ = specimen(tmp_path, "")
    cost = attest(root)["cost"]
    assert cost["not_read"] == [f"verify-01 (no [cost.rates] for {CODEX})"]
    assert cost["partial"] is True
    assert cost["usd"] == 0.5 and cost["reported_usd"] == 0.5 and cost["estimated_usd"] == 0.0
    assert cost["basis_by_stage"] == {"build": "reported"}
    assert cost["rates"] == {}


def test_a_claude_code_record_stays_reported_and_a_stage_with_both_reads_mixed(
        tmp_path: Path) -> None:
    """A stage whose attempts include #277's verify-01 (priced) and a verify
    that reported its own cost reads `mixed`; a reported cost is never
    re-priced, though its model has a rate."""
    invocations = SPECIMEN + [("verify", 2, 20.0, 0.1, "claude-code")]
    root, _ = specimen(tmp_path, RATE_TABLE, invocations, {**MODELS, "verify-02": CLAUDE})
    cost = attest(root)["cost"]
    assert cost["basis_by_stage"] == {"build": "reported", "verify": "mixed"}
    assert cost["by_stage_usd"]["verify"] == round(VERIFY_ESTIMATE + 0.1, 6)
    assert cost["reported_usd"] == 0.6 and cost["estimated_usd"] == VERIFY_ESTIMATE
    assert CLAUDE not in cost["rates"]


def test_doctor_proof_six_holds_a_v5_estimate_and_fails_one_that_differs(tmp_path: Path) -> None:
    """#277's verify-01 priced in a v5 statement holds; an estimate, a sum,
    a basis or a carried rate that is not what the records and rates give
    fails, naming the item and the cost field."""
    root, merge = specimen(tmp_path, RATE_TABLE)
    statement = attest(root)
    seal(root, merge, statement, "https://attavo.dev/attestation/v5")
    held = proof_six(root)
    assert held["holds"] is True and held["sealed"] == ["1"], held

    edits: list[tuple[tuple[str, ...], Any]] = [
        (("estimated_usd",), 0.25), (("usd",), 0.75), (("by_stage_usd", "verify"), 0.3),
        (("basis_by_stage", "verify"), "reported"), (("rates", CODEX, "input_usd_per_mtok"), 2.0),
        (("estimate_note",), "A bill.")]
    for path, value in edits:
        forged: dict[str, Any] = json.loads(json.dumps(statement))
        node = forged["cost"]
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
        seal(root, merge, forged, "https://attavo.dev/attestation/v5")
        failed = proof_six(root)
        assert failed["holds"] is False, (path, failed)
        assert "item 1" in failed["reason"] and "cost" in failed["reason"], failed


def test_doctor_proof_six_still_holds_v4_and_v3_statements_by_their_own_rules(
        tmp_path: Path) -> None:
    """A statement sealed before #307 priced nothing: #277's verify-01 stays
    unread in it, though the manifest has a rate now, and it holds. v4 by
    its predicateType and, with none declared, by carrying no v5 field."""
    root, merge = specimen(tmp_path, RATE_TABLE)
    statement = attest(root)
    v4: dict[str, Any] = json.loads(json.dumps(statement))
    for field in V5_COST_FIELDS:
        v4["cost"].pop(field)
    v4["cost"]["usd"] = 0.5
    v4["cost"]["by_stage_usd"] = {"build": 0.5}
    v4["cost"]["by_adapter_usd"] = {"claude-code": 0.5}
    v4["cost"]["partial"] = True
    v4["cost"]["not_read"] = ["verify-01"]
    for declared in ("https://attavo.dev/attestation/v4", None):
        seal(root, merge, v4, declared)
        held = proof_six(root)
        assert held["holds"] is True and held["sealed"] == ["1"], (declared, held)
    v4["cost"]["usd"] = 0.75
    seal(root, merge, v4, "https://attavo.dev/attestation/v4")
    assert proof_six(root)["holds"] is False, "v4's cost is still checked"

    v3: dict[str, Any] = json.loads(json.dumps(statement))
    for field in V5_COST_FIELDS:
        v3["cost"].pop(field)
    v3["cost"].update({"usd": 0.5, "by_stage_usd": {"build": 0.5},
                       "by_adapter_usd": {"claude-code": 0.5},
                       "partial": True, "not_read": ["verify-01"]})
    v3.pop("tokens")
    v3["cost_by_stage_usd"] = {"build": 0.5, "verify": None}
    seal(root, merge, v3, "https://attavo.dev/attestation/v3")
    held = proof_six(root)
    assert held["holds"] is True and held["sealed"] == ["1"], held


def test_the_manifest_schema_accepts_the_rate_table_and_refuses_a_rate_with_no_as_of() -> None:
    """The product carries no rate of its own: the tenant's table, each
    rate dated and sourced."""
    cost = MANIFEST_SCHEMA["properties"]["cost"]
    jsonschema.validate({"rates": {CODEX: CODEX_RATE}}, cost)
    undated = {k: v for k, v in CODEX_RATE.items() if k != "as_of"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"rates": {CODEX: undated}}, cost)
    unsourced = {k: v for k, v in CODEX_RATE.items() if k != "source"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"rates": {CODEX: unsourced}}, cost)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"rates": {CODEX: {**CODEX_RATE, "input_usd_per_mtok": -1}}}, cost)


def test_an_item_with_no_priced_record_keeps_the_v4_totals(tmp_path: Path) -> None:
    """INVOCATIONS carry no model: nothing is priced, nothing is renamed in not_read."""
    root, _ = make_item(tmp_path, INVOCATIONS, manifest_extra=RATE_TABLE)
    cost = attest(root)["cost"]
    assert cost["usd"] == 0.875 and cost["estimated_usd"] == 0.0
    assert cost["not_read"] == ["verify-01", "verify-02", "counsel-01"]


def test_a_v5_statement_whose_carried_rate_differs_from_the_manifest_at_its_sealed_commit_fails(
        tmp_path: Path) -> None:
    """#277's verify-01 priced at a rate the manifest never declared at the
    sealed commit: the statement's own arithmetic is consistent, but the
    rate it quotes is not the tenant's, so proof six fails, naming cost.rates."""
    root, merge = specimen(tmp_path, RATE_TABLE)
    statement = attest(root)
    quoted = {**CODEX_RATE, "input_usd_per_mtok": 0.01, "output_usd_per_mtok": 0.01}
    statement["cost"]["rates"] = {CODEX: quoted}
    statement["cost"]["estimated_usd"] = 0.003604
    statement["cost"]["by_stage_usd"]["verify"] = 0.003604
    statement["cost"]["by_adapter_usd"]["codex"] = 0.003604
    statement["cost"]["usd"] = 0.503604
    seal(root, merge, statement, "https://attavo.dev/attestation/v5")
    failed = proof_six(root)
    assert failed["holds"] is False, failed
    assert "item 1" in failed["reason"] and "cost.rates" in failed["reason"], failed


def test_a_v5_statement_still_holds_after_the_manifests_rate_changes_in_a_later_commit(
        tmp_path: Path) -> None:
    """The sealed commit does not move: a rate edited afterwards unseals nothing."""
    root, merge = specimen(tmp_path, RATE_TABLE)
    seal(root, merge, attest(root), "https://attavo.dev/attestation/v5")
    manifest = root / "attavo.toml"
    manifest.write_text(manifest.read_text().replace("input_usd_per_mtok = 1.25",
                                                     "input_usd_per_mtok = 9.99"))
    git(root, "commit", "-q", "-am", "reprice")
    held = proof_six(root)
    assert held["holds"] is True and held["sealed"] == ["1"], held


def test_a_v5_statement_fails_when_the_manifest_cannot_be_read_at_its_sealed_commit(
        tmp_path: Path) -> None:
    """No fallback to the carried rates: a sealed commit that holds no
    manifest fails the proof and names that."""
    root, _ = specimen(tmp_path, RATE_TABLE)
    statement = attest(root)
    empty = git(root, "hash-object", "-t", "tree", "/dev/null").strip()
    bare = git(root, "commit-tree", empty, "-m", "no manifest").strip()
    envelope = {"predicateType": "https://attavo.dev/attestation/v5", "predicate": statement}
    reason = _accounting_disagrees(root, root / "records" / "1", "1", bare, envelope)
    assert reason is not None and "item 1" in reason, reason
    assert "attavo.toml cannot be read at the sealed commit" in reason, reason
