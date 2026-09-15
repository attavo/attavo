"""attavo stops: every non-pass record by class, provenance, and answerer
(issue #129).

Regression tests, written and run red first: on `main` before this change
there was no `stops` subcommand, so every one of these failed with
"unknown subcommand".

Run: python3 -m pytest tests -q
"""
import json
import subprocess
import sys
from pathlib import Path

from helpers import make_tenant

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"


def run_attavo(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ATTAVO), *args], cwd=str(root),
                          capture_output=True, text=True, check=False)


def write_record(root: Path, item: str, stage: str, n: int, content: dict) -> Path:
    records_dir = root / "records" / item
    records_dir.mkdir(parents=True, exist_ok=True)
    path = records_dir / f"{stage}-{n:02d}.json"
    path.write_text(json.dumps(content))
    return path


def write_counsel(root: Path, item: str, n: int, record: str, verdict: str) -> None:
    write_record(root, item, "counsel", n, {"record": record, "verdict": verdict,
                                            "citations": [], "proposal": None})


def build_fixture(tmp_path: Path, item: str = "1") -> Path:
    """One record per answerer kind: a guarded class with no counsel, a
    counsel record for each of the three verdicts (overriding a class
    that would otherwise answer "guard" or fall to "none"), a question
    with no counsel, an unclassed record with no counsel, and a record
    that predates stop entirely."""
    root = make_tenant(tmp_path, "ledger_base")

    write_record(root, item, "build", 1,
                {"status": "failed",
                 "stop": {"class": "ceiling", "provenance": "product", "detail": None}})

    write_record(root, item, "build", 2,
                {"status": "failed",
                 "stop": {"class": "no-cause", "provenance": "product", "detail": None}})
    write_counsel(root, item, 1, "build-02", "answer")

    write_record(root, item, "build", 3,
                {"status": "halted",
                 "stop": {"class": "question", "provenance": "spec", "detail": "what now?"}})

    write_record(root, item, "build", 4,
                {"status": "failed",
                 "stop": {"class": "unclassed", "provenance": "product", "detail": "boom line"}})

    write_record(root, item, "build", 5, {"status": "failed"})

    write_record(root, item, "verify", 1,
                {"status": "failed",
                 "stop": {"class": "claims-boundary", "provenance": "spec", "detail": None}})
    write_counsel(root, item, 2, "verify-01", "patch")

    write_record(root, item, "verify", 2,
                {"status": "failed",
                 "stop": {"class": "repeated-act", "provenance": "executor", "detail": None}})
    write_counsel(root, item, 3, "verify-02", "person")

    # A passing record must never appear as a row.
    write_record(root, item, "verify", 3, {"status": "pass"})

    return root


def rows_by_record(data: dict) -> dict:
    return {row["record"]: row for row in data["rows"]}


def test_stops_classifies_every_answerer_and_totals(tmp_path):
    """Acceptance row 1: every non-pass record in the fixture gets exactly
    one answerer from the closed list, and totals match."""
    root = build_fixture(tmp_path)

    run = run_attavo(root, "stops", "1", "--json")
    assert run.returncode == 0, run.stdout + run.stderr
    data = json.loads(run.stdout)

    rows = rows_by_record(data)
    assert set(rows) == {"build-01", "build-02", "build-03", "build-04", "build-05",
                        "verify-01", "verify-02"}
    assert rows["build-01"]["answerer"] == "guard"
    assert rows["build-02"]["answerer"] == "counsel:answer"
    assert rows["build-03"]["answerer"] == "person"
    assert rows["build-04"]["answerer"] == "none"
    assert rows["build-05"]["answerer"] == "none"
    assert rows["build-05"]["class"] == "before stop was recorded"
    assert rows["verify-01"]["answerer"] == "counsel:patch"
    assert rows["verify-02"]["answerer"] == "counsel:person"
    assert all(row["answerer"] for row in data["rows"])

    totals = data["totals"]
    assert totals["by_class"]["ceiling"] == 1
    assert totals["by_class"]["no-cause"] == 1
    assert totals["by_class"]["question"] == 1
    assert totals["by_class"]["unclassed"] == 1
    assert totals["by_class"]["claims-boundary"] == 1
    assert totals["by_class"]["repeated-act"] == 1
    assert totals["by_class"]["non-question"] == 0
    assert totals["by_answerer"] == {"guard": 1, "counsel:answer": 1, "person": 1,
                                     "none": 2, "counsel:patch": 1, "counsel:person": 1}
    assert totals["before_stop_was_recorded"] == 1


