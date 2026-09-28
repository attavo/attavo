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
from typing import Any

import pytest

from attavo.common import read_manifest
from attavo.doctor import proof_no_stop_lacks_cause
from attavo.records import write_record as write_stage_record
from attavo.stops import STOP_CLASSES, derive_stop, returned_stop
from helpers import make_tenant

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"


def run_attavo(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(ATTAVO), *args], cwd=str(root),
                          capture_output=True, text=True, check=False)


def write_record(root: Path, item: str, stage: str, n: int, content: dict[str, Any]) -> Path:
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


def rows_by_record(data: dict[str, Any]) -> dict[str, Any]:
    return {row["record"]: row for row in data["rows"]}


def test_stops_classifies_every_answerer_and_totals(tmp_path: Path) -> None:
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


def test_stops_counsel_wins_over_class_as_answerer(tmp_path: Path) -> None:
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


def test_stops_prints_before_stop_was_recorded_for_a_record_without_stop(tmp_path: Path) -> None:
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


def test_stops_prints_counsel_verdict_for_a_refused_spec_gate_record(tmp_path: Path) -> None:
    """Issue #133, acceptance row 3, reclassed by issue #137. A refused
    spec-01.json carries no stop field of its own (cmd_spec_gate is
    untouched by this lane), so its class is synthesized from status and
    refusal -- class "gate", provenance "spec", the refusal string
    itself as cause (issue #137's own specimen, tenant zero #683: a
    criterion the gate cannot express is not a write outside claims, the
    boundary claims-boundary used to name here) -- and a counsel record
    naming it wins as the answerer. A passing spec-02 must never appear
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
    assert rows["spec-01"]["class"] == "gate"
    assert rows["spec-01"]["provenance"] == "spec"
    assert rows["spec-01"]["detail"] == ("criterion fails against the tree plus stub: "
                                         "doctor-template-absent.sh")
    assert rows["spec-01"]["answerer"] == "counsel:patch"
    assert data["totals"]["by_class"]["gate"] == 1
    assert data["totals"]["by_answerer"] == {"counsel:patch": 1}


def test_stops_gate_class_answers_none_with_no_counsel_record(tmp_path: Path) -> None:
    """Issue #137. Unlike claims-boundary, "gate" is not a guarded class:
    a refused spec-gate record with no counsel record answers "none",
    never "guard" -- the fallback classification issue #133 reused had
    no guard behind it, since no code path rejects a gate refusal by
    name before a person or a counsel session reads it."""
    root = make_tenant(tmp_path, "ledger_base")
    write_record(root, "1", "spec", 1,
                {"status": "refused",
                 "refusal": "criterion passes against the tree: checks.sh"})

    run = run_attavo(root, "stops", "1", "--json")
    assert run.returncode == 0, run.stdout + run.stderr
    data = json.loads(run.stdout)

    row = rows_by_record(data)["spec-01"]
    assert row["class"] == "gate"
    assert row["answerer"] == "none"
    assert data["totals"]["by_answerer"] == {"none": 1}


def test_stops_json_is_one_object_matching_rows_and_totals(tmp_path: Path) -> None:
    """--json prints rows and totals as one object."""
    root = build_fixture(tmp_path)

    run_json = run_attavo(root, "stops", "1", "--json")
    assert run_json.returncode == 0, run_json.stdout + run_json.stderr
    data = json.loads(run_json.stdout)
    assert set(data) == {"item", "rows", "totals"}
    assert isinstance(data["rows"], list)
    assert isinstance(data["totals"], dict)
    assert set(data["totals"]) == {"by_class", "by_answerer", "before_stop_was_recorded"}


# --- derive_stop: a stop's class comes from the record's own fields, never
# from reading the transcript (issue #241, root cause classification-by-
# reading-prose). Written and run red first: on main the class was found by
# scanning the whole transcript for the bare name of any class. ---

EVERY_CLASS_QUOTED = "\n".join(
    f"a quoted record says {name} here" for name in STOP_CLASSES) + "\n"

GREEN_GATE: dict[str, Any] = {"criteria": [{"name": "a.sh", "exit": 0}],
                              "selected_tests": {"count": 1, "exit": 0}, "checks": None}
RED_TESTS_GATE: dict[str, Any] = {"criteria": [{"name": "a.sh", "exit": 0}],
                                  "selected_tests": {"count": 1, "exit": 1}, "checks": None}


SPECIMENS: list[Any] = [
    pytest.param("refused", {"refusal_kind": "foreign-commit",
                             "refusal": "commit 3f2a not authored by the executor"},
                 ("foreign-commit", "tenant", "commit 3f2a not authored by the executor"),
                 id="229-build-01-foreign-commit"),
    pytest.param("refused", {"refusal_kind": "claims", "refusal": "attavo/common.py"},
                 ("claims-boundary", "spec", "attavo/common.py"),
                 id="229-build-03-outside-claims"),
    pytest.param("failed", {"gate": RED_TESTS_GATE},
                 ("gate", "executor", "selected tests: exit 1, 1 selected"),
                 id="229-build-04-red-selected-tests"),
    pytest.param("halted", {"halted": {"question": "which overlay wins?\nand why"}},
                 ("question", "spec", "which overlay wins?"),
                 id="229-build-05-question"),
    pytest.param("failed", {"gate": RED_TESTS_GATE},
                 ("gate", "executor", "selected tests: exit 1, 1 selected"),
                 id="237-build-01-red-selected-tests-no-checks"),
]


@pytest.mark.parametrize(("status", "structure", "expected"), SPECIMENS)
def test_the_five_specimens_class_from_structure_whatever_the_transcript_quotes(
        status: str, structure: dict[str, Any], expected: tuple[str, str, str]) -> None:
    """#229 build-01, -03, -04, -05 and #237 build-01, replayed with a
    transcript that quotes every class name: the class is the record's own,
    and neither `no-cause` nor `forge-unconcluded` is ever derived."""
    stop = derive_stop(status, log=EVERY_CLASS_QUOTED, **structure)
    assert stop is not None
    assert (stop["class"], stop["provenance"], stop["detail"]) == expected
    assert stop["class"] not in ("no-cause", "forge-unconcluded")


def test_a_protected_path_refusal_is_claims_boundary_executor() -> None:
    stop = derive_stop("refused", refusal_kind="protected", refusal="records/1/sneaky.txt",
                       log=EVERY_CLASS_QUOTED)
    assert stop == {"class": "claims-boundary", "provenance": "executor",
                    "detail": "records/1/sneaky.txt"}


def test_a_failed_build_with_a_red_criterion_classes_gate_naming_the_criterion() -> None:
    gate: dict[str, Any] = {"criteria": [{"name": "ok.sh", "exit": 0}, {"name": "bad.sh", "exit": 4}],
                            "selected_tests": {"count": 2, "exit": 1}, "checks": {"exit": 1}}
    stop = derive_stop("failed", gate=gate, log=EVERY_CLASS_QUOTED)
    assert stop == {"class": "gate", "provenance": "executor", "detail": "criterion bad.sh: exit 4"}


def test_a_red_checks_run_alone_classes_gate_naming_the_checks_exit() -> None:
    gate: dict[str, Any] = {"criteria": [{"name": "ok.sh", "exit": 0}],
                            "selected_tests": {"count": 3, "exit": 0}, "checks": {"exit": 2}}
    stop = derive_stop("failed", gate=gate)
    assert stop == {"class": "gate", "provenance": "executor", "detail": "checks: exit 2"}


def test_a_green_gate_on_a_failed_record_is_not_a_gate_stop() -> None:
    """A failure the gate did not cause (an adapter that exited non-zero
    after a green gate, say) has no structure to class it: unclassed."""
    stop = derive_stop("failed", gate=GREEN_GATE, log="one\ntwo")
    assert stop == {"class": "unclassed", "provenance": "product", "detail": "two"}


def test_a_transcript_naming_every_class_and_no_structure_classes_unclassed() -> None:
    stop = derive_stop("failed", log=EVERY_CLASS_QUOTED)
    assert stop is not None
    assert (stop["class"], stop["provenance"]) == ("unclassed", "product")
    assert stop["detail"] == f"a quoted record says {STOP_CLASSES[-1]} here"


def test_a_transcript_naming_a_ceiling_is_not_a_ceiling() -> None:
    """The ceiling is reported on the adapter's report line, not read from
    the log's words."""
    stop = derive_stop("failed", log="Error: input_too_large: ceiling exceeded\n")
    assert stop is not None and stop["class"] == "unclassed"


