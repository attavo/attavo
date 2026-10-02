# #317 spike: the executor runs tests only through the test tool, and the tool can answer

Spike, 2026-10-01. Branch `lane/317-test-tool`, base `f4e9c01`. No source file changed; every prototype is a diff below, run from a scratch copy. Tags: **[M]** a measurement (the command is given), **[P]** a proposal, **[I]** an inference from measurements.

## Read this first: what limits these numbers

- **The machine was shared.** Other lanes ran on it throughout (`attest-lane-blue-313`, `screen-lane-purple-22`). Load average at the start of my runs went from 4 to 75 (`uptime`, recorded per run below). Every timing here is contended. I could not get a quiet machine. #291's 39.5 s at `-n 8` is not reproduced and not contradicted.
- **The #277 worktree vanished mid-session** (18:14, not by a command of mine). Its events survive at `.git/attavo/archive/277/build-01.events.jsonl` (339 lines, same file; copied to scratch before use). I also used the 13 other archived build events files as a corpus.
- **One live Claude Code invocation was made** (Q4), as permitted: fixture tenant, patched scratch adapter, 15 turns, 97 s, meter $0.285 (subscription).

## Summary

| Q | Finding | Tag |
|---|---|---|
| 1 | #277's diff selects the whole suite (`tests`) because of one path, `adapters/fixture/run`. 764 tests, 643 s serial under load. | M |
| 1 | By module: 7 of 27 `attavo/*.py` map to the whole suite (all deliberate); 20 map to 1-4 files. By item: 34 of 67 PRs merged since 2026-09-15 select the whole suite, and the usual cause is not a product module: it is `.github/workflows/*` (15), `bin/attavo` (8), `adapters/fixture/*` (4). | M |
| 2 | `xdist` 3.8.0 imports under the operator's `python3` (3.14.6). `-n auto` on this machine: 139 s and 142 s (vs 643 s serial), 279 s at load 33. | M |
| 2 | Parallel breaks tests: 1, 3 and 7 failures in three `-n auto` runs; 0 and 1 at `-n 4`. All are wall-clock assertions that pass serially and in isolation. | M, I |
| 3 | At the bound the tool prints about 525 B: dotted progress and one sentence. It names no test. A prototype prints about 555 B and names what passed, what failed, what did not run, and the narrowing command. | M, P |
| 4 | The deny-list misses `bash -c` and `python3.11 -m pytest` (live). It stopped an `env ... pytest` and a `&&` compound in the corpus. Guard (a) stopped all five listed evasions and, live, two of them. Gate (b) flagged all five and nothing in 302 real Bash calls. Each has evasions the other stops. | M |
| 4 | **(a) cannot work as the brief states it.** The adapter strips every `ATTAVO_*` name from the executor, `ATTAVO_STAGE` included (#198), so the guard never sees a stage. It needs one deliberate exception to #198, and that fails `test_situation_20`. | M |
| 5 | The adapter's test paragraph, quoted below, and the one sentence that replaces it. | P |

## Q1. What does a no-path call select, and why?

**Selection for #277 [M].**

```
git diff --name-only 2a6fc89 98781aa | python3 .attavo/bin/select-tests      ->  tests
```

`tests` is `WHOLE_SUITE`, the one path pytest needs to run everything. The selector returns it when one changed path is unknown (`select()` stops at the first `None`). Per path (the same command, one path at a time), one path alone is unknown: `adapters/fixture/run` (it is in `WHOLE_SUITE_PREFIXES`, deliberately: "the fixture adapter every build test invokes"). The other paths select, with `attavo/cli.py` -> 2 files, `attavo/counsel.py` -> 1, `attavo/types.py` -> 1, `adapters/claude-code/run` -> 3, `attavo.toml` -> 2, `schema/*` -> 2-3; `attest/**` and `docs/*.md` select nothing. Without the fixture adapter, #277's other paths select 12 test files (the union of the per-path results).

**What the whole suite is [M].** `python3 -m pytest tests --collect-only -q -m 'not own_records'`: 764 selected, 8 deselected, collected in 1.05 s.

**How long it takes serially [M].**

```
echo tests | xargs -r python3 -m pytest -q -m 'not own_records'       # [tests].run, as the tool runs it
764 passed, 8 deselected in 643.00s (0:10:43)     load average 10-12 at the start; another lane's pytest ran alongside
```

That is 2.7 times the tool's 240 s bound. A no-path call on this selection cannot finish serially. (The three timed-out results in the executor's log stopped at 48% progress or earlier.)

