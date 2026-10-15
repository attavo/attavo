"""attavo ledger: every act on an item by identity and class (issue #128).

Regression tests, written and run red first: on `main` before this change
there was no `ledger` subcommand, so every one of these failed with
"unknown subcommand".

Run: python3 -m pytest tests -q
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from helpers import git, make_tenant

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"

# The four identities issue #128 names: a person (writes the spec, in the
# real templates also the one who comments /approve), the records app (a
# GitHub App's own bot login -- "<app-slug>[bot]" -- that commits
# spec-gate and approval records in attavo-spec-gate.yml and
# attavo-approve.yml), the driver (drive's own record commits), and the
# executor (an adapter invocation's own commits).
PERSON = {"name": "Alice Author", "email": "alice@example.com"}
RECORDS_APP = {"name": "attavo-bot[bot]", "email": "123+attavo-bot[bot]@users.noreply.github.com"}
DRIVER = {"name": "attavo driver", "email": "driver@attavo.invalid"}
EXECUTOR = {"name": "attavo executor (fixture)", "email": "executor+fixture@attavo.invalid"}


def run_attavo(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(ATTAVO), *args], cwd=str(root),
                          capture_output=True, text=True, check=False)


def commit_as(root: Path, identity: dict[str, Any], message: str, paths: list[str]) -> str:
    """Stage exactly paths and commit as identity, both author and
    committer -- the same shape commit_record and the tenant's own CI
    templates set (never the tenant's ambient git config)."""
    git(root, "add", *paths)
    env = dict(os.environ)
    env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = identity["name"]
    env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = identity["email"]
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", message],
                   check=True, env=env)
    return git(root, "rev-parse", "HEAD").strip()


def write_record(root: Path, item: str, stage: str, n: int, content: dict[str, Any]) -> str:
    records_dir = root / "records" / item
    records_dir.mkdir(parents=True, exist_ok=True)
    path = records_dir / f"{stage}-{n:02d}.json"
    path.write_text(json.dumps(content))
    return str(path.relative_to(root))


def add_spec(root: Path, item: str = "1") -> str:
    spec_dir = root / "specs" / item
    spec_dir.mkdir(parents=True)
    (spec_dir / "spec.md").write_text("## Problem\n\nsomething.\n")
    return commit_as(root, PERSON, f"spec: item {item}", [f"specs/{item}"])


def build_merged_item(tmp_path: Path, item: str = "1") -> Path:
    """The order #651 had: a person's spec commit, the records app's
    spec-gate and approval records, the executor's own change, the
    driver's build and verify records, then the merge landing all of it
    on main."""
    root = make_tenant(tmp_path, "ledger_base")
    git(root, "checkout", "-b", "item")

    spec_sha = add_spec(root, item)

    rel = write_record(root, item, "spec", 1,
                       {"status": "pass", "spec_sha": spec_sha, "product_version": "v0.19.1"})
    commit_as(root, RECORDS_APP, f"attavo: spec-gate record for item {item}", [rel])

    rel = write_record(root, item, "approval", 1,
                       {"approved_by": "alice", "sha": spec_sha, "spec_sha": spec_sha,
                        "product_version": "v0.19.1"})
    commit_as(root, RECORDS_APP, f"attavo: approval record for item {item}", [rel])

    (root / "built.txt").write_text("built\n")
    commit_as(root, EXECUTOR, f"fixture: build item {item}", ["built.txt"])

    rel = write_record(root, item, "build", 1, {"status": "pass", "product_version": "v0.19.1"})
    commit_as(root, DRIVER, f"attavo: build record for item {item} (build-01)", [rel])

    rel = write_record(root, item, "verify", 1, {"status": "pass", "product_version": "v0.19.1"})
    commit_as(root, DRIVER, f"attavo: verify record for item {item} (verify-01)", [rel])

    git(root, "checkout", "main")
    subprocess.run(["git", "-C", str(root), "merge", "--no-ff", "item",
                    "-m", f"Merge pull request #{item} from x/item"], check=True)
    return root


