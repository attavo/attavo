"""attavo: the command. It names no command: each module of the package
declares the commands it provides (attavo.Command, in a module-level
COMMANDS tuple), and this module finds them, assembles --help from their
usage lines and help sections, and dispatches by name."""

from __future__ import annotations

import importlib
import os
import pkgutil
import sys

import attavo
from attavo import Command


def commands() -> list[Command]:
    """Every command the package's modules declare, in rank order."""
    own = __name__.rpartition(".")[2]
    found: dict[str, Command] = {}
    for info in pkgutil.iter_modules(attavo.__path__):
        if info.name == own:
            continue
        module = importlib.import_module(f"{attavo.__name__}.{info.name}")
        for command in getattr(module, "COMMANDS", ()):
            if command.name in found:
                raise RuntimeError(f"command {command.name!r} is declared twice")
            found[command.name] = command
    return sorted(found.values(), key=lambda c: (c.rank, c.name))


def help_text() -> str:
    table = commands()
    usage = "\n".join(f"    {line}" for c in table for line in c.usage)
    sections = sorted(s for c in table for s in c.help)
    return "\n\n".join(["attavo: the command.", usage, *(text for _, text in sections)])


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(help_text())
        return 0 if argv else 2
    table = commands()
    command = next((c for c in table if c.name == argv[0]), None)
    if command is None:
        print(f"attavo: unknown subcommand {argv[0]!r}; only "
              f"{', '.join(c.name for c in table)} exist at this commit", file=sys.stderr)
        return 2
    if command.transport and os.environ.get("ATTAVO_STAGE"):
        # The product's transport is never the executor's tool: a stage
        # runs inside an invocation whose environment names the stage,
        # and nothing under it may start, judge, or record another.
        print(f"attavo {argv[0]}: refused inside an executor (ATTAVO_STAGE is set); "
              f"the transport is the pass's, not the executor's", file=sys.stderr)
        return 2
    return command.run(argv[1:])