def test_a_report_line_ceiling_is_ceiling_executor() -> None:
    stop = derive_stop("failed", report_class="ceiling", log=EVERY_CLASS_QUOTED)
    assert stop == {"class": "ceiling", "provenance": "executor", "detail": None}


def test_returned_and_pass_are_unchanged() -> None:
    assert derive_stop("pass", log=EVERY_CLASS_QUOTED) is None
    assert derive_stop("returned") == {"class": "forge-unconcluded", "provenance": "tenant",
                                       "detail": None}


def test_foreign_commit_is_a_class_and_in_both_record_schemas() -> None:
    root = Path(__file__).resolve().parent.parent
    for stage in ("build", "verify"):
        schema = json.loads((root / "schema" / "records" / f"{stage}.schema.json").read_text())
        assert "foreign-commit" in schema["properties"]["stop"]["properties"]["class"]["enum"], stage
    assert "foreign-commit" in STOP_CLASSES


# --- issue #254: the writer and the reader agree by construction ---

GATES_LOG = "--- attavo gates, iteration 1 ---\ncriterion a.sh: exit 1\nlast line"

RED_CRITERION: dict[str, Any] = {"criteria": [{"name": "a.sh", "exit": 1}],
                                 "selected_tests": None, "checks": None}
RED_SELECTED: dict[str, Any] = {"criteria": [{"name": "a.sh", "exit": 0}],
                                "selected_tests": {"select": "s", "run": "r", "count": 2, "exit": 1},
                                "checks": None}
