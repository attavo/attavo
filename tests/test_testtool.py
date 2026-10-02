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
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from attavo.testtool import bound_report, read_progress, runner_calls_outside_the_tool
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
    # The counters live outside the tree: since item 227 the gate runs the
    # checks in a clean worktree of HEAD, not in the tenant's own directory.
    counters = tmp_path / "counters"
    counters.mkdir()
    root = tenant(tmp_path, checks=f'[checks]\ncommand = "echo ran >> {counters}/checks-ran.txt"\n'
                                   f'probe = "echo probed >> {counters}/probe-ran.txt"\nprobe_timeout_s = 30')
    approve_item(root)
    assert run_attavo(root, "build", "1").returncode == 0
    assert (counters / "probe-ran.txt").read_text().count("probed") == 1
    assert (counters / "checks-ran.txt").read_text().count("ran") == 1, "the checks ran before the build, not only at its gate"
    texts = [e["text"] for e in events(root) if e["kind"] == "gate"]
    assert texts[0] == "checking this machine can run the checks", texts
    assert texts[1] == "this machine can run the checks", texts


def test_build_without_a_probe_asks_the_old_way_and_says_so_in_the_feed(tmp_path: Path) -> None:
    counters = tmp_path / "counters"
    counters.mkdir()
    root = tenant(tmp_path, checks=f'[checks]\ncommand = "echo ran >> {counters}/checks-ran.txt"')
    approve_item(root)
    assert run_attavo(root, "build", "1").returncode == 0
    assert (counters / "checks-ran.txt").read_text().count("ran") == 2
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
    run = run_attavo(root, "test", "1", timeout=300)
    assert run.returncode == 124, run.stdout + run.stderr
    assert "did not finish within 1 s" in run.stdout, run.stdout
    child = int((root / "child.pid").read_text())
    # a process that was ended but not yet reaped still has a pid; its state says Z.
    # Ended means ended before the sleep it was started with would have: poll for the state.
    for _ in range(100):
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(child)], capture_output=True, text=True,
                               check=False).stdout.strip()
        if state == "" or state.startswith("Z"):
            break
        time.sleep(0.1)
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


def test_build_removes_inherited_attavo_checks_command_from_executor_environment(tmp_path: Path) -> None:
    """#212's review: the retired command reached the executor by inheritance
    when whoever launched attavo build had it exported."""
    root = tenant(tmp_path, checks='[checks]\ncommand = "true"', tests='[tests]\nselect = "cat"\nrun = "true"')
    (root / "attavo" / "adapters" / "fixture").mkdir(parents=True)
    probe = root / "attavo" / "adapters" / "fixture" / "run"
    probe.write_text('#!/bin/sh\nenv | grep "^ATTAVO_" | sort > "$PWD/seen-env.txt"\n'
                     'echo built > built.txt\ngit add built.txt\ngit commit -q -m built\n'
                     'printf \'{"status": "done", "question": null}\\n\'\n')
    probe.chmod(0o755)
    (root / ".gitignore").write_text("seen-env.txt\n")
    commit_all(root, "a tenant-side adapter that prints what it was given")
    approve_item(root)
    run_attavo(root, "build", "1", env={"ATTAVO_CHECKS_COMMAND": "python3 -m pytest tests"})
    seen = (root / "seen-env.txt").read_text()
    assert "ATTAVO_CHECKS_COMMAND" not in seen, seen
    assert "ATTAVO_TEST_COMMAND" in seen, seen


def select(*paths: str) -> list[str]:
    run = subprocess.run([sys.executable, str(SELECT)], input="".join(p + "\n" for p in paths),
                         capture_output=True, text=True, check=True)
    return run.stdout.split()


def test_this_repositorys_selector_fails_safe_on_a_path_it_does_not_know() -> None:
    assert select("attavo/a_module_nobody_mapped.py") == ["tests"]
    assert select("attavo/report.py", "attavo/a_module_nobody_mapped.py") == ["tests"]
    assert select("scripts/anything.py") == ["tests"]
    # #317: these are placed now (rows and the import graph), not fail-safed
    assert select("bin/attavo") != ["tests"] and select("attavo/common.py") != ["tests"]


