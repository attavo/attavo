"""templates/workflows/attavo-spec-gate.yml: the gate runs against a work
item's branch and pushes its own spec-NN.json record. Without a skip, that
record commit itself triggers the gate again, which pushes another record,
forever -- the loop from issue #9 (a pilot tenant's PR #595: two operator
releases of held runs produced two more record commits).

Rejection test, written and run red before the skip step existed: the
template file didn't exist at all, so TEMPLATE.read_text() raised
FileNotFoundError.

This test extracts the literal `run:` shell out of the template file itself
(tests/helpers.py:extract_step_run) and runs it against real commits, so it
tracks the template's actual skip logic instead of a paraphrase of it.

Run: python3 -m pytest tests -q
"""
import os
import subprocess
from pathlib import Path

from helpers import commit_all, extract_step_run, git, job_if_condition, make_tenant, step_block

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "workflows" / "attavo-spec-gate.yml"
STEP_NAME = "Skip when the head commit only touches records"
JOB_NAME = "spec-gate"


def run_skip_step(root: Path) -> str:
    """Run the template's skip step against root's HEAD commit exactly as
    GitHub Actions would (a real GITHUB_OUTPUT file), and return the 'skip'
    output it wrote.
    """
    script = extract_step_run(TEMPLATE.read_text(), STEP_NAME)
    output_file = root / "_github_output.txt"
    output_file.write_text("")
    env = dict(os.environ, GITHUB_OUTPUT=str(output_file))
    run = subprocess.run(["bash", "-eo", "pipefail", "-c", script], cwd=str(root),
                         env=env, capture_output=True, text=True, check=False)
    assert run.returncode == 0, run.stderr
    for line in output_file.read_text().splitlines():
        if line.startswith("skip="):
            return line[len("skip="):]
    raise AssertionError(f"step wrote no 'skip' output; stdout={run.stdout!r}")


def test_skip_is_true_when_the_head_commit_only_touches_records(tmp_path):
    root = make_tenant(tmp_path, "valid")
    (root / "records" / "1").mkdir(parents=True)
    (root / "records" / "1" / "spec-01.json").write_text("{}\n")
    commit_all(root, "attavo: spec-gate record for #1")

    assert run_skip_step(root) == "true"


def test_skip_is_false_when_the_head_commit_touches_specs(tmp_path):
    root = make_tenant(tmp_path, "valid")
    (root / "specs" / "1" / "spec.md").write_text("# spec, amended\n")
    commit_all(root, "spec: item 1")

    assert run_skip_step(root) == "false"


def test_skip_is_false_when_the_head_commit_touches_both_records_and_specs(tmp_path):
    root = make_tenant(tmp_path, "valid")
    (root / "records" / "1").mkdir(parents=True)
    (root / "records" / "1" / "spec-01.json").write_text("{}\n")
    (root / "specs" / "1" / "spec.md").write_text("# spec, amended\n")
    commit_all(root, "spec + record")

    assert run_skip_step(root) == "false"


def test_skip_is_false_on_a_merge_commit_from_main(tmp_path):
    """Issue #17. `git show --name-only --format= HEAD` on a merge commit
    lists only paths that differ from *both* parents -- for a clean
    update-from-main merge (no conflict, every changed path traces to
    exactly one parent), that list is empty even though the merge brought
    in a real, non-record change, so the old command reads no paths and
    the step's `skip` default of `true` stands. `attavo/code-factory`
    commit e6b5710 (PR #600's update-branch merge) hit exactly this: the
    gate logged skip=true and wrote no spec-05.json.

    The fixture branch's tip is a record commit; main moves on with an
    unrelated file; merging main into the item branch reproduces the
    clean-merge shape the old command was blind to.
    """
    root = make_tenant(tmp_path, "valid")
    git(root, "checkout", "-b", "attavo/1-x")
    (root / "records" / "1").mkdir(parents=True)
    (root / "records" / "1" / "spec-01.json").write_text("{}\n")
    commit_all(root, "attavo: spec-gate record for #1")

    git(root, "checkout", "main")
    (root / "on-main.txt").write_text("main moved on\n")
    commit_all(root, "unrelated change on main")

    git(root, "checkout", "attavo/1-x")
    git(root, "merge", "--no-ff", "-m", "Merge branch 'main' into attavo/1-x", "main")

    assert run_skip_step(root) == "false"


def test_template_pushes_on_refusal():
    """Issue #28, acceptance 5. A step after a failed one is skipped by
    default -- PR #600's specimen: `attavo spec-gate` refused, and the
    refusal record never reached the branch because the push step
    afterward never ran, existing only in the run log. The push step
    must carry if: always() (still gated on the skip check, so the
    anti-loop protection from issue #9 holds), and nothing upstream may
    swallow the gate's own failure with continue-on-error.
    """
    text = TEMPLATE.read_text()

    push_block = step_block(text, "- name: Commit and push the spec-gate record")
    if_line = next(l for l in push_block.splitlines() if l.strip().startswith("if:"))
    assert "always()" in if_line
    assert "steps.touch.outputs.skip" in if_line

    gate_block = step_block(text, "- name: attavo spec-gate")
    assert "continue-on-error" not in gate_block


def test_job_guards_on_the_attavo_branch_prefix():
    """A pull_request event fires this workflow on every PR, not just ones
    from a work item's attavo/N-* branch. Without a branch guard, a PR from
    any other branch runs the gate, which cannot derive an item id and exits
    2, so the required spec-gate check goes red on ordinary PRs (issue #12).
    """
    condition = job_if_condition(TEMPLATE.read_text(), JOB_NAME)
    assert condition is not None, f"job {JOB_NAME!r} has no if: condition"
    assert "startsWith(github.head_ref, 'attavo/')" in condition
