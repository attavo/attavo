"""templates/workflows/attavo-coherence.yml: attavo attest, run on
pull_request and merge_group, so an incoherent build/approval/verify
chain refuses before the merge that a signed attestation demands, while a
re-run is still possible (issue #62, acceptance row 5). On pull_request
the run line also carries --partial (issue #67): the verify record a
complete chain would need is the output of a stage that is itself
waiting on this check, so only the merge group and main still demand the
whole chain.

Issue #69, section "The lane": attest never writes now, so there is
nothing left to sign after the merge. This job mints and signs the
attestation itself, on the merge group alone, against the exact commit
that lands -- the two steps that do it carry no continue-on-error: a
signing failure fails this required context and the merge does not
happen. Still read-only otherwise -- no app token, no push.

Run: python3 -m pytest tests -q
"""
import json
from pathlib import Path

from helpers import extract_step_run, job_if_condition, step_block, with_keys

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "workflows" / "attavo-coherence.yml"
JOB_NAME = "coherence"
ACTIONS_ATTEST_INPUTS = set(
    json.loads((ROOT / "tests" / "fixtures" / "actions-attest-v2-inputs.json").read_text())["inputs"])


def test_template_runs_on_pull_request_and_merge_group():
    text = TEMPLATE.read_text()
    lines = text.splitlines()
    on_idx = next(i for i, l in enumerate(lines) if l.strip() == "on:")
    on_body = "\n".join(lines[on_idx + 1: on_idx + 6])
    assert "pull_request:" in on_body
    assert "merge_group:" in on_body


def test_job_has_the_three_required_permissions():
    """Row 6, issue #69: contents: read (checkout), id-token: write and
    attestations: write (actions/attest@v2, on merge_group) -- exactly
    these three, nothing that would let the job push or mint a token."""
    text = TEMPLATE.read_text()
    assert f"\n  {JOB_NAME}:\n" in text
    assert ("\n    permissions:\n"
            "      contents: read\n"
            "      id-token: write\n"
            "      attestations: write\n") in text


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


def test_attest_step_run_starts_with_attest_partial_by_event_redirected_to_a_file():
    """Row 6: attest no longer accepts --dry-run -- the same command runs
    everywhere, --partial only on pull_request -- and its stdout, the
    statement or a not-yet/zero-item line, is captured to a file the
    later steps read from."""
    script = extract_step_run(TEMPLATE.read_text(), "attavo attest")
    first_line = script.splitlines()[0]
    assert first_line == (
        "attavo attest ${{ github.event_name == 'pull_request' && '--partial' || '' }} "
        '> "$RUNNER_TEMP/attestation.json"'
    )


def test_attest_step_sets_a_statement_output_from_a_json_object_check_not_a_grep():
    """The captured file is a statement only when attest actually printed
    one -- a not-yet or zero-item line is prose, not JSON, and a merge
    group carrying no item must not hand that prose to actions/attest.
    The check reads a python3 -c json.load's exit status into the
    statement output; a grep on the file's content is exactly what this
    replaces (it would say yes for any line that merely looks like JSON).
    Written and run red before the step set this output at all."""
    script = extract_step_run(TEMPLATE.read_text(), "attavo attest")
    assert "grep" not in script
    assert "json.load" in script
    assert 'echo "statement=true" >> "$GITHUB_OUTPUT"' in script
    assert 'echo "statement=false" >> "$GITHUB_OUTPUT"' in script


def test_statement_check_discards_the_not_yet_lines_traceback():
    """Issue #76: on every pull-request run before an item's later record
    lands, `attavo attest --partial` prints its not-yet line to stdout --
    the ruled contract (issue #67) -- which this step captures to the
    file the json.load line then parses; json.load raises on that prose,
    and the interpreter's traceback reaches the step's stderr and the run
    log, read by an operator as the check having already failed (it had
    not: the exit code is correct and statement=false is correct).
    2>/dev/null on the json.load line discards only that traceback -- the
    exit code, and everything else in this file, is unchanged. Red
    today."""
    script = extract_step_run(TEMPLATE.read_text(), "attavo attest")
    json_load_line = next(l for l in script.splitlines() if "json.load" in l)
    assert json_load_line.rstrip().endswith("2>/dev/null; then")


def test_digest_and_signing_steps_require_the_statement_output():
    """The header's promise -- a not-yet or zero-item line makes the
    captured file a non-statement the later steps skip -- must be a real
    guard, not just true on the happy path: on the merge group of a pull
    request carrying no item, attest exits 0 with a non-JSON line, and
    without this guard actions/attest would be handed prose. Written and
    run red before either step read this output."""
    text = TEMPLATE.read_text()
    for name in ("Compute the merge commit's subject digest", "Sign the attestation record"):
        block = step_block(text, f"- name: {name}")
        if_line = next(l.strip() for l in block.splitlines() if l.strip().startswith("if:"))
        assert if_line == ("if: github.event_name == 'merge_group' && "
                           "steps.attest.outputs.statement == 'true'"), name


def test_checkout_step_has_no_ref_and_full_history():
    text = TEMPLATE.read_text()
    checkout_block = step_block(text, "- uses: actions/checkout@v4")
    assert "ref:" not in checkout_block
    assert "fetch-depth: 0" in checkout_block


def test_predicate_type_is_the_ruled_url():
    """Moved from the deleted tests/test_attest_template.py (issue #54's
    test, retired alongside the file it exercised)."""
    assert "predicate-type: https://attavo.dev/attestation/v1" in TEMPLATE.read_text()


def test_signing_step_inputs_are_a_subset_of_actions_attest_v2_and_include_predicate_path():
    """Issue #70: the template once passed predicate-file, an input the
    action has never declared, and continue-on-error swallowed the
    resulting warning as a signing refusal. Reading the action's own
    declared inputs (tests/fixtures/actions-attest-v2-inputs.json, the ten
    names from that run's own warning) catches the typo class rather than
    the one typo."""
    block = step_block(TEMPLATE.read_text(), "- name: Sign the attestation record")
    keys = with_keys(block)
    assert keys, "no with: keys found on the signing step"
    assert set(keys) <= ACTIONS_ATTEST_INPUTS, set(keys) - ACTIONS_ATTEST_INPUTS
    assert "predicate-path" in keys


def test_signing_step_has_no_continue_on_error():
    """Ruled: the signing step on the merge group has no continue-on-error
    -- a signing failure fails the required context and the merge does
    not happen (reversing the 2026-09-09 refusal-tolerance ruling for
    this step)."""
    block = step_block(TEMPLATE.read_text(), "- name: Sign the attestation record")
    assert "continue-on-error" not in block


def test_no_template_runs_on_push_and_attest_template_is_gone():
    """Row 7: templates/workflows/attavo-attest.yml -- the one template
    that ran on push and wrote to the tree -- no longer exists, and
    nothing that remains runs on push."""
    templates_dir = ROOT / "templates" / "workflows"
    assert not (templates_dir / "attavo-attest.yml").exists()
    for path in sorted(templates_dir.glob("*.yml")):
        lines = path.read_text().splitlines()
        on_idx = next(i for i, l in enumerate(lines) if l.strip() == "on:")
        jobs_idx = next(i for i, l in enumerate(lines) if l.strip() == "jobs:")
        on_body = "\n".join(lines[on_idx + 1:jobs_idx])
        assert "push:" not in on_body, path.name
