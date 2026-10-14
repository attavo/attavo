## Problem

Class: fix

A behavioural criterion passes the gate on any failure, including a failure of its own setup. Under #340 a behavioural criterion only has to fail on the tree and on the tree plus stub, so a script that cannot set up its scenario fails on both and the gate accepts it. The build can then never satisfy it: prose with an exit code.

Specimen: #349's behavioural criterion exited 2, its own code for "the scenario could not be set up", on the tree and on the stub in the CI gate; v0.34.0 accepted 2/2 as "fails on both". Only its push script, on the operator's Mac, had required exactly 1. A second: `grep -q` on a missing file exits 2, so a structural criterion written that way cannot be told from a crash.

The change: exit codes are a contract. A criterion exits 0 when it holds, 1 when its assertion fails, and anything else when it could not check. Before approval, every criterion must exit exactly 1 on the tree, and a behavioural one exactly 1 on the tree plus stub; any other non-zero exit is refused before kinds are judged, as "criterion could not check (exit N): <name>". After a build, a criterion whose exit is neither 0 nor 1 is refused the same way, never judged. The spec-gate help says so. Merged specs are not re-gated and are left as they are.

The stub is thin: three tests in `tests/test_spec_gate.py`, red on the stub (the two refusals) and one holding the thin-stub pass with criteria that exit exactly 1. The build writes the logic.

Retires: "fails" meaning any non-zero exit.

## Claims

- attavo/spec.py
- tests/test_spec_gate.py

## Criteria

- [structural] the-rejection-tests-exist.sh: tests/test_spec_gate.py carries test_a_behavioural_criterion_that_exits_other_than_1_on_both_is_refused_by_name and test_a_structural_criterion_that_exits_other_than_1_on_the_tree_is_refused_by_name
- [behavioural] a-criterion-that-could-not-check-is-refused.sh: on the valid fixture, a behavioural criterion that exits 2 on the tree and on the stub is refused with "criterion could not check (exit 2): broken.sh", with the record's refusal saying the same
