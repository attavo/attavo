## Problem

Every build, verify, and counsel record carries `wall_clock_s` and `cost_usd`, and nothing in the product sums them. The attestation statement carries the final attempt per stage and no more, so a view derived its own figure: on 2026-09-18 the operator screen showed #158 as 20 minutes of product time (its final build plus its final verify, no counsel) where its nineteen invocations ran 114.9 minutes (builds 5,570 s, verifies 909 s, counsels 412 s) against 2,021 minutes of lead time. The item spent six percent of its lead time inside an invocation, which is the number that says where the next minute is worth removing, and no record carried it. The operator ruled 2026-09-19 that this accounting lives in the product; and, the same day, that dollars are summed across stages and executors with a subtotal per executor beside the total (superseding the 2026-09-09 ruling against a cross-executor sum), a total that lacks any record's cost saying so by name. Pricing a record whose executor reports no cost, from a published rate per token shown in Configure, is the cost-basis item and is not this one.

Retires: any view's own arithmetic over records as the source of an item's durations or cost (attavo/screen's Analyze derivation, deleted by a screen item filed against this one); "product time" as a name for the final attempt; the statement's silence about every attempt but the last.

The statement for this item's own merge is printed by the pinned release and carries no `time`; the first statement that does is sealed after the release that ships this and the pin to it. Proof six does not check a statement that carries no `time`.

## Claims

- attavo/attest.py
- attavo/doctor.py
- attavo/types.py
- schema/attestation.schema.json
- tests/test_attest_time.py
- tests/test_attest.py
- tests/test_doctor_sealed.py

## Criteria

- attest-sums-every-invocation.sh: on a fixture item with three builds, two verifies, and one counsel, attavo attest prints a statement valid against schema/attestation.schema.json at $id v3 whose time block carries attempts and invocation_s per stage (350.75, 40.0, 7.5), invocation_s 398.25 for the item, lead_s from approval-01's at to the merge's committer time, waiting_s as their difference, and leaves wall_clock_by_stage_s as the final attempt
- attest-names-a-record-without-wall-clock.sh: when build-02 carries no wall_clock_s, time.not_read is ["build-02"], time.build.partial and time.partial are true, time.build.attempts is still 3, and the missing value is never summed as zero
- attest-sums-cost-across-executors.sh: cost.usd is the sum of every record's cost_usd across stages and adapters with by_stage_usd and by_adapter_usd beside it; while one adapter's records carry no cost the total is partial and not_read names each such record; once they carry one the total is whole and both adapters have a subtotal
- doctor-fails-a-statement-whose-time-is-not-its-records.sh: proof every-attested-item-is-sealed holds on a sealed statement whose time.invocation_s equals its records' sum, fails naming item 1 and time.invocation_s when the statement says 20.0, and holds on a statement sealed before time existed
- the-accounting-has-its-tests.sh: tests/test_attest_time.py carries the four regression tests by name, attavo/types.py's AttestRecord mirrors the schema's properties, and the schema requires time and cost
