"""templates/workflows/attest-spec-gate.yml: the gate runs against a work
item's branch and pushes its own spec-NN.json record. Without a skip, that
record commit itself triggers the gate again, which pushes another record,
forever -- the loop from issue #9 (code-factory PR #595: two operator
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

from helpers import commit_all, extract_step_run, job_if_condition, make_tenant

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "workflows" / "attest-spec-gate.yml"
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
    commit_all(root, "attest: spec-gate record for #1")

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


def test_job_guards_on_the_attest_branch_prefix():
    """A pull_request event fires this workflow on every PR, not just ones
    from a work item's attest/N-* branch. Without a branch guard, a PR from
    any other branch runs the gate, which cannot derive an item id and exits
    2, so the required spec-gate check goes red on ordinary PRs (issue #12).
    """
    condition = job_if_condition(TEMPLATE.read_text(), JOB_NAME)
    assert condition is not None, f"job {JOB_NAME!r} has no if: condition"
    assert "startsWith(github.head_ref, 'attest/')" in condition
