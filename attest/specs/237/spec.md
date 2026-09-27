## Problem

Bucket: dogfood-tests-told-apart-by-a-list

Item #229's fourth attempt ran 3,375 s and cost $2.62 to fail on one test. The executor's change touched `attavo/common.py` and `attavo/build.py`, `select-tests` mapped that to the whole suite, and the gate ran `tests/test_doctor.py::test_no_invocation_lacks_record` in its worktree, where that test read build-04's own `.started` marker as a dead invocation. The test is one of the seven that read this repository's own records and belong to the doctor job alone. It was the one the deselect lists did not name.

There were three of those lists: `[tests].run` in the manifest, the `test` job in `ci.yml`, and `DESELECT_FLAGS` in `tests/test_self_tenancy.py`, each naming six tests by hand, kept equal by a test that counted them, and a fourth in the doctor job's `-k` expression naming five. Meanwhile the marker that says what these tests are, `@pytest.mark.own_records`, already existed: the inherit path runs `-m own_records` alone, and `tests/test_own_records_guard.py` fails any unmarked test that reads the repository's own records, with the six listed tests exempted precisely because the list stood in for the marker. Two mechanisms, disjoint sets, and the list was the one that went stale. A list a person copies is prose; a marker on the test is the fact.

This is the first fix item filed under the convergence ruling of 2026-09-27: defects are classified by the root cause that made them possible, each fix closes a bucket by making the state unrepresentable rather than adding a check, and the number read over time is the count of distinct buckets, not of symptoms. The item carries the mechanism with it.

One marker, no list. The six doctor tests gain `@pytest.mark.own_records` beside the one that had it. `[tests].run` becomes `xargs -r python3 -m pytest -q -m 'not own_records'`, `ci.yml`'s full-suite step carries the same expression, and the doctor job's step becomes `python3 -m pytest tests -q -m own_records`, the same step the inherit path already runs. Every `--deselect` in the manifest and the workflow goes, `DESELECT_FLAGS` and its test go, the guard's exemption and the test that counted the list go. What remains is the guard, which now exempts nothing, and one new test naming the set: every test in `test_doctor.py` that calls `run_doctor(ROOT)` carries the marker. `attavo test` uses `[tests].run` and so inherits the expression; an executor that names a marked test by hand gets nothing run, which is right.

The bucket line. A fix item's spec names its bucket as one line in the problem, `Bucket: <slug>`. The tenant's closed list is `buckets.md` beside its specs directory (`attest/buckets.md` here), one bucket per line, `- <slug>: <one sentence>`. `attavo spec-gate` refuses a spec whose bucket line names a slug the file does not list, or names any slug when the file does not exist, before any criterion runs; a spec with no bucket line is not asked for one, since a capability item has no bucket. Adding a bucket is an item's own act in the same change as its fix. The count is read with `grep -h '^Bucket:' attest/specs/*/spec.md | sort | uniq -c`; it becomes a subcommand when it moves a decision and not before. This repository's `attest/buckets.md` lists the five buckets the ruling named, with their specimens.

Not in this item: the driver's records-only commits, the classifier's order, the marker's pid, the adapter's tools (buckets 1, 5, 4 and 2, each its own item); any change to what the doctor's proofs assert; the analyze read of bucket counts.

Retires: three hand-copied deselect lists and the test that counted them; the doctor job's `-k` list; the guard's exemption; symptom-grain fix items without a named bucket.

## Claims

- attavo/spec.py
- attavo/cli.py
- attavo.toml
- .github/workflows/ci.yml
- attest/buckets.md
- tests/test_doctor.py
- tests/test_own_records_guard.py
- tests/test_self_tenancy.py
- tests/test_ci_inherit.py
- tests/test_spec_gate.py
- tests/test_cli_help.py

## Criteria

- the-own-records-tests-are-told-apart-by-the-marker-and-the-gate-refuses-an-unlisted-bucket.sh: `[tests].run` and `ci.yml`'s full-suite step carry `-m 'not own_records'` and no `--deselect`; the doctor job and the inherit path run `-m own_records`; every `run_doctor(ROOT)` test in `test_doctor.py` carries the marker; the guard reads no deselect list; `attest/buckets.md` lists the five buckets; on a fixture tenant a spec with `Bucket: liveness-by-proxy` is refused naming the missing `buckets.md`, refused again naming the unlisted slug once the file lists another bucket, and passes once the slug is listed, with the refusals written before any criterion ran
