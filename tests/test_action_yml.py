"""action.yml: the composite action's own steps -- what a tenant's workflow
actually installs via `uses: attavo/attavo@<tag>`, not a template the tenant
copies. This file ships with the product.

Issue #66: every record a workflow writes carries product_version:
unknown. Read a real tenant run's log (attavo/code-factory, the spec-gate
workflow, run 34554129450, 2026-09-11): the step's own echo command
rendered `ATTAVO_PRODUCT_VERSION=` with the ref missing --
`github.action_ref` is empty inside this composite action's own `run`
step, though the workflow's own step-display name (computed differently)
still showed the tag -- and the following step's env block shows
`ATTAVO_PRODUCT_VERSION:` with no value. Mechanism 1 of the issue's three
named candidates, not 2 or 3.

The fix cannot fall back to git describe against PRODUCT_ROOT (the
directory this file lives in, once installed): a tenant's checkout of a
pinned action has no .git -- GitHub extracts a tag into
_actions/<owner>/<repo>/<ref>/ from a tarball, the same layout
tests/test_record_schemas.py::test_product_version_unknown_when_product_root_is_not_a_git_repo
reproduces -- and that fallback correctly reporting "unknown" there is
ruled, existing behavior this lane does not touch. The ref is the
directory's own name, already read once in this file for
github.action_path/bin; reading it again needs no git and no
github.action_ref, and is only consulted when the ref is empty and the
directory is not a git checkout -- so `uses: ./` in this repo's own
action-self-check job, a real git checkout with tags, still gets its
answer from git describe exactly as before.

Run: python3 -m pytest tests -q
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from helpers import make_tenant

ROOT = Path(__file__).resolve().parent.parent
ACTION_YML = ROOT / "action.yml"
ATTAVO = ROOT / "bin" / "attavo"


def _product_version_script() -> str:
    """The literal shell of the action.yml step that sets
    ATTAVO_PRODUCT_VERSION. action.yml's steps put `run:` on the same
    line as the list dash (`- run: ...` or `- run: |`), unlike the
    workflow templates helpers.extract_step_run reads (`- name: X` then a
    separate `run:` line), so this reads the block directly rather than
    reusing that helper."""
    lines = ACTION_YML.read_text().splitlines()
    marker = next(i for i, l in enumerate(lines) if "ATTAVO_PRODUCT_VERSION" in l)
    start = marker
    while not re.match(r"^\s*- run:", lines[start]):
        start -= 1
    dash_indent = len(lines[start]) - len(lines[start].lstrip(" "))
    first = lines[start].strip()[len("- run:"):].strip()
    if first and first != "|":
        return first
    block: list[str] = []
    block_indent = None
    for line in lines[start + 1:]:
        if not line.strip():
            block.append("")
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent <= dash_indent + 2:
            break
        if block_indent is None:
            block_indent = indent
        block.append(line[block_indent:])
    return "\n".join(block).rstrip("\n")


def test_product_version_reaches_the_record_when_action_ref_is_empty_and_checkout_has_no_git(tmp_path):
    """Row 2, issue #66: the action's own step, run the way a tenant's
    pinned checkout runs it -- github.action_ref empty, the installed
    directory named by its ref and carrying no .git -- must still produce
    a record whose product_version is that ref. Red today: action.yml
    writes only from github.action_ref, which this test renders empty,
    the observed mechanism."""
    action_dir = tmp_path / "_actions" / "attavo" / "attavo" / "v0.13.0"
    (action_dir / "bin").mkdir(parents=True)
    shutil.copy(ATTAVO, action_dir / "bin" / "attavo")
    shutil.copytree(ROOT / "attavo", action_dir / "attavo")
    shutil.copytree(ROOT / "schema", action_dir / "schema")

    script = (_product_version_script()
             .replace("${{ github.action_ref }}", "")
             .replace("${{ github.action_path }}", str(action_dir)))

    github_env = tmp_path / "github_env"
    github_env.write_text("")
    subprocess.run(["bash", "-c", script], check=True,
                   env={**os.environ, "GITHUB_ENV": str(github_env)})

    env_lines = dict(line.split("=", 1) for line in github_env.read_text().splitlines() if line)
    assert env_lines.get("ATTAVO_PRODUCT_VERSION") == "v0.13.0"

    tenant = make_tenant(tmp_path, "build_valid", name="tenant")

    env = dict(os.environ)
    env["ATTAVO_PRODUCT_VERSION"] = env_lines["ATTAVO_PRODUCT_VERSION"]
    run = subprocess.run([sys.executable, str(action_dir / "bin" / "attavo"), "spec-gate", "1"],
                         cwd=str(tenant), env=env, capture_output=True, text=True, check=False)
    assert run.returncode == 0, run.stderr

    rec = json.loads((tenant / "records" / "1" / "spec-01.json").read_text())
    assert rec["product_version"] == "v0.13.0"


def test_product_version_step_leaves_git_checkouts_to_git_describe(tmp_path):
    """The fallback added for row 2 must not fire when the installed
    directory is a real git checkout (this repo's own action-self-check
    job, `uses: ./` against a full clone with tags) -- there,
    github.action_ref is empty exactly as in the tarball case, but git
    describe already gives the right answer and the path's own directory
    name (a checkout dir, not a ref) would be wrong."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".git").mkdir()

    script = (_product_version_script()
             .replace("${{ github.action_ref }}", "")
             .replace("${{ github.action_path }}", str(checkout)))

    github_env = tmp_path / "github_env"
    github_env.write_text("")
    subprocess.run(["bash", "-c", script], check=True,
                   env={**os.environ, "GITHUB_ENV": str(github_env)})
    env_lines = dict(line.split("=", 1) for line in github_env.read_text().splitlines() if line)
    assert env_lines.get("ATTAVO_PRODUCT_VERSION", "") == ""
