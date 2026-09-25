## Problem

`attavo drive N` is a process that lasts as long as the stage it runs. It writes the invocation marker, pushes it, runs build or verify in-process, polls the forge for up to ten minutes, commits and pushes the record, and only then returns. Whoever called it holds it for the whole of that: the operator's shell, or the screen's server, which on #652 and #206 read six stage lines minutes late and on #194 twice lost a refusal nobody could read (#171). When the caller dies the stage dies with it, and the marker it pushed is what proof five later reads as a silence. `--recover` is a separate act a person must remember, and it refuses a tenant that has not declared a stall bound even when the invocation's pid is plainly gone. Two callers at once both run the stage: nothing holds an attempt.

This is R2 of the reconciler (`claude/reconciler-brief-2026-09-20.md`, rulings A, B, C's recovery half, F, G, H; 2026-09-20). `attavo drive N` becomes one pass: it reads the report (#206), acts at most once, and returns within seconds. The acts are three, one per report state that is the product's move: `owes-recovery` writes the died or hung record (what `--recover` did); `next-build` and `next-review` start the stage. Every other level is printed in the report's words and left, exit 0; exit 1 is a refusal. Starting a stage is the lease: the pass writes the marker, commits it, and pushes that commit as a plain fast-forward with no merge and no retry; a rejection that is not a fast-forward, or a ref another push holds, means another pass started this attempt, and the loser says so, resets its own marker commit, and starts nothing. A push the remote refuses outright is exit 1 and starts nothing. On a pushed marker the pass spawns `attavo worker N <stage> NN` in its own session, its stdout and stderr to `<git-dir>/attavo/<stage>-NN.worker.log`, and returns; the worker outlives the pass, runs the stage in-process exactly as the drive did (counsel and file-stops on a non-pass record, one `git commit --only`, one push with the merge retry), and touches `<git-dir>/attavo/<stage>-NN.heartbeat` every `ATTAVO_HEARTBEAT_S` seconds (15) while it runs. The report and the pass read alive from the heartbeat first (fresh within three beats: alive; stale: died) and fall back to the pid only when there is no heartbeat file. The stall bound decides hung, never died: with no `[drive].stall_after_s` a dead invocation is still recovered as died and an alive one is running for as long as it lives. A record written and not yet committed reads as running (the worker commits after it writes); one left uncommitted by a worker whose heartbeat is stale reads as a refusal naming the file. The review takes the checks the pass read on the head before the marker (ruling B): the pass writes them to `<git-dir>/attavo/verify-NN.forge-checks.json` and names the file in `ATTAVO_FORGE_CHECKS`; `attavo verify` reads that instead of polling, and polls as before when the variable is unset. The pass fast-forwards its working copy to origin's branch before reading, never while a worker holds the copy (a marker without its record, or a record not yet committed). `--again` starts the next attempt of a stopped stage, the operator's resume until rulings drive retries (R3, R4). `--all` runs one pass in every `../attavo-worktree-N` beside the checkout. `--until-ready` repeats the pass in one process, sleeping `ATTAVO_DRIVE_POLL_INTERVAL_S` between passes, until the level is the operator's or done (exit 0), a check concludes red (exit 1), or the checks have not concluded within `ATTAVO_DRIVE_POLL_BOUND_S` (exit 1). `--recover` is gone and exits 2 saying so. Nothing new is a record: the marker and the records are the worker's facts on the branch; the heartbeat, the worker log, and the forge-checks file are local under the git dir and never committed.

The stub under `stub/` is thin: a pass that reads the report, recovers, or writes and pushes the marker with the record push's own retry and starts the stage body in a child of its own session, with the review handed the checks; no heartbeat, no lease, no fast-forward, no `--again`, `--all`, or `--until-ready`. Seventeen of the tests named below fail on it. `python3 -m mypy` exits 0 on the built tree with no `type: ignore`.

Retires: the stage run inside `attavo drive`; the forge poll inside the drive (`_drive_step`); `--recover` as an operator's act; the refusal to recover without a stall bound; `attavo verify`'s own poll when a pass has read the checks; the synchronous `drive` calls in this repository's tests.

## Claims

- attavo/drive.py
- attavo/verify.py
- attavo/report.py
- attavo/cli.py
- tests/test_pass.py
- tests/test_drive.py
- tests/test_report.py
- tests/test_proof_five_elsewhere.py
- tests/test_record_schemas.py
- tests/test_stub_ratio.py
- tests/test_cli_help.py

## Criteria

- a-pass-returns-while-the-build-runs.sh: on a fixture tenant whose build adapter sleeps four seconds, attavo drive 1 exits 0 in under three seconds with the marker pushed and no build record yet, and the passing build record reaches origin within a minute
- a-dead-invocation-is-recovered-by-a-pass.sh: on a fixture tenant carrying a pushed build-01.started whose pid is gone, attavo drive --recover 1 exits non-zero and writes nothing, and attavo drive 1 writes and pushes build-01.json with status failed and stop class died
- the-review-reads-the-checks-the-pass-read.sh: on a fixture tenant whose checks command counts its calls, the pass that starts the review after a passed build reads the forge exactly once, on the head before its marker, and the passing verify record's forge_checks.sha is that head
- the-tests-exist.sh: tests/test_pass.py carries the nine tests named, and tests/test_drive.py the ten
