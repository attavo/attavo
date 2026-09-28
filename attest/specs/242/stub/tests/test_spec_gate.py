"""The spec contract: attavo spec-gate N.

Each test is a rejection test, written and run red before the capability
existed: on `main` at commit 22ca1e1 there was no `spec-gate` subcommand, so
every one of these failed with "unknown subcommand".

Run: python3 -m pytest tests -q
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

from helpers import commit_all, git, make_tenant

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"


def run_attavo(root: Path, *args: str, env: dict[str, Any] | None = None) -> subprocess.CompletedProcess[str]:
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run([sys.executable, str(ATTAVO), *args], cwd=str(root),
                          capture_output=True, text=True, check=False, env=full_env)


def latest_record(root: Path, item: str, stage: str) -> dict[str, Any]:
    records = sorted((root / "records" / item).glob(f"{stage}-*.json"))
    assert records, f"no {stage} record for item {item}"
    return cast(dict[str, Any], json.loads(records[-1].read_text()))


def test_spec_gate_refuses_prose_criterion(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "prose_criterion")
    run = run_attavo(root, "spec-gate", "1")
    assert run.returncode == 1
    assert "missing.sh" in run.stdout
    rec = latest_record(root, "1", "spec")
    assert rec["status"] == "refused"
    assert "missing.sh" in rec["refusal"]
    # issue #49: this fixture's manifest carries no [attavo] table at all --
    # product_pin is the absent fact, null, not a crash reading it.
    assert rec["product_pin"] is None
    assert rec["product_version"]


def test_spec_gate_refuses_criterion_passing_on_tree(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "passes_on_tree")
    run = run_attavo(root, "spec-gate", "1")
    assert run.returncode == 1
    assert "checks.sh" in run.stdout
    rec = latest_record(root, "1", "spec")
    assert rec["status"] == "refused"
    assert "checks.sh" in rec["refusal"]


def test_spec_gate_refuses_criterion_failing_on_stub(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "fails_on_stub")
    run = run_attavo(root, "spec-gate", "1")
    assert run.returncode == 1
    assert "checks.sh" in run.stdout
    rec = latest_record(root, "1", "spec")
    assert rec["status"] == "refused"
    assert "checks.sh" in rec["refusal"]


def test_spec_gate_refuses_wrong_headings(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "wrong_headings")
    run = run_attavo(root, "spec-gate", "1")
    assert run.returncode == 1
    assert "Design" in run.stdout
    rec = latest_record(root, "1", "spec")
    assert rec["status"] == "refused"
    assert "Design" in rec["refusal"]


def test_spec_gate_passes_and_writes_record(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "valid")
    run = run_attavo(root, "spec-gate", "1")
    assert run.returncode == 0

    first = root / "records" / "1" / "spec-01.json"
    assert first.is_file()
    rec = json.loads(first.read_text())
    assert rec["status"] == "pass"
    assert rec["spec_sha"]
    assert rec["criteria"] == [{"name": "checks_marker.sh", "on_tree": 1, "on_stub": 0}]
    assert rec["refusal"] is None

    run2 = run_attavo(root, "spec-gate", "1")
    assert run2.returncode == 0
    assert (root / "records" / "1" / "spec-02.json").is_file()


def amend_problem(root: Path) -> None:
    spec_md = root / "specs" / "1" / "spec.md"
    spec_md.write_text(spec_md.read_text().replace(
        "does not emit", "used to not emit"))
    commit_all(root, "amend problem prose")


def approve_valid(root: Path) -> None:
    assert run_attavo(root, "spec-gate", "1").returncode == 0
    assert run_attavo(root, "approve", "1", "--by", "alice").returncode == 0


def complete_the_build(root: Path) -> None:
    """The marker.txt the criterion checks for, committed with no touch to
    spec.md: the same commit shape a real `attavo build` leaves behind --
    spec_sha, and so the approval, is unchanged."""
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "marker.txt").write_text("built\n")
    commit_all(root, "build completed")


def test_spec_gate_reports_approved_when_approval_is_valid(tmp_path: Path) -> None:
    """Issue #21, acceptance 1. Once a build has made the criterion pass on
    the tree, the gate must not re-run it and refuse the very success
    approval exists to bind: it reports the approval instead. Red today:
    the unmodified gate refuses this by name ("criterion passes against
    the tree: checks_marker.sh").
    """
    root = make_tenant(tmp_path, "valid")
    approve_valid(root)
    complete_the_build(root)

    run = run_attavo(root, "spec-gate", "1")
    assert run.returncode == 0

    rec = latest_record(root, "1", "spec")
    assert rec["status"] == "approved"
    assert rec["criteria"] == []
    assert rec["refusal"] is None
    assert rec["spec_sha"]
    approval = json.loads(sorted((root / "records" / "1").glob("approval-*.json"))[-1].read_text())
    assert rec["approval_sha"] == approval["sha"]


def test_spec_gate_runs_when_approval_expired(tmp_path: Path) -> None:
    """Issue #21, acceptance 2. The same fixture, after an edit above the
    line expires the approval: the gate must fall through to running the
    criteria for real, not keep reporting approved past the approval it
    no longer holds.
    """
    root = make_tenant(tmp_path, "valid")
    approve_valid(root)
    complete_the_build(root)
    assert run_attavo(root, "spec-gate", "1").returncode == 0  # approved, short-circuited

    amend_problem(root)

    run = run_attavo(root, "spec-gate", "1")
    assert run.returncode == 1
    rec = latest_record(root, "1", "spec")
    assert rec["status"] == "refused"
    assert "checks_marker.sh" in rec["refusal"]


def run_real_build(root: Path) -> subprocess.CompletedProcess[str]:
    """A real `attavo build`, the fixture adapter writing the marker.txt
    the criterion checks for -- inside src/**, the claim -- unlike
    complete_the_build above, which only fakes the commit shape."""
    return run_attavo(root, "build", "1", env={"ATTAVO_FIXTURE_WRITE": "src/marker.txt"})


def test_spec_gate_built_after_build(tmp_path: Path) -> None:
    """Issue #28, acceptance 1. Approve, build for real, then an
    amendment above the line expires the approval: the gate reports
    status built, not a refusal. Red today: refused for passing on the
    tree, PR #600's specimen.

    Issue #36 changed built to judge each criterion (both on_tree and
    on_stub, as a pass record already does) rather than the tree alone;
    this fixture's stub also satisfies checks_marker.sh, so on_stub is
    0 here, not null -- the null-on_stub shape was retired with the
    "criterion fails against the tree" refusal it belonged to.
    """
    root = make_tenant(tmp_path, "valid")
    approve_valid(root)
    assert run_real_build(root).returncode == 0

    amend_problem(root)

    run = run_attavo(root, "spec-gate", "1")
    assert run.returncode == 0

    rec = latest_record(root, "1", "spec")
    assert rec["status"] == "built"
    assert rec["criteria"] == [{"name": "checks_marker.sh", "on_tree": 0, "on_stub": 0}]
    assert rec["refusal"] is None


def test_spec_gate_built_refuses_a_failing_criterion(tmp_path: Path) -> None:
    """Issue #28, acceptance 2. Same shape, but a criterion no longer
    satisfiable at all -- gone from the tree and from the stub overlay
    both -- is refused by name, not rubber-stamped built just because a
    build record exists.

    Issue #36 made built judge tree-fail-stub-pass as an accepted new
    obligation, not a refusal, so this specimen must fail on the stub
    too (removed from specs/1/stub/, not just the real tree) to still
    exercise a refusal at all.
    """
    root = make_tenant(tmp_path, "valid")
    approve_valid(root)
    assert run_real_build(root).returncode == 0

    (root / "src" / "marker.txt").unlink()
    (root / "specs" / "1" / "stub" / "src" / "marker.txt").unlink()
    commit_all(root, "regression: the marker is gone from the tree and the stub")
    amend_problem(root)

    run = run_attavo(root, "spec-gate", "1")
    assert run.returncode == 1

    rec = latest_record(root, "1", "spec")
    assert rec["status"] == "refused"
    assert rec["refusal"] == "criterion fails against the tree plus stub: checks_marker.sh"
    assert rec["criteria"] == [{"name": "checks_marker.sh", "on_tree": 1, "on_stub": 1}]


def test_spec_gate_removed_deletes_the_path_from_the_stub(tmp_path: Path) -> None:
    """Issue #132, acceptance 1. stub/.removed names a path the change
    deletes; the gate must drop it from the stubbed copy before running
    the criteria, so a criterion asserting its absence passes on the
    stub and fails on the tree, where the path is still there. Red
    today: the overlay is add-only, so the path survives into the
    stubbed tree and the criterion fails on both.
    """
    root = make_tenant(tmp_path, "removed_present")
    run = run_attavo(root, "spec-gate", "1")
    assert run.returncode == 0

    rec = latest_record(root, "1", "spec")
    assert rec["status"] == "pass"
    assert rec["criteria"] == [{"name": "absent.sh", "on_tree": 1, "on_stub": 0}]
    assert rec["refusal"] is None


def test_spec_gate_removed_refuses_a_path_absent_from_the_tree(tmp_path: Path) -> None:
    """Issue #132, acceptance 2. A .removed entry naming a path that
    isn't in the tree at all removes nothing, so it is refused by name
    before any criterion runs -- no criteria entries in the record.
    """
    root = make_tenant(tmp_path, "removed_absent")
    run = run_attavo(root, "spec-gate", "1")
    assert run.returncode == 1
    assert "nope.txt" in run.stdout

    rec = latest_record(root, "1", "spec")
    assert rec["status"] == "refused"
    assert "nope.txt" in rec["refusal"]
    assert rec["criteria"] == []


def test_spec_gate_removed_refuses_a_path_the_stub_also_supplies(tmp_path: Path) -> None:
    """Issue #132, acceptance 3. A path named in .removed that the stub
    also supplies is a contradiction -- add and remove the same path --
    and is refused by name before any criterion runs.
    """
    root = make_tenant(tmp_path, "removed_contradicts_stub")
    run = run_attavo(root, "spec-gate", "1")
    assert run.returncode == 1
    assert "src/marker.txt" in run.stdout

    rec = latest_record(root, "1", "spec")
    assert rec["status"] == "refused"
    assert "src/marker.txt" in rec["refusal"]
    assert rec["criteria"] == []


def test_spec_gate_removed_refuses_a_path_outside_the_tree(tmp_path: Path) -> None:
    """Issue #132, acceptance 4. A .removed entry that escapes the tree
    is refused by name before any criterion runs -- the stubbed copy is
    the only thing the gate may delete from.
    """
    root = make_tenant(tmp_path, "removed_outside_tree")
    run = run_attavo(root, "spec-gate", "1")
    assert run.returncode == 1
    assert "../escape.txt" in run.stdout

    rec = latest_record(root, "1", "spec")
    assert rec["status"] == "refused"
    assert "../escape.txt" in rec["refusal"]
    assert rec["criteria"] == []


def test_spec_gate_phase1_rule_unchanged_without_build(tmp_path: Path) -> None:
    """Issue #28, acceptance 3. No build record at all: the third verdict
    does not apply, and Phase 1's original rule holds unchanged."""
    root = make_tenant(tmp_path, "valid")
    run = run_attavo(root, "spec-gate", "1")
    assert run.returncode == 0
    rec = latest_record(root, "1", "spec")
    assert rec["status"] == "pass"
    assert rec["criteria"] == [{"name": "checks_marker.sh", "on_tree": 1, "on_stub": 0}]


def add_scripted_criterion(root: Path, satisfies_stub: bool) -> None:
    """The move an operator makes when an executor skipped a ruling
    nothing enforced: script it, after the build. Adds one criterion,
    extra.sh, testing for extra.txt -- present in the stub overlay only
    when satisfies_stub, so the new criterion fails on the tree (the
    build never created it) and either passes or fails on the stub.
    """
    spec_md = root / "specs" / "1" / "spec.md"
    spec_md.write_text(
        spec_md.read_text() + "- extra.sh: a ruling scripted after the build\n")
    script = root / "specs" / "1" / "criteria" / "extra.sh"
    script.write_text("#!/bin/sh\ntest -f extra.txt\n")
    script.chmod(0o755)
    if satisfies_stub:
        stub_marker = root / "specs" / "1" / "stub" / "extra.txt"
        stub_marker.write_text("stub\n")
    commit_all(root, "amend: script a ruling the executor skipped")


def test_built_accepts_a_criterion_added_after_the_build(tmp_path: Path) -> None:
    """Issue #36, acceptance 1. A criterion added after a build, failing
    on the tree (the build never satisfied it) but passing on the stub
    (a new obligation the next build must meet), does not refuse built
    -- it is exactly the move an operator makes when an executor skipped
    a ruling nothing enforced: script it. Red today: built refuses any
    tree failure regardless of the stub; specimen attavo/code-factory
    spec-22.json, a correct amendment refused by name.
    """
    root = make_tenant(tmp_path, "valid")
    approve_valid(root)
    assert run_real_build(root).returncode == 0

    add_scripted_criterion(root, satisfies_stub=True)

    run = run_attavo(root, "spec-gate", "1")
    assert run.returncode == 0

    rec = latest_record(root, "1", "spec")
    assert rec["status"] == "built"
    assert rec["refusal"] is None
    by_name = {c["name"]: c for c in rec["criteria"]}
    assert by_name["checks_marker.sh"] == {"name": "checks_marker.sh", "on_tree": 0, "on_stub": 0}
    assert by_name["extra.sh"] == {"name": "extra.sh", "on_tree": 1, "on_stub": 0}


def test_built_refuses_a_criterion_failing_on_both(tmp_path: Path) -> None:
    """Issue #36, acceptance 2. Same shape, but the new criterion is not
    satisfiable at all -- fails on the tree and on the stub both:
    refused by name, not accepted as a scriptable new obligation.
    """
    root = make_tenant(tmp_path, "valid")
    approve_valid(root)
    assert run_real_build(root).returncode == 0

    add_scripted_criterion(root, satisfies_stub=False)

    run = run_attavo(root, "spec-gate", "1")
    assert run.returncode == 1

    rec = latest_record(root, "1", "spec")
    assert rec["status"] == "refused"
    assert rec["refusal"] == "criterion fails against the tree plus stub: extra.sh"
    by_name = {c["name"]: c for c in rec["criteria"]}
    assert by_name["extra.sh"] == {"name": "extra.sh", "on_tree": 1, "on_stub": 1}


def write_build_record(root: Path, item: str, *, sha: str, status: str) -> None:
    """A minimal build-01.json, faked directly rather than run through the
    fixture adapter: the adapter has no way to both commit and halt with a
    question in one invocation (adapters/fixture/run answers a question
    before it ever reaches the write-and-commit step), and cmd_spec_gate
    reads nothing off a build record but sha and status.
    """
    records_dir = root / "records" / item
    records_dir.mkdir(parents=True, exist_ok=True)
    (records_dir / "build-01.json").write_text(json.dumps({"sha": sha, "status": status}) + "\n")


def test_spec_gate_built_after_halted_build_that_committed(tmp_path: Path) -> None:
    """Issue #98, acceptance 1. The executor commits its work, then halts
    with a question -- the common shape, since it commits before it asks.
    An amendment above the line must still be judged against the tree the
    halted build produced, not refused for the success that tree already
    holds. Red today: refused, "criterion passes against the tree:
    checks_marker.sh" -- the status != "halted" clause excluded the one
    build that had done the work; tenant zero's #663 build-01/spec-02 is
    the specimen.
    """
    root = make_tenant(tmp_path, "valid")
    approve_valid(root)
    complete_the_build(root)
    build_sha = git(root, "rev-parse", "HEAD").strip()
    write_build_record(root, "1", sha=build_sha, status="halted")

    amend_problem(root)

    run = run_attavo(root, "spec-gate", "1")
    assert run.returncode == 0

    rec = latest_record(root, "1", "spec")
    assert rec["status"] == "built"
    assert rec["criteria"] == [{"name": "checks_marker.sh", "on_tree": 0, "on_stub": 0}]
    assert rec["refusal"] is None


def test_spec_gate_stub_verdict_after_halted_build_that_committed_nothing(tmp_path: Path) -> None:
    """Issue #98, acceptance 2. A halted build whose sha equals its
    start_sha did no work -- commit_postdates already treats that as
    postdating nothing -- so the gate must keep judging it against the
    stub, exactly as before the fix: Phase 1's rule, not built. The
    criterion still fails on the real tree (no build ever touched it) and
    passes on the stub, so the verdict is pass, not built and not refused.
    """
    root = make_tenant(tmp_path, "valid")
    approve_valid(root)
    approval_sha = git(root, "rev-parse", "HEAD").strip()
    write_build_record(root, "1", sha=approval_sha, status="halted")

    amend_problem(root)

    run = run_attavo(root, "spec-gate", "1")
    assert run.returncode == 0

    rec = latest_record(root, "1", "spec")
    assert rec["status"] == "pass"
    assert rec["criteria"] == [{"name": "checks_marker.sh", "on_tree": 1, "on_stub": 0}]
    assert rec["refusal"] is None


def add_root_cause_line(root: Path, root_cause: str) -> None:
    spec_md = root / "specs" / "1" / "spec.md"
    # a root-cause item is core (ruled 2026-09-27); the fixture declares fix
    text = spec_md.read_text().replace("Class: fix\n", "Class: core\n", 1)
    text = text.replace("## Problem\n", f"## Problem\n\nRoot-cause: {root_cause}\n", 1)
    spec_md.write_text(text)
    commit_all(root, f"name root cause {root_cause}")


def test_spec_gate_refuses_a_root_cause_the_tenant_does_not_list_and_passes_a_listed_one(tmp_path: Path) -> None:
    """Root causes (#229 build-04 and the convergence ruling of
    2026-09-27): a fix item names the root cause its change closes as one
    'Root-cause: <slug>' line in its problem, and the gate refuses a slug
    the tenant's root-causes.md (beside the specs directory) does not
    list, or a line with no such file. Written red first: on v0.30.0 the
    line is prose and the gate passes it."""
    root = make_tenant(tmp_path, "valid")
    add_root_cause_line(root, "shared-working-copy")
    run = run_attavo(root, "spec-gate", "1")
    assert run.returncode == 1, run.stdout + run.stderr
    rec = latest_record(root, "1", "spec")
    assert rec["status"] == "refused"
    assert rec["refusal"] == "root cause: shared-working-copy named but root-causes.md does not exist", rec["refusal"]
    assert rec["criteria"] == [], "refused before any criterion ran"

    (root / "root-causes.md").write_text(
        "# root causes\n\n- liveness-by-proxy: a pid stood in for the holder\n")
    commit_all(root, "list one root cause")
    run = run_attavo(root, "spec-gate", "1")
    assert run.returncode == 1
    assert latest_record(root, "1", "spec")["refusal"] == "root cause: shared-working-copy is not listed in root-causes.md"

    (root / "root-causes.md").write_text(
        "# root causes\n\n- liveness-by-proxy: a pid stood in for the holder\n"
        "- shared-working-copy: one checkout, many writers\n")
    commit_all(root, "list the root cause")
    run = run_attavo(root, "spec-gate", "1")
    assert run.returncode == 0, run.stdout + run.stderr
    rec = latest_record(root, "1", "spec")
    assert rec["status"] == "pass" and rec["refusal"] is None


def test_spec_gate_leaves_a_spec_with_no_root_cause_line_alone(tmp_path: Path) -> None:
    """A capability item has no root cause; the check is on the line, not
    on every spec, and a tenant with no root-causes.md is not asked for
    one."""
    root = make_tenant(tmp_path, "valid")
    assert not (root / "root-causes.md").exists()
    assert run_attavo(root, "spec-gate", "1").returncode == 0


# --- the item's class and the research policy (ruled 2026-09-25, 2026-09-27) ---

def set_class_line(root: Path, line: str | None) -> None:
    spec_md = root / "specs" / "1" / "spec.md"
    text = spec_md.read_text().replace("Class: fix\n", "" if line is None else line + "\n", 1)
    spec_md.write_text(text)
    commit_all(root, f"class line: {line}")


def test_spec_gate_refuses_a_spec_that_declares_no_class_before_any_criterion_runs(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "valid")
    set_class_line(root, None)
    run = run_attavo(root, "spec-gate", "1")
    assert run.returncode == 1, run.stdout + run.stderr
    rec = latest_record(root, "1", "spec")
    assert rec["status"] == "refused" and rec["criteria"] == [], rec
    assert rec["refusal"] == "class: none declared; add a line 'Class: core' or 'Class: fix' to the problem", rec["refusal"]
    assert rec["class"] is None and rec["research"] == "not required by policy", rec


def test_spec_gate_refuses_a_class_other_than_core_or_fix(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "valid")
    set_class_line(root, "Class: feature")
    assert run_attavo(root, "spec-gate", "1").returncode == 1
    assert latest_record(root, "1", "spec")["refusal"] == "class: feature is not core or fix"


def test_spec_gate_refuses_a_root_cause_item_declared_fix(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "valid")
    (root / "root-causes.md").write_text("# root causes\n\n- shared-working-copy: one checkout, many writers\n")
    spec_md = root / "specs" / "1" / "spec.md"
    spec_md.write_text(spec_md.read_text().replace("## Problem\n", "## Problem\n\nRoot-cause: shared-working-copy\n", 1))
    commit_all(root, "a root cause named on a fix")
    assert run_attavo(root, "spec-gate", "1").returncode == 1
    assert latest_record(root, "1", "spec")["refusal"] == "class: fix with root cause shared-working-copy; a root-cause item is core"


def test_spec_gate_records_the_class_and_that_research_was_not_required(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "valid")
    set_class_line(root, "Class: core")
    run = run_attavo(root, "spec-gate", "1")
    assert run.returncode == 0, run.stdout + run.stderr
    rec = latest_record(root, "1", "spec")
    assert rec["status"] == "pass" and rec["class"] == "core", rec
    assert rec["research"] == "not required by policy", rec


def test_with_research_required_a_core_spec_owes_a_research_record_and_a_fix_spec_does_not(tmp_path: Path) -> None:
    root = make_tenant(tmp_path, "valid")
    manifest = root / "attavo.toml"
    text = manifest.read_text()
    assert "[spec]\n" in text, text
    manifest.write_text(text.replace("[spec]\n", '[spec]\nresearch = "required"\n', 1))
    commit_all(root, "research required")
    run = run_attavo(root, "spec-gate", "1")
    assert run.returncode == 0, run.stdout + run.stderr
    assert latest_record(root, "1", "spec")["research"] == "required"
    set_class_line(root, "Class: core")
    assert run_attavo(root, "spec-gate", "1").returncode == 1
    rec = latest_record(root, "1", "spec")
    assert rec["refusal"] == "research: [spec].research is required and the item has no research record", rec
    (root / "records" / "1" / "research-01.json").write_text("{}\n")
    commit_all(root, "a research record")
    assert run_attavo(root, "spec-gate", "1").returncode == 0


def test_an_approved_and_built_item_whose_spec_declares_no_class_still_regates_built(tmp_path: Path) -> None:
    """No item in flight is stranded: class is required before approval,
    and an item approved and built before the gate read class re-gates
    after an amendment exactly as it did."""
    root = make_tenant(tmp_path, "valid")
    approve_valid(root)
    assert run_real_build(root).returncode == 0
    set_class_line(root, None)
    run = run_attavo(root, "spec-gate", "1")
    assert run.returncode == 0, run.stdout + run.stderr
    rec = latest_record(root, "1", "spec")
    assert rec["status"] == "built" and rec["class"] is None, rec
