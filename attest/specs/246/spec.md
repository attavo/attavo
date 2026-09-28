## Problem

Class: fix

The stub ratio is the share of the lines a build adds that are lines of the spec's stub at the same path. It exists to say whether the build wrote the change or shipped the spec author's prototype. It counts the stub's own test files, and a build is expected to ship those as they are: they are the item's red tests, carried into the tree. Every item whose stub carries tests reads high for a reason that says nothing about who wrote the code. Readings recomputed from the recorded diffs with this repository's selector (read on `dae6d76`; the selector and `stub_ratio` are unchanged on `3f1bd57`):

| item | recorded | today's function | with this change | test files left out |
|---|---|---|---|---|
| #206 | null (before the ratio) | 0.652 | 0.466 | `tests/test_report.py`, `tests/test_types_mirror_schemas.py` |
| #214 | 0.75 | 0.75 | 0.286 | `tests/test_stub_ratio.py`, `tests/test_testtool.py` |
| #217 | 0.546 | 0.546 | 0.218 | `tests/test_drive.py`, `tests/test_pass.py` |
| #224 | 0.926 | 0.926 | 0.859 | four test files |

The recomputed column matches every recorded value, so the reconstruction is the record's. #224 stays high with its tests left out because its stub was the change, which is the reading the ratio is for.

The product must not name a test directory or a file pattern: a tenant says what a test is through `[tests].select`. A stub file is a test when the tenant's selector, given that path alone, selects it: the path itself, or an identifier under it such as `path::name`. This repository's selector does exactly that for `tests/test_*.py` and answers `tests` (the whole suite) for anything else, including `tests/helpers.py`, which is therefore counted as code. Checked in the session on `dae6d76`: `attavo/verify.py` selects three test files and not itself; `tests/test_stub_ratio.py` selects itself; `README.md` selects nothing.

This item adds `stub_test_paths(root, stub_dir, select_command)`, which runs the selector once per stub file (bounded at 60 s each, through `run_bounded`), and gives `stub_ratio` a `tests` argument whose paths are left out on both sides of the ratio, as the specs and records directories already are. When the tests cannot be told apart (no `[tests].select`, or the selector exits non-zero or times out) the ratio is null: not measured, which is not a number. Both write sites in `cmd_verify` pass the stub's test paths.

The 2026-09-25 bridge ruling says the stub's tests are excluded from the ratio's numerator. This spec leaves them out of both sides. Left out of the numerator alone, the shipped tests would still count in the denominator as lines the build wrote, and the ratio would understate the build's copying by the size of the test files. That is a reading of the ruling, stated for the operator to confirm at approval.

Two fixture corrections. The fixture tenant's selector is `cat`, which declares every path a test of itself. Under this change every file in its stub is left out and the ratio reads null. `tests/test_stub_ratio.py`'s two verify tests now set a selector that selects only `tests/` before they run, and they assert what they asserted before (0.0 and 1.0).

Not in this item, split on 2026-09-27: freezing the stub's test bodies so a build cannot weaken a red test. Which unit is frozen (file or test function) and how a tenant-generic product finds a test body is a design question, so it is an item of its own, proposed core. Also not here: a record field naming the files left out (a schema bump for a value a re-run reproduces), and tenant zero, which files no new items and whose ratio is computed only at verify.

The stub under `stub/` is the change: `attavo/verify.py` and `tests/test_stub_ratio.py`, four tests red on `3f1bd57`. `python3 -m mypy` exits 0 on the stub, and the full suite passes on it.

Sequencing, from the Plan frontier: nothing in flight claims `attavo/verify.py` or `tests/test_stub_ratio.py`. #229 (merged) made a build pass only on committed work; this item's merge block still checks that the branch carries `stub_test_paths` before it merges.

Retires: the stub's red tests read as prototype code the build chose to copy; #214's 0.75 and #217's 0.546 as readings of anchoring.

## Claims

- attavo/verify.py
- tests/test_stub_ratio.py

## Criteria

- the-stubs-tests-are-left-out-of-the-ratio.sh: with a stub of one module and one test file and a selector that selects `tests/test_*` for themselves, `stub_test_paths` answers the test file alone; the ratio over a diff adding both files at the stub's lines plus one line of the builder's is 0.5 with the tests left out; it is null when the tests cannot be told apart, with no selector, or with a selector that exits 3; and `tests/test_stub_ratio.py` carries the four tests named
