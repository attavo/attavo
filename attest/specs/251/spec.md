## Problem

Class: core

Two sessions built this product on 2026-09-27 and neither could see what the other was changing. The collisions were found by hand: #237 claimed `attavo/spec.py` while the Plan lane was about to specify a change to the same file. They were worked out between sessions through the operator and session tooling (`frontier.py --github`, kept in the Plan lane's handoff folder, not in the product). The product has everything needed to answer the question without that: each item's spec states its Claims, and every branch in flight is a git ref. Nothing in the product reads them together.

This item adds `attavo claims [N] [--json]`, a read from git alone. Work in flight is every branch under `refs/remotes/origin/` whose tip is not on the default branch (`refs/remotes/origin/HEAD`, as everywhere else in the product). For each it gives the paths the branch changes outside `paths.records` and `paths.specs`, and, when the branch carries an item's spec, that spec's Claims, parsed by `parse_spec` itself so the two can never disagree about what a claim is. With an item, it compares the item's own Claims with every other branch in flight, names each overlap by branch and path, and exits 1 when there is one. The item's own branch is never a collision. A claim is a glob, as build's boundary reads it. Two claims overlap when either matches the other, or, where one carries a wildcard, when their literal prefixes nest: "may collide" is reported, never "cannot". With `refs/remotes/origin/HEAD` unset, it refuses by name, as `verify` and the doctor do. It writes nothing, gates nothing and blocks nothing.

This is the lightweight version of claims arbitration (the Plan Map's E5, L1) and the read the screen's frontier view will render. What it will not see, stated so no one reads its silence as clear: intent declared in an open issue before any branch exists, and work that has not been pushed. The first needs the forge, which is a later item behind a manifest command like `[forge].checks_command`, never a vendor call from the product.

The selector maps `attavo/claims.py` to `tests/test_claims.py`, so a build touching the module runs its own tests, not the whole suite.

The stub under `stub/` is the change on `00a22d0`: `attavo/claims.py`, the dispatch and help text in `attavo/cli.py`, the selector line, and `tests/test_claims.py`, whose five tests are red on `00a22d0` (no such module). The help text keeps the unknown-subcommand message's `stops, test exist` ending, which a test reads. `python3 -m mypy` exits 0 on the change, and the suite passes on it.

Retires: `frontier.py --github` as the only way to see work in flight (it stays for planned items, which live in the Plan store, until the Plan area reads them); collisions found by the operator relaying one session's claims to another.

## Claims

- attavo/claims.py
- attavo/cli.py
- .attavo/bin/select-tests
- tests/test_claims.py

## Criteria

- claims-reads-work-in-flight-and-names-a-collision.sh: on the fixture tenant with one other branch in flight touching `docs/`, `attavo claims 1` reports no collision and `--json` lists that branch alone; once the branch also touches `src/marker.txt`, inside item 1's `src/**`, `attavo claims 1` exits 1 naming `lane/9-other: src/marker.txt`; and `tests/test_claims.py` carries the five tests named
