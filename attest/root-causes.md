# Root causes

The closed list a fix item names with a `Root-cause:` line in its spec's
problem. `attavo spec-gate` refuses a root cause that is not listed here;
adding one is an item's deliberate act, in the same change as the fix.
Symptom counts do not converge. The number to read is the count of
distinct root causes named by the fix items merged over the last N items:
flat while items merge is convergence; growing names the next invariant.

Read it with: grep -h '^Root-cause:' attest/specs/*/spec.md | sort | uniq -c

- shared-working-copy: the executor, the driver, the screen and counsel wrote into one checkout; the invariant is that while a build runs the checkout is the executor's and every other actor writes records from a detached worktree (#227; #229 build-01, build-04's marker in the gate worktree; the driver's records-only commits; tests/test_retry.py::test_counsels_verdict_routes_the_next_pass, 8 of 40 runs with passes every 0.5 s beside a committing worker (#257), not reproduced on main in 30 runs (#262), so its attribution here is unconfirmed)
- deny-list-tool-surface: the executor's tools were removed one at a time as each one hurt; the invariant is a boundary told at the edit and a foreground long enough for one test run (#227 build-01; #229 build-01, build-05)
- dogfood-tests-told-apart-by-a-list: the tests that read this repository's own records were named in hand-copied lists instead of by their marker; the invariant is one marker and no list (#229 build-04)
- liveness-by-proxy: a stage's liveness was read from a pid and a host name written by a process that is not the holder; the invariant is a heartbeat written by the holder (#229 build-04, build-05)
- classification-by-reading-prose: a stop's class and provenance were derived by scanning the transcript for words; the invariant is that class and provenance come from structured output and gate results (#219; #229 build-01, build-03, build-04, build-05)
- tests-on-real-clocks: the product judged its bounds on the machine's own clock, so a test reached a bound only by waiting for it, and fixed loops of passes with sleeps hoped a detached worker had finished; the invariant is that every bound the product judges reads one clock, a file the tests set instead of waiting, and a test waits on a fact, never on time; tests/test_no_real_clock_waits.py holds it, reading every test source and failing on time.sleep, on a sub-second bound variable and on a loop that reads wall time, with no exceptions list, and the forge poll's bound and the --until-ready bound now read that clock too (#332; #257; tests/test_retry.py's 36 s of real sleeps and the suite bound crossed at 180 s; tests/test_testtool.py::test_attavo_test_ends_a_run_past_its_bound_with_everything_it_started, PermissionError on os.killpg under load on 2026-09-28, PR #256; tests/test_drive.py::test_drive_line_buffers_stdout_so_a_pipe_reader_sees_the_start_line_before_the_stage_ends under -n auto on 2026-10-02, the merge queue for #322 and CI for #327: ATTAVO_HEARTBEAT_S=0.2 made three beats 0.6 s of real time, a loaded runner's late beat read as died, #330)

## Specimens

Every defect found since 2026-09-25, under the root cause that made it possible, or marked as the screen's or as a ruling. A new defect lands here in the same change as its fix; one that fits no listed cause is the reason to add a cause. Read with: grep -c '^| [0-9]' attest/root-causes.md

| # | defect (specimen) | root cause | state |
|---|---|---|---|
| 1 | an uncommitted tree passed the gate (#227) | shared-working-copy | fixed, #229 |
| 2 | the screen's counsel commit refused as foreign (#229 build-01) | shared-working-copy | fixed, attavo/screen #45 |
| 3 | the driver committed records in the executor's checkout; reset --hard on a lost lease; a merge retry without the driver's identity; a rebase in the pass | shared-working-copy | fixed, #262 |
| 4 | the running build's own marker read in the gate worktree (#229 build-04, #237 build-01) | shared-working-copy, with liveness-by-proxy | test side fixed by #240; liveness reads the heartbeat since #248; no test names this case |
| 5 | Monitor and ScheduleWakeup allowed (#227; #229 build-01, build-05) | deny-list-tool-surface | fixed, #250 |
| 6 | a two-minute Bash foreground against a 240 s test bound (#229 build-05) | deny-list-tool-surface | fixed, #250 |
| 7 | the executor read the operator's Claude Code memory file | deny-list-tool-surface | open: the sandbox item |
| 8 | a stub path not checked against the claims (#229 build-03) | deny-list-tool-surface | fixed, #250 |
| 9 | three deselect lists, one stale (#229 build-04) | dogfood-tests-told-apart-by-a-list | fixed, #240 |
| 10 | the doctor job's -k list missed proof seven | dogfood-tests-told-apart-by-a-list | fixed, #240 |
| 11 | the inherit path ran doctor proofs without origin/HEAD | dogfood-tests-told-apart-by-a-list | fixed, #240 |
| 12 | the marker's pid was the driver's (#229 build-04, build-05) | liveness-by-proxy | fixed, #248 |
| 13 | again_by recorded true instead of the actor | liveness-by-proxy | fixed, #248 |
| 14 | class read from the transcript: no-cause three times, forge-unconcluded once | classification-by-reading-prose | fixed, #244; checked by proof four since #256 |
| 15 | a ceiling classed by text markers | classification-by-reading-prose | fixed, #244 |
| 16 | the suite bound of 180 s crossed by one second | tests-on-real-clocks | bound raised to 300 s in #240; the bound is a ruling |
| 17 | tests/test_retry.py::test_counsels_verdict_routes_the_next_pass flaky (#219, #237) | unconfirmed | deterministic since #260; not reproduced on main in 30 runs (#262) |
| 18 | the retry tests spent 36 s on real sleeps | tests-on-real-clocks | fixed, #260 |
| 19 | select-tests maps a product-module change to the whole suite, about 8 minutes per gate iteration | none: a ruling | a cost for the operator to decide |
| 20 | the screen offered typed stage transitions and nobody ran the pass | none: the screen's | fixed, attavo/screen #49 |
| 21 | the screen's conversation outlived its item | none: the screen's | fixed, attavo/screen #49 |
| 22 | a live worker reported as never committed while counsel ran | liveness-by-proxy | fixed, #255 |
| 23 | every question's provenance is spec | none: a ruling | open |
| 24 | file-stops issue bodies are record dumps (#231 to #235, #239) | none: a ruling | open |
| 25 | the screen's Recover called the removed --recover | none: the screen's | fixed, attavo/screen #49 |
| 26 | the claimed-edit rules never exercised: #251's build wrote with cp in Bash | deny-list-tool-surface | open: the live check is a build that tries an unclaimed Edit |
| 27 | the executor ran find / (#251 build-01) | deny-list-tool-surface | open: the sandbox item |
| 28 | tests/test_testtool.py os.killpg flake under load (PR #256) | tests-on-real-clocks | open, named in #260 |
| 29 | screen lanes checked out branches in the checkout the operator's server serves | shared-working-copy | lanes work in their own worktree from attavo/screen #56 |
| 30 | the stage record's first write in the checkout, then git add there | shared-working-copy | open: the residual of #262 |
