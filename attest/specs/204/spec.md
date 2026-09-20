## Problem

Proof six holds each sealed statement to its records, and the release that adds the statement's `time` and `cost` (#194) checks one number of them loosely. The review of #194 filed both halves. #202: `_time_disagrees` allows `abs(stated - summed) <= 1.0`, so a statement saying 398.75 of records that sum to 398.25 holds, although both sides are the same function's rounding of the same records and cannot honestly differ at all. #203: only `time.invocation_s` is compared, so the per-stage seconds and attempts, `lead_s`, `waiting_s`, `partial`, `not_read`, and every field of `cost` can say anything while the proof holds. No sealed statement carries `time` yet (the pinned release prints none), so this lands before the first one that does and wedges nothing that exists.

The check this replaces also excepted nothing on purpose but could not have checked `lead_s`: it passed no seal time. The statement is computed at the merge commit (read on #194's own sealed statement: its `sha` is the merge on `main`), so the doctor recomputes `lead_s` and `waiting_s` from that commit's committer time and excepts no field.

Two rules keep a sealed, immutable statement from ever wedging the doctor, the lesson of #194's `verify-01`: numbers are compared as numbers (a store may print 40.0 as 40), and only the keys a statement carries are compared, so a field a later release adds is not demanded of a statement sealed before it. A key the statement carries that the function does not produce is a disagreement, as is one of `time` and `cost` without the other.

Retires: `_time_disagrees` and its one-second tolerance; the comparison of one field standing for the accounting; #202; #203.

## Claims

- attavo/doctor.py
- attavo/attest.py
- tests/test_attest_time.py
- tests/test_doctor_sealed.py

## Criteria

- doctor-fails-a-subsecond-disagreement.sh: on a fixture item whose records sum to 398.25, proof every-attested-item-is-sealed fails a sealed statement whose time.invocation_s says 398.75, naming item 1 and time.invocation_s
- doctor-fails-every-field-that-is-not-its-records.sh: the proof holds on the statement attest printed, and on the same statement with 40.0 printed as 40 and one key absent; it fails, naming item 1 and the field, when any of time.build.invocation_s, time.lead_s, time.waiting_s, time.not_read, cost.usd, cost.by_adapter_usd, cost.partial, or cost.not_read is not what the records sum to while time.invocation_s stays true, when the statement carries a cost key the function does not produce, and when it carries time without cost; it does not check a statement that carries neither
- the-whole-accounting-has-its-tests.sh: tests/test_attest_time.py carries the three tests by name, attavo/doctor.py has no _time_disagrees and no tolerance, and calls item_time_and_cost exactly once
