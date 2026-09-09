"""templates/workflows/attavo-spec-gate.yml: the gate runs against a work
item's branch and pushes its own spec-NN.json record. Without a skip, that
record commit itself triggers the gate again, which pushes another record,
forever -- the loop from issue #9 (a pilot tenant's PR #595: two operator
releases of held runs produced two more record commits).

The skip step's own decision logic -- whether HEAD, against its first
parent, touches only paths.records -- moved into the product as `attavo
record-only` (issue #19, Lane 4 of issue #54); the three shell-execution
tests and the merge-commit test that used to run this step's bash directly
now call that subcommand instead, in tests/test_record_only.py. What
remains here is that the template's step is exactly the one-line wiring
issue #19 describes, and the two behaviors issue #19 does not touch.

Deleted, this lane, verbatim from the template (the shell `attavo
record-only` replaces):

    records_rel="$(python3 -c "import tomllib; print(tomllib.load(open('attavo.toml','rb'))['paths']['records'])")"
    skip=true
    while IFS= read -r f; do
      [ -z "$f" ] && continue
      case "$f" in
        "$records_rel"/*) ;;
        *) skip=false ;;
      esac
    done <<< "$(git diff --name-only HEAD^1 HEAD)"
    echo "skip=$skip" >> "$GITHUB_OUTPUT"

Run: python3 -m pytest tests -q
"""
from pathlib import Path

from helpers import job_if_condition, step_block

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "workflows" / "attavo-spec-gate.yml"
JOB_NAME = "spec-gate"


def test_skip_step_reads_record_only_exit_code():
    """Issue #19: the template step becomes one run line reading `attavo
    record-only`'s exit code, installed unconditionally before it runs
    rather than only once the (no-longer-computed) skip is known."""
    text = TEMPLATE.read_text()

    install_block = step_block(text, "- uses: <owner>/attavo@<tag>")
    assert "if:" not in install_block

    touch_block = step_block(text, "- name: Skip when the head commit only touches records")
    assert "attavo record-only" in touch_block
    assert "GITHUB_OUTPUT" in touch_block
    assert "git diff" not in touch_block
    assert "tomllib" not in touch_block


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


def test_template_declares_merge_group_alongside_pull_request():
    """Issue #62, acceptance row 6. The job's own if: is unchanged, so a
    merge group -- with no attavo/N-* head_ref -- skips the job and
    satisfies the required context there the same way an ordinary pull
    request that isn't a work item's branch does (issue #12)."""
    text = TEMPLATE.read_text()
    lines = text.splitlines()
    on_idx = next(i for i, l in enumerate(lines) if l.strip() == "on:")
    on_body = "\n".join(lines[on_idx + 1: on_idx + 4])
    assert "pull_request:" in on_body
    assert "merge_group:" in on_body


def test_job_guards_on_the_attavo_branch_prefix():
    """A pull_request event fires this workflow on every PR, not just ones
    from a work item's attavo/N-* branch. Without a branch guard, a PR from
    any other branch runs the gate, which cannot derive an item id and exits
    2, so the required spec-gate check goes red on ordinary PRs (issue #12).
    """
    condition = job_if_condition(TEMPLATE.read_text(), JOB_NAME)
    assert condition is not None, f"job {JOB_NAME!r} has no if: condition"
    assert "startsWith(github.head_ref, 'attavo/')" in condition
