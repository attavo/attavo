## Problem

Class: fix

The reviews of the 2026-10-14 wave passed with four findings, none blocking, each a few lines. They are one item because each is small and they share a wave.

1. #339's rejection test proves the claim's `at` equals the version's `at`, not that the clock is read once. A call count on `now_iso` for one `plan claims` write proves the stronger claim (the product already reads once; the test is what is missing).
2. `store.put`'s own stamp, when the caller passes no `at`, has no regression test.
3. #351's review read the gate as judging criterion kinds before the exit-code contract. The spec's own push found the contract is already judged first: a structural criterion that exits 1 on the tree and 2 on the stub is refused "criterion could not check (exit 2)" on main today. The finding needs no logic; its test pins the order.
4. `attavo/types.py` types an intervention's `class` as `str`; the schema is the enum `amend`, `launch`, `transport`. A TypedDict for an intervention carries it as a `Literal`, and the mirror test reads enums against literals.

The stub is thin: the four tests, one per finding, in their files; the one that needs logic (4) is red on the stub. The build writes the logic.

Retires: four gaps a second reader found that the first reader's tests did not cover.

## Claims

- attavo/spec.py
- attavo/types.py
- tests/test_plan.py
- tests/test_store.py
- tests/test_spec_gate.py
- tests/test_types_mirror_schemas.py

## Criteria

- [structural] the-rejection-tests-exist.sh: the four tests exist, one per finding, in tests/test_plan.py, tests/test_store.py, tests/test_spec_gate.py and tests/test_types_mirror_schemas.py
- [behavioural] the-intervention-class-type-is-the-schemas-enum.sh: a TypedDict in attavo.types for an intervention types class as the Literal of the schema's enum
