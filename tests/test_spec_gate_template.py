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

from helpers import extract_step_run, job_if_condition, step_block

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


def test_template_pushes_after_the_gate_ran_whatever_it_said():
    """Issue #116, acceptance rows 1-2. `always()` (issue #28's fix) also
    ran the commit step after a mint or checkout failure, with no bot
    identity to commit as -- on 2026-09-13 that produced a second, unrelated
    exit-128 failure on top of the real one, naming the wrong step. The
    step must instead run whenever the gate itself ran, whatever it said
    (`steps.gate.outcome` is 'success' or 'failure'), and not when the gate
    never ran. A step condition with no status function implies success(),
    so the failure branch needs `!cancelled()` to run at all:
    https://docs.github.com/en/actions/learn-github-actions/expressions#status-check-functions
    Reintroducing `always()` recreates the exit-128 second failure; dropping
    the failure branch recreates issue #28 (a refusal record that never
    reaches the branch).
    """
    text = TEMPLATE.read_text()

    gate_block = step_block(text, "- name: attavo spec-gate")
    assert "id: gate" in gate_block
    assert "continue-on-error" not in gate_block

    push_block = step_block(text, "- name: Commit and push the spec-gate record")
    if_line = next(l for l in push_block.splitlines() if l.strip().startswith("if:"))
    assert "always()" not in if_line
    assert "!cancelled()" in if_line
    assert "steps.touch.outputs.skip" in if_line
    assert "steps.gate.outcome == 'success'" in if_line
    assert "steps.gate.outcome == 'failure'" in if_line


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


def test_counsel_step_runs_only_when_the_gate_refuses():
    """Issue #133, acceptance row 4. The counsel step's own guard names
    the gate step's outcome, 'failure' -- the same outcome the
    commit-and-push step already keys on -- so a passing gate never
    produces a counsel record. It runs before the commit-and-push step,
    whose `git add` already sweeps the whole records directory, so
    counsel-NN.json rides into the same commit as the spec-NN.json
    record it read, with no separate add needed for it.
    """
    text = TEMPLATE.read_text()

    counsel_block = step_block(text, "- name: attavo counsel")
    assert "id: counsel" in counsel_block
    assert "run: attavo counsel" in counsel_block
    if_line = next(l for l in counsel_block.splitlines() if l.strip().startswith("if:"))
    assert "steps.gate.outcome == 'failure'" in if_line
    assert "steps.gate.outcome == 'success'" not in if_line
    assert "steps.touch.outputs.skip" in if_line

    assert text.index("- name: attavo counsel") < text.index(
        "- name: Commit and push the spec-gate record")

    push_script = extract_step_run(text, "Commit and push the spec-gate record")
    assert 'git add "$records_rel"' in push_script


def test_record_commit_names_the_item_not_the_pull_request_number():
    """Issue #121: tenant zero's item #651 chain read `spec-gate record for
    #675` -- PR #675's own number, not the item's. The record lives under
    <paths.records>/651/, and #675 names nothing there. The commit step
    derives the item once in shell, from GITHUB_HEAD_REF (the branch the
    record is for -- this job only ever runs on a pull_request event, per
    the job guard above, so the env var is always the real head branch),
    the same digits-between-attavo/-and-first-dash pattern bin/attavo's own
    derive_item_id uses, and refuses before committing when the branch
    doesn't match."""
    script = extract_step_run(TEMPLATE.read_text(), "Commit and push the spec-gate record")
    assert "github.event.pull_request.number" not in script
    assert "GITHUB_HEAD_REF" in script
    assert "attavo/([0-9]+)-" in script
    assert 'git commit -m "attavo: spec-gate record for item $item"' in script
    assert script.index("exit 1") < script.index("git commit"), (
        "the item-derivation guard must run, and be able to refuse, before the commit")
