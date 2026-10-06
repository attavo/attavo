from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import sys
import uuid
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from attavo.common import MANIFEST, derive_item_id, git, read_manifest
from attavo import Command

# --- test: the executor's one test command (issue #212) --
# The tests [tests].select names for every path the item has changed since
# it left the default branch, run through [tests].run, bounded by
# [tests].call_timeout_s. The executor is given this and not the tenant's
# whole-tree checks: given a checks command it ran it (#194's 590 s wait;
# #204 and #206 ran the suite twice inside one build), and the gate runs
# them after every commit whatever the executor does. ---

TIMED_OUT_EXIT = 124

PRODUCT_ROOT = Path(__file__).resolve().parent.parent

# What an ended run is given to leave on its own after SIGTERM, before its
# whole process group is sent SIGKILL.
END_GRACE_S = 2


def _signal_group(proc: "subprocess.Popen[str]", sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        # gone, or -- on macOS -- a group whose every member has already exited
        # and is waiting to be reaped, which answers EPERM, not ESRCH (seen under
        # load: #317's full-suite runs at -n auto)
        pass


def run_bounded(command: str, root: Path, stdin: str, timeout_s: int | None,
                env: dict[str, str] | None = None) -> tuple[int, str, str, bool]:
    """(exit, stdout, stderr, timed_out) of `command` run through the shell
    from root. The command is its own process group's leader, so a run past
    timeout_s is ended with everything it started -- a test runner's
    workers and a shell's background jobs alike -- rather than only the
    shell, which would leave them holding the pipes and the machine. A run
    that outlasts its bound exits 124, as timeout(1) does."""
    proc = subprocess.Popen(command, shell=True, cwd=str(root), stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True, env=env)
    try:
        out, err = proc.communicate(stdin, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _signal_group(proc, signal.SIGTERM)
        try:
            out, err = proc.communicate(timeout=END_GRACE_S)
        except subprocess.TimeoutExpired:
            _signal_group(proc, signal.SIGKILL)
            try:
                out, err = proc.communicate(timeout=END_GRACE_S)
            except subprocess.TimeoutExpired:
                # something the run started left its group and holds the pipes
                out, err = "", ""
        _signal_group(proc, signal.SIGKILL)
        return TIMED_OUT_EXIT, out, err, True
    _signal_group(proc, signal.SIGKILL)
    return proc.returncode, out, err, False


# --- the worker budget (#317): a bulkhead, not a pool. Every `attavo test`
# call that runs takes a slot under <common git dir>/attavo/test-slots/ (a
# file per call, removed when the call ends) and runs with
# max(1, cores // calls holding a slot) workers, so several items building
# at once share the machine instead of each asking for all of it (the
# specimen: load averages of 33 to 75 with `-n auto` per call). A holder
# that died without removing its file is reaped by its pid. The runner
# reads the number from ATTAVO_TEST_WORKERS. ---

SLOTS_DIR = "attavo/test-slots"


def host_cores() -> int:
    """ATTAVO_TEST_CORES when set (a test's, or an operator holding some
    cores back), else what the machine has."""
    declared = os.environ.get("ATTAVO_TEST_CORES", "")
    return int(declared) if declared.isdigit() and int(declared) > 0 else (os.cpu_count() or 1)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def slots_dir(root: Path) -> Path:
    common = Path(git(root, "rev-parse", "--git-common-dir").strip())
    return (common if common.is_absolute() else root / common) / SLOTS_DIR


def workers_for(cores: int, running: int) -> int:
    return max(1, cores // max(1, running))


@contextmanager
def test_slot(root: Path) -> Iterator[tuple[int, int, int]]:
    """(workers, running, cores) for this call, holding its slot until the
    block ends. running counts this call and every live holder; a slot
    whose pid is dead is removed first."""
    directory = slots_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    mine = directory / f"{os.getpid()}-{uuid.uuid4().hex[:8]}.slot"
    mine.write_text(f"{os.getpid()}\n")
    try:
        running = 0
        for slot in sorted(directory.glob("*.slot")):
            try:
                holder = int(slot.read_text().split()[0])
            except (OSError, ValueError, IndexError):
                holder = 0
            if slot == mine or (holder and _alive(holder)):
                running += 1
            else:
                slot.unlink(missing_ok=True)
        cores = host_cores()
        yield workers_for(cores, running), running, cores
    finally:
        mine.unlink(missing_ok=True)


# --- the answer at the bound (#317): what passed, what failed, what did not
# run, and the command that narrows. The runner writes one line per test to
# the file named by ATTAVO_TEST_PROGRESS as the test ends (attavo/
# pytest_progress.py, loaded for a tenant that declares [tests].progress =
# "pytest"), so a run that is ended still leaves its results behind. ---

PROGRESS_PLUGIN = "attavo.pytest_progress"
PROGRESS_FILES_SHOWN = 12
PROGRESS_FAILED_SHOWN = 10


def read_progress(path: Path) -> tuple[list[str], dict[str, str]]:
    """(collected ids in the order first seen, id -> outcome) from a
    progress file: `C <id>` a collected test, `R <outcome> <id>` a result."""
    collected: dict[str, None] = {}
    results: dict[str, str] = {}
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return [], {}
    for line in lines:
        kind, _, rest = line.partition(" ")
        if kind == "C" and rest:
            collected[rest] = None
        elif kind == "R":
            outcome, _, test_id = rest.partition(" ")
            if test_id:
                results[test_id] = outcome
    return list(collected), results


def bound_report(item_id: str, bound: int | None, collected: list[str], results: dict[str, str],
                 narrow: str) -> str:
    """The text printed when a run reaches its bound."""
    passed = sum(1 for o in results.values() if o in ("passed", "skipped", "xfailed", "xpassed"))
    failed = sorted(i for i, o in results.items() if o == "failed")
    not_run = [i for i in collected if i not in results]
    files = Counter(i.split("::")[0] for i in not_run)
    lines = [f"attavo test {item_id}: stopped at {bound} s with {len(not_run)} of {len(collected)} "
             f"tests not run ({passed} passed, {len(failed)} failed); the run and everything it "
             "started were ended."]
    if failed:
        more = f" (+{len(failed) - PROGRESS_FAILED_SHOWN} more)" if len(failed) > PROGRESS_FAILED_SHOWN else ""
        lines.append("failed: " + ", ".join(failed[:PROGRESS_FAILED_SHOWN]) + more)
    if files:
        shown = sorted(files.items())[:PROGRESS_FILES_SHOWN]
        more_files = (f" (+{len(files) - PROGRESS_FILES_SHOWN} more files)"
                      if len(files) > PROGRESS_FILES_SHOWN else "")
        lines.append("not run: " + ", ".join(f"{f} ({n})" for f, n in shown) + more_files)
    lines.append(f"narrow it: {narrow} <path>   (one or a few files finish well inside {bound} s)")
    return "\n".join(lines)


def unreadable_bound_report(item_id: str, bound: int | None, narrow: str) -> str:
    return (f"attavo test {item_id}: the run did not finish within {bound} s; it and everything it "
            f"started were ended; what ran is not known to the tool (the tenant declares no "
            f"[tests].progress).\nnarrow it: {narrow} <path>")


# --- the gate on how tests were run (#317): a build's own events name every
# Bash call the executor made. A call that ran a test runner other than
# through `attavo test` returns the build, by name. The command text is read
# at command position (after VAR=x, env, timeout N, nice ... and inside
# `sh -c '...'`), so `pip install pytest` and `grep pytest` are not runs.
# It reads what the events carry: not the inside of a script, and not past
# the events' own cap on a command's length. ---

TEST_BYPASS_STEER = "run tests only with attavo test"

_SEPARATORS = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")
_WRAPPERS = {"timeout", "env", "time", "nice", "nohup", "command", "exec", "sudo", "xargs"}
_SHELL_C = re.compile(r"\b(?:ba|z)?sh\s+-c\s+(['\"])(.*)\1")


def _command_segments(command: str) -> Iterator[str]:
    for segment in _SEPARATORS.split(command):
        yield segment
        inner = _SHELL_C.search(segment)
        if inner:
            yield from _command_segments(inner.group(2))


def _command_words(segment: str) -> list[str]:
    try:
        words = shlex.split(segment)
    except ValueError:
        words = segment.split()
    i = 0
    while i < len(words):
        word = words[i]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", word):
            i += 1
        elif word.split("/")[-1] in _WRAPPERS or (
                i and words[i - 1].split("/")[-1] in _WRAPPERS
                and (word.startswith("-") or re.fullmatch(r"[\d.]+[smhd]?", word))):
            i += 1
        else:
            break
    return words[i:]


_XARGS_TAKES_A_VALUE = {"-n", "-P", "-I", "-L", "-s", "-d", "-E", "-a"}


def tenant_runner(run: str) -> list[str]:
    """The runner a tenant's [tests].run names: its command after any
    environment assignments and xargs with its flags, plus the subcommand
    words and a `-m module` that follow it, up to the first flag --
    "python3 -m pytest" from `xargs -r python3 -m pytest -q`. [] when
    the line names none."""
    try:
        words = shlex.split(run)
    except ValueError:
        words = run.split()
    i = 0
    while i < len(words) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[i]):
        i += 1
    if words[i:i + 1] == ["xargs"]:
        i += 1
        while i < len(words) and words[i].startswith("-"):
            i += 2 if words[i] in _XARGS_TAKES_A_VALUE else 1
    runner = words[i:i + 1]
    i += 1
    if words[i:i + 1] == ["-m"] and words[i + 1:i + 2]:
        return runner + words[i:i + 2]
    while runner and i < len(words) and not words[i].startswith("-"):
        runner.append(words[i])
        i += 1
    return runner


# A run line that starts with a shell or a no-op names no runner: matching
# it would refuse every `sh` or `true` the executor calls.
_NOT_A_RUNNER = {"true", ":", "sh", "bash", "zsh", "env", "cat", "echo", "exit"}


def _same_runner(words: list[str], runner: list[str]) -> bool:
    if not runner or runner[0] in _NOT_A_RUNNER or words[1:len(runner)] != runner[1:] or len(words) < len(runner):
        return False
    a, b = words[0].removeprefix("./"), runner[0].removeprefix("./")
    return a == b or a.split("/")[-1] == b.split("/")[-1]


def runs_a_test_runner(segment: str, runner: list[str] | None = None) -> bool:
    words = _command_words(segment)
    if not words:
        return False
    if runner and _same_runner(words, runner):
        return True
    base = words[0].split("/")[-1]
    if base in ("pytest", "py.test"):
        return True
    if re.fullmatch(r"python[\d.]*", base) and words[1:3] and words[1] == "-m" \
            and words[2] in ("pytest", "unittest"):
        return True
    if base in ("npm", "go", "cargo") and words[1:2] == ["test"]:
        return True
    return base == "npx" and words[1:2] == ["vitest"]


def runner_calls_outside_the_tool(events: list[dict[str, Any]],
                                  run: str = "") -> list[tuple[str, str]]:
    """[(time, command)] for every Bash call in events that ran a test
    runner, except a call whose result was a permission denial (it did not
    run). A call through `attavo test` is not a runner call: the runner it
    starts is the tool's own. run is the tenant's [tests].run: the runner
    it names counts as a runner beside the built-in ones."""
    runner = tenant_runner(run)
    found: list[tuple[str, str]] = []
    for i, event in enumerate(events):
        text = str(event.get("text", ""))
        if event.get("kind") != "tool_call" or not text.startswith("Bash "):
            continue
        command = text[len("Bash "):]
        result = events[i + 1] if i + 1 < len(events) and events[i + 1].get("kind") == "tool_result" else {}
        reply = str(result.get("text", ""))
        if reply.startswith("Permission to use Bash") and "denied" in reply:
            continue
        if any(runs_a_test_runner(s, runner) for s in _command_segments(command)):
            found.append((str(event.get("at", "")), command[:100]))
    return found


def read_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return events
    for line in lines:
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


def without_records(paths: list[str], records_rel: str) -> list[str]:
    """paths other than paths.records itself and what is under it -- the
    records directory is where the product writes, not the item's work."""
    prefix = records_rel.rstrip("/") + "/"
    return [p for p in paths if p and p != records_rel and not p.startswith(prefix)]


def changed_since_default_branch(root: Path, records_rel: str) -> list[str]:
    """Every path the item has changed since it left the default branch,
    committed or not: tracked paths whose content differs from the
    merge-base with what refs/remotes/origin/HEAD names -- deleted and
    renamed-from paths included, staged or not -- and untracked paths that
    are not ignored. Sorted; the records directory left aside.
    RuntimeError when the default branch cannot be named."""
    default = git(root, "symbolic-ref", "-q", "refs/remotes/origin/HEAD").strip()
    base = git(root, "merge-base", default, "HEAD").strip()
    tracked = git(root, "diff", "--name-only", "--no-renames", "-z", base).split("\0")
    untracked = git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0")
    return sorted(set(without_records(tracked + untracked, records_rel)))


def _fingerprint(root: Path) -> str:
    """What the tree is right now: HEAD, the tracked diff against it, and
    every untracked, unignored file's name and content hash."""
    import hashlib
    h = hashlib.sha256()
    h.update(git(root, "rev-parse", "HEAD").encode())
    h.update(git(root, "diff", "HEAD", "--no-color").encode())
    for name in git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0"):
        if not name:
            continue
        h.update(name.encode() + b"\0")
        try:
            h.update(hashlib.sha256((root / name).read_bytes()).digest())
        except OSError:
            pass
    return h.hexdigest()


def _cache_path(root: Path) -> Path:
    from attavo.events import stage_events_dir
    return stage_events_dir(root) / "test-cache.json"


def _event(text: str) -> None:
    events = os.environ.get("ATTAVO_EVENTS")
    if events:
        from attavo.events import append_event
        append_event(Path(events), "test", text)


def cmd_test(argv: list[str]) -> int:
    root = Path(".").resolve()
    flags = {"--all", "--fresh"}
    positional = [a for a in argv if a not in flags]
    whole, fresh = "--all" in argv, "--fresh" in argv
    item_id = positional[0] if positional else derive_item_id(root)
    named = positional[1:]
    if not item_id:
        print("attavo test: cannot derive item id; pass N or run on an attavo/N-* branch",
              file=sys.stderr)
        return 2
    m = read_manifest(root)
    if m is None:
        print(f"attavo test: no {MANIFEST} at {root}", file=sys.stderr)
        return 2
    tests_cfg: dict[str, Any] = m.get("tests") or {}
    if not tests_cfg.get("select") or not tests_cfg.get("run"):
        print("attavo test: no [tests] table in the manifest "
              "([tests].select and [tests].run are both required)", file=sys.stderr)
        return 2
    import json
    import time
    if named and whole:
        print("attavo test: name tests or pass --all, not both", file=sys.stderr)
        return 2
    if named:
        mode, identifiers = "named", named
    elif whole:
        mode, identifiers = "all", ["tests"]
    else:
        mode = "mapped"
        try:
            changed = changed_since_default_branch(root, m["paths"]["records"])
        except RuntimeError as exc:
            print(f"attavo test: cannot name what item {item_id} has changed: {exc}; "
                  "run `git remote set-head origin -a`", file=sys.stderr)
            return 2
        select_run = subprocess.run(tests_cfg["select"], shell=True, cwd=str(root),
                                    input="".join(p + "\n" for p in changed),
                                    capture_output=True, text=True, check=False)
        if select_run.returncode != 0:
            # Nothing selected because the selector failed is not nothing to run.
            sys.stderr.write(select_run.stderr)
            print(f"attavo test: [tests].select exited {select_run.returncode}; nothing was run",
                  file=sys.stderr)
            return 2
        identifiers = [line for line in select_run.stdout.splitlines() if line.strip()]
        if select_run.stderr.strip():
            # the selector's own reasons (why a path selected the whole suite)
            sys.stderr.write(select_run.stderr)
        if not identifiers:
            print(f"attavo test {item_id}: no test is selected for the {len(changed)} "
                  "path(s) changed since the default branch")
            _event(f"attavo test {item_id}: mode mapped, 0 selected, exit 0, 0 s")
            return 0

    inside = bool(os.environ.get("ATTAVO_STAGE"))
    excluded = [x for x in (tests_cfg.get("exclude_in_executor") or []) if x in identifiers] if inside else []
    if excluded:
        identifiers = [i for i in identifiers if i not in excluded]
        print(f"attavo test {item_id}: left out inside an executor: {', '.join(excluded)}")

    key = _fingerprint(root) + "|" + mode + "|" + "\n".join(identifiers)
    cache_file = _cache_path(root)
    cache: dict[str, Any] = {}
    if cache_file.is_file():
        try:
            cache = json.loads(cache_file.read_text())
        except ValueError:
            cache = {}
    hit = cache.get(key)
    if hit and not fresh:
        print(f"attavo test {item_id}: {len(identifiers)} selected; unchanged since the run at "
              f"{hit['at']} (exit 0); not rerun; pass --fresh to rerun")
        _event(f"attavo test {item_id}: mode {mode}, {len(identifiers)} selected, exit 0, cached")
        return 0

    bound = tests_cfg.get("call_timeout_s")
    progress_file: Path | None = None
    run_env = dict(os.environ)
    if tests_cfg.get("progress") == "pytest":
        progress_file = cache_file.parent / f"test-progress-{os.getpid()}.txt"
        progress_file.parent.mkdir(parents=True, exist_ok=True)
        progress_file.write_text("")
        run_env["ATTAVO_TEST_PROGRESS"] = str(progress_file)
        run_env["PYTEST_ADDOPTS"] = (run_env.get("PYTEST_ADDOPTS", "") + f" -p {PROGRESS_PLUGIN}").strip()
        run_env["PYTHONPATH"] = os.pathsep.join(
            [str(PRODUCT_ROOT)] + ([run_env["PYTHONPATH"]] if run_env.get("PYTHONPATH") else []))
    narrow = f"python3 {Path(sys.argv[0]).resolve()} test {item_id}"
    try:
        with test_slot(root) as (workers, running, cores):
            run_env["ATTAVO_TEST_WORKERS"] = str(workers)
            print(f"attavo test {item_id}: {len(identifiers)} selected; {workers} worker(s) of "
                  f"{cores} cores, {running} call(s) running", flush=True)
            started = time.monotonic()
            code, out, err, timed_out = run_bounded(tests_cfg["run"], root,
                                                    "".join(i + "\n" for i in identifiers), bound,
                                                    env=run_env)
            elapsed = int(time.monotonic() - started)
        if timed_out:
            collected, results = read_progress(progress_file) if progress_file else ([], {})
            if collected:
                sys.stdout.write(bound_report(item_id, bound, collected, results, narrow) + "\n")
            else:
                sys.stdout.write(out)
                print(unreadable_bound_report(item_id, bound, narrow))
            sys.stderr.write(err)
        else:
            sys.stdout.write(out)
            sys.stderr.write(err)
    finally:
        if progress_file:
            progress_file.unlink(missing_ok=True)
    _event(f"attavo test {item_id}: mode {mode}, {len(identifiers)} selected, "
           f"exit {code}, {elapsed} s, {workers} workers")
    if code == 0:
        from attavo.common import now_iso
        cache[key] = {"at": now_iso()}
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cache))
    return code


