"""attavo: the package. Each module that provides a command declares it as a
module-level COMMANDS tuple of Command; attavo.cli finds them there, assembles
--help from their sections, and dispatches by name."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Command:
    """One command a module provides.

    usage: the lines the command adds to the top of --help (without the
    four-space indent). help: its prose sections as (order, text); sections
    from every command are printed ordered by order. rank orders the usage
    lines and the unknown-subcommand message. transport: the product's own
    transport, refused inside an executor (ATTAVO_STAGE set)."""
    name: str
    run: Callable[[list[str]], int]
    usage: tuple[str, ...]
    rank: int
    help: tuple[tuple[int, str], ...] = ()
    transport: bool = False
