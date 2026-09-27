## Problem

On 2026-09-27 item #227 was specified, approved at a commit, built by one invocation, judged by another, merged through the queue and sealed in the attestation store, and its change does not exist. The executor copied the item's stub into the working tree, ran tests in the background, answered `done`, and never committed (83 events, 32 tool calls, none of them `git add` or `git commit`). The build's gate ran the criteria, the selected tests and the tenant's checks in that working tree and passed them; the build record committed itself with `--only`, `sha` equal to `start_sha`; the review read a build that changed nothing and passed it; `attest` on the merge group checked the chain and sealed it; six doctor proofs held over it. Main carries `attest/specs/227/` and `attest/records/227/` and not one line of the change.

Every statement in that attestation is true and the attestation is worthless, because two things were never asked. Whether the gate reads the commit: it read the executor's directory, where a file written and never committed is indistinguishable from one that is on the branch. Whether a build built anything: nothing in build, verify, the pass or the doctor compares `sha` to `start_sha`.

This item asks both.

The gate reads the commit. Each iteration's criteria, selected tests and tenant checks run in a detached worktree of `HEAD` (`gate_checkout`: `git worktree add --detach`, removed when the iteration's gate is done), never in the executor's directory. A file written and not committed cannot pass a criterion, a test or a check on the executor's behalf; a tenant checks command that writes beside itself writes into the worktree and is gone with it. The pre-executor probe (`[checks].probe`) still runs against the approved tree in place, as it did.

An empty iteration is told so. When an iteration ends with `HEAD` equal to `start_sha`, the gate runs (on the approved tree, where the criteria already fail), the log and the feedback both carry "you committed nothing: no commit since <start>; N path(s) modified or untracked in the working tree, which the gate does not read" and "commit your work; the gate reads the commit, not the tree", and the loop continues. An executor that answers `done` before committing on its first iteration and commits on its second passes as before (`test_an_empty_iteration_is_fed_back_and_a_later_commit_passes`).

An empty build is halted. When the last iteration ends with `HEAD` equal to `start_sha`, the record is `halted` with a new stop class `empty`, provenance `executor`, detail "the executor committed nothing; ..." with the count of paths left in the tree. The class is the executor's, so counsel reads it and a steer retries it under R3; no new family. `STOP_CLASSES` and the build and verify schemas carry `empty` (build/9, verify/8). The status can never be `pass` with `sha` equal to `start_sha`.

Verify refuses an empty build. A passing build record whose `sha` equals its `start_sha` is refused before any adapter runs: "the latest build record names no change (sha equals start_sha ...); nothing to review". The build no longer writes such a record; the refusal is verify's own guard against any record shaped this way, whoever wrote it.

The doctor's seventh proof: no attested item is empty. For every merged, fully-staged item, the passing build's `sha` is not its `start_sha`. `[doctor].built_since` names a commit at or before which merges are before the window: named on every run, never failed, so #227 stands on the record as the specimen and the tenant is not red forever. Absent, the window is proof three's. A build record with no `start_sha` (written before the field) is counted as not readable, not failed. This repository declares `built_since = "dae6d765f3d6"`, #227's merge. The proof's test is dogfood like the other five and is deselected from `[tests].run` and the CI test job the same way; the doctor job runs it.

Not in this item: re-doing #227's change (its own item, filed on this product once merged); a ruling record voiding an attestation (R4); reading records from `HEAD` in every stage (R5); an executor-side change to the adapter (the instruction already says commit and stop; a mechanism is what was missing, not a sentence).

The stub under `stub/` is the change: `gate_checkout` and the empty-iteration handling in `attavo/build.py`, the class in `attavo/stops.py` and the two schemas, verify's refusal, proof seven and the manifest key, the fixture adapter's `ATTAVO_FIXTURE_NO_COMMIT`, the self-tenant's window and deselect, and seven tests red on the tree (v0.29.0). `tests/test_cli_help.py::test_help_names_the_seventh_proof_and_the_empty_stop` is red on the stub by design; the help text is the build's. Four existing tests move with the gate: two in `tests/test_testtool.py` whose checks counter lived in the tree the gate no longer reads, `test_self_tenancy.py`'s assertion that the manifest has no `[doctor]` table, `test_own_records_guard.py`'s deselect count, and `test_stub_ratio.py`'s pinned `verify/7`. `python3 -m mypy` exits 0 on the stub.

Retires: the working tree as what the build's gate reads; `pass` as a status a build with no commit can hold; a review of a build that changed nothing; a definition of done with no word about whether anything was built.

## Claims

- attavo/build.py
- attavo/verify.py
- attavo/stops.py
- attavo/doctor.py
- attavo/cli.py
- schema/records/build.schema.json
- schema/records/verify.schema.json
- schema/manifest.schema.json
- adapters/fixture/run
- attavo.toml
- .github/workflows/ci.yml
- tests/test_build.py
- tests/test_verify.py
- tests/test_doctor.py
- tests/test_cli_help.py
- tests/test_testtool.py
- tests/test_self_tenancy.py
- tests/test_own_records_guard.py
- tests/test_stub_ratio.py
- tests/test_stops.py
- tests/test_record_schemas.py
- tests/test_types_mirror_schemas.py

## Criteria

- an-executor-that-commits-nothing-does-not-pass.sh: on a fixture tenant whose executor writes the change into the tree and never commits, `attavo build 1` ends halted with stop class `empty`, provenance `executor`, `sha` equal to `start_sha`, every criterion non-zero, the log saying the executor committed nothing and that the gate reads the commit, and `attavo verify 1` refuses the halted build with no review record written
- a-passing-build-record-that-names-no-change-is-refused-by-verify-and-the-doctor.sh: a passing build record edited so that `sha` equals `start_sha` is refused by verify by name with no review record; merged with its item's other records it fails the doctor's `no-attested-item-is-empty` proof naming the item and the merge; with `[doctor].built_since` at that merge the proof holds and names the item before the window; this repository's manifest declares `built_since` and the seven tests named exist