def test_this_repositorys_selector_maps_a_module_to_its_own_tests_and_prose_to_nothing() -> None:
    picked = select("attavo/report.py")
    assert "tests/test_report.py" in picked and picked != ["tests"], picked
    assert select("tests/test_ledger.py") == ["tests/test_ledger.py"]
    assert select("README.md", "attest/records/1/build-01.json", "attest/specs/9/spec.md") == []
    assert "tests/test_executor_conformance.py" in select("adapters/codex/run")


def test_deleted_test_file_selects_the_whole_suite() -> None:
    """#212's review: a deleted test file selected nothing, so the gate could
    pass a deletion. A file that is gone cannot select itself; the fail-safe
    rule the operator ruled applies instead."""
    assert select("tests/test_this_file_was_deleted.py") == ["tests"]
    assert select("tests/test_report.py") == ["tests/test_report.py"]


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


# --- the test tool as the executor's only way to run tests (v0.28.0's first
# item). Specimen: #219's attempt 2 ran pytest directly 32 times against 2
# runs of `attavo test` and spent a third of its 7,668 s after the tree was
# green; attempt 3 ran it 0 times and passed in 247 s. Tests written red
# first against v0.28.0 (f8b39f9): no cache, no modes, no test event, no
# exclusion, the transport accepted inside an executor, the raw runner
# allowed. ---

MODES = ('[tests]\nselect = "sed -n s#^src/#tests/test_#p"\n'
         'run = "sh -c \'cat > selected.txt; echo run >> runs.log; cat selected.txt\'"\n'
         'exclude_in_executor = ["tests/test_slow.py"]')


