import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def _short_forge_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    """The drive's forge poll reads its interval from the environment at call time (issue #84), so a test's looks at the forge are paced short; the interval judges nothing. The poll's bound is judged on the one clock (#332), which a test that polls holds in a file and its forge advances; no test sets a bound short enough for a slow machine to cross."""
    monkeypatch.setenv("ATTAVO_DRIVE_POLL_INTERVAL_S", "0.05")


# A build runs this suite with the executor identity exported as
# GIT_AUTHOR_*/GIT_COMMITTER_* (issue #33), and those outrank a fixture's
# own `git -c user.name=...` in git's precedence. #289 build-01's two red
# test_drive tests were that, and its executor's diagnosis of them reset
# the item's HEAD. The suite's fixtures name their own identities; none
# inherits one.
INHERITED_IDENTITY = ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_AUTHOR_DATE",
                      "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "GIT_COMMITTER_DATE")


@pytest.fixture(autouse=True)
def _no_inherited_git_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in INHERITED_IDENTITY:
        monkeypatch.delenv(name, raising=False)