RED_CHECKS: dict[str, Any] = {"criteria": [{"name": "a.sh", "exit": 0}],
                              "selected_tests": {"select": "s", "run": "r", "count": 2, "exit": 0},
                              "checks": {"command": "c", "exit": 2}}
GREEN: dict[str, Any] = {"criteria": [{"name": "a.sh", "exit": 0}],
                         "selected_tests": {"select": "s", "run": "r", "count": 2, "exit": 0},
                         "checks": {"command": "c", "exit": 0}}
FORGE_RED: dict[str, Any] = {"command": "c", "sha": "s", "conclusions": [
    {"name": "test", "conclusion": "failure"}, {"name": "lint", "conclusion": "success"}]}
FORGE_PENDING: dict[str, Any] = {"command": "c", "sha": "s", "conclusions": [
    {"name": "test", "conclusion": "in_progress"}]}
BLOCKING: list[dict[str, Any]] = [{"title": "b", "specimen": "s", "retires": "r",
                                   "rejection_test": "t", "severity": "blocking"}]


def _build_case(status: str, *, gate_fields: dict[str, Any] | None = None, **derive: Any) -> Any:
    fields = {"criteria": [], "selected_tests": None, "checks": None, **(gate_fields or {})}
    gate = {"criteria": [{"name": c["name"], "exit": c["exit"]} for c in fields["criteria"]],
            "selected_tests": ({"count": fields["selected_tests"]["count"],
                                "exit": fields["selected_tests"]["exit"]}
                               if fields["selected_tests"] else None),
            "checks": {"exit": fields["checks"]["exit"]} if fields["checks"] else None}
    record = {"status": status, "refusal": derive.get("refusal"),
              "refusal_kind": derive.get("refusal_kind"), "halted": derive.get("halted"),
              "report_class": derive.get("report_class"), **fields}
    stop = derive_stop(status, gate=gate, log=GATES_LOG, **derive)
    return "build", record, stop


