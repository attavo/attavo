"""The build's calibration (operator's rulings, 2026-09-20): a declared probe
in place of a whole run of the checks before the first build; one bounded
test command for the executor; a selector that fails safe. The specimen is
#194's build-01: 215 s of suite before the executor began, 241 s of it at
the gate, and a 590 s wait on a third run inside the executor, around 220 s
of work.

Run: python3 -m pytest tests -q
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import jsonschema

from helpers import commit_all, git, make_tenant
from test_drive import approve_item, record_files, run_attavo

ROOT = Path(__file__).resolve().parent.parent
SELECT = ROOT / ".attavo" / "bin" / "select-tests"
MANIFEST_SCHEMA = json.loads((ROOT / "schema" / "manifest.schema.json").read_text())


def tenant(tmp_path: Path, checks: str = "", tests: str = "") -> Path:
    root = make_tenant(tmp_path, "build_valid")
    toml = root / "attavo.toml"
    text = toml.read_text()
    if tests:
        text = text.replace('[tests]\nselect = "cat"\nrun = "true"', tests)
    toml.write_text(text + ("\n" + checks + "\n" if checks else ""))
    commit_all(root, "configure")
    git(root, "checkout", "-q", "-b", "attavo/1-x")
    return root


def events(root: Path) -> list[dict[str, Any]]:
    found = sorted(root.glob(".git/**/build-01.events.jsonl"))
    assert found, "no events file for build-01"
    return [json.loads(line) for line in found[-1].read_text().splitlines()]


def build_record(root: Path) -> dict[str, Any]:
    record: dict[str, Any] = json.loads(record_files(root, "build")[-1].read_text())
    return record


def test_build_runs_the_probe_and_not_the_whole_checks_before_the_executor(tmp_path: Path) -> None:
    root = tenant(tmp_path, checks='[checks]\ncommand = "echo ran >> checks-ran.txt"\n'
                                   'probe = "echo probed >> probe-ran.txt"\nprobe_timeout_s = 30')
    (root / ".gitignore").write_text("checks-ran.txt\nprobe-ran.txt\n")
    commit_all(root, "ignore the counters")
    approve_item(root)
    assert run_attavo(root, "build", "1").returncode == 0
    assert (root / "probe-ran.txt").read_text().count("probed") == 1
    assert (root / "checks-ran.txt").read_text().count("ran") == 1, "the checks ran before the build, not only at its gate"
    texts = [e["text"] for e in events(root) if e["kind"] == "gate"]
    assert texts[0] == "checking this machine can run the checks", texts
    assert texts[1] == "this machine can run the checks", texts


def test_build_without_a_probe_asks_the_old_way_and_says_so_in_the_feed(tmp_path: Path) -> None:
    root = tenant(tmp_path, checks='[checks]\ncommand = "echo ran >> checks-ran.txt"')
    (root / ".gitignore").write_text("checks-ran.txt\n")
    commit_all(root, "ignore the counter")
    approve_item(root)
    assert run_attavo(root, "build", "1").returncode == 0
    assert (root / "checks-ran.txt").read_text().count("ran") == 2
    assert [e["text"] for e in events(root) if e["kind"] == "gate"][0] == "checking this machine can run the checks"


def test_a_failing_probe_refuses_the_build_as_an_environment_stop_before_the_executor(tmp_path: Path) -> None:
    root = tenant(tmp_path, checks='[checks]\ncommand = "true"\nprobe = "echo no pytest here; exit 3"\nprobe_timeout_s = 30')
    approve_item(root)
    assert run_attavo(root, "build", "1").returncode == 1
    record = build_record(root)
    assert (record["status"], record["stop"]["class"], record["stop"]["provenance"]) == (
        "refused", "environment", "tenant"), record
    assert record["refusal"] == "[checks].probe exited 3 on the approved tree", record["refusal"]
    assert record["checks"] == {"command": "echo no pytest here; exit 3", "exit": 3}, record["checks"]
    assert "no pytest here" in record["stop"]["detail"], record["stop"]
    assert not (root / "built.txt").exists(), "the executor ran"


def test_a_probe_past_its_bound_is_ended_and_refuses_the_build(tmp_path: Path) -> None:
    root = tenant(tmp_path, checks='[checks]\ncommand = "true"\nprobe = "sleep 30"\nprobe_timeout_s = 1')
    approve_item(root)
    started = time.monotonic()
    assert run_attavo(root, "build", "1").returncode == 1
    assert time.monotonic() - started < 15
    record = build_record(root)
    assert record["refusal"] == "[checks].probe did not finish within 1 s", record["refusal"]
    assert record["stop"]["class"] == "environment", record["stop"]


def test_the_manifest_schema_requires_a_bound_with_a_probe(tmp_path: Path) -> None:
    checks = MANIFEST_SCHEMA["properties"]["checks"]
    jsonschema.validate({"command": "true", "probe": "true", "probe_timeout_s": 5}, checks)
    jsonschema.validate({"command": "true"}, checks)
    try:
        jsonschema.validate({"command": "true", "probe": "true"}, checks)
    except jsonschema.ValidationError:
        pass
    else:
        raise AssertionError("a probe with no bound validated")
    jsonschema.validate({"select": "cat", "run": "true", "call_timeout_s": 240},
                        MANIFEST_SCHEMA["properties"]["tests"])


SELECTING = ('[tests]\nselect = "sed -n \'s#^src/\\\\(.*\\\\)[.]py$#tests/test_\\\\1.py#p\'"\n'
             'run = "sh -c \'cat > selected.txt; cat selected.txt\'"')


def test_attavo_test_runs_what_the_tenant_selects_for_the_items_changes_committed_or_not(tmp_path: Path) -> None:
    root = tenant(tmp_path, tests=SELECTING)
    (root / ".gitignore").write_text("selected.txt\n")
    (root / "src").mkdir()
    (root / "src" / "committed.py").write_text("x = 1\n")
    commit_all(root, "a committed change")
    (root / "src" / "unstaged.py").write_text("y = 2\n")
    (root / "records" / "1").mkdir(parents=True, exist_ok=True)
    (root / "records" / "1" / "note.py").write_text("")
    run = run_attavo(root, "test", "1")
    assert run.returncode == 0, run.stdout + run.stderr
    assert (root / "selected.txt").read_text().split() == ["tests/test_committed.py", "tests/test_unstaged.py"]
    assert "2 selected" in run.stdout, run.stdout


def test_attavo_test_exits_as_the_run_does_and_says_when_nothing_is_selected(tmp_path: Path) -> None:
    failing = tenant(tmp_path, tests='[tests]\nselect = "cat"\nrun = "sh -c \'cat >/dev/null; exit 7\'"')
    (failing / "anything.txt").write_text("z\n")
    assert run_attavo(failing, "test", "1").returncode == 7
    quiet = tenant(tmp_path / "quiet", tests='[tests]\nselect = "true"\nrun = "exit 9"')
    (quiet / "anything.txt").write_text("z\n")
    run = run_attavo(quiet, "test", "1")
    assert run.returncode == 0 and "no test is selected" in run.stdout, run.stdout + run.stderr


def test_attavo_test_ends_a_run_past_its_bound_with_everything_it_started(tmp_path: Path) -> None:
    root = tenant(tmp_path, tests='[tests]\nselect = "cat"\ncall_timeout_s = 1\n'
                                  'run = "sh -c \'cat >/dev/null; sleep 30 & echo $! > child.pid; wait\'"')
    (root / ".gitignore").write_text("child.pid\n")
    (root / "anything.txt").write_text("z\n")
    started = time.monotonic()
    run = run_attavo(root, "test", "1")
    assert time.monotonic() - started < 15
    assert run.returncode == 124, run.stdout + run.stderr
    assert "did not finish within 1 s" in run.stdout, run.stdout
    child = int((root / "child.pid").read_text())
    time.sleep(0.5)
    # a process that was ended but not yet reaped still has a pid; its state says Z
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(child)], capture_output=True, text=True,
                           check=False).stdout.strip()
    assert state == "" or state.startswith("Z"), f"the run's own child outlived the timeout (state {state!r})"


def test_build_hands_the_executor_the_test_command_and_no_checks_command(tmp_path: Path) -> None:
    root = tenant(tmp_path, checks='[checks]\ncommand = "true"', tests='[tests]\nselect = "cat"\nrun = "true"\ncall_timeout_s = 240')
    (root / "attavo" / "adapters" / "fixture").mkdir(parents=True)
    probe = root / "attavo" / "adapters" / "fixture" / "run"
    probe.write_text('#!/bin/sh\nenv | grep "^ATTAVO_" | sort > "$PWD/seen-env.txt"\n'
                     'echo built > built.txt\ngit add built.txt\ngit commit -q -m built\n'
                     'printf \'{"status": "done", "question": null}\\n\'\n')
    probe.chmod(0o755)
    (root / ".gitignore").write_text("seen-env.txt\n")
    commit_all(root, "a tenant-side adapter that prints what it was given")
    approve_item(root)
    run_attavo(root, "build", "1")
    seen = (root / "seen-env.txt").read_text()
    assert f"ATTAVO_TEST_COMMAND=python3 {ROOT / 'bin' / 'attavo'} test 1" in seen, seen
    assert "ATTAVO_TEST_TIMEOUT_S=240" in seen, seen
    assert "ATTAVO_CHECKS_COMMAND" not in seen, seen


def select(*paths: str) -> list[str]:
    run = subprocess.run([sys.executable, str(SELECT)], input="".join(p + "\n" for p in paths),
                         capture_output=True, text=True, check=True)
    return run.stdout.split()


def test_this_repositorys_selector_fails_safe_on_a_path_it_does_not_know() -> None:
    assert select("attavo/a_module_nobody_mapped.py") == ["tests"]
    assert select("attavo/report.py", "attavo/a_module_nobody_mapped.py") == ["tests"]
    assert select("scripts/anything.py") == ["tests"]
    assert select("bin/attavo") == ["tests"]
    assert select("attavo/common.py") == ["tests"], "a module every test stands on selects everything"


def test_this_repositorys_selector_maps_a_module_to_its_own_tests_and_prose_to_nothing() -> None:
    assert select("attavo/report.py") == ["tests/test_report.py"]
    assert select("tests/test_ledger.py") == ["tests/test_ledger.py"]
    assert select("README.md", "attest/records/1/build-01.json", "attest/specs/9/spec.md") == []
    assert "tests/test_executor_conformance.py" in select("adapters/codex/run")


def test_every_test_file_the_selector_names_exists_and_every_module_is_mapped_or_falls_through() -> None:
    namespace: dict[str, Any] = {}
    exec(compile(SELECT.read_text(), str(SELECT), "exec"), {"__name__": "selector"}, namespace)
    named = {t for ts in namespace["MODULE_TESTS"].values() for t in ts}
    named |= {t for _, ts in namespace["PREFIX_TESTS"] for t in ts}
    missing = sorted(t for t in named if not (ROOT / t).is_file())
    assert not missing, f"the selector names test files that do not exist: {missing}"
    for module in sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "attavo").glob("*.py")):
        assert select(module), f"{module} selects nothing at all"


def test_this_repositorys_manifest_declares_the_probe_a_gate_without_the_suite_and_a_bound() -> None:
    import tomllib
    m = tomllib.loads((ROOT / "attavo.toml").read_text())
    assert "pytest tests" not in m["checks"]["command"], m["checks"]["command"]
    assert "mypy" in m["checks"]["command"], m["checks"]["command"]
    assert m["checks"]["probe"] and m["checks"]["probe_timeout_s"] <= 120, m["checks"]
    assert m["tests"]["call_timeout_s"] <= 300, m["tests"]
    jsonschema.validate(m["checks"], MANIFEST_SCHEMA["properties"]["checks"])
    jsonschema.validate(m["tests"], MANIFEST_SCHEMA["properties"]["tests"])
