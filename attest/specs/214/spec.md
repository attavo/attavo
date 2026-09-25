## Problem

The reviews of #208 and #212 filed four findings against code the product now runs. Three are defects and one is closed by a later item.

1. A returned review record (the forge's checks not green) writes `stub_ratio` as a constant null (#208's review). The ratio is a function of the diff and the stub, neither of which depends on the forge, and a returned record is the one an operator reads when asking why review did not run.
2. `attavo build` builds the executor's environment from its own, so `ATTAVO_CHECKS_COMMAND` exported by whoever launched the build reaches the executor, restoring the command #212 retired (#212's review).
3. This repository's selector returns nothing for a deleted test file (#212's review): the gate could pass a deletion. A file that is gone cannot select itself, so the fail-safe rule the operator ruled on 2026-09-20 applies: the whole suite.
4. The builder recorded a pass after its own type check failed to run (#208's review). Closed by #212, whose gate runs the type check on the final tree; nothing here changes for it, and the finding is cited as closed.

The stub under `stub/` is thin: a returned record carries 0.0 whenever a stub exists, a deleted test file selects itself, and the build's environment is untouched; all three tests named below fail on it. `python3 -m mypy` exits 0 on the built tree with no `type: ignore`.

Retires: the constant null at the returned write site; the exemption of a deleted test file from the fail-safe rule; the inherited command.

## Claims

- attavo/verify.py
- attavo/build.py
- .attavo/bin/select-tests
- tests/test_stub_ratio.py
- tests/test_testtool.py

## Criteria

- a-returned-review-carries-the-ratio.sh: on a fixture item whose forge reads red, attavo verify writes a returned record whose stub_ratio is a number, not null
- a-deleted-test-file-selects-something-and-the-tests-exist.sh: this repository's selector answers a non-empty selection for a test file that does not exist and the file itself for one that does; tests/test_stub_ratio.py and tests/test_testtool.py carry the three tests by name