def modes_tenant(tmp_path: Path) -> Path:
    """build_valid with src/a.py on main and src/b.py on the item's branch,
    a selector that maps src/X to tests/test_X, and a run command that
    counts its own runs."""
    root = make_tenant(tmp_path, "build_valid")
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text().replace('[tests]\nselect = "cat"\nrun = "true"', MODES))
    (root / ".gitignore").write_text("selected.txt\nruns.log\n")
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("x = 1\n")
    commit_all(root, "configure")
    git(root, "update-ref", "refs/remotes/origin/main", git(root, "rev-parse", "HEAD").strip())
    git(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    git(root, "checkout", "-q", "-b", "attavo/1-x")
    (root / "src" / "b.py").write_text("y = 2\n")
    commit_all(root, "change b")
    return root


def runs(root: Path) -> int:
    log = root / "runs.log"
    return log.read_text().count("run") if log.is_file() else 0


def selected(root: Path) -> list[str]:
    return sorted((root / "selected.txt").read_text().split())


def test_attavo_test_answers_from_the_cache_on_an_unchanged_tree(tmp_path: Path) -> None:
    root = modes_tenant(tmp_path)
    assert run_attavo(root, "test", "1").returncode == 0
    assert runs(root) == 1 and selected(root) == ["tests/test_b.py"]
    started = time.monotonic()
    second = run_attavo(root, "test", "1")
    assert time.monotonic() - started < 5
    assert second.returncode == 0 and runs(root) == 1, second.stdout + second.stderr
    assert "not rerun" in second.stdout and "--fresh" in second.stdout, second.stdout
    (root / "src" / "b.py").write_text("y = 3\n")
    assert run_attavo(root, "test", "1").returncode == 0 and runs(root) == 2, "an edited tree was answered from the cache"
    assert run_attavo(root, "test", "1", "--fresh").returncode == 0 and runs(root) == 3
    failing = root / "attavo.toml"
    failing.write_text(failing.read_text().replace("cat selected.txt'", "cat selected.txt; exit 1'"))
    assert run_attavo(root, "test", "1").returncode == 1 and runs(root) == 4
    assert run_attavo(root, "test", "1").returncode == 1 and runs(root) == 5, "a failing run was cached"


def test_attavo_test_runs_a_named_test_and_the_whole_suite_on_request(tmp_path: Path) -> None:
    root = modes_tenant(tmp_path)
    named = run_attavo(root, "test", "1", "tests/test_a.py::test_a")
    assert named.returncode == 0 and selected(root) == ["tests/test_a.py::test_a"], named.stdout + named.stderr
    whole = run_attavo(root, "test", "1", "--all")
    assert whole.returncode == 0 and selected(root) == ["tests"], whole.stdout + whole.stderr
    mapped = run_attavo(root, "test", "1")
    assert mapped.returncode == 0 and selected(root) == ["tests/test_b.py"], mapped.stdout + mapped.stderr
    both = run_attavo(root, "test", "1", "--all", "tests/test_a.py")
    assert both.returncode == 2 and "not both" in both.stderr, both.stdout + both.stderr


def test_attavo_test_writes_one_event_per_run(tmp_path: Path) -> None:
    root = modes_tenant(tmp_path)
    events_file = tmp_path / "events.jsonl"
    env = {"ATTAVO_EVENTS": str(events_file)}
    assert run_attavo(root, "test", "1", "tests/test_a.py::test_a", env=env).returncode == 0
    assert run_attavo(root, "test", "1", "--all", env=env).returncode == 0
    assert run_attavo(root, "test", "1", env=env).returncode == 0
    assert run_attavo(root, "test", "1", env=env).returncode == 0
    rows = [json.loads(line) for line in events_file.read_text().splitlines() if line.strip()]
    texts = [r["text"] for r in rows if r["kind"] == "test"]
    assert len(texts) == 4, rows
    assert "mode named, 1 selected, exit 0" in texts[0], texts
    assert "mode all, 1 selected, exit 0" in texts[1], texts
    assert "mode mapped, 1 selected, exit 0" in texts[2] and re.search(r", \d+ s, \d+ workers$", texts[2]), texts
    assert texts[3].endswith("cached"), texts


def test_attavo_test_leaves_out_excluded_tests_inside_an_executor(tmp_path: Path) -> None:
    root = modes_tenant(tmp_path)
    (root / "src" / "slow.py").write_text("z = 1\n")
    commit_all(root, "change slow")
    outside = run_attavo(root, "test", "1")
    assert outside.returncode == 0 and selected(root) == ["tests/test_b.py", "tests/test_slow.py"], outside.stdout
    inside = run_attavo(root, "test", "1", "--fresh", env={"ATTAVO_STAGE": "build"})
    assert inside.returncode == 0 and selected(root) == ["tests/test_b.py"], inside.stdout + inside.stderr
    assert "left out inside an executor: tests/test_slow.py" in inside.stdout, inside.stdout
    jsonschema.validate({"select": "cat", "run": "true", "exclude_in_executor": ["tests/test_slow.py"]},
                        MANIFEST_SCHEMA["properties"]["tests"])


def test_the_transport_refuses_inside_an_executor(tmp_path: Path) -> None:
    root = modes_tenant(tmp_path)
    for argv in (["drive", "1"], ["worker", "1", "build", "01"], ["build", "1"], ["verify", "1"],
                 ["counsel", "1"], ["file-stops", "1"]):
        run = run_attavo(root, *argv, env={"ATTAVO_STAGE": "build"})
        assert run.returncode == 2, (argv, run.stdout, run.stderr)
        assert f"attavo {argv[0]}: refused inside an executor" in run.stderr, (argv, run.stderr)
        assert not record_files(root, "build"), argv
    assert run_attavo(root, "test", "1", env={"ATTAVO_STAGE": "build"}).returncode == 0
    assert run_attavo(root, "drive", "1", "--report", env={"ATTAVO_STAGE": "build"}).returncode == 2


def test_the_claude_code_adapter_denies_no_test_runner_and_says_one_sentence(tmp_path: Path) -> None:
    """#317: the runner deny-list is retired; the product reads the events
    afterwards. The instruction says one thing about tests."""
    from test_adapters import dry_run
    argv = dry_run(tmp_path, extra_env={"ATTAVO_TEST_COMMAND": "python3 /product/bin/attavo test 7"})
    denied = argv[argv.index("--disallowedTools") + 1].split(",")
    assert not [d for d in denied if d.startswith("Bash(")], denied
    assert "Agent" in denied and "Workflow" in denied, denied
    instruction = argv[argv.index("-p") + 1]
    assert ("run tests only with `python3 /product/bin/attavo test 7 [path]`, which chooses the tests "
            "for your changes and says what it did not run; when it and the criteria pass, commit and "
            "stop, because the gates run for you and return any failure") in instruction, instruction
    for gone in ("Do not run the test runner directly", "whole-tree check", "stops at"):
        assert gone not in instruction, gone
    for adapter in ("claude-code", "codex"):
        conformance = json.loads((ROOT / "adapters" / adapter / "conformance.json").read_text())
        assert set(conformance["denied_commands"]) == {"mechanism", "patterns"}, adapter
    claude = json.loads((ROOT / "adapters" / "claude-code" / "conformance.json").read_text())
    assert set(claude["denied_commands"]["patterns"]) <= set(denied), "conformance.json and the adapter disagree"
    assert not [p for p in claude["denied_commands"]["patterns"] if p.startswith("Bash(")]


def test_every_module_under_attavo_is_placed_by_a_row_or_the_import_graph() -> None:
    """#317: no module of attavo/ selects the whole suite. A new module is
    placed by a MODULE_TESTS row or because a test file imports it,
    directly or through another module -- or this fails, naming it."""
    modules = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "attavo").glob("*.py"))
    whole = []
    for module in modules:
        picked = subprocess.run([sys.executable, str(SELECT)], input=module + "\n", capture_output=True,
                                text=True, check=True).stdout.split()
        if picked == ["tests"] or not picked:
            whole.append(module)
    assert not whole, f"module(s) the selector cannot place: {whole}"