def test_ledger_classifies_every_commit_and_counts_operator_acts(tmp_path: Path) -> None:
    """Acceptance row 1: on a fixture with the four identities, every
    commit gets exactly one class and the summary counts operator acts 2
    -- the same shape tenant zero's own #651 reads (1 approve, 1 merge)."""
    root = build_merged_item(tmp_path)

    run = run_attavo(root, "ledger", "1", "--json")
    assert run.returncode == 0, run.stdout + run.stderr
    data = json.loads(run.stdout)

    classes = [row["class"] for row in data["rows"]]
    assert classes == ["spec", "record", "approve", "change", "record", "record", "merge"]
    assert all(row["class"] and row["sha"] and row["identity"] for row in data["rows"])

    summary = data["summary"]
    assert summary["operator_acts"] == 2
    assert summary["product_acts"] == 3
    assert summary["executor_commits"] == 1
    assert summary["stops"] == 0
    assert summary["since"] == "v0.19.1"


def test_ledger_stops_count_matches_stops_including_a_gate_refusal(tmp_path: Path) -> None:
    """Issue #137, acceptance row 2. A refused spec-gate record and a
    non-pass build record: ledger's own stops count is the same set
    `attavo stops` counts, gate refusals included -- both computed by
    item_stops, not two producers reading one item and printing
    different numbers (tenant zero #683: stops printed 1, ledger printed
    0, because ledger's own count only ever looked at build and verify).
    """
    root = make_tenant(tmp_path, "ledger_base")
    add_spec(root)

    write_record(root, "1", "spec", 1,
                {"status": "refused",
                 "refusal": "criterion fails against the tree plus stub: "
                            "doctor-template-absent.sh"})
    write_record(root, "1", "build", 1, {"status": "failed"})

    ledger_run = run_attavo(root, "ledger", "1", "--json")
    assert ledger_run.returncode == 0, ledger_run.stdout + ledger_run.stderr
    stops_run = run_attavo(root, "stops", "1", "--json")
    assert stops_run.returncode == 0, stops_run.stdout + stops_run.stderr

    ledger_data = json.loads(ledger_run.stdout)
    stops_data = json.loads(stops_run.stdout)

    assert len(stops_data["rows"]) == 2
    assert ledger_data["summary"]["stops"] == len(stops_data["rows"])


def test_ledger_marks_a_pre_v018_record_commit_as_record_or_launch(tmp_path: Path) -> None:
    """Acceptance row 2: a person's record commit before v0.18.0 prints
    record-or-launch with the note, never launch or record -- before that
    version the driver and the executor had no identity of their own to
    tell a genuine launch apart from an automated one."""
    root = make_tenant(tmp_path, "ledger_base")
    add_spec(root)

    rel = write_record(root, "1", "build", 1, {"status": "pass", "product_version": "v0.17.0"})
    person_sha = commit_as(root, PERSON, "manual build record for item 1", [rel])

    run = run_attavo(root, "ledger", "1", "--json")
    assert run.returncode == 0, run.stdout + run.stderr
    data = json.loads(run.stdout)

    row = next(r for r in data["rows"] if r["sha"] == person_sha)
    assert row["class"] == "record-or-launch"
    assert row["note"] == "not distinguishable before v0.18.0"
    assert data["summary"]["operator_acts"] == 0
    assert data["summary"]["product_acts"] == 0


