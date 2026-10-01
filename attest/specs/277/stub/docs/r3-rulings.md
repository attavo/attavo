# R3 (retries, bounds and budgets): the nine rulings of 2026-09-25

All nine proposed by the session and ruled, agreed, by the operator on 2026-09-25 with the session's recommendations adopted. They sit on top of ruling C and its two amendments in the reconciler brief (2026-09-20). Item #219 carries the spec; v0.28.0 carries the code.

1. **Failure signature.** `stop.signature` on every non-pass stop, compared by string equality, runs of digits removed on every family: last event line (died, hung); adapter's stop detail (transient, others); sorted names of red required checks (returned check); sorted titles of blocking findings (returned review). The exhaustion report prints signatures side by side; a signature too fine is the known failure mode and the side-by-side is how it is seen.
2. **What a returned retry carries.** The red check's log via `[forge].check_log_command`, or the review's blocking findings. Collapsed with 3 into one input.
3. **How a steer reaches the next attempt.** The existing `ATTAVO_FEEDBACK` file, seeded on the retried attempt's first iteration with class and signature, the evidence, and the newest steer newer than the blocking record (counsel's `answer` now, an operator's `/rule` after R4). The record carries `retry_of` and `steer`. No new adapter input.
4. **Transient wait.** `[drive].retry_after_s`, default 300; died and hung retry on the next pass with no wait.
5. **Ceilings.** `[drive.build].ceiling_s` and `[drive.verify].ceiling_s`, read against the corpus of build wall clocks at each pin; past it, terminated and recorded hung with `stop.bound = "ceiling"`; non-streaming adapters (`conformance.json` `streams_events: false`) are judged by the ceiling alone; the doctor refuses a `[drive]` table without both ceilings.
6. **Budget accounting.** Every marker counts, recovered attempts included; the report always says attempt N of the budget.
7. **`--again`.** Resets a spent budget only, recorded in the marker; a no-op inside the budget; retired in R4.
8. **Environment refusal.** Recorded in R3, never retried per item; acted on by R4's tenant-wide breaker.
9. **Exhaustion report's home.** `attavo drive N --report` (state stopped, `attempts`, `signatures_match`, `rulings_available`); no new command.

Found while writing the spec: a review with findings passed whatever their severity (severity is free text); the returned-review family needed a definition, so a finding of severity `blocking` fails the review as `returned` and every other severity is filed as before. R2 as merged reads alive from the heartbeat and never calls a heartbeated worker hung by event age; the ceiling is therefore the only wall-clock bound a worker has, which is what ruling 5 intended.

Exclusions: no cross-executor retry; no backoff beyond the one fixed wait.
