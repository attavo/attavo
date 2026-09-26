## Problem

The executor is handed one test command, `attavo test N` (issue #212), and told not to run the whole suite. It runs the raw test runner anyway. #219's second build attempt ran `pytest` directly 32 times against 2 runs of `attavo test`, spent a third of its 7,668 s after the tree was green, and was recorded hung at the ceiling; the same executor, handed the same instruction on the third attempt, ran `pytest` 0 times and passed in 247 s. Nothing in the product distinguished the two attempts but the executor's habit, and nothing in the product could stop the habit: the instruction is a sentence, the tool it names cannot run one test, runs the same selection again for every call however little changed, leaves no trace in the item's feed, and runs inside the executor every test the tenant's selector names, including tests that need a live tenant or the working directory the executor is standing in. The same attempt ran `attavo drive` inside its own build: the product's transport, called from inside a stage, took the item's lease as a second pass, lost it, and `git reset --hard HEAD^` the tree the executor was writing (`attavo/drive.py` `commit_record`'s loser path, which resets its own marker and which no guard kept out of an executor's hands).

This item makes the one test command the executor's only way to run tests, and makes it good enough that the executor has no reason to reach past it. It is a fix-class item: no research record, the specimen is the evidence, and the stub is the change.

Modes. `attavo test N` keeps its meaning: the tests `[tests].select` names for what the item has changed (`mapped`). `attavo test N <test>...` runs the named tests and nothing else (`named`), so an executor working on one test runs one test; `attavo test N --all` runs the whole suite through `[tests].run` with `tests` as the one identifier (`all`), so a whole-suite run, when the executor wants one, goes through the same bound, the same event, and the same exclusion. Naming tests and `--all` together is refused (exit 2).

The cache. A run that exits 0 is remembered against a fingerprint of the tree: HEAD, the diff against it, and every untracked unignored file's name and content hash, together with the mode and the identifiers. An unchanged tree asked the same question again is answered from the cache in under a second and says so ("unchanged since the run at T (exit 0); not rerun; pass --fresh to rerun"); `--fresh` reruns. Only exit-0 results are cached: a failing run is rerun every time, because the executor's next question after a failure is whether its edit fixed it. The cache lives beside the stage's events under the git directory (`<git-dir>/attavo/test-cache.json`), outside the tree, so it is never committed and never appears in `git status`.

Every run is an event. When `ATTAVO_EVENTS` is set, one line of kind `test` per invocation: `attavo test N: mode M, K selected, exit C, S s` (or `cached`). The build's feed shows what the executor ran and how often, and the executor-habits measurement (`executor-habits.py`, the rule recorded 2026-09-25) reads the feed instead of the transcript.

Exclusion inside an executor. `[tests].exclude_in_executor`, a list of test identifiers the tool leaves out when `ATTAVO_STAGE` is set, saying so ("left out inside an executor: ..."); outside an executor and in CI they run. For tests that need a live tenant, the network, or the process's own working directory. This repository declares none today: no test of its own has been shown to need it, and an empty list is prose.

The transport refuses. `attavo drive`, `worker`, `build`, `verify`, `counsel` and `file-stops` exit 2 without acting when `ATTAVO_STAGE` is set: "refused inside an executor (ATTAVO_STAGE is set); the transport is the pass's, not the executor's". No lease is taken, no marker moves, no record is written, no tree is reset. `attavo test` and the read-only commands are unaffected.

The adapters. The claude-code adapter's `--disallowedTools` names, beside the subagent tools, the raw runners as permission-rule patterns (`Bash(pytest:*)`, `Bash(python3 -m pytest:*)`, `Bash(python -m pytest:*)`, `Bash(npm test:*)`, `Bash(npx vitest:*)`, `Bash(go test:*)`, `Bash(cargo test:*)`) and the transport (`Bash(attavo drive:*)`, `Bash(attavo worker:*)`); the instruction sentence names the single-test form and says when to stop: "Run a single test with `<cmd> <path or test id>`. Do not run the test runner directly, the whole test suite, or any whole-tree check yourself: when the criteria and `<cmd>` pass, commit and stop; the gates are run for you and any failure is returned to you on the next iteration." The codex adapter carries the same sentence and declares in `conformance.json` that it denies nothing per command (its sandbox governs writes and network, not command names); the product's own refusal is what holds there. Both adapters' `conformance.json` carry `denied_commands` with `mechanism` and `patterns`, so a second adapter must say what it denies.

The selector. `.attavo/bin/select-tests` names its shared modules (`SHARED_MODULES`: the modules most of the others import, each of which selects the whole suite) so that every module under `attavo/` is placed, in `MODULE_TESTS` or in that tuple, and a new module that is neither fails the suite until it is.

Not in this item: a test map for tenant zero (its selector is its own); denying commands on codex; rendering the `test` events on the screen (the screen tails the feed and shows every kind already); the executor-habits measurement itself.

The stub under `stub/` is the change, tests included: the eight tests named in the criteria are red on the tree (f8b39f9) and seven are green on the stub; `tests/test_cli_help.py::test_help_names_the_test_modes_the_cache_and_the_transport_refusal` is red on the stub by design, because the help text is the build's. Three existing assertions in `tests/test_adapters.py` that `--disallowedTools` is exactly `Agent,Workflow` are loosened to a prefix in the stub, and `tests/test_record_schemas.py`'s count of `json.loads(` sites rises by one for the cache. `python3 -m mypy` exits 0 on the stub with no `type: ignore`.

Retires: the instruction sentence as the only thing between the executor and the raw runner; the transport as something an executor can call; the same selection run again on an unchanged tree; the transcript as the only record of what the executor ran; `attavo/drive.py`'s reset of the executor's tree as a reachable outcome of a build.

## Claims

- attavo/testtool.py
- attavo/cli.py
- schema/manifest.schema.json
- adapters/claude-code/run
- adapters/claude-code/conformance.json
- adapters/codex/run
- adapters/codex/conformance.json
- .attavo/bin/select-tests
- tests/test_testtool.py
- tests/test_adapters.py
- tests/test_cli_help.py
- tests/test_record_schemas.py

## Criteria

- the-tool-caches-an-unchanged-tree.sh: on a fixture tenant whose run command counts its runs, the second `attavo test 1` on an unchanged tree runs nothing, says it was not rerun, and returns in under three seconds; an edit reruns; `--fresh` reruns
- a-named-test-and-the-whole-suite-are-modes-and-every-run-is-an-event.sh: `attavo test 1 tests/test_a.py::test_a` hands the run command that one identifier, `--all` hands it `tests`, a bare call hands it the mapped selection, and with `ATTAVO_EVENTS` set each of the three writes one event of kind `test` naming its mode and exit 0
- inside-an-executor-the-transport-refuses-and-excluded-tests-are-left-out.sh: with `ATTAVO_STAGE=build`, `attavo test 1` leaves out the test the manifest's `exclude_in_executor` names and says so, and `attavo drive 1`, `attavo worker 1 build 01` and `attavo build 1` each exit non-zero saying they are refused inside an executor; without it, the excluded test runs
- the-adapters-deny-the-raw-runner-and-the-tests-exist.sh: the claude-code adapter's dry run names the three pytest patterns in `--disallowedTools` and its instruction says when to stop; both adapters' `conformance.json` carry `denied_commands`; `tests/test_testtool.py` and `tests/test_cli_help.py` carry the eight tests named
