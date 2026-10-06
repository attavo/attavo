"""Every git call the product makes runs with optional locks off (#343).

Git takes an "optional" index lock even for a read (`git status` refreshes
the index), and a pass's read in a checkout then fails a worker's write
there. attavo/common.py::git_env carries GIT_OPTIONAL_LOCKS=0; this reads
every module under attavo/ (ast, no import) and fails on a call to
subprocess.run, Popen or check_output whose command is a list or tuple
literal beginning with "git" and that has no `env=git_env(...)`. The git
helper in common.py is the other way in, and sets it itself.

Run: python3 -m pytest tests/test_git_calls_take_no_lock.py -q
"""
from __future__ import annotations

import ast
from pathlib import Path

from attavo.common import git_env

PRODUCT = Path(__file__).resolve().parent.parent / "attavo"
SPAWNERS = {"run", "Popen", "check_output"}


def _is_git_command(node: ast.AST) -> bool:
    if not isinstance(node, (ast.List, ast.Tuple)) or not node.elts:
        return False
    first = node.elts[0]
    return isinstance(first, ast.Constant) and first.value == "git"


def _calls_git_env(node: ast.AST) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "git_env")


def unlocked_git_calls(source: str) -> list[int]:
    """The lines of the git calls in `source` that do not run in git_env()."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in SPAWNERS and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "subprocess" and node.args):
            continue
        if not _is_git_command(node.args[0]):
            continue
        env = next((k.value for k in node.keywords if k.arg == "env"), None)
        if env is None or not _calls_git_env(env):
            found.append(node.lineno)
    return sorted(found)


def test_every_git_call_the_product_makes_runs_with_optional_locks_off() -> None:
    caught = []
    for path in sorted(PRODUCT.rglob("*.py")):
        for line in unlocked_git_calls(path.read_text()):
            caught.append(f"{path.relative_to(PRODUCT.parent)}:{line}")
    assert not caught, ("a git call runs without env=git_env(), so a read of it can take git's index lock:\n"
                        + "\n".join(caught))


def test_the_check_catches_a_bare_git_call_and_passes_one_in_git_env() -> None:
    assert unlocked_git_calls('import subprocess\nsubprocess.run(["git", "status"])\n') == [2]
    assert unlocked_git_calls('import subprocess\nsubprocess.Popen(("git", "status"), env={})\n') == [2]
    assert unlocked_git_calls('import subprocess\nsubprocess.check_output(["git", "log"], env=other())\n') == [2]
    assert unlocked_git_calls(
        'import subprocess\nsubprocess.run(["git", "-C", d, "status"],\n    capture_output=True, env=git_env())\n') == []
    assert unlocked_git_calls('import subprocess\nsubprocess.run(["ls", "-l"])\n') == []


def test_git_env_turns_optional_locks_off_and_keeps_what_it_is_given() -> None:
    assert git_env()["GIT_OPTIONAL_LOCKS"] == "0"
    assert git_env({"X": "1"})["X"] == "1"