# --- #317: tests run only through the tool, and the product reads how they were run ---

EVASIONS = ["cd tests && pytest", "bash -c 'pytest'", "timeout 60 python3 -m pytest",
            "python3.11 -m pytest", "env X=1 pytest"]
THE_TOOL = "python3 /product/bin/attavo test 1"


def tool_call(command: str, at: str = "2026-10-01T10:00:00Z") -> list[dict[str, str]]:
    return [{"at": at, "kind": "tool_call", "text": "Bash " + command},
            {"at": at, "kind": "tool_result", "text": "ok"}]


@pytest.mark.parametrize("command", EVASIONS + ["pytest -q", "python3 -m pytest -q tests/x.py",
                                                "python3 -m unittest", "npm test", "cargo test"])
def test_a_test_runner_run_outside_the_tool_is_named(command: str) -> None:
    found = runner_calls_outside_the_tool(tool_call(command))
    assert found == [("2026-10-01T10:00:00Z", command)], found


@pytest.mark.parametrize("command", [
    f"{THE_TOOL} tests/test_a.py 2>&1 | tail -50",
    f"for f in tests/test_*.py; do {THE_TOOL} $f; done",
    "pip install pytest", "grep -rn pytest tests | head", "git commit -m 'run pytest'",
    "python3 -c 'import pytest; print(pytest.__version__)'", "ls tests && cat tests/conftest.py",
])
def test_the_tool_and_a_mention_of_a_runner_are_not_a_runner_run(command: str) -> None:
    assert runner_calls_outside_the_tool(tool_call(command)) == []


def test_a_call_the_permission_layer_denied_did_not_run() -> None:
    events = [{"at": "t", "kind": "tool_call", "text": "Bash pytest -q"},
              {"at": "t", "kind": "tool_result",
               "text": "Permission to use Bash with command pytest -q has been denied."}]
    assert runner_calls_outside_the_tool(events) == []


def gated_tenant(tmp_path: Path, calls: list[str], tests: str = "") -> tuple[Path, dict[str, str]]:
    """A tenant whose build adapter appends the given Bash calls to the
    stage's events as an executor's stream would, then commits."""
    root = tenant(tmp_path, checks='[checks]\ncommand = "true"', tests=tests)
    feed = tmp_path / "executor-events.jsonl"
    feed.write_text("".join(json.dumps(e) + "\n" for c in calls for e in tool_call(c)))
    (root / "attavo" / "adapters" / "fixture").mkdir(parents=True)
    run = root / "attavo" / "adapters" / "fixture" / "run"
    run.write_text('#!/bin/sh\ncat "$FIXTURE_EVENTS" >> "$ATTAVO_EVENTS"\n'
                   'cp "$ATTAVO_FEEDBACK" "$SEEN_FEEDBACK"\n'
                   'if [ -f built.txt ]; then echo again >> built.txt; else echo built > built.txt; fi\ngit add built.txt\ngit commit -q -m built\n'
                   'printf \'{"status": "done", "question": null, "model": "fixture"}\\n\'\n')
    run.chmod(0o755)
    commit_all(root, "an adapter that streams the given calls")
    approve_item(root)
    return root, {"FIXTURE_EVENTS": str(feed), "SEEN_FEEDBACK": str(tmp_path / "seen-feedback.txt")}