def test_ledger_classifies_an_invocation_marker_commit_as_a_product_record(tmp_path: Path) -> None:
    """Issue #151: the invocation marker's own commit, authored by the
    driver identity, touches paths.records without matching a stage
    record's own NN.json name -- classified "record", the product's own
    act, never "transport". Otherwise the same order build_merged_item
    sets up, with the marker's own commit landing just ahead of the
    build record it precedes in a real drive run."""
    root = make_tenant(tmp_path, "ledger_base")
    git(root, "checkout", "-b", "item")

    spec_sha = add_spec(root, "1")

    rel = write_record(root, "1", "spec", 1,
                       {"status": "pass", "spec_sha": spec_sha, "product_version": "v0.19.1"})
    commit_as(root, RECORDS_APP, "attavo: spec-gate record for item 1", [rel])

    rel = write_record(root, "1", "approval", 1,
                       {"approved_by": "alice", "sha": spec_sha, "spec_sha": spec_sha,
                        "product_version": "v0.19.1"})
    commit_as(root, RECORDS_APP, "attavo: approval record for item 1", [rel])

    marker_dir = root / "records" / "1"
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker = marker_dir / "build-01.started"
    marker.write_text(json.dumps({"at": "2026-01-01T00:00:00Z", "host": "h", "pid": 1,
                                  "sha": spec_sha, "product_version": "v0.19.1"}))
    marker_sha = commit_as(root, DRIVER, "attavo: build invocation 01 started for item 1",
                           [str(marker.relative_to(root))])

    (root / "built.txt").write_text("built\n")
    commit_as(root, EXECUTOR, "fixture: build item 1", ["built.txt"])

    rel = write_record(root, "1", "build", 1, {"status": "pass", "product_version": "v0.19.1"})
    commit_as(root, DRIVER, "attavo: build record for item 1 (build-01)", [rel])

    rel = write_record(root, "1", "verify", 1, {"status": "pass", "product_version": "v0.19.1"})
    commit_as(root, DRIVER, "attavo: verify record for item 1 (verify-01)", [rel])

    git(root, "checkout", "main")
    subprocess.run(["git", "-C", str(root), "merge", "--no-ff", "item",
                    "-m", "Merge pull request #1 from x/item"], check=True)

    run = run_attavo(root, "ledger", "1", "--json")
    assert run.returncode == 0, run.stdout + run.stderr
    data = json.loads(run.stdout)

    row = next(r for r in data["rows"] if r["sha"] == marker_sha)
    assert row["class"] == "record"

    classes = [r["class"] for r in data["rows"]]
    assert classes == ["spec", "record", "approve", "record", "change", "record", "record", "merge"]
    assert data["summary"]["product_acts"] == 4
    assert data["summary"]["operator_acts"] == 2


def test_ledger_json_is_one_object_matching_the_text_forms_numbers(tmp_path: Path) -> None:
    """Acceptance row 3: --json is one object with rows and summary, and
    the text form prints the same numbers."""
    root = build_merged_item(tmp_path)

    run_json = run_attavo(root, "ledger", "1", "--json")
    assert run_json.returncode == 0, run_json.stdout + run_json.stderr
    data = json.loads(run_json.stdout)
    assert set(data) == {"item", "rows", "summary"}
    assert isinstance(data["rows"], list)
    assert isinstance(data["summary"], dict)

    run_text = run_attavo(root, "ledger", "1")
    assert run_text.returncode == 0, run_text.stdout + run_text.stderr
    summary = data["summary"]
    assert f"operator acts: {summary['operator_acts']}" in run_text.stdout
    assert f"product acts: {summary['product_acts']}" in run_text.stdout
    assert f"executor commits: {summary['executor_commits']}" in run_text.stdout
    assert f"stops: {summary['stops']}" in run_text.stdout
    assert f"since: {summary['since']}" in run_text.stdout


# Issue #350: each ledger row names what its commit carries.