def test_stops_counsel_wins_over_class_as_answerer(tmp_path):
    """Acceptance row 2: a counsel record wins over the class as answerer
    -- a guarded class that would otherwise answer "guard" answers
    "counsel:<verdict>" once a counsel record names it."""
    root = make_tenant(tmp_path, "ledger_base")
    write_record(root, "1", "build", 1,
                {"status": "failed",
                 "stop": {"class": "ceiling", "provenance": "product", "detail": None}})
    write_counsel(root, "1", 1, "build-01", "answer")

    run = run_attavo(root, "stops", "1", "--json")
    assert run.returncode == 0, run.stdout + run.stderr
    data = json.loads(run.stdout)

    row = rows_by_record(data)["build-01"]
    assert row["class"] == "ceiling"
    assert row["answerer"] == "counsel:answer"
    assert data["totals"]["by_answerer"] == {"counsel:answer": 1}


def test_stops_prints_before_stop_was_recorded_for_a_record_without_stop(tmp_path):
    """Acceptance row 3: a record without stop prints the doctor's phrase
    and counts under "before stop was recorded"."""
    root = make_tenant(tmp_path, "ledger_base")
    write_record(root, "1", "build", 1, {"status": "failed"})

    run = run_attavo(root, "stops", "1", "--json")
    assert run.returncode == 0, run.stdout + run.stderr
    data = json.loads(run.stdout)

    row = rows_by_record(data)["build-01"]
    assert row["class"] == "before stop was recorded"
    assert row["provenance"] is None
    assert data["totals"]["before_stop_was_recorded"] == 1
    assert data["totals"]["by_class"]["unclassed"] == 0

    run_text = run_attavo(root, "stops", "1")
    assert run_text.returncode == 0, run_text.stdout + run_text.stderr
    assert "before stop was recorded" in run_text.stdout


def test_stops_prints_counsel_verdict_for_a_refused_spec_gate_record(tmp_path):
    """Issue #133, acceptance row 3. A refused spec-01.json carries no
    stop field of its own (cmd_spec_gate is untouched by this lane), so
    its class is derived from status and refusal -- claims-boundary/spec,
    the same class a build or verify refusal of this shape already
    carries -- and a counsel record naming it wins as the answerer, the
    same as any other guarded class. A passing spec-02 must never appear
    as a row."""
    root = make_tenant(tmp_path, "ledger_base")
    write_record(root, "1", "spec", 1,
                {"status": "refused",
                 "refusal": "criterion fails against the tree plus stub: "
                            "doctor-template-absent.sh"})
    write_counsel(root, "1", 1, "spec-01", "patch")
    write_record(root, "1", "spec", 2, {"status": "pass"})

    run = run_attavo(root, "stops", "1", "--json")
    assert run.returncode == 0, run.stdout + run.stderr
    data = json.loads(run.stdout)

    rows = rows_by_record(data)
    assert set(rows) == {"spec-01"}
    assert rows["spec-01"]["class"] == "claims-boundary"
    assert rows["spec-01"]["provenance"] == "spec"
    assert rows["spec-01"]["answerer"] == "counsel:patch"
    assert data["totals"]["by_class"]["claims-boundary"] == 1
    assert data["totals"]["by_answerer"] == {"counsel:patch": 1}


def test_stops_json_is_one_object_matching_rows_and_totals(tmp_path):
    """--json prints rows and totals as one object."""
    root = build_fixture(tmp_path)

    run_json = run_attavo(root, "stops", "1", "--json")
    assert run_json.returncode == 0, run_json.stdout + run_json.stderr
    data = json.loads(run_json.stdout)
    assert set(data) == {"item", "rows", "totals"}
    assert isinstance(data["rows"], list)
    assert isinstance(data["totals"], dict)
    assert set(data["totals"]) == {"by_class", "by_answerer", "before_stop_was_recorded"}
