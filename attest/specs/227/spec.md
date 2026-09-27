## Problem

Approval binds to the spec's commit and is the operator's act; the product's rule is that a stage runs on that act and on nothing less. On #224 the approval was written by hand (`attavo approve` from the item's working copy, 2026-09-26) and never committed; `attavo approval-status` read it as `valid`, the pass started the build on it, the build passed, the review passed, and the merge queue's `attavo attest` was the first thing to refuse: "no approval record for item 224". Two stages ran on an act that was on no branch, and the record that would have let anyone audit them from git did not exist until the operator committed it afterwards.

The product's own path never does this: the screen posts `/approve`, the tenant's `attavo-approve.yml` runs `attavo approve` and commits and pushes the record. The hole is in the reader, not the writer. `approval_status` reads the latest approval record from the working tree and asks only whether its `spec_sha` is current; a record that exists only in the tree, or that differs in the tree from what `HEAD` carries, answers `valid`.

This item gives `approval_status` a fourth answer. `uncommitted`: the latest approval record is not in `HEAD` byte for byte, either because it was never committed or because the tree's copy has been edited. Everything that reads the function inherits the refusal with no further code: `attavo approval-status` prints `uncommitted` and exits 1; `attavo build` refuses ("approval is uncommitted") and writes no record; the pass's `drive_state` reads `no-approval` and starts nothing; the report's state is `waiting-on-approval`, kind `yours`, and its sentence says what to commit and where. The one new sentence is the report's. `attavo approve` also says, as it writes, that the record must be committed and that nothing runs on it until then.

Not in this item: `attavo approve` committing on its own (the approve workflow commits after it, and would then have nothing to commit; changing the template changes every tenant at the next pin); reading any other record from `HEAD` rather than the tree (that is the working-copy reconciler item, R5, where every stage's reads move to the branch's facts); a doctor proof.

The fixtures. 116 of the suite's tests approve an item and never commit the record, which is untrue to production, where the approval is always a commit on the branch. The stub adds `tests/helpers.py::commit_approval` (the approval record alone, `git commit --only`, the approve workflow's own shape) and calls it after each of the fifteen `approve` calls in twelve test files; `tests/test_spec_gate.py`'s halted-build fixture reads the approval's `sha` from the record instead of `HEAD`. That is the bulk of the diff and it is a fixture correction, not a behaviour change: every one of those tests asserts what it asserted before.

The stub under `stub/` is the change: `approval_status`, the report's sentence, the helper, the fixture lines, and three tests red on the tree (v0.29.0). `tests/test_approve.py::test_approve_says_the_record_must_be_committed` is red on the stub by design; the sentence is the build's. `python3 -m mypy` exits 0 on the stub.

Retires: `valid` as the answer for an approval that is on no branch; the merge queue as the first place an uncommitted approval is refused.

## Claims

- attavo/approval.py
- attavo/report.py
- tests/helpers.py
- tests/test_approve.py
- tests/test_report.py
- tests/test_build.py
- tests/test_verify.py
- tests/test_verify_polls.py
- tests/test_verify_codex_proof.py
- tests/test_drive.py
- tests/test_counsel.py
- tests/test_spec_gate.py
- tests/test_record_schemas.py

## Criteria

- an-uncommitted-approval-runs-nothing.sh: on a fixture tenant with a passing gate and an approval written but not committed, `attavo approval-status 1` prints `uncommitted` and exits 1, `attavo build 1` refuses with "approval is uncommitted" and writes no record, `attavo drive 1 --report` reads `waiting-on-approval` with a sentence naming the record to commit, a pass commits nothing and starts nothing; once the record is committed the status is `valid` and the report reads `next-build`; and the four tests named exist
