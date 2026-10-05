"""cli.py names no command: each module declares the commands it provides
(name, function, help section), and cli.py finds them, assembles --help from
their sections, and dispatches by name. Adding a command means adding its
declaration in its own module and nothing else.

Run: python3 -m pytest tests -q
"""
import ast
import importlib
import pkgutil
import re
import subprocess
import sys
from pathlib import Path

import attavo
from attavo import Command, cli

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"


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


def test_help_carries_every_declared_command() -> None:
    """Every declared command's usage lines and help sections appear in
    --help, and no command name is declared twice."""
    declared: list[Command] = []
    for info in pkgutil.iter_modules(attavo.__path__):
        module = importlib.import_module(f"attavo.{info.name}")
        declared.extend(getattr(module, "COMMANDS", ()))
    names = [c.name for c in declared]
    assert len(names) == len(set(names)), "a command is declared twice"
    assert {c.name for c in cli.commands()} == set(names)
    run = subprocess.run([sys.executable, str(ATTAVO), "--help"],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 0
    assert run.stdout == cli.help_text() + "\n"
    for c in declared:
        for line in c.usage:
            assert f"    {line}\n" in run.stdout, (c.name, line)
        for _, text in c.help:
            assert text in run.stdout, (c.name, text[:40])
