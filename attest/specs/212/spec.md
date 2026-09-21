## Problem

A build spends most of its time running the whole test suite, three times, around the executor's work. Read from #194's `build-01`: 215 s before the executor began, where `attavo build` runs `[checks].command` whole only to learn whether it can run at all (exit 2) and the feed shows nothing; 241 s at the gate, the whole suite again; and a 590 s wait inside the executor on a third run, because the instruction names the tenant's checks command and an executor given a command runs it (tenant zero's build-10 earned the present wording, "are run for you"; it did not stop #194, #204, or #206, where the suite was run twice inside one build). About 1,050 s of suite around 220 s of work, ahead of a required `test` check on the forge that runs the same suite and that the item waits on before review in any case.

The operator ruled four changes on 2026-09-20.

1. The question asked before the first build is answered by a probe the tenant declares: `[checks].probe`, run once inside `[checks].probe_timeout_s` (required with it), exit 0 meaning this machine can run the checks and anything else, or the bound, an environment refusal written as today's is. A tenant with no probe is asked the old way. Either way the feed says "checking this machine can run the checks" before and the answer after.
2. The executor is given one test command, `attavo test N` (`ATTAVO_TEST_COMMAND`, with `ATTAVO_TEST_TIMEOUT_S` when the tenant declares `[tests].call_timeout_s`): it selects, through the tenant's `[tests].select`, for every path the item has changed since it left the default branch, committed or not, the records directory aside; runs them through `[tests].run`; exits as the run does; and at the bound ends the run with everything it started and exits 124. `ATTAVO_CHECKS_COMMAND` leaves the contract and the instruction; the instruction says not to run the whole suite or any whole-tree check.
3. One definition of the checks, split by where each runs: this repository's gate (`[checks].command`) runs the type check, which is fast and which the forge also requires (#206's `build-01` passed a gate that did not run it and left the head red); the whole suite runs once, as the forge's required `test` check. Until the reconciler's R3 returns a build whose head a required check rejects, a suite failure the selected tests missed costs the operator a Rebuild, which the operator accepted.
4. This repository's selector maps a module to its own tests and fails safe: a path it does not know selects the whole suite; a test file selects itself; prose and the repository's own specs and records select nothing.

The stub under `stub/` is thin by the operator's ruling: `attavo test` sees committed changes only and ends nothing it started, the probe has no bound and no feed lines, the selector knows one module, and ten of the thirteen tests in `tests/test_testtool.py` fail on it. Three tests in `tests/test_self_tenancy.py` pinned the old selector and the suite in the gate; the stub carries them as they must read after this item. `python3 -m mypy` exits 0 on the built tree with no `type: ignore`; the builder runs it last, after its final change to any file. The builder does not run the whole suite.

Retires: the whole run of the checks before a build where a tenant declares a probe; `ATTAVO_CHECKS_COMMAND` and the sentence that named it; the whole suite in this repository's `[checks].command`; the selector's `bin/attavo` row and its silence on `attavo/**`.

## Claims

- attavo/build.py
- attavo/testtool.py
- attavo/cli.py
- adapters/claude-code/run
- adapters/codex/run
- adapters/README.md
- adapters/claude-code/README.md
- adapters/codex/README.md
- adapters/*/conformance.json
- schema/manifest.schema.json
- attavo.toml
- .attavo/bin/select-tests
- README.md
- tests/test_testtool.py
- tests/test_executor_conformance.py
- tests/test_adapters.py
- tests/test_adapters_codex.py
- tests/test_adapter_claude_code.py
- tests/test_adapter_codex.py
- tests/test_build.py
- tests/test_cli_help.py
- tests/test_self_tenancy.py
- tests/test_record_schemas.py

## Criteria

- build-asks-the-probe-and-not-the-whole-checks.sh: with a probe declared, one build runs the probe once and the checks once (at its gate, not before it); with a probe that exits 3 the build is refused as an environment stop and the executor never runs
- attavo-test-runs-the-selected-tests-inside-a-bound.sh: attavo test 1 hands the run exactly what the tenant's select names for the item's changed paths, exits 7 when the run does, and exits 124 within seconds when the run outlasts a 2 s call_timeout_s
- the-selector-fails-safe-and-the-gate-drops-the-suite.sh: this repository's selector answers tests for a module it does not know, tests/test_report.py for attavo/report.py, and nothing for prose and records; attavo.toml's gate command runs the type check and not the suite, and declares a probe with its bound and a call_timeout_s
- the-executor-is-given-one-test-command.sh: ATTAVO_CHECKS_COMMAND is named nowhere in attavo/build.py or either real adapter, ATTAVO_TEST_COMMAND is named in all three, the manifest schema carries probe (requiring probe_timeout_s with it) and call_timeout_s, tests/test_testtool.py carries its thirteen tests by name, and the conformance suite carries situation 7 under its new name
