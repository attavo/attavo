"""adapters/executor_env.py -- the one place an adapter decides what its
executor's process inherits (issue #198).

The product hands an adapter its contract as ATTAVO_* environment
variables, among them ATTAVO_EVENTS, the item's live events file: a write
handle that belongs to the adapter alone. Every tool the executor runs
inherits whatever environment the executor was launched with, so an
executor launched with the adapter's own environment lets a test suite
that runs adapters/fixture/run append to that file (attavo/attavo#194,
build-01: 26 of 83 lines) and lets a test that asserts a refusal find
ATTAVO_ADAPTER_CONFIG naming a model.

An adapter keeps reading its own variables from os.environ and passes
executor_env() as env= to every process it starts to be the executor.
Probes of the tool itself (`--version`) need no such care.
"""
from __future__ import annotations

import os
from collections.abc import Mapping

CONTRACT_PREFIX = "ATTAVO_"


def executor_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """A copy of environ (default: os.environ) with every ATTAVO_* name
    removed. Everything else -- PATH, HOME, the executor identity's
    GIT_* variables, the tool's own credentials -- passes through."""
    source = os.environ if environ is None else environ
    return {k: v for k, v in source.items() if not k.startswith(CONTRACT_PREFIX)}