DERIVE_CASES: list[Any] = [
    pytest.param(*_build_case("refused", refusal_kind="foreign-commit", refusal="commit 3f2a"),
                 id="build-foreign-commit"),
    pytest.param(*_build_case("refused", refusal_kind="claims", refusal="a.py"), id="build-claims"),
    pytest.param(*_build_case("refused", refusal_kind="protected", refusal="records/1/x"),
                 id="build-protected"),
    pytest.param(*_build_case("refused", refusal="no kind at all"), id="build-refused-unclassed"),
    pytest.param(*_build_case("failed", gate_fields=RED_CRITERION), id="build-gate-criterion"),
    pytest.param(*_build_case("failed", gate_fields=RED_SELECTED), id="build-gate-selected-tests"),
    pytest.param(*_build_case("failed", gate_fields=RED_CHECKS), id="build-gate-checks"),
    pytest.param(*_build_case("failed", gate_fields=GREEN), id="build-green-gate-unclassed"),
    pytest.param(*_build_case("halted", halted={"question": "which one?"}), id="build-question"),
    pytest.param(*_build_case("halted", halted={"question": ""}), id="build-halt-no-question"),
    pytest.param(*_build_case("failed", report_class="ceiling"), id="build-report-ceiling"),
    pytest.param(*_build_case("failed", report_class="non-question"),
                 id="build-report-non-question"),
    pytest.param(*_build_case("failed", report_class="transient"), id="build-report-transient"),
    pytest.param(*_build_case("failed", report_class="empty"), id="build-report-empty"),
    pytest.param(*_build_case("failed", report_class="no-such-class"),
                 id="build-report-unenumerated"),
    pytest.param("build",
                 {"status": "refused", "refusal": "checks cannot run", "refusal_kind": "environment",
                  "halted": None, "report_class": None, **RED_CHECKS},
                 {"class": "environment", "provenance": "tenant", "detail": "boom",
                  "signature": "boom", "bound": None}, id="build-environment-preflight"),
    pytest.param("verify",
                 {"status": "returned", "findings": None, "report_class": None,
                  "forge_checks": FORGE_RED}, returned_stop(FORGE_RED), id="verify-forge-red"),
    pytest.param("verify",
                 {"status": "returned", "findings": None, "report_class": None,
                  "forge_checks": FORGE_PENDING}, returned_stop(FORGE_PENDING),
                 id="verify-forge-unconcluded"),
    pytest.param("verify",
                 {"status": "failed", "findings": BLOCKING, "report_class": None,
                  "forge_checks": FORGE_PENDING},
                 {"class": "returned", "provenance": "tenant", "detail": "b", "signature": "b",
                  "bound": None}, id="verify-blocking-finding"),
    pytest.param("verify",
                 {"status": "failed", "findings": None, "report_class": "transient",
                  "forge_checks": FORGE_PENDING},
                 derive_stop("failed", report_class="transient", log=GATES_LOG),
                 id="verify-report-transient"),
    pytest.param("verify",
                 {"status": "failed", "findings": None, "report_class": None,
                  "forge_checks": FORGE_PENDING},
                 derive_stop("failed", log=GATES_LOG), id="verify-unclassed"),
]


@pytest.mark.parametrize(("stage", "fields", "stop"), DERIVE_CASES)
def test_every_stop_a_writer_produces_re_derives_to_itself(
        tmp_path: Path, stage: str, fields: dict[str, Any], stop: dict[str, Any]) -> None:
    """Issue #254 row 4: write each case's record through write_record, at
    the schema's own version, and run proof four over it -- the class the
    writer stored and the class the doctor re-derives are one."""
    root = tmp_path / "tenant"
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    write_stage_record(root / "records" / "1", stage, {**fields, "stop": stop},
                       {"paths": {"records": "records"}}, "1", log=GATES_LOG)

    p = proof_no_stop_lacks_cause(root, read_manifest(root))
    assert p.holds, p.reason
    assert "1 non-pass record(s)" in p.reason
