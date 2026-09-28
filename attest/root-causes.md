# Root causes

The closed list a fix item names with a `Root-cause:` line in its spec's
problem. `attavo spec-gate` refuses a root cause that is not listed here;
adding one is an item's deliberate act, in the same change as the fix.
Symptom counts do not converge. The number to read is the count of
distinct root causes named by the fix items merged over the last N items:
flat while items merge is convergence; growing names the next invariant.

Read it with: grep -h '^Root-cause:' attest/specs/*/spec.md | sort | uniq -c

- shared-working-copy: the executor, the driver, the screen and counsel wrote into one checkout; the invariant is that while a build runs the checkout is the executor's and every other actor writes records from a detached worktree (#227; #229 build-01, build-04's marker in the gate worktree; the driver's records-only commits)
- deny-list-tool-surface: the executor's tools were removed one at a time as each one hurt; the invariant is a boundary told at the edit and a foreground long enough for one test run (#227 build-01; #229 build-01, build-05)
- dogfood-tests-told-apart-by-a-list: the tests that read this repository's own records were named in hand-copied lists instead of by their marker; the invariant is one marker and no list (#229 build-04)
- liveness-by-proxy: a stage's liveness was read from a pid and a host name written by a process that is not the holder; the invariant is a heartbeat written by the holder (#229 build-04, build-05)
- classification-by-reading-prose: a stop's class and provenance were derived by scanning the transcript for words; the invariant is that class and provenance come from structured output and gate results (#219; #229 build-01, build-03, build-04, build-05)
- tests-on-real-clocks: the product judged its bounds on the machine's own clock, so a test reached a bound only by waiting for it, and fixed loops of passes with sleeps hoped a detached worker had finished; the invariant is that every bound the product judges reads one clock, a file the tests set instead of waiting, and a test waits on a fact, never on time (#257; tests/test_retry.py's 36 s of real sleeps and the suite bound crossed at 180 s; tests/test_retry.py::test_counsels_verdict_routes_the_next_pass, three sightings; tests/test_testtool.py::test_attavo_test_ends_a_run_past_its_bound_with_everything_it_started, PermissionError on os.killpg under load on 2026-09-28, PR #256)
