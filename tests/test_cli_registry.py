"""cli.py names no command: each module declares the commands it provides
(name, function, help section), and cli.py finds them, assembles --help from
their sections, and dispatches by name. Adding a command means adding its
declaration in its own module and nothing else.

Run: python3 -m pytest tests -q
"""
import ast
import re
import subprocess
import sys
from pathlib import Path

from attavo import cli

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"
FIXTURE = ROOT / "tests" / "fixtures" / "cli_help.txt"


def _strings(tree: ast.AST) -> list[str]:
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def test_cli_names_no_command_and_no_module_beyond_the_package() -> None:
    tree = ast.parse((ROOT / "attavo" / "cli.py").read_text())
    names = [c.name for c in cli.commands()]
    assert len(names) >= 19, names
    text = "\n".join(_strings(tree))
    for name in names:
        assert not re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text), (
            f"cli.py names the command {name!r}")
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module in ("__future__", "attavo"), (
                f"cli.py imports from {node.module}")
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] != "attavo" or alias.name == "attavo", alias.name
        if isinstance(node, (ast.Name, ast.Attribute)):
            ident = node.id if isinstance(node, ast.Name) else node.attr
            assert not ident.startswith("cmd_"), ident


def test_assembled_help_equals_the_text_captured_from_origin_main() -> None:
    expected = FIXTURE.read_text()
    assert cli.help_text() + "\n" == expected
    run = subprocess.run([sys.executable, str(ATTAVO), "--help"],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 0
    assert run.stdout == expected
