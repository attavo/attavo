## Problem

Class: core

The statement says no person intervened, whatever happened. `attavo/attest.py` writes `"interventions": []` and `"acts": []` into every statement, hard-coded ("Neither is populated by anything today"). An empty list is not an absent field: it states that there were none. The product's claim is a change merged with every intervention on the record, and no statement it seals keeps that claim.

Specimen: #344's v5 statement, merged as PR #345 at d2354e27, carries `acts: []` and `interventions: []`. The ledger for the same item (`attavo ledger`) shows `operator acts: 4`: the approval (22b70a8), two merges of main into the item's branch made mid-run by the operator (8d4fd15, 19c384f), without which the item would neither have gated nor reviewed, and the merge (d2354e27).

Delete first: the hard-coded empty lists.

The classification already exists. `attavo/ledger.py::item_ledger` gives each commit between the item's first spec commit and its merge exactly one class. The statement reads those same rows; nothing classifies twice.

- `acts` lists the operator's ruled acts on the item: each `approve` row and the `merge` row, as `{act, sha, by, at}`. `rule` stays in the schema's enum for the ruling record (#276) and is not produced here.
- `interventions` lists every other operator row (`amend`, `launch`, `transport`) as `{class, sha, by, at}`, with `by` the commit's author name and `at` its committer time.
- `sha` becomes required in both item shapes. The predicate moves to `attestation/v6`. v3, v4 and v5 statements read as sealed, and nothing sealed is rewritten.
- Doctor proof six recomputes both lists for a v6 statement from the commits at the sealed commit, as it does for time and cost. A statement whose lists differ fails by name.

The stub is thin: it adds `tests/test_attest_interventions.py`, the rejection test, which builds the specimen's shape on the `build_valid` fixture (a merge of main into the item's branch between approval and build) and is red on the stub. The build writes the logic.

Retires: an attestation that states there were no interventions without reading for any.

## Claims

- attavo/attest.py
- attavo/doctor.py
- attavo/ledger.py
- attavo/types.py
- schema/attestation.schema.json
- tests/test_attest_interventions.py
- tests/test_attest.py
- tests/test_attest_time.py
- tests/test_estimated_cost.py
- tests/test_doctor.py

## Criteria

- [structural] the-rejection-test-exists.sh: tests/test_attest_interventions.py carries test_a_transport_between_approval_and_merge_is_an_intervention
- [behavioural] a-transport-mid-run-is-on-the-statement.sh: on the build_valid fixture, a merge of main into the item's branch made between approval and build is listed in the statement's interventions by its sha with class transport, and acts carry approve and merge