@pytest.mark.parametrize("command", EVASIONS)
def test_a_build_that_ran_a_runner_outside_the_tool_is_returned_by_name(tmp_path: Path, command: str) -> None:
    root, env = gated_tenant(tmp_path, [f"{THE_TOOL} tests/test_a.py", command])
    run = run_attavo(root, "build", "1", env=env)
    assert run.returncode == 1, run.stdout + run.stderr
    rec = build_record(root)
    assert rec["status"] == "refused" and rec["refusal_kind"] == "test-bypass", rec
    assert rec["stop"]["class"] == "test-bypass" and rec["stop"]["provenance"] == "executor"
    assert command in rec["stop"]["detail"] and "attavo test" in rec["stop"]["detail"], rec["stop"]
    jsonschema.validate(rec, json.loads((ROOT / "schema" / "records" / "build.schema.json").read_text()))


ODD_RUN = "xargs -r -n 4 ./bin/oddtest --fast"
ODD_RUNNER = '[tests]\nselect = "cat"\nrun = "xargs -r -n 4 ./bin/oddtest --fast"'


def test_the_runner_the_tenants_run_line_names_is_a_runner_the_gate_recognizes() -> None:
    from attavo.testtool import tenant_runner
    assert tenant_runner("xargs -r python3 -m pytest -q -n 4") == ["python3", "-m", "pytest"]
    assert tenant_runner("FOO=1 xargs -r -n 4 ./bin/oddtest --fast") == ["./bin/oddtest"]
    assert runner_calls_outside_the_tool(tool_call("true && sh -c 'ls'"), "true") == []
    assert runner_calls_outside_the_tool(tool_call("bash x.sh"), "bash -c 'x'") == []
    assert tenant_runner("") == [] and tenant_runner("xargs -r") == []


@pytest.mark.parametrize("command", ["./bin/oddtest tests/a", "cd x && ./bin/oddtest", "env X=1 bin/oddtest"])
def test_a_runner_not_on_the_built_in_list_is_named_by_the_tenants_own_run_line(
        tmp_path: Path, command: str) -> None:
    root, env = gated_tenant(tmp_path, [f"{THE_TOOL} tests/test_a.py", command], tests=ODD_RUNNER)
    assert runner_calls_outside_the_tool(tool_call(command)) == []  # the built-ins alone miss it
    assert runner_calls_outside_the_tool(tool_call(command), ODD_RUN) != []
    run = run_attavo(root, "build", "1", env=env)
    assert run.returncode == 1, run.stdout + run.stderr
    rec = build_record(root)
    assert rec["status"] == "refused" and rec["refusal_kind"] == "test-bypass", rec
    assert command in rec["stop"]["detail"], rec["stop"]


def test_a_build_that_used_only_the_tool_is_not_returned(tmp_path: Path) -> None:
    root, env = gated_tenant(tmp_path, [f"{THE_TOOL} tests/test_a.py 2>&1 | tail -20", "pip install pytest",
                                        "grep -rn pytest tests"])
    run = run_attavo(root, "build", "1", env=env)
    assert run.returncode == 0, run.stdout + run.stderr
    assert build_record(root)["status"] == "pass"


def test_the_retry_of_a_returned_build_is_steered_to_the_tool(tmp_path: Path) -> None:
    from attavo.stops import RETRYABLE_CLASSES
    assert "test-bypass" in RETRYABLE_CLASSES
    root, env = gated_tenant(tmp_path, ["cd tests && pytest -q"])
    assert run_attavo(root, "build", "1", env=env).returncode == 1
    (tmp_path / "executor-events.jsonl").write_text("")
    assert run_attavo(root, "build", "1", env=env).returncode == 0
    seen = (tmp_path / "seen-feedback.txt").read_text()
    assert "stopped as test-bypass" in seen and "cd tests && pytest -q" in seen, seen
    assert "run tests only with attavo test" in seen, seen


