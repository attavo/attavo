## Problem

An invocation that begins and does not end leaves nothing. On 2026-09-17 two build attempts of item #148 died between passing their gates and writing their record: the first with its spawning process, the second under a live one; each left a worktree holding an unrecorded executor commit, no record, no stop, and no line anywhere the product owns. `attavo doctor` held on both, because every proof reads records and a dead invocation writes none; the operator screen rendered the row from the last record and so showed a failed attempt as the whole truth while nothing ran; recovery was three hand-written shell blocks. The product has no primitive for "this invocation began", so nothing can say "and it did not end".

Retires: the by-hand recovery of a dead or hung drive (the worktree removed, the branch reset, the events archived by a person); the screen's in-memory notion of a running drive as the only witness that one runs; silence as an outcome.

## Claims

- bin/attavo
- schema/records/build.schema.json
- schema/records/verify.schema.json
- schema/manifest.schema.json
- attavo.toml
- tests/test_doctor.py
- tests/test_drive.py
- tests/test_ledger.py

## Criteria

- doctor-fails-on-an-invocation-without-record.sh: on a fixture tenant with a build-01.started marker naming a dead pid on this host and no build-01.json, attavo doctor --json fails proof no-invocation-lacks-record naming build-01, and holds once build-01.json exists
- recover-writes-a-died-record.sh: on the same fixture, attavo drive --recover 1 writes build-01.json with status failed and stop class died, provenance product, with a log beside it, committed by the driver identity with a clean tree, after which proof five holds
- died-and-hung-are-stop-classes.sh: both stage schemas' stop enums name died and hung, the manifest schema declares [drive].stall_after_s, and bin/attavo's STOP_CLASSES names both
