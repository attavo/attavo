## Problem

Class: core
Root-cause: liveness-by-proxy

`test_two_passes_at_once_start_one_worker_and_the_loser_says_so` fails intermittently. It failed in full runs on `d4c0bc1` and `5df6926`, and it knocked #273 out of the merge queue once. When it fails, both passes print "Build started". It was filed as #289, "the pass lock admits two workers under load", and it is not a lock problem.

The lease is sound. Only one marker push can land. The defect is in how the losing pass reads the winner's live invocation, and the specimen is the test itself. Two clones of one tenant sit on one host. Pass A pushes `build-01.started` and starts its worker. Its worker writes its heartbeat under A's git dir: `stage_events_dir` is `<git-dir>/attavo`, per checkout, and per linked worktree too. Pass B then fetches and fast-forwards, sees A's marker with no record, and asks `read_invocation` whether it is running. The marker's `host` is B's own host, and B looks for the heartbeat in its own git dir, finds none, and reads "died". So it recovers, or starts a second attempt.

Load decides only the order. When B fast-forwards before A's push lands, B loses the lease, correctly. When it fast-forwards after, it misreads the heartbeat. Proof five (`proof_no_invocation_lacks_record`) makes the same assumption, and so does the signal to a hung holder, which reads its pid from this checkout's heartbeat file.

This is liveness-by-proxy again. The root cause's invariant is "a heartbeat written by the holder". Here, the host name stands in for "where the holder's heartbeat can be read". Two checkouts on one host are ordinary now. The product owns each item's working copy (#263), the screen drives from its own checkout, and a wave approved at once means several passes on one machine. Any of those can wrongly declare a running worker dead.

This item makes the marker say where its holder beats, and every reader read there.

- `write_invocation_marker` adds `events`, the absolute directory the holder's heartbeat will be written in. The pass writes the marker from a detached worktree, so `_start_stage` passes the checkout's own `stage_events_dir(root)`, never the worktree's.
- `holder_heartbeat_path(root, info, stage, nn)`, in `attavo/doctor.py`, is the one place that answers where to read the heartbeat:
  - a marker from this host that names a directory is read in that directory;
  - a marker from this host written before markers named one keeps this checkout's own directory;
  - a marker from another host returns None, since no file here holds its heartbeat.
- `read_invocation`, proof five, and the hung-holder signal all read through it.
- When a marker from this host names a directory that holds no heartbeat yet, the invocation is treated like one from another host: running while the marker is younger than the stall bound, died after. That covers another checkout's worker that has not reached its first beat. A stale heartbeat is still died, and the stall bound still decides hung, never died.
- A marker without `events` keeps the old reading exactly, so the markers already in the records read as they did.

The stub under `stub/` is the change on `fa51116`. `attavo/doctor.py` and `attavo/drive.py` change, and `tests/test_pass.py` gains three tests, all red on `fa51116`:

- the race made deterministic: a second clone's pass after the first has started reads running, starts nothing, and no second invocation is pushed;
- the heartbeat read where the marker says: fresh there, absent while young, absent when old, and a legacy marker;
- the marker a pass writes names its checkout's directory.

The flaky test passes on the change, and so does the full suite, apart from four doctor proofs that read this repository's own history, which a scratch copy cannot supply; they fail identically on `fa51116` there. `python3 -m mypy` exits 0.

Retires: a live worker in another checkout on the same host read as died; the flaky two-passes test and the merge-queue removals it caused; the host name as the answer to where a heartbeat can be read.

## Claims

- attavo/drive.py
- attavo/doctor.py
- tests/test_pass.py

## Criteria

- a-second-checkout-reads-the-holders-heartbeat-where-its-marker-says.sh: on the fixture tenant, with no test runner, `write_invocation_marker` writes `events` as the checkout's own `<git-dir>/attavo`; `read_invocation` reads a marker from this host naming another directory as running with a fresh heartbeat there, running with none yet while younger than the stall bound, and died when older; a marker naming no directory reads died as before; and `tests/test_pass.py` carries the three tests named
