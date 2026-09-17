## Problem

`attavo ledger --json` on tenant zero's twelve items runs 537 git processes and takes 8.7 s, read on 2026-09-17 with cProfile and a `GIT_TRACE` file: `merge_introducing_records` diffs every merge reachable from HEAD, per item, until it finds the one; `_commit_touches` runs one `git diff` per walked commit; `_record_at` runs one `git show` per record. Git answers each in 16 ms; the process is the cost, and it grows with every commit and record the corpus gains. The operator screen pays the whole of it on every read of Execute, Analyze, and a detail, so the ledger's ten seconds are the screen's ten seconds.

Retires: the git process per merge, per commit, and per record in the ledger's walk.

## Claims

- bin/attavo
- tests/test_ledger.py

## Criteria

- ledger-six-items-under-sixty-git-processes.sh: on a six-item corpus the script builds with git, attavo ledger --json lists every item's rows and runs at most sixty git processes, counted from a GIT_TRACE file
- ledger-records-cost-no-git-process.sh: an item with twelve records costs the ledger no more git processes than the same item with four, within two
