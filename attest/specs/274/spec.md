## Problem

Class: fix

`attest/root-causes.md` is the closed list a fix names with a `Root-cause:` line, and the count of distinct causes named by merged fixes is the convergence reading. The list says what each cause is but not which defects it made possible, so whether every known defect fits a listed cause, the evidence that the list is closed, lives in no file a script reads. Thirty defects have been found since 2026-09-25; they were mapped to the six causes, the screen, or a ruling in the operator's session of 2026-09-29, and that mapping exists only there.

One specimen in the list is wrong as written. The shared-working-copy line names `tests/test_retry.py::test_counsels_verdict_routes_the_next_pass` (8 of 40 failures with passes every 0.5 s beside a committing worker, #257), but #262 could not reproduce the collision on main in 30 runs, so the attribution is unconfirmed and the line must say so.

This item adds a `## Specimens` section: one table row per defect, numbered 1 to 30, naming the listed cause (or `unconfirmed`, `none: a ruling`, `none: the screen's`) and the state. It qualifies the shared-working-copy line. It adds no cause and changes no cause's line otherwise. The table's rows begin with `|`, never with `- `, so the spec gate's reading of the list (lines of the form `- <slug>: <sentence>`) still finds exactly six causes.

The stub under `stub/` is the change: the whole file as it should read.

Retires: the defect-to-cause mapping as a conversation nobody can read; the unconfirmed specimen stated as confirmed.

## Claims

- attest/root-causes.md

## Criteria

- the-specimens-are-in-the-closed-list.sh: the spec gate's own reader finds exactly the six causes; the file has a Specimens section with rows 1 to 30; every row names a listed cause, `unconfirmed`, a ruling or the screen; every listed cause has at least one row; rows 19, 23 and 24 are rulings; row 17 is unconfirmed and says it was not reproduced on main; the shared-working-copy line says the same
