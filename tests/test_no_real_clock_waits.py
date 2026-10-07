"""No test waits on the real clock (#332): the invariant of #257, held by a check.

Every bound the product judges reads one clock, a file the tests set
(attavo/clock.py, ATTAVO_CLOCK_FILE), and a test waits on a fact: a file, a
process, an event. This reads every test source (ast, no import) and fails on

  sleep       a call to time.sleep, in a test, a fixture or a helper;
  bound       a bound environment variable (ATTAVO_*_S, but not a pacing
              *_INTERVAL_S: how often the product looks is not what it
              judges) or a *_S constant set to a sub-second string: a bound
              a loaded machine crosses by being slow;
  wall loop   a while loop that reads wall time (time.time, time.monotonic,
              time.perf_counter, datetime.now) in its test or its body;
  barrier     a shell wait loop (a while or until loop that pauses) in a command string
              that does not count its looks: left to itself it is ended only
              by a bound the product judges on the machine's clock, which a
              loaded machine crosses by being slow to start the other side
              (test_concurrent_calls_hold_one_slot_each..., #332).

The check has no exceptions list: a wait that is honest names its fact
through tests/helpers.py::wait_for, which counts tries and reads no clock.

Run: python3 -m pytest tests/test_no_real_clock_waits.py -q
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent
WALL_TIME = {("time", "time"), ("time", "monotonic"), ("time", "perf_counter"),
             ("time", "time_ns"), ("time", "monotonic_ns"), ("datetime", "now"),
             ("datetime", "utcnow")}


def _call_name(node: ast.AST) -> tuple[str, str] | None:
    """(owner, attr) for `owner.attr(...)`, ('', name) for `name(...)`."""
    if not isinstance(node, ast.Call):
        return None
    f = node.func
    if isinstance(f, ast.Attribute):
        owner = f.value
        if isinstance(owner, ast.Attribute):
            return owner.attr, f.attr
        return (owner.id if isinstance(owner, ast.Name) else ""), f.attr
    if isinstance(f, ast.Name):
        return "", f.id
    return None


def _sub_second(node: ast.AST | None) -> bool:
    if not isinstance(node, ast.Constant):
        return False
    try:
        return 0 < float(str(node.value)) < 1
    except (TypeError, ValueError):
        return False


def _is_bound_name(name: object) -> bool:
    return (isinstance(name, str) and name.endswith("_S")
            and not name.endswith("_INTERVAL_S"))


SHELL_WAIT = re.compile(r"\b(while|until)\b[^\n]*?\bsleep\b", re.S)
LOOK_COUNTED = re.compile(r"\$\(\(\s*\w+\s*\+\s*1\s*\)\)")


def _text(node: ast.AST) -> str:
    """The constant text of a string or f-string, its fields left out."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
    return ""


def offences(source: str) -> list[tuple[int, str]]:
    """(line, rule) for each place the source waits on the real clock."""
    found: list[tuple[int, str]] = []
    tree = ast.parse(source)
    imported_sleep = any(isinstance(n, ast.ImportFrom) and n.module == "time"
                         and any(a.name == "sleep" for a in n.names) for n in ast.walk(tree))
    for node in ast.walk(tree):
        call = _call_name(node)
        if call in {("time", "sleep")} or (imported_sleep and call == ("", "sleep")):
            found.append((node.lineno, "sleep"))  # type: ignore[attr-defined]
        if call and call[1] == "setenv" and len(node.args) == 2:  # type: ignore[attr-defined]
            key, value = node.args  # type: ignore[attr-defined]
            if isinstance(key, ast.Constant) and _is_bound_name(key.value) and _sub_second(value):
                found.append((node.lineno, "bound"))  # type: ignore[attr-defined]
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and _is_bound_name(k.value) and _sub_second(v):
                    found.append((k.lineno, "bound"))
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and _is_bound_name(t.id) and _sub_second(node.value):
                    found.append((node.lineno, "bound"))
                if (isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant)
                        and _is_bound_name(t.slice.value) and _sub_second(node.value)):
                    found.append((node.lineno, "bound"))
        if isinstance(node, (ast.Constant, ast.JoinedStr)):
            text = _text(node)
            if SHELL_WAIT.search(text) and not LOOK_COUNTED.search(text):
                found.append((node.lineno, "barrier"))
        if isinstance(node, ast.While):
            reads = [c for c in ast.walk(node) if _call_name(c) in WALL_TIME]
            if reads:
                found.append((node.lineno, "wall loop"))
    return sorted(set(found))


def test_no_test_waits_on_the_real_clock() -> None:
    caught = []
    for path in sorted(TESTS.rglob("*.py")):
        for line, rule in offences(path.read_text()):
            caught.append(f"{path.relative_to(TESTS.parent)}:{line}: {rule}")
    assert not caught, "a test waits on the real clock; wait on a fact or set the clock file:\n" + "\n".join(caught)


def test_the_check_catches_each_form_it_names() -> None:
    assert offences("import time\ntime.sleep(0.2)\n") == [(2, "sleep")]
    assert offences("from time import sleep\nsleep(1)\n") == [(2, "sleep")]
    assert offences('env = {"ATTAVO_HEARTBEAT_S": "0.2"}\n') == [(1, "bound")]
    assert offences('def f(m):\n    m.setenv("ATTAVO_DRIVE_POLL_BOUND_S", "0.5")\n') == [(2, "bound")]
    assert offences('POLL_BOUND_S = "0.5"\n') == [(1, "bound")]
    assert offences('env["ATTAVO_HEARTBEAT_S"] = "0.1"\n') == [(1, "bound")]
    wait = "while [ ! -f x ]; do " + "sleep 0.1; done"
    assert offences(f"c = {wait!r}\n") == [(1, "barrier")]
    assert offences('c = f"' + "while [ $(ls {b}) -lt 3 ]; do " + 'sleep 0.05; done"\n') == [(1, "barrier")]
    assert offences("import time\nwhile time.monotonic() < d:\n    pass\n") == [(2, "wall loop")]
    assert offences("import time\nwhile not ok():\n    if time.time() > d:\n        break\n") == [(2, "wall loop")]


def test_the_check_passes_what_waits_on_a_fact() -> None:
    assert offences('env = {"ATTAVO_HEARTBEAT_S": "15", "ATTAVO_DRIVE_POLL_INTERVAL_S": "0.05"}\n') == []
    assert offences("import time\nstamp = time.time() - 60\n") == []
    counted = "n=0; while [ ! -f x ] && [ $n -lt 1200 ]; do " + "sleep 0.1; n=$((n+1)); done"
    assert offences(f"c = {counted!r}\n") == []
    assert offences("while not path.exists():\n    pass\n") == []


def test_the_check_has_no_exceptions_list() -> None:
    names = {n.id for n in ast.walk(ast.parse(Path(__file__).read_text())) if isinstance(n, ast.Name)}
    assert not {n for n in names if "allow" in n.lower() or "exempt" in n.lower() or "skip" in n.lower()}