# --- #317: the selector's rows and its import graph ---

def selector(*paths: str, script: Path = SELECT) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(script)], input="".join(p + "\n" for p in paths),
                          capture_output=True, text=True, check=True)


def test_workflows_bin_attavo_and_the_fixture_adapter_select_the_tests_that_name_them() -> None:
    for path, expected in ((".github/workflows/ci.yml", "tests/test_ci_inherit.py"),
                           ("bin/attavo", "tests/test_cli_help.py"),
                           ("adapters/fixture/run", "tests/test_executor_conformance.py")):
        picked = selector(path).stdout.split()
        assert expected in picked and picked != ["tests"], (path, picked)


def test_a_module_most_others_import_selects_the_files_that_reach_it_not_the_whole_suite() -> None:
    run = selector("attavo/common.py")
    picked = run.stdout.split()
    assert picked != ["tests"] and len(picked) < len(list((ROOT / "tests").glob("test_*.py"))), picked
    assert "tests/test_drive.py" in picked and "tests/test_build.py" in picked, picked
    assert run.stderr == ""


def test_a_path_the_graph_cannot_place_selects_the_whole_suite_and_says_why() -> None:
    for path, reason in (("pyproject.toml", "no row maps it"),
                         ("tests/conftest.py", "pytest loads it for every test"),
                         (".attavo/bin/forge-checks", "no row maps it")):
        run = selector(path)
        assert run.stdout.split() == ["tests"], (path, run.stdout)
        assert f"select-tests: whole suite -- {path}: " in run.stderr and reason in run.stderr, run.stderr


def test_the_import_graph_is_built_from_source_and_follows_imports_through_modules(tmp_path: Path) -> None:
    """A repository the selector has never seen, whose modules would fail
    if imported: a changed module selects the test files that import it
    directly or through another module; one nothing imports is the whole
    suite, with the reason."""
    repo = tmp_path / "repo"
    (repo / ".attavo" / "bin").mkdir(parents=True)
    (repo / "attavo").mkdir()
    (repo / "tests").mkdir()
    script = repo / ".attavo" / "bin" / "select-tests"
    script.write_text(SELECT.read_text())
    (repo / "attavo" / "__init__.py").write_text("")
    (repo / "attavo" / "a.py").write_text("from attavo import b\nraise RuntimeError('never imported')\n")
    (repo / "attavo" / "b.py").write_text("raise RuntimeError('never imported')\n")
    (repo / "attavo" / "c.py").write_text("x = 1\n")
    (repo / "tests" / "test_x.py").write_text("from attavo.a import f\n")
    (repo / "tests" / "test_y.py").write_text("import json\n")
    assert selector("attavo/b.py", script=script).stdout.split() == ["tests/test_x.py"]
    assert selector("attavo/a.py", script=script).stdout.split() == ["tests/test_x.py"]
    run = selector("attavo/c.py", script=script)
    assert run.stdout.split() == ["tests"] and "attavo/c.py: no table row, and no test file imports it" in run.stderr


# --- #317: the worker budget ---

def slot_tenant(tmp_path: Path, run: str) -> Path:
    return tenant(tmp_path, tests=f'[tests]\nselect = "cat"\ncall_timeout_s = 60\nrun = "{run}"')


WORKERS_RUN = "sh -c 'cat >/dev/null; echo workers=$ATTAVO_TEST_WORKERS'"


def slot_dir(root: Path) -> Path:
    return root / ".git" / "attavo" / "test-slots"


def test_one_call_alone_gets_every_core_and_gives_its_slot_back(tmp_path: Path) -> None:
    root = slot_tenant(tmp_path, WORKERS_RUN)
    run = run_attavo(root, "test", "1", "tests/test_a.py", env={"ATTAVO_TEST_CORES": "6"})
    assert run.returncode == 0, run.stdout + run.stderr
    assert "workers=6" in run.stdout, run.stdout
    assert "6 worker(s) of 6 cores, 1 call(s) running" in run.stdout, run.stdout
    assert list(slot_dir(root).glob("*.slot")) == []