def build_merged_item_with_a_marker_a_transport_and_a_store(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """build_merged_item's order plus a marker commit, a person's transport
    commit after approval, and a [forge].attestation_command whose store
    holds the merge's statement. Returns (root, shas by role)."""
    root = make_tenant(tmp_path, "ledger_base")
    (root / "attavo.toml").write_text(
        (root / "attavo.toml").read_text() + '\n[forge]\nattestation_command = "sh ./fake-store"\n')
    (root / "fake-store").write_text(
        '#!/bin/sh\nread sha\ntest -f "statements/$sha.json" && cat "statements/$sha.json"\n')
    commit_as(root, PERSON, "install the store read", ["attavo.toml", "fake-store"])
    git(root, "checkout", "-b", "item")
    shas: dict[str, str] = {}
    shas["spec"] = add_spec(root, "1")
    rel = write_record(root, "1", "spec", 1, {"status": "pass", "spec_sha": shas["spec"], "product_version": "v0.19.1"})
    shas["spec_record"] = commit_as(root, RECORDS_APP, "attavo: spec-gate record for item 1", [rel])
    rel = write_record(root, "1", "approval", 1, {"approved_by": "alice", "sha": shas["spec"],
                                                  "spec_sha": shas["spec"], "product_version": "v0.19.1"})
    shas["approve"] = commit_as(root, RECORDS_APP, "attavo: approval record for item 1", [rel])
    (root / "NOTES.md").write_text("a person's commit mid-run\n")
    shas["transport"] = commit_as(root, PERSON, "notes", ["NOTES.md"])
    marker = root / "records" / "1" / "build-01.started"
    marker.write_text(json.dumps({"at": "2026-01-01T00:00:00Z", "host": "h", "sha": shas["spec"],
                                  "product_version": "v0.19.1"}))
    shas["marker"] = commit_as(root, DRIVER, "attavo: build invocation 01 started for item 1",
                               [str(marker.relative_to(root))])
    (root / "built.txt").write_text("built\n")
    shas["change"] = commit_as(root, EXECUTOR, "fixture: build item 1", ["built.txt"])
    rel = write_record(root, "1", "build", 1, {"status": "pass", "product_version": "v0.19.1"})
    shas["build"] = commit_as(root, DRIVER, "attavo: build record for item 1 (build-01)", [rel])
    rel = write_record(root, "1", "verify", 1, {"status": "pass", "product_version": "v0.19.1"})
    shas["verify"] = commit_as(root, DRIVER, "attavo: verify record for item 1 (verify-01)", [rel])
    git(root, "checkout", "main")
    subprocess.run(["git", "-C", str(root), "merge", "--no-ff", "item", "-m", "Merge pull request #1 from x/item"],
                   check=True)
    shas["merge"] = git(root, "rev-parse", "HEAD").strip()
    store = root / "statements"
    store.mkdir()
    (store / f"{shas['merge']}.json").write_text(json.dumps({
        "_type": "https://in-toto.io/Statement/v1",
        "predicateType": "https://attavo.dev/attestation/v6",
        "subject": [{"name": "attavo/attavo", "digest": {"sha256": "ab" * 32}}],
        "predicate": {"item": "1"}}))
    return root, shas


def test_each_ledger_row_names_what_its_commit_carries(tmp_path: Path) -> None:
    """Specimen: #344's ledger (2026-10-07) read "not read" for the marker
    commits, the two transports and the merge, when each carries something
    nameable: the .started file, nothing by design, and the statement."""
    root, shas = build_merged_item_with_a_marker_a_transport_and_a_store(tmp_path)
    run = run_attavo(root, "ledger", "1", "--json")
    assert run.returncode == 0, run.stdout + run.stderr
    rows = {r["sha"]: r for r in json.loads(run.stdout)["rows"]}
    assert rows[shas["spec"]]["carries"] == ["specs/1/spec.md"]
    assert rows[shas["spec_record"]]["carries"] == ["records/1/spec-01.json"]
    assert rows[shas["approve"]]["carries"] == ["records/1/approval-01.json"]
    assert rows[shas["marker"]]["carries"] == ["records/1/build-01.started"]
    assert rows[shas["change"]]["carries"] == ["built.txt"]
    assert rows[shas["build"]]["carries"] == ["records/1/build-01.json"]
    assert rows[shas["transport"]]["class"] == "transport" and rows[shas["transport"]]["carries"] == []
    merge = rows[shas["merge"]]
    assert merge["carries"] == []
    assert merge["statement"] == {"predicate_type": "https://attavo.dev/attestation/v6", "subject": "ab" * 32}


def test_the_merge_row_says_not_read_when_the_store_does_not_answer(tmp_path: Path) -> None:
    root, shas = build_merged_item_with_a_marker_a_transport_and_a_store(tmp_path)
    (root / "fake-store").write_text("#!/bin/sh\nexit 2\n")
    run = run_attavo(root, "ledger", "1", "--json")
    assert run.returncode == 0, run.stdout + run.stderr
    merge = next(r for r in json.loads(run.stdout)["rows"] if r["sha"] == shas["merge"])
    assert merge["statement"] is None
    assert "not read" in merge["note"] and "store" in merge["note"]