COMMANDS = (
    Command(
        name='test',
        run=cmd_test,
        usage=('attavo test [N] [--all | <test>...] [--fresh]',),
        rank=190,
        help=(
            (100, """\
test [N] runs the tests [tests].select names for every path the item has
changed since it left the default branch (refs/remotes/origin/HEAD),
committed or not and untracked included, paths.records aside, through
[tests].run (mode mapped, the default). On this repository [tests].run
excludes, by the pytest.mark.own_records marker and by nothing else, the
tests that read this repository's own records -- the doctor's proofs
among them -- since they belong to the doctor job alone. test N <test>... runs exactly the
named tests instead (mode named), so an executor working on one test runs
one test; test N --all runs the whole suite through [tests].run with
"tests" as the one identifier (mode all), the same bound, event, and
exclusion as the other two. Naming tests and --all together is refused
(exit 2). It prints how many were selected (or that none was, and runs
nothing), exits as the run does, and, when [tests].call_timeout_s is
declared and the run outlasts it, ends the run and everything it started
and exits 124, printing, in place of the runner's partial output, what
passed, what failed and what did not run, and the command that narrows it
(for a tenant that declares [tests].progress; otherwise that the run was
ended and the narrowing command). Each call that runs takes a slot under
<common git dir>/attavo/test-slots/ and exports ATTAVO_TEST_WORKERS to
[tests].run: max(1, cores // calls holding a slot), so items building at
once share the machine; it prints the workers it took, and a slot whose
holder died is reaped by its pid. Paths the selector cannot place select the
whole suite and the selector's reason is printed. It exits 2 when it cannot
name the changes or the selector fails. A run that exits 0 is cached against a fingerprint of the tree
(HEAD, the diff against it, and every untracked unignored file's name and
content hash, together with the mode and the identifiers); asked again on
an unchanged tree it says so -- unchanged since the earlier run, not
rerun -- without spending a second on it; --fresh reruns anyway. A
failing run is never cached, since the next question after one is always
whether an edit fixed it. [tests].exclude_in_executor names test
identifiers left out, and said so, when ATTAVO_STAGE is set (inside an
executor); they run outside one and in CI. When ATTAVO_EVENTS is set, one
event of kind test is written per invocation, naming the mode, the count
selected, the exit code, and the seconds spent (or that the answer came
from the cache). It is the one test command an executor is given (issue
#212); the product's own transport -- drive, worker, build, verify,
counsel, file-stops -- refuses to run at all when ATTAVO_STAGE is set,
since the transport is the pass's, not the executor's."""),
        ),
    ),
)
