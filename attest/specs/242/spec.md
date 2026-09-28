## Problem

Class: core

An item's class decides what it owes before its spec. The 2026-09-25 bridge ruling says the operator declares it at filing: core when the change touches the mechanism, fix otherwise, and a core item owes a research record. The 2026-09-27 rulings add two things. Root-cause items are core: a root cause lies behind several issues, and closing it changes the mechanism. And the research requirement sits behind a flag, off until the Plan area hosts research. No spec on `main` declares a class, and the gate has no way to read one. The spec-to-merge lane and the Plan lane both use "fix", and they mean different things by it. The record cannot say which policy an item passed under.

This item makes the class one line in the problem, `Class: core` or `Class: fix`, parsed exactly as `Root-cause:` is (#237). The gate refuses a spec with no class line, or with a class other than `core` or `fix`, before any criterion runs. It also refuses a spec that names a root cause and declares `fix`. The class is required on the pre-approval path only: an item already approved and built re-gates after an amendment as it did, so no item in flight is stranded.

The flag is `[spec].research` in the manifest, with one value, `"required"`. It sits under `[spec]` because it states what a spec owes, and it leaves the gates table generic. When it is `"required"`, a core spec owes a research record (`research-NN.json` in the item's records) before its gate passes. That is an existence check only. The record's shape, the script that writes it, and the checks against the spike belong to the deferred item P1b. Absent, which is this repository's setting and every tenant's until they choose otherwise, no research record is owed. Every spec-gate record now carries `class` and `research` (`"required"` or `"not required by policy"`), so "configured is not enforcing" is a fact on the record. `schema/records/spec.schema.json` goes to spec/3, with both fields optional, so every record written before spec/3 still validates. `attavo/types.py` mirrors them.

Fixture corrections: the twelve fixture specs under `tests/fixtures/` and the doctor's gate-rejection fixture under `attest/rejections/` each gain `Class: fix`. Without it, every fixture would be refused for its class before it reached the refusal its test asserts. #237's root-cause test declares its item `core`, since a root-cause item is core. Every test asserts what it asserted before.

After this merges, every new spec in this repository carries a `Class:` line. The spec-to-merge lane has been told.

The stub under `stub/` is the change on `c4d450f` (`main` after #237). The item was first researched speculatively on #237's stub over `6d4f8e1` and remade here once #237 landed with its rename. Six tests are red on `c4d450f`. `python3 -m mypy` exits 0 on the change, and the suite passes on it.

Not in this item (P1b, deferred until the Plan area hosts research): the `attavo research` command and the record's schema; hypotheses as scripts run in a detached copy of the spike; spikes pushed as `spike/<N>`; predecessors read from ancestry; the doctor's proof that no core item filed after the flag is set entered Pre-flight without research. Also not here: turning the flag on.

Retires: class as a word two lanes use differently with nothing that reads it; the bridge ruling's research requirement as prose that no mechanism enforced or turned off.

## Claims

- attavo/spec.py
- attavo/types.py
- schema/records/spec.schema.json
- schema/manifest.schema.json
- tests/test_spec_gate.py
- tests/fixtures/*/specs/*/spec.md
- tests/fixtures/gate_rejection/rejections/spec/specs/1/spec.md
- attest/rejections/spec/specs/1/spec.md

## Criteria

- the-gate-reads-the-class-and-the-research-policy.sh: on the fixture tenant, a spec with no class line is refused naming the class before any criterion runs; with `Class: core` the gate passes and records `class: core` and `research: not required by policy`; with `[spec].research = "required"` the same core spec is refused for its missing research record; and `tests/test_spec_gate.py` carries the six tests named
