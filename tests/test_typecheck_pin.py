"""Issue #189: the interpreter is pinned once. [tool.mypy].python_version in
pyproject.toml names it; every setup-python step in ci.yml, the typecheck
job's among them, pins the same one, so the type check and the suite read
the same Python.

Run: python3 -m pytest tests -q
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"

PIN = re.compile(r"^\s*python-version:\s*[\"']?([^\"'\s#]+)[\"']?\s*(?:#.*)?$")


def mypy_config() -> dict[str, Any]:
    return cast(dict[str, Any], tomllib.loads(PYPROJECT.read_text())["tool"]["mypy"])


def job_lines(job: str) -> list[str]:
    """The raw lines of one top-level job in ci.yml (the file is read as
    text, as the other workflow tests do, so the suite needs no YAML
    library)."""
    lines = CI_YML.read_text().splitlines()
    start = next(i for i, l in enumerate(lines) if l == f"  {job}:")
    body = []
    for line in lines[start + 1:]:
        if line.strip() and len(line) - len(line.lstrip(" ")) < 4:
            break
        body.append(line)
    return body


def setup_python_pins(lines: list[str]) -> list[str]:
    """The python-version of each setup-python step in `lines`."""
    pins = []
    for i, line in enumerate(lines):
        if "uses: actions/setup-python@" not in line:
            continue
        for following in lines[i + 1:]:
            if following.strip().startswith("- "):
                break
            m = PIN.match(following)
            if m:
                pins.append(m.group(1))
                break
    return pins


def test_the_typecheck_job_pins_the_interpreter_mypy_is_configured_for() -> None:
    version = mypy_config()["python_version"]
    assert setup_python_pins(job_lines("typecheck")) == [version]


def test_every_setup_python_step_pins_that_same_interpreter() -> None:
    version = mypy_config()["python_version"]
    pins = setup_python_pins(CI_YML.read_text().splitlines())
    assert pins, "ci.yml pins no interpreter at all"
    assert set(pins) == {version}, (version, pins)


def test_mypy_is_strict_over_the_package_and_the_entry() -> None:
    config = mypy_config()
    assert config["strict"] is True
    assert config["files"] == ["attavo", "bin/attavo", "tests"]
