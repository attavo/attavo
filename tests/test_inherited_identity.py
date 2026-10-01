"""#289 build-01: a build exports the executor identity as GIT_AUTHOR_*
and GIT_COMMITTER_*, and the tenant's tests run under it. No test may
inherit it: a fixture that names its own author must get that author."""
import os
import subprocess
from pathlib import Path

from conftest import INHERITED_IDENTITY


def test_no_test_inherits_a_git_identity_from_the_environment() -> None:
    assert [n for n in INHERITED_IDENTITY if n in os.environ] == []


def test_a_fixture_commit_carries_the_author_it_names(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=Fixture", "-c",
                    "user.email=fixture@example.com", "commit", "-q", "--allow-empty", "-m", "x"],
                   check=True)
    author = subprocess.run(["git", "-C", str(tmp_path), "log", "-1", "--format=%an <%ae>"],
                            capture_output=True, text=True, check=True).stdout.strip()
    assert author == "Fixture <fixture@example.com>"
