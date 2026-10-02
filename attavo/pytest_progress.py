"""pytest_progress: the plugin `attavo test` loads (-p attavo.pytest_progress)
for a tenant that declares [tests].progress = "pytest". It appends one line
per collected test (`C <id>`) and one per finished test (`R <outcome> <id>`)
to the file ATTAVO_TEST_PROGRESS names, each line opened, written and closed
at once, so a run the tool ends at its bound still leaves its results. Under
pytest-xdist every worker loads it and writes its own lines; a short append
is atomic, and the reader takes the union."""
from __future__ import annotations

import os
from typing import Any


def _append(line: str) -> None:
    path = os.environ.get("ATTAVO_TEST_PROGRESS")
    if not path:
        return
    with open(path, "a") as handle:
        handle.write(line + "\n")


def pytest_collection_finish(session: Any) -> None:
    for item in session.items:
        _append(f"C {item.nodeid}")


def pytest_runtest_logreport(report: Any) -> None:
    # the call phase decides a test; setup or teardown errors and skips count too
    if report.when == "call" or (report.when == "setup" and report.outcome != "passed"):
        _append(f"R {report.outcome} {report.nodeid}")