**Does a product module map to the whole suite? (row 19) [M].** The question is wrongly framed; the numbers split it.

By module (`select-tests` over every product path):

| Paths | Whole suite | Mapped |
|---|---|---|
| `attavo/*.py` (27) | 7: `__init__`, `approval`, `common`, `events`, `records`, `spec`, `stops` | 20, to 1-4 files each |
| `bin/attavo`, `scripts/*`, `tests/helpers.py`, `tests/conftest.py`, `pyproject.toml`, `adapters/fixture/run` | all | none |
| `adapters/{claude-code,codex}/run`, `schema/*`, `templates/*`, `action.yml` | none | all |

The seven shared modules are named in `SHARED_MODULES` on purpose.

By item (each PR merged to `main` since 2026-09-15, its diff against first parent, through `select-tests`):

```
34 of 67 merged PRs select the whole suite
first forcing path: .github/workflows/attavo-approve.yml 8   bin/attavo 8   .github/workflows/ci.yml 7
                    adapters/fixture/run 3   attavo/stops.py 2   attavo/spec.py 2   adapters/fixture/conformance.json 1   attavo/__init__.py 1
```

"First forcing path" is the first unknown path in git's order; the list shows the top eight of ten. Counting every unknown path a PR carries: a workflow file is among them in 16 PRs, `bin/attavo` in 12, a shared module in 10, the fixture adapter in 5, `tests/conftest.py` in 4, `pyproject.toml` in 2. If workflow files and `bin/attavo` selected tests, **16 of the 34 would still select the whole suite and 18 would not** (the same selector run with those two paths treated as known and silent). The selector has no row for either path. **[P]** Add `.github/workflows/*` and `bin/attavo` to the selector (workflows -> the tests that read them, such as `test_action_yml`, `test_ci_inherit` and the template tests; `bin/attavo` -> `test_cli_help`, `test_testtool`, `test_build`). That is a selector change with a rejection test, separate from this item.

**Recommendation [P].** Keep the whole-suite fail-safe. Fix the two missing rows. Make the whole suite finish (Q2) and answer when it cannot (Q3).

## Q2. Parallel inside the tool

**xdist [M].** `python3 -c 'import xdist; print(xdist.__version__)'` -> `3.8.0` (Python 3.14.6, `/opt/homebrew/bin/python3`). It is not declared anywhere on `main`: `ci.yml` installs `pytest jsonschema`, and `[checks].probe` imports `pytest, mypy, jsonschema`. #291 ("pytest -n auto in CI only") is closed, but `ci.yml` at `f4e9c01` does not carry it. Without xdist, `-n auto` is a usage error and the run exits non-zero (`-p no:xdist -n auto`: pytest prints its usage trailer).

**Timing and failures [M].** The full selection, `-n auto` (10 cores), three runs, `-p no:cacheprovider`:

