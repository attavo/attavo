## Problem

The product cannot say what an item is waiting on. `attavo drive N` decides from the item's records and one read of the forge, and then either acts or prints one line into a log and exits: on #194 a concluded red check made it print "stopped: forge checks not green", exit 1, and write nothing, twice, while the operator screen showed a Launch button (#171). The decision is already a pure function (`drive_state` plus `read_forge_checks`); nothing exposes it without also acting on it, so every view derives its own answer and a refusal is an event nobody recorded instead of a level anybody can read.

This is R1 of the reconciler (`claude/reconciler-brief-2026-09-20.md`, rulings A and G, 2026-09-20): `attavo drive N --report` prints, as JSON against `schema/drive-report.schema.json`, the item's state, whose move it is (`yours`, `waits`, `acts`, `done`), and one sentence in the operator's words. It writes nothing, starts nothing, and never waits: it reads the forge once and does not call `poll_forge_checks`. No behaviour of `attavo drive N` without the flag changes. The states are the ones today's product can be in; attempt budgets, the breaker, and rulings arrive with R3 and R4 and add states then.

A new reader must not wedge on a record that already exists (#194's `verify-01`): the report returns a state for every item this repository's records directory carries.

Retires: #171 (a refusal is the report's current level, recomputed on every read, so there is nothing to record); any view's own derivation of what an item is waiting on (deleted by a screen item filed against this one once a release carries it).

## Claims

- attavo/report.py
- attavo/drive.py
- attavo/types.py
- attavo/cli.py
- schema/drive-report.schema.json
- tests/test_report.py
- tests/test_types_mirror_schemas.py
- tests/test_cli_help.py

## Criteria

- report-names-a-red-check-and-writes-nothing.sh: with a built item whose head carries a concluded red check, drive 1 --report exits 0, prints state blocked-on-checks, kind waits, stage verify, the check by name and conclusion, and a sentence naming the check and the head that says neither "drive" nor "verify"; HEAD, the working tree, and the item's records directory are the same before and after
- report-never-waits.sh: with a check still in_progress and the drive's poll bound set to 30 s, the report returns in under 10 s with state waiting-on-checks and the check named
- report-walks-an-item-and-names-whose-move-it-is.sh: across one item's life the report reads waiting-on-approval yours, next-build acts, owes-recovery acts naming build attempt 1 as died (and writes no record), stopped yours naming build-01, its class, and its filed issue, next-review acts, and ready-to-merge yours
- the-report-has-its-contract-and-its-tests.sh: schema/drive-report.schema.json closes its properties, requires every one, and enumerates the eleven states and four kinds; DriveReport mirrors it in tests/test_types_mirror_schemas.py; tests/test_report.py carries the six tests by name
