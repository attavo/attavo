"""The statement's `time` and `cost` blocks: every invocation of an item
summed by `attavo attest` from the records it already reads, and doctor's
sixth proof holding the statement to those records.

Regression, run red first on main at 1d2257b: the statement carried the
final attempt per stage and nothing else, so a view derived its own
"product time" -- 20 minutes for an item whose nineteen invocations ran
115 -- and no record said otherwise.

Run: python3 -m pytest tests -q
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import jsonschema

from helpers import git

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"
SCHEMA = json.loads((ROOT / "schema" / "attestation.schema.json").read_text())

# (stage, attempt, wall_clock_s, cost_usd, adapter): three builds on one
# executor, two verifies and one counsel on another that reports no cost.
INVOCATIONS: list[tuple[str, int, float | None, float | None, str]] = [
    ("build", 1, 100.5, 0.25, "alpha"),
    ("build", 2, 200.0, 0.5, "alpha"),
    ("build", 3, 50.25, 0.125, "alpha"),
    ("verify", 1, 10.0, None, "beta"),
    ("verify", 2, 30.0, None, "beta"),
    ("counsel", 1, 7.5, None, "beta"),
]


def run(root: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(ATTAVO), *args], cwd=str(root), input=stdin,
                          capture_output=True, text=True, check=False)


def write(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


def make_item(tmp_path: Path, invocations: list[tuple[str, int, float | None, float | None, str]]
              ) -> tuple[Path, str]:
    """A tenant with one item merged on main's first-parent line: spec and
    approval records, then every invocation in `invocations`, the chain
    coherent on the latest of each stage. Returns (root, merge sha)."""
    root = tmp_path / "tenant"
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main", ".")
    git(root, "config", "user.email", "fixture@attavo.invalid")
    git(root, "config", "user.name", "fixture")
    (root / "attavo.toml").write_text(
        '[paths]\nrecords = "records"\nspecs = "specs"\n\n'
        '[forge]\nattestation_command = "sh ./fake-store"\n')
    (root / "fake-store").write_text(
        '#!/bin/sh\nread sha\ntest -f "statements/$sha.json" && cat "statements/$sha.json"\n')
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "install")
    git(root, "checkout", "-q", "-b", "attavo/1-x")
    rec = root / "records" / "1"
    write(rec / "spec-01.json", {"status": "pass", "sha": "s1", "at": "2026-01-01T00:00:00Z"})
    write(rec / "approval-01.json", {"sha": "a1", "spec_sha": "s1", "at": "2026-01-01T00:00:00Z"})
    for stage, n, wall, cost, adapter in invocations:
        body: dict[str, Any] = {"status": "pass", "sha": f"{stage}{n}", "adapter": adapter,
                                "approval_sha": "a1", "cost_usd": cost}
        if wall is not None:
            body["wall_clock_s"] = wall
        if stage == "verify":
            last_build = max(i[1] for i in invocations if i[0] == "build")
            body["build_sha"] = f"build{last_build}"
        write(rec / f"{stage}-{n:02d}.json", body)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "records for item 1")
    git(root, "checkout", "-q", "main")
    git(root, "merge", "-q", "--no-ff", "-m", "Merge branch 'attavo/1-x'", "attavo/1-x")
    git(root, "update-ref", "refs/remotes/origin/main", "main")
    git(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    return root, git(root, "rev-parse", "HEAD").strip()


def attest(root: Path) -> dict[str, Any]:
    done = run(root, "attest", "1")
    assert done.returncode == 0, done.stdout + done.stderr
    statement: dict[str, Any] = json.loads(done.stdout)
    return statement


def proof_six(root: Path) -> dict[str, Any]:
    out = json.loads(run(root, "doctor", "--json", str(root)).stdout)
    found: dict[str, Any] = [p for p in out["proofs"]
                             if p["proof"] == "every-attested-item-is-sealed"][0]
    return found


def test_attest_sums_every_invocation_per_stage_and_per_item(tmp_path: Path) -> None:
    root, _ = make_item(tmp_path, INVOCATIONS)
    statement = attest(root)
    jsonschema.validate(statement, SCHEMA)
    time = statement["time"]
    assert time["build"] == {"attempts": 3, "invocation_s": 350.75, "partial": False}
    assert time["verify"] == {"attempts": 2, "invocation_s": 40.0, "partial": False}
    assert time["counsel"] == {"attempts": 1, "invocation_s": 7.5, "partial": False}
    assert time["invocation_s"] == 398.25
    assert time["partial"] is False and time["not_read"] == []
    # the final attempt stays where it was, and is not the sum
    assert statement["wall_clock_by_stage_s"] == {"build": 50.25, "verify": 30.0}
    # lead runs from approval-01's `at` to the merge's committer time
    merged_at = int(git(root, "show", "-s", "--format=%ct", "HEAD").strip())
    assert time["lead_s"] == merged_at - 1767225600
    assert time["waiting_s"] == round(time["lead_s"] - 398.25, 3)


def test_attest_names_a_record_without_wall_clock_and_never_sums_it_as_zero(tmp_path: Path) -> None:
    invocations = [i if i[:2] != ("build", 2) else ("build", 2, None, 0.5, "alpha")
                   for i in INVOCATIONS]
    root, _ = make_item(tmp_path, invocations)
    time = attest(root)["time"]
    assert time["not_read"] == ["build-02"]
    assert time["build"]["partial"] is True and time["partial"] is True
    assert time["build"]["attempts"] == 3
    assert time["verify"]["partial"] is False


def test_attest_sums_cost_across_executors_and_says_when_the_total_is_partial(tmp_path: Path) -> None:
    root, _ = make_item(tmp_path, INVOCATIONS)
    cost = attest(root)["cost"]
    assert cost["usd"] == 0.875
    assert cost["by_stage_usd"] == {"build": 0.875}
    assert cost["by_adapter_usd"] == {"alpha": 0.875}
    assert cost["partial"] is True
    assert cost["not_read"] == ["verify-01", "verify-02", "counsel-01"]

    priced = [i if i[3] is not None else (i[0], i[1], i[2], 0.0625, i[4]) for i in INVOCATIONS]
    root2, _ = make_item(tmp_path / "priced", priced)
    cost2 = attest(root2)["cost"]
    assert cost2["usd"] == 0.875 + 3 * 0.0625
    assert cost2["by_adapter_usd"] == {"alpha": 0.875, "beta": 0.1875}
    assert cost2["partial"] is False and cost2["not_read"] == []


def test_doctor_proof_six_fails_a_statement_whose_time_is_not_its_records(tmp_path: Path) -> None:
    root, merge = make_item(tmp_path, INVOCATIONS)
    statement = attest(root)
    store = root / "statements"
    store.mkdir()
    envelope = {"_type": "https://in-toto.io/Statement/v1", "predicate": statement}
    (store / f"{merge}.json").write_text(json.dumps(envelope))
    held = proof_six(root)
    assert held["holds"] is True and held["sealed"] == ["1"], held

    statement["time"]["invocation_s"] = 20.0
    (store / f"{merge}.json").write_text(json.dumps(envelope))
    failed = proof_six(root)
    assert failed["holds"] is False, failed
    assert "item 1" in failed["reason"] and "time.invocation_s" in failed["reason"], failed

    # a statement sealed before `time` and `cost` existed carries neither
    # and is not checked; one without the other is a disagreement (#203)
    del statement["time"]
    del statement["cost"]
    (store / f"{merge}.json").write_text(json.dumps(envelope))
    assert proof_six(root)["holds"] is True


def seal(root: Path, merge: str, statement: dict[str, Any]) -> None:
    store = root / "statements"
    store.mkdir(exist_ok=True)
    envelope = {"_type": "https://in-toto.io/Statement/v1", "predicate": statement}
    (store / f"{merge}.json").write_text(json.dumps(envelope))


def test_doctor_rejects_a_subsecond_time_invocation_mismatch(tmp_path: Path) -> None:
    """#202: the check allowed one second either way, so a statement could
    say 398.75 of records that sum to 398.25 and hold."""
    root, merge = make_item(tmp_path, INVOCATIONS)
    statement = attest(root)
    assert statement["time"]["invocation_s"] == 398.25
    statement["time"]["invocation_s"] = 398.75
    seal(root, merge, statement)
    failed = proof_six(root)
    assert failed["holds"] is False, failed
    assert "item 1" in failed["reason"] and "time.invocation_s" in failed["reason"], failed


def test_doctor_rejects_statement_whose_time_or_cost_breakdown_disagrees_with_records(
        tmp_path: Path) -> None:
    """#203: only time.invocation_s was compared, so every other field of
    the accounting could say anything. Each case leaves invocation_s true."""
    root, merge = make_item(tmp_path, INVOCATIONS)
    true_statement = attest(root)
    seal(root, merge, true_statement)
    assert proof_six(root)["holds"] is True

    def forged(path: tuple[str, ...], value: Any) -> dict[str, Any]:
        copy: dict[str, Any] = json.loads(json.dumps(true_statement))
        node = copy
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
        return copy

    cases: list[tuple[tuple[str, ...], Any, str]] = [
        (("time", "build", "invocation_s"), 1.0, "time.build"),
        (("time", "build", "attempts"), 1, "time.build"),
        (("time", "lead_s"), 1.0, "time.lead_s"),
        (("time", "waiting_s"), 1.0, "time.waiting_s"),
        (("time", "partial"), True, "time.partial"),
        (("time", "not_read"), ["build-02"], "time.not_read"),
        (("cost", "usd"), 0.5, "cost.usd"),
        (("cost", "by_stage_usd"), {"build": 0.5}, "cost.by_stage_usd"),
        (("cost", "by_adapter_usd"), {"alpha": 0.5}, "cost.by_adapter_usd"),
        (("cost", "partial"), False, "cost.partial"),
        (("cost", "not_read"), [], "cost.not_read"),
        (("cost", "rate_usd"), 1.0, "cost.rate_usd"),
    ]
    for path, value, named in cases:
        seal(root, merge, forged(path, value))
        failed = proof_six(root)
        assert failed["holds"] is False, (path, failed)
        assert "item 1" in failed["reason"] and named in failed["reason"], (path, failed)

    one_block: dict[str, Any] = json.loads(json.dumps(true_statement))
    del one_block["cost"]
    seal(root, merge, one_block)
    assert proof_six(root)["holds"] is False


def test_doctor_holds_a_true_statement_however_its_store_prints_it(tmp_path: Path) -> None:
    """The rule that keeps a sealed statement from wedging the doctor: a
    store may print 40.0 as 40, and a release after the seal may add a
    field the sealed statement never carried. Neither is a disagreement."""
    root, merge = make_item(tmp_path, INVOCATIONS)
    statement = attest(root)
    assert statement["time"]["verify"]["invocation_s"] == 40.0
    statement["time"]["verify"]["invocation_s"] = 40
    del statement["time"]["counsel"]
    del statement["time"]["build"]["partial"]
    seal(root, merge, statement)
    held = proof_six(root)
    assert held["holds"] is True and held["sealed"] == ["1"], held