| Run | Load at start (1/5/15 min) | Wall | Result | Failed |
|---|---|---|---|---|
| 1 | 3.9 / 9.9 / 11.9 | 139 s | 763 passed | `test_drive::test_drive_until_ready_waits_out_the_bound_on_a_forge_still_running` |
| 2 | 17.5 / 13.7 / 13.1 | 142 s | 761 passed | that one, `test_testtool::test_attavo_test_ends_a_run_past_its_bound_with_everything_it_started`, `test_retry::test_a_transient_stop_retries_plainly_after_the_wait` |
| 3 | 33.1 / 20.7 / 16.0 | 279 s | 757 passed | 7: the three above, `test_retry::test_a_stage_past_its_ceiling_is_hung_by_the_ceiling`, `test_pass::test_a_stale_heartbeat_reads_died_...`, `test_pass::test_the_holders_heartbeat_is_read_where_its_marker_says`, `test_drive::test_drive_line_buffers_stdout_so_a_pipe_reader_sees_the_start_line_before_the_stage_ends` |
| `-n 4` run 1 | 34.7 / 56.7 / 39.5 | 186 s | 764 passed | none |
| `-n 4` run 2 | 30.2 / 44.0 / 37.5 | 206 s | 763 passed | `test_drive_until_ready_...` |

