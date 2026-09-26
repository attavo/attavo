## Problem

After R2 a pass starts a stage and a worker runs it, but nothing in the product starts a stage again. A build whose worker died is recovered as `died` and then waits for a person; a review the vendor refused at capacity is a `failed` record, a counsel run, a filed issue, and two presses (#194's `verify-01`); a build whose head a required check rejected is a level (`blocked-on-checks`) that never becomes a record, so the item sits until someone reads the screen (#206's `build-01`, red on `typecheck`); a review that returns blocking findings stops the item as if the findings were a question for the operator. Every one of these is a failure whose answer does not touch the definition of done, and the project's rule is that such a failure needs the operator only if the spec must change. Nothing bounds a stage's wall clock either: a review on an adapter that streams no events is judged by a stall bound it can never trip, and the only ceiling is the operator's patience.

This is R3 of the reconciler (`claude/reconciler-brief-2026-09-20.md`, ruling C with its two amendments, section 6; the nine rulings of 2026-09-25 recorded in `claude/r3-rulings-2026-09-25.md`). It adds the retry families, one budget, the early stop, the exhaustion report, the stage ceilings, and the input the next attempt is handed; it retires the operator as the only thing that ever runs a stage twice.

The families. A stage record that is not a pass belongs to one of four retryable families or to a person. `died` and `hung` (recovery's classes, unchanged) and `transient` (new: a vendor's capacity, rate, or 5xx refusal, classed by the adapter on its report line as every class is) retry plainly: the next pass starts the next attempt, asks counsel nothing, and a `transient` waits `[drive].retry_after_s` (default 300) from its record before the attempt starts, the report reading "retrying build in N s" meanwhile. `returned` (new) is a build whose head a required check rejected, or a build whose review returned a blocking finding (a finding whose `severity` is `blocking` makes the review's status `failed` with class `returned`; every other severity leaves the review a pass and is filed as today): the pass writes the check rejection as a returned review record the moment the report reads a concluded red check on a passed build's head (class `returned`, provenance tenant, no adapter run — the shape `attavo verify` already writes for a forge that is not green), so the rejection is a record and not a level; the next build attempt is handed the evidence; counsel is asked only when the second attempt ends with the same signature. Every other non-pass record is read by counsel as today, and counsel's verdict routes: `answer` starts the next attempt with the answer as its steer; `patch` files and the report reads "waiting on #N"; `person` is the operator's. A question, a claims-boundary stop, a tenant's gate refusal, and an environment refusal are never retried by this item; environment refusal is recorded here and acted on by R4's breaker.

One budget. `[drive].attempts` (default 3) per stage, shared by plain, returned, and steered attempts; every attempt with a marker counts, a recovered one included, because the budget bounds what the product spends on a stage. Two attempts of one stage ending with the identical failure signature stop the stage early and do not spend the third. When the budget is spent, or the early stop fires, the report's state is `stopped` and it carries `attempts`: for each attempt its class, signature, the bound it crossed (`stall`, `ceiling`, or none), wall clock and cost; whether the signatures match; and `rulings_available` naming what each would do (`--again` resets the budget, flagged unlikely to help when the signatures match; amending the spec, which moves the approval). Counsel is read once more at exhaustion and its sentence rides beside the comparison, never instead of it.

The signature. Every non-pass stop carries `stop.signature`, a string a script computes from the record and compares by equality, with runs of digits removed: for `died` and `hung`, the last event line; for `transient`, the adapter's stop detail; for a `returned` check rejection, the sorted names of the required checks that concluded red; for a `returned` review, the sorted titles of its blocking findings; for every other class, the stop detail. The exhaustion report prints the signatures side by side.

The next attempt's input. The product already hands an attempt a feedback file (`ATTAVO_FEEDBACK`), empty on a first iteration. A retried attempt's first iteration starts with that file carrying, in this order and only the parts that exist: the prior attempt's class and signature; the evidence (the failing check's log read through `[forge].check_log_command`, or the review's blocking findings); and the newest steer newer than the blocking record (counsel's `answer` text now; an operator's ruling after R4), with the record it came from named. The attempt's record carries `retry_of` (the prior attempt's stem) and `steer` (the counsel record's stem, or null). No new adapter input, no new environment variable: one file the contract already passes.

Ceilings. `[drive.build].ceiling_s` and `[drive.verify].ceiling_s`, wall clock from the marker's `at`. A worker alive past its stage's ceiling is terminated by the next pass and recorded `hung` with `stop.bound = "ceiling"`. An adapter whose `conformance.json` declares `"streams_events": false` is judged by the ceiling alone; its report never says "no event for N s". The doctor refuses a manifest that sets `[drive]` without both ceilings and `attempts`, as it refuses one without `stall_after_s`. The self-tenant declares build 2,400 s, verify 1,200 s, attempts 3, retry_after_s 300; the manifest schema carries the keys.

`--again` after this item starts the next attempt of a stage whose budget is spent, resets that stage's budget, and writes `again_by` into the new marker; inside the budget it is a no-op that says the pass retries on its own. It is retired in R4.

Not in this item: retrying on a different executor; any wait other than the one fixed `retry_after_s`; the breaker; rulings as records.

The stub under `stub/` is thin: the two classes and the signature, the schema keys, the doctor's refusal, a pass that retries the four families inside the budget with the early stop and the transient wait, the returned record on a red check with its log handed to the next build, and the ceiling. It has no steer, no blocking-finding return, no transient wait in the report's words, no `--again` narrowing, no adapter classification, and no updates to the existing tests the stop shape and the doctor's refusal break. Ten of the eleven tests in `tests/test_retry.py` are red on the tree; five are red on the stub, listed in the issue. `python3 -m mypy` exits 0 on the built tree with no `type: ignore`.

Retires: the operator as the only thing that runs a stage a second time; `blocked-on-checks` as a level with no record behind it (with it, `tests/test_drive.py::test_drive_pass_reads_a_red_forge_as_blocked_and_starts_nothing` and `test_drive_until_ready_stops_when_the_forge_goes_red_after_a_build`, which encode that level, are rewritten to the returned record); the counsel run on a vendor's capacity error (#194's `verify-01`, replayed as this item's rejection fixture); the stall bound as a bound on adapters that do not stream; `--again` inside the budget.

## Claims

- attavo/drive.py
- attavo/report.py
- attavo/verify.py
- attavo/build.py
- attavo/stops.py
- attavo/doctor.py
- attavo/types.py
- schema/manifest.schema.json
- schema/records/build.schema.json
- schema/records/verify.schema.json
- schema/drive-report.schema.json
- adapters/fixture/run
- adapters/fixture/conformance.json
- adapters/claude-code/run
- adapters/claude-code/conformance.json
- adapters/codex/run
- adapters/codex/conformance.json
- attavo.toml
- tests/test_retry.py
- tests/test_pass.py
- tests/test_drive.py
- tests/test_report.py
- tests/test_doctor.py
- tests/test_build.py
- tests/test_verify.py
- tests/test_verify_polls.py
- tests/test_proof_five_elsewhere.py
- tests/test_record_schemas.py
- tests/test_stub_ratio.py
- tests/test_types_mirror_schemas.py

## Criteria

- a-transient-stop-retries-plainly.sh: on a fixture tenant whose build adapter refuses once with the vendor's capacity error and passes on its second run, passes alone carry the item from an approved spec to a passing build-02 whose retry_of is build-01, build-01's stop class is transient, and no counsel record exists
- a-red-check-becomes-a-returned-record-and-the-next-build-is-handed-its-log.sh: on a fixture tenant whose checks command reads red once after the first build and green after, a pass writes verify-01 with status returned and class returned naming the red check in its signature, the next pass starts build-02 whose first feedback file names that check and its log, and no operator act occurs between approval and the passing review
- two-identical-failures-stop-at-two-and-the-report-compares-them.sh: on a fixture tenant whose build adapter dies on the same event line every run, passes write build-01 and build-02 as died with equal signatures and start no build-03; attavo drive N --report reads stopped with two attempts, matching signatures, and --again among the rulings available
- a-stage-past-its-ceiling-is-hung-by-the-ceiling.sh: on a fixture tenant whose build adapter sleeps past a ceiling of two seconds and declares it does not stream, a pass terminates the worker and writes build-01 as hung with bound ceiling, and the report never speaks of event age
- the-manifest-and-the-tests-exist.sh: the doctor refuses a fixture manifest with a [drive] table and no ceilings, and tests/test_retry.py carries the tests named
