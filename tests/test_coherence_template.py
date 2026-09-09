"""templates/workflows/attavo-coherence.yml: attavo attest --dry-run,
run on pull_request and merge_group, so an incoherent build/approval/verify
chain refuses before the merge that attavo attest itself demands, while a
re-run is still possible (issue #62, acceptance row 5). On pull_request
the run line also carries --partial (issue #67, acceptance row 5): the
verify record a complete chain would need is the output of a stage that
is itself waiting on this check, so only the merge group and main still
demand the whole chain.

Read-only throughout -- no app token, no push -- unlike the other three
templates, which write a record back to the branch.

Run: python3 -m pytest tests -q
"""
from pathlib import Path

from helpers import job_if_condition, step_block

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "workflows" / "attavo-coherence.yml"
JOB_NAME = "coherence"


def test_template_runs_on_pull_request_and_merge_group():
    text = TEMPLATE.read_text()
    lines = text.splitlines()
    on_idx = next(i for i, l in enumerate(lines) if l.strip() == "on:")
    on_body = "\n".join(lines[on_idx + 1: on_idx + 6])
    assert "pull_request:" in on_body
    assert "merge_group:" in on_body


def test_job_id_is_coherence_with_read_only_permissions():
    text = TEMPLATE.read_text()
    assert f"\n  {JOB_NAME}:\n" in text
    assert "\n    permissions:\n      contents: read\n" in text


def test_job_has_no_branch_guard():
    """Unlike spec-gate, coherence runs on any pull request -- it has
    nothing that needs an attavo/N-* branch to derive."""
    condition = job_if_condition(TEMPLATE.read_text(), JOB_NAME)
    assert condition is None


def test_no_step_mints_a_token_or_pushes():
    text = TEMPLATE.read_text()
    assert "create-github-app-token" not in text
    assert "git push" not in text
    assert "secrets." not in text


def test_run_step_is_exactly_attest_dry_run_partial_by_event():
    """Issue #67, acceptance row 5: --partial only on pull_request -- a
    merge group still demands the whole chain, since nothing after it
    can still supply a record it finds missing."""
    text = TEMPLATE.read_text()
    run_lines = [l.strip().removeprefix("- ") for l in text.splitlines()
                if l.strip().removeprefix("- ").startswith("run:")]
    assert run_lines == [
        "run: attavo attest --dry-run "
        "${{ github.event_name == 'pull_request' && '--partial' || '' }}"
    ]


def test_checkout_step_has_no_ref_and_full_history():
    text = TEMPLATE.read_text()
    checkout_block = step_block(text, "- uses: actions/checkout@v4")
    assert "ref:" not in checkout_block
    assert "fetch-depth: 0" in checkout_block