**Why they fail [M, I].** `test_drive_until_ready_...` asserts `elapsed < 0.4` on a subprocess (`tests/test_drive.py:678`); it failed with `took 0.70s`. The other six are the same kind (bounds, heartbeats, a pipe reader's timing); I read the first one's source, not the rest. Control: the seven, run serially three times at load 44-62, all passed (7 passed in 25-32 s); run together at `-n auto` and alone, they passed in 9.5 s. So they fail when the whole suite runs at once, and not for any shared state I could find. I did not separate xdist from load: on a quiet machine they may pass at `-n auto`.

**Overhead [M].** One four-test file: 0.19 s serial, 0.90 s at `-n auto` (+0.7 s per call).

**The change to `[tests].run` [P].**

```diff
-run = "xargs -r python3 -m pytest -q -m 'not own_records'"
+run = "xargs -r python3 -m pytest -q -n auto -m 'not own_records'"
```

with three conditions: (1) `[checks].probe` imports `xdist`, so a machine without it fails by name before a build starts; (2) the three tests that failed in two or more runs (`test_drive_until_ready_...` in four of five parallel runs; `test_retry_...transient...` and `test_testtool_...past_its_bound...` in two) are widened or put in a serial group, with a rejection test that runs the suite at `-n auto` ten times; (3) the tool applies it only when the selection is large (a directory such as `tests`, or more than N files), because a small call pays 0.7 s for nothing. Condition 3 needs a second manifest key, `[tests].run_parallel`, which the tool uses when `tests` or more than N identifiers are selected. **Recommendation:** make the change only after condition 2. Today it would turn a slow answer into a flaky one.

## Q3. Behaviour at the bound

**What the tool prints today [M].** `attavo/testtool.py` ends the run's process group at `call_timeout_s`, prints the runner's partial stdout, then one sentence, and exits 124. From the five no-path calls in #277's `build-01.log`, the executor's whole result for a timed-out call was 524-527 B:

```
.................................................................. [ 28%]
.........................F.............................................. [ 38%]
........................................................................ [ 48%]
...attavo test 277: the run did not finish within 240 s; it and everything it started were ended
```

It carries a percentage and an `F` with no name, and no count of what ran. It says nothing about narrowing. (From the archived events: 21 calls containing `attavo test` account for 2,178 s, matching the issue's total; I count 21 where the issue says 22.)

**Prototype [P], scratch only.** The tool runs the tenant's runner verbosely and holds the output, and on timeout prints a short answer from the per-test lines it holds; it needs the list of what was selected, from `--collect-only` (0.3-0.4 s, run only when the bound is hit). It works under `-n auto`, because it matches test ids and not positions. The file (`bound_report.py`) is at the end of this document. Run against the real suite with a 45 s serial bound and a 20 s `-n auto` bound:

```
attavo test 277: stopped at 45 s with 658 of 764 tests not run (106 passed, 0 failed).
not run: tests/test_adapters.py (2), tests/test_adapters_codex.py (2), tests/test_attest_time.py (4), tests/test_build.py (45), ... (+35 more files)
narrow it: attavo test 277 <path>   (one or a few files finish well inside 45 s)
```

**Size [M].** Printed to the executor: 551 B (serial) and 556 B (`-n auto`), against 524-527 B today: about 5% more, and it carries the facts the executor lacked. The tool captured 11.9 KB (serial, 45 s) and 57 KB (`-n auto`, 20 s) of runner output; the executor sees none of it. **[P]** When the run fails without timing out, the tool prints the failing tests' own sections, as it does today.

**Limits [M, P].** Matching `PASSED/FAILED` lines is pytest-specific; a generic tenant declares the reader (`[tests].reader`) or gets today's output. The sentence "narrow it" must not be offered when the call already named a path.

**Recommendation [P].** Adopt it, with the runner's `-v` and `--collect-only` supplied by the tool for a tenant that declares `runner = "pytest"`.

## Q4. Enforcement

### The evasions against each mechanism

Where "deny-list" is Claude Code's own `--disallowedTools` matching (`DENIED_COMMANDS`). Columns: **corpus** = the 14 archived build events files; **live** = my one invocation; **model** = my own prefix-matching model of the deny-list, which is not a measurement of Claude Code.

| Evasion | Deny-list | (a) marker guard | (b) events gate |
|---|---|---|---|
| `cd tests && pytest` | not measured; the corpus shows a `git stash ... && python3 -m pytest` compound denied (281-build-01), so probably denied | refused (rc 4) [M, simulated] | flagged [M] |
| `bash -c 'pytest'` | **not stopped: reached pytest** [M, live] | refused [M, live, rc 4] | flagged [M] |
| `timeout 60 python3 -m pytest` | not measured (`timeout` is absent on this macOS) | refused [M, simulated with a shell function] | flagged [M] |
| `python3.11 -m pytest` | **not stopped: reached pytest** [M, live] | refused [M, live, rc 4] | flagged [M] |
| `env X=1 pytest` | not measured; the corpus shows `env -u GIT_AUTHOR_NAME ... python3 -m pytest` denied (289-build-02), so probably denied | refused [M, simulated] | flagged [M] |
| **Not in the brief:** | | | |
| `pytest --noconftest` | n/a | **runs** [M] | flagged [M] |
| `ATTAVO_TEST_TOOL=1 pytest` or `env -u ATTAVO_STAGE pytest` | n/a | **runs** [M] | flagged [M] |
| `python3 -c 'import pytest; pytest.main()'` | n/a | refused [M] | **not flagged** [M] |
| a script that holds `pytest`, written by `Write`, run as `sh run.sh` | n/a | refused [I] (the guard sits in the runner) | **not flagged** [M] |

"Simulated" means the guard was run in a scratch tenant under `ATTAVO_STAGE=build` with the command through `sh -c`, not through a model.

### (a) Process boundary

**Prototype [P].** The tool starts the runner with a marker. The tenant's runner refuses without it when a stage is set. Diffs, scratch copy only:

```diff
@@ tests/conftest.py (tenant, new hook)
+def pytest_configure(config: pytest.Config) -> None:
+    if os.environ.get("ATTAVO_STAGE") and not os.environ.get("ATTAVO_TEST_TOOL"):
+        raise pytest.UsageError("attavo: tests run in a build only through `attavo test N [paths]`; "
+                                "this runner was not started by it")
```

`attavo/testtool.py` (the marker):

```diff
--- a/attavo/testtool.py
+++ b/attavo/testtool.py
@@ -31,8 +31,8 @@
         pass
 
 
-def run_bounded(command: str, root: Path, stdin: str, timeout_s: int | None
-                ) -> tuple[int, str, str, bool]:
+def run_bounded(command: str, root: Path, stdin: str, timeout_s: int | None,
+                env: dict[str, str] | None = None) -> tuple[int, str, str, bool]:
     """(exit, stdout, stderr, timed_out) of `command` run through the shell
     from root. The command is its own process group's leader, so a run past
     timeout_s is ended with everything it started -- a test runner's
@@ -41,7 +41,7 @@
     that outlasts its bound exits 124, as timeout(1) does."""
     proc = subprocess.Popen(command, shell=True, cwd=str(root), stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
-                            start_new_session=True)
+                            start_new_session=True, env=env)
     try:
         out, err = proc.communicate(stdin, timeout=timeout_s)
     except subprocess.TimeoutExpired:
@@ -190,7 +190,8 @@
     print(f"attavo test {item_id}: {len(identifiers)} selected", flush=True)
     started = time.monotonic()
     code, out, err, timed_out = run_bounded(tests_cfg["run"], root,
-                                            "".join(i + "\n" for i in identifiers), bound)
+                                            "".join(i + "\n" for i in identifiers), bound,
+                                            env={**os.environ, "ATTAVO_TEST_TOOL": "1"})
     elapsed = int(time.monotonic() - started)
     sys.stdout.write(out)
     sys.stderr.write(err)
```

`adapters/claude-code/run` (the stage name must reach the executor):

```diff
--- a/adapters/claude-code/run
+++ b/adapters/claude-code/run
@@ -644,14 +644,15 @@
     """The variables this adapter sets itself for its executor: the Bash
     foreground and its ceiling, from ATTAVO_TEST_TIMEOUT_S. None when no
     bound was exported."""
+    stage = {"ATTAVO_STAGE": os.environ["ATTAVO_STAGE"]} if os.environ.get("ATTAVO_STAGE") else {}
     bound = os.environ.get("ATTAVO_TEST_TIMEOUT_S")
     if not bound:
-        return {}
+        return stage
     try:
         millis = str((int(bound) + FOREGROUND_MARGIN_S) * 1000)
     except ValueError:
         return {}
-    return {"BASH_DEFAULT_TIMEOUT_MS": millis, "BASH_MAX_TIMEOUT_MS": millis}
+    return {**stage, "BASH_DEFAULT_TIMEOUT_MS": millis, "BASH_MAX_TIMEOUT_MS": millis}
 
 
 def build_command(instruction: str, config: dict, schema: dict, stage: str) -> list[str]:
```

**The finding that changes the design [M].** The brief says "while `ATTAVO_STAGE` is set". Inside an executor it is not. `adapters/executor_env.py` drops every `ATTAVO_*` name (#198), and `foreground_env()` adds only two `BASH_*` variables. Measured with `ATTAVO_ADAPTER_DRY_RUN=1 ATTAVO_STAGE=build` (the `env` the dry run prints is what the executor's process gets beyond the stripped copy of the environment):

```
main:    {'BASH_DEFAULT_TIMEOUT_MS': '300000', 'BASH_MAX_TIMEOUT_MS': '300000'}
patched: {'ATTAVO_STAGE': 'build', 'BASH_DEFAULT_TIMEOUT_MS': '300000', 'BASH_MAX_TIMEOUT_MS': '300000'}
```

So on `main` the guard would never fire in a real build. The same fact makes `inside = bool(os.environ.get("ATTAVO_STAGE"))` in `testtool.py:168` (and so `exclude_in_executor`) false when the executor runs the tool, and the "the product refuses its transport on its own account too (ATTAVO_STAGE)" half of the `DENIED_COMMANDS` comment inert. I did not test those two; I read the code. The patch forwards `ATTAVO_STAGE` only, and `tests/test_executor_conformance.py::test_situation_20_the_executor_sees_no_attavo_variable[claude-code]` then fails, as it should: `claude-code launched its executor with ATTAVO_STAGE`. Enforcement (a) is therefore an amendment to #198's rule: **the executor may see `ATTAVO_STAGE`, and no other `ATTAVO_*` name.** That is the operator's ruling.

**Live invocation [M].** Patched scratch adapter, fixture tenant with the guard, `ATTAVO_TEST_COMMAND` the real tool from the scratch copy, spec instructing two evasions. The executor ran, from the events:

```
Bash bash -c 'pytest -q'; echo "EXIT:$?"      -> ERROR: attavo: tests run in a build only through `attavo test N [paths]`; this runner was not started by it   (EXIT:4)
Bash python3.11 -m pytest -q; echo "EXIT:$?"  -> the same sentence                                                                                              (EXIT:4)
Bash python3 .../bin/attavo test 1 tests      -> attavo test 1: 1 selected ... 1 passed
```

Neither evasion was stopped by the permission layer: the sentence is the guard's, not a Claude Code denial. Without the adapter patch, the same invocation would have run both: by the dry-run above, the guard has no stage to read. I did not run that variant live (one invocation only).

**Does it stop every listed evasion? Yes, 5 of 5** (2 live, 3 simulated). It does not stop `--noconftest`, an executor that sets the marker or unsets the stage, or any runner with no hook. **Cost:** a 5-line hook per tenant runner, one contract amendment, and a doctor proof. **Script-checkable [P]:** yes. `ATTAVO_STAGE=build <runner>` in the tenant must exit non-zero with that sentence; with the marker it must pass; the conformance test asserts that the executor sees `ATTAVO_STAGE` and no other `ATTAVO_*`. **Owner [P]:** the marker and the stage pass-through are product and adapter; the hook is tenant-specific, and a tenant declares it generically as `[tests].guard = "pytest"` (the product ships the hook for known runners and `doctor` proves it), or leaves it unset.

### (b) After the fact

**Prototype [P]** (`events_gate.py`, at the end): a gate step that reads the build's events, finds each Bash call that ran a runner (a runner in command position, after skipping `VAR=x`, `env`, `timeout N`, `nice`, and looking inside `bash -c '...'`), skips a call whose result was a permission denial (it did not run), and refuses the build naming the call and time.

**Measured [M].**

- The five listed evasions: 5 of 5 flagged. `pytest -q`, `python3 -m pytest`, `python3 -m unittest`: flagged. `attavo test` calls, `for f in ...; do attavo test N $f; done`, `pip install pytest`, `grep -rn pytest`, `git commit -m 'run pytest'`: not flagged.
- The corpus: 14 build events files, 302 Bash calls, 6 denied pytest-shaped calls: **0 flagged, exit 0**. (My first version flagged `pip install pytest`; command-position matching fixed it. That is the false-positive risk of a matcher over command text.)
- It does not see inside a script, an `-c` string, or a call longer than 400 characters (the events cap, `EVENT_TEXT_MAX_CHARS`).

**Does it stop every listed evasion? It does not stop any; it refuses after the fact.** The build has already spent its time, and the fix is a retry. **Cost:** none at run time (a read of a file of a few hundred lines); one new gate step. **Script-checkable [P]:** yes, with a fixture events file per evasion (exit 1, names the call) and a clean one (exit 0). **Owner [P]:** product. It needs no contract change and no tenant hook, because events already carry the command.

### Comparison and recommendation [P]

| | (a) process boundary | (b) after the fact |
|---|---|---|
| Stops the listed five | yes (5 of 5) | refuses after (5 of 5) |
| Gaps | `--noconftest`; setting the marker; a runner without a hook | scripts; `python -c`; commands over 400 characters |
| Needs a contract change | yes: `ATTAVO_STAGE` reaches the executor (#198) | no |
| Per tenant | a runner hook | none |
| Time cost of an evasion | none, the runner refuses at once | the whole build |

Each one's gaps are the other's coverage (`--noconftest` is flagged by (b); a script is refused by (a)). Order of work: **first Q3 and the parallel run** (the five evasions in #277 all followed a tool that could not answer; nothing in the corpus ran a runner outside the tool after a denial, 0 of 302). Then **(b)**, which is cheap, needs no ruling, and turns the instruction's "do not" into a check. **(a)** only if (b) shows a build evading, because it costs a contract amendment and a hook per tenant.

## Q5. The instruction

`adapters/claude-code/run:277-288` (`build_instruction`), as it stands, with the bound clause present when `ATTAVO_TEST_TIMEOUT_S` is set:

> to run tests while you work, run `<test_command>`: it runs the tests selected for the files you have changed and stops at 240 s. Run a single test with `<test_command> <path or test id>`. Do not run the test runner directly, the whole test suite, or any whole-tree check yourself: when the criteria and `<test_command>` pass, commit and stop; the gates are run for you and any failure is returned to you on the next iteration

**Proposed sentence [P]**, once Q3 ships and (b) is the check:

> Run tests only with `<test_command> [path]`, which chooses the tests for your changes and says what it did not run; when it and the criteria pass, commit and stop, because the gates run for you and return any failure.

It drops the prohibitions that the tool and (b) now carry, and the "stops at N s" clause, which the tool's own answer replaces. `adapters/README.md` and `adapters/claude-code/README.md` carry the same paragraph and change with it.

## What I did not do

- Run any of `drive`, `worker`, `approve` or `counsel` on this repository.
- Measure on an idle machine. The `-n auto` pass/fail results are the thing most likely to change.
- Measure how Claude Code's deny matching treats `cd tests && pytest`, `timeout N ...` and `env X=1 pytest` directly (the corpus suggests it denies the `env` and `&&` shapes).
- Read the source of six of the seven flaky tests.
- Verify the issue's count of 22 test-tool calls: I find 21 with the same 2,178 s total.

## Decisions for the operator

1. **Parallel in the tool.** Make `[tests].run` parallel (`-n auto`, large selections only), with `xdist` in `[checks].probe` and CI, once the three recurring wall-clock tests are fixed? Or keep serial and rely on selection fixes and the bound report. I recommend the first, in that order.
2. **Enforcement.** Which mechanism, and does #198 bend? I recommend (b) now (product, no ruling), and (a) later, only with the amendment "the executor sees `ATTAVO_STAGE` and no other `ATTAVO_*`".
3. **The no-path call.** Adopt the bound report (Q3) and map `.github/workflows/*` and `bin/attavo` in the selector (18 of the 34 whole-suite PRs since 2026-09-15), or refuse a whole-suite call inside an executor by name? I recommend the first.

## Prototype files (scratch; not committed as source)

`bound_report.py` (Q3):

```python
"""Prototype for #317 Q3 (scratch, not a source change): what `attavo test N`
prints when the run reaches [tests].call_timeout_s."""
import re, subprocess, sys, time
sys.path.insert(0, "/Users/davidbean/Projects/attest-lane-green-317")
from collections import Counter
from attavo.testtool import run_bounded

ROOT = "/Users/davidbean/Projects/attest-lane-green-317"
RESULT = re.compile(r"(?:^|\s)(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b.*?(tests/\S+::\S+)|(tests/\S+::\S+)\s+(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)")

def seen(out: str) -> dict[str, str]:
    r = {}
    for line in out.splitlines():
        m = RESULT.search(line)
        if m:
            tid = m.group(2) or m.group(3); st = m.group(1) or m.group(4)
            r[tid] = st
    return r

def bound_report(out: str, collected: list[str], bound: int, item: str, named: str) -> str:
    done = seen(out)
    passed = sum(1 for s in done.values() if s in ("PASSED", "SKIPPED", "XFAIL", "XPASS"))
    failed = sorted(t for t, s in done.items() if s in ("FAILED", "ERROR"))
    notrun = [t for t in collected if t not in done]
    files = Counter(t.split("::")[0] for t in notrun)
    lines = [f"attavo test {item}: stopped at {bound} s with {len(notrun)} of {len(collected)} tests not run "
             f"({passed} passed, {len(failed)} failed)."]
    if failed:
        lines.append("failed: " + ", ".join(failed[:10]) + (f" (+{len(failed)-10} more)" if len(failed) > 10 else ""))
    if files:
        shown = ", ".join(f"{f} ({n})" for f, n in sorted(files.items())[:12])
        more = f" (+{len(files)-12} more files)" if len(files) > 12 else ""
        lines.append("not run: " + shown + more)
    lines.append(f"narrow it: attavo test {item} <path>   (one or a few files finish well inside {bound} s)")
    return "\n".join(lines)

if __name__ == "__main__":
    bound = int(sys.argv[1]); extra = sys.argv[2:]  # e.g. -n auto
    t0 = time.monotonic()
    col = subprocess.run("python3 -m pytest tests --collect-only -q -m 'not own_records'", shell=True, cwd=ROOT,
                         capture_output=True, text=True).stdout
    collected = [l for l in col.splitlines() if "::" in l]
    t_col = time.monotonic() - t0
    code, out, err, timed = run_bounded("python3 -u -m pytest tests -v -p no:cacheprovider -m 'not own_records' " + " ".join(extra), ROOT, "", bound)
    rep = bound_report(out, collected, bound, "277", "")
    print(f"# exit={code} timed_out={timed} collect={t_col:.1f}s collected={len(collected)} captured_runner_bytes={len(out)}", file=sys.stderr)
    print(rep)
    print(f"# report_bytes={len(rep.encode())}", file=sys.stderr)
```

`events_gate.py` (Q4b):

```python
"""Prototype for #317 Q4(b), scratch only: refuse a build by name when a Bash
call ran a test runner other than through `attavo test`. Reads the stage's
events (one {at, kind, text} per line). Exit 1 and one line per offender."""
import json, re, shlex, sys

SEPS = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")
WRAPPERS = {"timeout", "env", "time", "nice", "nohup", "command", "exec", "sudo", "xargs"}

def segments(cmd: str):
    # also look inside `bash -c '...'` / `sh -c "..."` bodies
    for seg in SEPS.split(cmd):
        yield seg
        m = re.search(r"\b(?:ba|z)?sh\s+-c\s+(['\"])(.*)\1", seg)
        if m:
            yield from segments(m.group(2))

def command_words(seg: str) -> list[str]:
    """The words of seg from the command itself: leading VAR=value, and a
    wrapper (env, timeout 60, nice -n 5 ...) with its own flags and numbers,
    are skipped, so a runner is found only where a command would start."""
    try:
        toks = shlex.split(seg)
    except ValueError:
        toks = seg.split()
    i = 0
    while i < len(toks):
        t = toks[i]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", t):
            i += 1
        elif t.split("/")[-1] in WRAPPERS or (i and toks[i-1].split("/")[-1] in WRAPPERS and (t.startswith("-") or re.fullmatch(r"[\d.]+[smhd]?", t))):
            i += 1
        else:
            break
    return toks[i:]

def runs_runner(seg: str) -> bool:
    w = command_words(seg)
    if not w:
        return False
    base = w[0].split("/")[-1]
    if base in ("pytest", "py.test"):
        return True
    if re.fullmatch(r"python[\d.]*", base) and w[1:3] and w[1] == "-m" and w[2] in ("pytest", "unittest"):
        return True
    if base in ("npm", "go", "cargo") and w[1:2] == ["test"]:
        return True
    return base == "npx" and w[1:2] == ["vitest"]

def offenders(events: list[dict]):
    out = []
    for i, e in enumerate(events):
        if e.get("kind") != "tool_call" or not e["text"].startswith("Bash "):
            continue
        cmd = e["text"][5:]
        nxt = events[i+1]["text"] if i+1 < len(events) and events[i+1].get("kind") == "tool_result" else ""
        if nxt.startswith("Permission to use Bash") and "denied" in nxt:
            continue                       # refused by the deny-list: it did not run
        for seg in segments(cmd):
            if runs_runner(seg):
                out.append((e["at"], cmd[:100])); break
    return out

if __name__ == "__main__":
    bad = 0
    for path in sys.argv[1:]:
        ev = [json.loads(l) for l in open(path)]
        for at, cmd in offenders(ev):
            bad += 1; print(f"{path.split('/')[-1]}: {at} ran a test runner outside `attavo test`: {cmd}")
    sys.exit(1 if bad else 0)
```