def holder(root: Path, alive: bool) -> "subprocess.Popen[bytes]":
    """A process standing in for another call: its pid in a slot file."""
    proc = subprocess.Popen(["sleep", "60"])
    if not alive:
        proc.kill()
        proc.wait()
    slot_dir(root).mkdir(parents=True, exist_ok=True)
    (slot_dir(root) / f"{proc.pid}-held.slot").write_text(f"{proc.pid}\n")
    return proc


def test_a_call_among_three_takes_a_third_of_the_cores(tmp_path: Path) -> None:
    root = slot_tenant(tmp_path, WORKERS_RUN)
    others = [holder(root, alive=True), holder(root, alive=True)]
    try:
        run = run_attavo(root, "test", "1", "tests/test_a.py", env={"ATTAVO_TEST_CORES": "6"})
    finally:
        for proc in others:
            proc.kill()
            proc.wait()
    assert "workers=2" in run.stdout and "2 worker(s) of 6 cores, 3 call(s) running" in run.stdout, run.stdout
    assert len(list(slot_dir(root).glob("*.slot"))) == 2   # its own is gone, the two holders' remain


def test_a_dead_holders_slot_is_reaped_and_not_counted(tmp_path: Path) -> None:
    root = slot_tenant(tmp_path, WORKERS_RUN)
    holder(root, alive=False)
    run = run_attavo(root, "test", "1", "tests/test_a.py", env={"ATTAVO_TEST_CORES": "6"})
    assert "workers=6" in run.stdout and "1 call(s) running" in run.stdout, run.stdout
    assert list(slot_dir(root).glob("*.slot")) == []


def test_concurrent_calls_hold_one_slot_each_and_the_last_to_arrive_sees_all_three(tmp_path: Path) -> None:
    barrier = tmp_path / "barrier"
    barrier.mkdir()
    seen = tmp_path / "slots-held-at-barrier"
    run_cmd = (f"sh -c 'cat >/dev/null; touch {barrier}/$$; "
               f"while [ $(ls {barrier} | grep -vc done) -lt 3 ]; do sleep 0.05; done; "
               f"ls {slot_dir(tmp_path / 'build_valid')} | wc -l >> {seen}; touch {barrier}/done.$$; "
               f"while [ $(ls {barrier} | grep -c done) -lt 3 ]; do sleep 0.05; done; "
               f"echo workers=$ATTAVO_TEST_WORKERS'")
    root = slot_tenant(tmp_path, run_cmd)
    procs = [subprocess.Popen([sys.executable, str(ROOT / "bin" / "attavo"), "test", "1", f"tests/t{i}.py"],
                              cwd=str(root), stdout=subprocess.PIPE, text=True,
                              env={**os.environ, "ATTAVO_TEST_CORES": "6"}) for i in range(3)]
    outs = [p.communicate(timeout=120)[0] for p in procs]
    workers = sorted(int(o.split("workers=")[1].split()[0]) for o in outs)
    assert all(p.returncode == 0 for p in procs), outs
    assert workers[0] == 2 and workers[-1] <= 6, workers     # the last to take a slot found three
    assert set(seen.read_text().split()) == {"3"}, seen.read_text()   # three held at once
    assert list(slot_dir(root).glob("*.slot")) == []


# --- #317: the answer at the bound ---

def test_the_bound_report_names_what_passed_failed_and_did_not_run() -> None:
    collected = [f"tests/test_a.py::t{i}" for i in range(3)] + ["tests/test_b.py::t0", "tests/test_c.py::t0",
                                                                   "tests/test_c.py::t1"]
    results = {"tests/test_a.py::t0": "passed", "tests/test_a.py::t1": "passed",
               "tests/test_a.py::t2": "failed", "tests/test_b.py::t0": "skipped"}
    text = bound_report("9", 240, collected, results, "attavo test 9")
    assert text.splitlines()[0] == ("attavo test 9: stopped at 240 s with 2 of 6 tests not run "
                                    "(3 passed, 1 failed); the run and everything it started were ended.")
    assert "failed: tests/test_a.py::t2" in text
    assert "not run: tests/test_c.py (2)" in text
    assert text.splitlines()[-1].startswith("narrow it: attavo test 9 <path>")
    big = bound_report("9", 240, [f"tests/test_{i:02d}.py::t" for i in range(40)], {}, "attavo test 9")
    assert "(+28 more files)" in big and len(big) < 1500


