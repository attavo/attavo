## Problem

Class: fix

`attavo plan claims` reads the wall clock twice for one write. `attavo/plan.py::set_claims` stamps the claim with `now_iso()`, and `attavo/store.py::put` stamps the version envelope with a second `now_iso()`. `now_iso` has one-second resolution, so when the two calls straddle a second the claim's `at` and the version's `at` differ.

Specimen: lane orange's full run for #336 at 14dc209, under `-n auto`, failed `tests/test_plan.py::test_plan_claims_writes_the_draft_claims_beside_the_order_and_clear_removes_them`, which asserts the two are equal. It passes alone. The test is right; the product is wrong. Root-cause: the clock read is in the product, not the test: one write takes two readings of the time and assumes they agree.

The change: one plan write reads the clock once. The stamp is taken once per write and used for both the claim entry and the store version, so they agree by construction. No other caller of `store.put` changes its output.

The stub is thin: it adds the rejection test to `tests/test_plan.py`, red on the stub. The build writes the logic.

Retires: the second clock read in a plan write.

## Claims

- attavo/plan.py
- attavo/store.py
- tests/test_plan.py
- tests/test_store.py

## Criteria

- [structural] the-rejection-test-exists.sh: tests/test_plan.py carries test_a_plan_write_stamps_the_claim_and_the_version_from_one_clock_read
- [behavioural] a-claims-write-reads-the-clock-once.sh: with the clock forced to a different second on every read, attavo plan claims writes a claim whose at equals its store version's at
