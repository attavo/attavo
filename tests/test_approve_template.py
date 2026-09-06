"""templates/workflows/attavo-approve.yml: the operator-facing "/approve"
workflow runs `attavo approve` against the right item and commits the
approval record under the tenant's real records path.

Rejection test, written and run red against the template as it stood at
commit dd0a877: its "attest approve" step passed the PR/issue number as the
item id (the id is the issue number, not the PR number, and the scripts
already derive it from the work item's own branch when omitted), and its
commit step ran `git add records`, ignoring paths.records.

This test extracts the literal `run:` shell out of the template file itself
(tests/helpers.py:extract_step_run) rather than reimplementing it, so it
tracks the template's actual behavior instead of a paraphrase of it.

Run: python3 -m pytest tests -q
"""
import os
import subprocess
import sys
from pathlib import Path

from helpers import extract_step_run, git, make_tenant

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"
TEMPLATE = ROOT / "templates" / "workflows" / "attavo-approve.yml"


def run_attavo(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ATTAVO), *args], cwd=str(root),
                          capture_output=True, text=True, check=False)


def run_step(root: Path, script: str) -> subprocess.CompletedProcess:
    """Run an extracted step's shell the way GitHub Actions does: bash, -e
    -o pipefail, so a failing command (e.g. `git add` of a bad pathspec)
    stops the rest of the step, same as it would in the real workflow.
    """
    env = dict(os.environ, PATH=f"{ROOT / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}")
    return subprocess.run(["bash", "-eo", "pipefail", "-c", script], cwd=str(root),
                          env=env, capture_output=True, text=True, check=False)


def rendered(step_name: str, **subs: str) -> str:
    script = extract_step_run(TEMPLATE.read_text(), step_name)
    for token, value in subs.items():
        script = script.replace(token, value)
    return script


def build_tenant(tmp_path: Path) -> Path:
    """A tenant with records at a non-default path, on branch attavo/7-x,
    with a passing spec-gate record for item 7 -- and a bare remote to push
    the approval commit to, matching what the workflow's checkout gives it.
    """
    root = make_tenant(tmp_path, "nondefault_records")
    git(root, "checkout", "-b", "attavo/7-x")
    assert run_attavo(root, "spec-gate", "7").returncode == 0

    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--quiet", "--bare", "-b", "main", str(bare)], check=True)
    git(root, "remote", "add", "origin", str(bare))
    return root


def run_workflow(root: Path) -> None:
    """Run the template's two `run:` steps against a PR numbered 12,
    commenter alice, on the ref actions/checkout would have left us on. The
    app's bot identity (a separate step this test doesn't execute -- it
    calls the GitHub API) is substituted with a fixed fake value, the same
    way the PR/issue number and the ref are.
    """
    approve = run_step(root, rendered(
        "attavo approve",
        **{"${{ github.event.issue.number }}": "12",
           "${{ github.event.comment.user.login }}": "alice"}))
    if approve.returncode != 0:
        return
    run_step(root, rendered(
        "Commit and push the approval record",
        **{"${{ github.event.issue.number }}": "12",
           "${{ steps.pr.outputs.ref }}": "attavo/7-x",
           "${{ steps.bot.outputs.login }}": "attavo-app[bot]",
           "${{ steps.bot.outputs.email }}":
               "12345+attavo-app[bot]@users.noreply.github.com"}))


def committed(root: Path, path: str) -> bool:
    show = subprocess.run(["git", "-C", str(root), "show", f"HEAD:{path}"],
                          capture_output=True, text=True, check=False)
    return show.returncode == 0


def test_approve_template_records_the_item_from_the_branch_not_the_pr(tmp_path):
    root = build_tenant(tmp_path)

    run_workflow(root)

    assert committed(root, "attavo/records/7/approval-01.json"), (
        "the workflow should approve item 7 (from the attavo/7-x branch) and "
        "commit the record at paths.records, even though the PR is #12")
    assert not (root / "records").exists(), (
        "nothing should land under the default 'records/' path; "
        "paths.records for this tenant is 'attavo/records'")