PROGRESS_SCRIPT = """cat >/dev/null
printf 'C t/a.py::one\\nC t/a.py::two\\nC t/b.py::three\\nR passed t/a.py::one\\nR failed t/a.py::two\\n' >> "$ATTAVO_TEST_PROGRESS"
sleep 60
"""


def test_a_run_that_reaches_the_bound_prints_what_ran_and_how_to_narrow(tmp_path: Path) -> None:
    root = tenant(tmp_path, tests='[tests]\nselect = "cat"\ncall_timeout_s = 3\nprogress = "pytest"\n'
                                  'run = "sh progress.sh"')
    (root / "progress.sh").write_text(PROGRESS_SCRIPT)
    run = run_attavo(root, "test", "1", "t", timeout=120)
    assert run.returncode == 124, run.stdout + run.stderr
    assert "stopped at 3 s with 1 of 3 tests not run (1 passed, 1 failed)" in run.stdout, run.stdout
    assert "failed: t/a.py::two" in run.stdout and "not run: t/b.py (1)" in run.stdout, run.stdout
    assert "narrow it: python3 " in run.stdout and " test 1 <path>" in run.stdout, run.stdout
    assert list((root / ".git" / "attavo").glob("test-progress-*")) == []
    assert list(slot_dir(root).glob("*.slot")) == []


def test_a_run_that_reaches_the_bound_with_no_progress_declared_still_says_how_to_narrow(tmp_path: Path) -> None:
    root = tenant(tmp_path, tests='[tests]\nselect = "cat"\ncall_timeout_s = 1\nrun = "sleep 60"')
    run = run_attavo(root, "test", "1", "t", timeout=120)
    assert run.returncode == 124
    assert "did not finish within 1 s" in run.stdout and "narrow it: python3 " in run.stdout, run.stdout
    assert "[tests].progress" in run.stdout, run.stdout


@pytest.mark.parametrize("workers", ["0", "2"])
def test_the_progress_plugin_records_every_collected_and_finished_test(tmp_path: Path, workers: str) -> None:
    """The plugin `attavo test` loads for a pytest tenant: a line per
    collected test and per finished one, from every xdist worker."""
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "test_p.py").write_text("def test_ok():\n    pass\n\n\ndef test_bad():\n    assert False\n\n\n"
                                     "import pytest\n\n\n@pytest.mark.skip\ndef test_skipped():\n    pass\n")
    progress = tmp_path / "progress.txt"
    progress.write_text("")
    run = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-n", workers,
                          "-p", "attavo.pytest_progress", str(suite)],
                         cwd=str(suite), capture_output=True, text=True, check=False,
                         env={**os.environ, "PYTHONPATH": str(ROOT), "ATTAVO_TEST_PROGRESS": str(progress)})
    assert run.returncode == 1, run.stdout + run.stderr
    collected, results = read_progress(progress)
    ids = {i.split("::")[-1] for i in collected}
    assert ids == {"test_ok", "test_bad", "test_skipped"}, collected
    assert {i.split("::")[-1]: o for i, o in results.items()} == {
        "test_ok": "passed", "test_bad": "failed", "test_skipped": "skipped"}, results


def test_signalling_a_process_group_that_has_already_exited_is_not_an_error() -> None:
    """On macOS killpg answers EPERM for a group whose members have all
    exited and are not yet reaped; the bound's cleanup must treat that as
    gone, as it does ESRCH. Seen as a PermissionError out of run_bounded
    under load."""
    from unittest import mock

    from attavo.testtool import _signal_group
    proc = mock.Mock(pid=1)
    for error in (ProcessLookupError(), PermissionError(1, "Operation not permitted")):
        with mock.patch("os.killpg", side_effect=error):
            _signal_group(proc, 9)
