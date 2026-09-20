## Problem

Three things stand between `main` and a release that tenants can pin, all found by the product's own reviews.

Proof six holds a sealed statement to its records (#204). Its review found two ways a false statement still holds: a statement carrying `"time": null` and no `cost` is read as sealed before the accounting existed, because absence is tested by value and not by key, and is not checked at all; and both sides of every number are converted to float before they are compared, so an integer one past 2**53 equals the float beside it, where the spec says exactly. No sealed statement carries these fields until the release after this one, so the fixes land before any immutable statement can meet the defects.

Every review record carries `weak_stub`, `stub_ratio`, and `mutation`, and `attavo verify` writes false, null, and null at both of its write sites: three measurements no release has computed. They said "fine" on #194 and #204, where the build shipped the spec author's stub almost line for line and the review judged that author's code without knowing it; on #206, the first item with a thin stub, the review found nothing wrong with the code. `stub_ratio` becomes real: the share of the lines the item's diff adds, outside the specs and records directories and blank lines aside, that are a line of the stub's file at the same path. `weak_stub` and `mutation` are deleted until something earns them; the review record's schema moves to version 6.

The stub under `stub/` is thin by the operator's ruling of 2026-09-20: its `stub_ratio` answers 0.0 without reading the diff and it does not touch `attavo/doctor.py`, so six of the eight tests named below fail on it. The two proof-six fixes have no criterion of their own; the required `test` check runs the two tests the review named, and the second criterion keeps them from being dropped. `python3 -m mypy` exits 0 on the built tree with no `type: ignore`; the builder runs it last, after its final change to any file.

Retires: `weak_stub`; `mutation`; `stub_ratio` as a constant; the float conversion in `_same`; absence tested by value.

## Claims

- attavo/doctor.py
- attavo/verify.py
- attavo/types.py
- schema/records/verify.schema.json
- tests/test_stub_ratio.py
- tests/test_attest_time.py
- tests/test_file_findings.py
- tests/test_verify.py
- adapters/claude-code/run
- adapters/codex/run

## Criteria

- verify-records-a-stub-ratio-and-drops-two-fields.sh: on a fixture item built and reviewed on its own branch, verify-01 passes and carries stub_ratio as a number from 0 to 1 and neither weak_stub nor mutation; schema/records/verify.schema.json is at version 6, requires and describes stub_ratio, and declares neither of the other two
- the-pre-release-fixes-have-their-tests.sh: tests/test_attest_time.py carries the two tests #204's review named, tests/test_stub_ratio.py carries its six by name, and neither attavo/verify.py, attavo/types.py, nor the review record's schema names weak_stub or mutation
