## Problem

Class: fix

The spec gate passes a spec that asserts nothing. A spec whose Criteria section is empty, with no `criteria/` directory, gates `pass (0 criterion/criteria)` and can be approved: an approval bound to no executable claim, and a build that any tree satisfies. Specimen: the operator's probe of 2026-10-06 against 36f68c0, on the `build_valid` fixture with its Criteria emptied, printed `status: pass criteria: [] refusal: None`. The prose check compares the listed criteria with the files in `criteria/`, so zero and zero match, zero criteria run, and nothing fails. #341's "no structural criterion" rule applies only when there is at least one criterion.

Before approval, a spec with no criteria is refused before any criterion runs, with "criteria: empty; the spec asserts nothing", beside today's "claims: empty; the build may touch nothing". The built path is unchanged.

The stub is thin: it adds the rejection test to `tests/test_spec_gate.py` and nothing else. The test is red on the stub, and the build makes it pass.

Retires: an approvable spec with nothing to check.

## Claims

- attavo/spec.py
- tests/test_spec_gate.py

## Criteria

- [structural] the-rejection-test-exists.sh: tests/test_spec_gate.py carries test_a_spec_with_no_criteria_is_refused_before_any_criterion_runs
- [behavioural] a-spec-with-no-criteria-is-refused.sh: on the build_valid fixture with its Criteria emptied and criteria/ removed, attavo spec-gate exits non-zero and prints "criteria: empty; the spec asserts nothing"
