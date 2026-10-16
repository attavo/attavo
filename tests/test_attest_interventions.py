"""The statement's acts and interventions are read from the item's own
commits, the same classification `attavo ledger` makes (issue #349).

Specimen: #344's v5 statement (merged as PR #345, d2354e27) carried
`acts: []` and `interventions: []`, hard-coded in attavo/attest.py. Its
ledger showed four operator acts: an approval, two merges of main into the
item's branch made mid-run (8d4fd15, 19c384f), and the merge.

Run: python3 -m pytest tests/test_attest_interventions.py -q
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from helpers import commit_all, git, make_tenant
from test_attest import STATEMENT_SCHEMA, configure_forge, merge_into_main, run_attavo
from test_attest_time import INVOCATIONS, attest, make_item, proof_six, seal


def traverse_with_a_transport(root: Path) -> str:
    """spec-gate and approve on the item's branch; then a person merges
    main into it, as the operator did twice on #344; then build and
    verify. Returns the sha of that transport merge."""
    git(root, "checkout", "-b", "attavo/1-x")
    assert run_attavo(root, "spec-gate", "1").returncode == 0
    commit_all(root, "attavo: spec-gate record for #1")
    assert run_attavo(root, "approve", "1", "--by", "alice").returncode == 0
    commit_all(root, "attavo: approval record for #1")
    git(root, "checkout", "main")
    (root / "OTHER.md").write_text("landed on main while the item was in flight\n")
    commit_all(root, "an unrelated change on main")
    git(root, "checkout", "attavo/1-x")
    git(root, "merge", "--no-ff", "-m", "Merge main into attavo/1-x", "main")
    transport = git(root, "rev-parse", "HEAD").strip()
    assert run_attavo(root, "build", "1").returncode == 0
    commit_all(root, "attavo: build record for #1")
    assert run_attavo(root, "verify", "1").returncode == 0
    commit_all(root, "attavo: verify record for #1")
    merge_into_main(root, "attavo/1-x")
    return transport


def test_a_transport_between_approval_and_merge_is_an_intervention(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "build_valid")
    configure_forge(root)
    transport = traverse_with_a_transport(root)
    run = run_attavo(root, "attest")
    assert run.returncode == 0, run.stdout + run.stderr
    statement = json.loads(run.stdout)
    jsonschema.validate(statement, STATEMENT_SCHEMA)
    interventions = statement["interventions"]
    assert any(i["sha"] == transport and i["class"] == "transport" for i in interventions), interventions
    acts = {a["act"] for a in statement["acts"]}
    assert {"approve", "merge"} <= acts, statement["acts"]
    assert all(row["sha"] and row["by"] and row["at"] for row in statement["acts"] + interventions)


def test_doctor_proof_six_recomputes_acts_and_interventions_for_a_v6_statement(
        tmp_path: Path) -> None:
    """Proof six holds a true v6 statement's acts and interventions, the
    same way it already holds time and cost, and fails one whose lists are
    not what the item's own commits give, naming the field. make_item's
    fixture carries no specs/ tree at all, so both lists are legitimately
    `[]` here (item_ledger finds no first spec commit to walk from) --
    declared explicitly as v6, since the heuristic that sniffs it from a
    sha-bearing row has nothing to find in an empty list."""
    root, merge = make_item(tmp_path, INVOCATIONS)
    statement = attest(root)
    assert statement["acts"] == [] and statement["interventions"] == []
    seal(root, merge, statement, "https://attavo.dev/attestation/v6")
    held = proof_six(root)
    assert held["holds"] is True and held["sealed"] == ["1"], held

    forged_acts: dict[str, Any] = json.loads(json.dumps(statement))
    forged_acts["acts"] = [{"act": "approve", "sha": "f" * 40, "by": "nobody",
                           "at": "2026-01-01T00:00:00Z"}]
    seal(root, merge, forged_acts, "https://attavo.dev/attestation/v6")
    failed = proof_six(root)
    assert failed["holds"] is False, failed
    assert "item 1" in failed["reason"] and "acts" in failed["reason"], failed

    forged_interventions: dict[str, Any] = json.loads(json.dumps(statement))
    forged_interventions["interventions"] = [
        {"class": "transport", "sha": "f" * 40, "by": "nobody", "at": "2026-01-01T00:00:00Z"}]
    seal(root, merge, forged_interventions, "https://attavo.dev/attestation/v6")
    failed = proof_six(root)
    assert failed["holds"] is False, failed
    assert "item 1" in failed["reason"] and "interventions" in failed["reason"], failed


def test_doctor_proof_six_rejects_a_v6_statement_without_time_or_cost_whose_acts_or_interventions_differ(
        tmp_path: Path) -> None:
    """#353's review of #349: `_accounting_disagrees` returned None
    outright when a statement carried neither `time` nor `cost`, so a v6
    statement that omitted both was held with forged acts or
    interventions never compared. From v6 both are always required, so
    their bare absence now fails by name too, as a null block already
    did."""
    root, merge = make_item(tmp_path, INVOCATIONS)
    statement = attest(root)
    assert statement["acts"] == [] and statement["interventions"] == []
    del statement["time"]
    del statement["cost"]
    seal(root, merge, statement, "https://attavo.dev/attestation/v6")
    failed = proof_six(root)
    assert failed["holds"] is False, failed
    assert "item 1" in failed["reason"] and "time" in failed["reason"], failed

    forged_acts: dict[str, Any] = json.loads(json.dumps(statement))
    forged_acts["acts"] = [{"act": "approve", "sha": "f" * 40, "by": "nobody",
                           "at": "2026-01-01T00:00:00Z"}]
    seal(root, merge, forged_acts, "https://attavo.dev/attestation/v6")
    failed = proof_six(root)
    assert failed["holds"] is False, failed
    assert "item 1" in failed["reason"] and "acts" in failed["reason"], failed

    forged_interventions: dict[str, Any] = json.loads(json.dumps(statement))
    forged_interventions["interventions"] = [
        {"class": "transport", "sha": "f" * 40, "by": "nobody", "at": "2026-01-01T00:00:00Z"}]
    seal(root, merge, forged_interventions, "https://attavo.dev/attestation/v6")
    failed = proof_six(root)
    assert failed["holds"] is False, failed
    assert "item 1" in failed["reason"] and "interventions" in failed["reason"], failed


def test_v6_schema_rejects_an_intervention_class_outside_amend_launch_transport(
        tmp_path: Path) -> None:
    """#353: the schema took any non-empty string for an intervention's
    class, though the producer (`item_acts_and_interventions`) emits only
    amend, launch and transport."""
    root, _ = make_item(tmp_path, INVOCATIONS)
    statement = attest(root)
    jsonschema.validate(statement, STATEMENT_SCHEMA)
    for cls in ("amend", "launch", "transport"):
        valid: dict[str, Any] = json.loads(json.dumps(statement))
        valid["interventions"] = [{"at": "2026-01-01T00:00:00Z", "class": cls,
                                   "by": "nobody", "sha": "f" * 40}]
        jsonschema.validate(valid, STATEMENT_SCHEMA)
    for cls in ("merge", "approve", "spec", "change", "record", "bogus"):
        invalid: dict[str, Any] = json.loads(json.dumps(statement))
        invalid["interventions"] = [{"at": "2026-01-01T00:00:00Z", "class": cls,
                                     "by": "nobody", "sha": "f" * 40}]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(invalid, STATEMENT_SCHEMA)


def test_attest_refuses_when_the_ledger_cannot_read_the_default_branch(tmp_path: Path) -> None:
    """Specimen: item 350's statement, sealed 2026-10-15 from a merge-group
    checkout with no refs/remotes/origin/HEAD: the refused merges read was
    signed as `acts: []` and `interventions: []`. attest names the ref and
    prints no statement."""
    root, _ = make_item(tmp_path, INVOCATIONS)
    git(root, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
    run = run_attavo(root, "attest", "1")
    assert run.returncode == 2, run.stdout + run.stderr
    assert "refs/remotes/origin/HEAD" in run.stderr
    assert run.stdout == ""


def test_the_statement_carries_interventions_and_no_acts(tmp_path: Path) -> None:
    root, _ = make_item(tmp_path, INVOCATIONS)
    statement = attest(root)
    assert "interventions" in statement and "acts" not in statement
    jsonschema.validate(statement, STATEMENT_SCHEMA)
    with_acts: dict[str, Any] = json.loads(json.dumps(statement))
    with_acts["acts"] = []
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(with_acts, STATEMENT_SCHEMA)
