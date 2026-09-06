"""Both product templates authenticate their record push as a GitHub App
installation, not a bot account with no login behind it (issue #15's
Lane 1, the record-push identity): the specimen is PR #595's bot record
pushes, which created runs GitHub held as action_required with no jobs, so
the PR's head sat on a commit whose required checks never ran. A push
authenticated as an app installation creates the pull_request run on the
record commit; the default GITHUB_TOKEN does not.

Rejection test, written and run red before the identity step existed:
neither template contained a `create-github-app-token` step at all, and
the checkout step's `token:` was absent, so `step_block` on the marker
below raised StopIteration.

Run: python3 -m pytest tests -q
"""
from pathlib import Path

from helpers import step_block

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = [
    ROOT / "templates" / "workflows" / "attavo-approve.yml",
    ROOT / "templates" / "workflows" / "attavo-spec-gate.yml",
]


def test_templates_push_with_an_app_token():
    for template in TEMPLATES:
        text = template.read_text()

        token_step = step_block(text, "- name: Mint an installation token")
        assert "uses: actions/create-github-app-token@" in token_step, (
            f"{template.name}: no create-github-app-token step")
        assert "secrets.ATTAVO_APP_ID" in token_step, (
            f"{template.name}: app-id does not read secrets.ATTAVO_APP_ID")
        assert "secrets.ATTAVO_APP_PRIVATE_KEY" in token_step, (
            f"{template.name}: private-key does not read "
            "secrets.ATTAVO_APP_PRIVATE_KEY")

        checkout_step = step_block(text, "- uses: actions/checkout@v4")
        assert "steps.app-token.outputs.token" in checkout_step, (
            f"{template.name}: checkout's token: does not reference the "
            "app-token step's output")
