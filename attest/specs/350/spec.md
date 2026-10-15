## Problem

Class: fix

The ledger's rows carry a sha, an identity and a class, and nothing about what the commit holds. Readers then guess. The screen derives a "record file" column from each commit's diff and reads "not read" for commits that carry something nameable: invocation marker commits (`build-01.started`), a person's transport commits (nothing, by design), and the merge, whose record is the item's statement in the forge's attestation store, not a file in the tree. Specimen: #344's ledger in Analyze on 2026-10-07, five rows "not read". "Not read" should mean a source did not answer.

The change: every row carries `carries`, the repository-relative paths the commit touched under the records and specs paths (and, for the executor's change rows, every path it touched); a transport row carries `[]`. The merge row also carries `statement`: `{predicate_type, subject}` read through the manifest's `[forge].attestation_command` with the merge sha, the same read doctor proof six already makes (`statement_for`); when the store does not answer or holds none, `statement` is null and a `note` says "not read: …" naming which. The text form prints what each row carries. The screen's own derivation is then retired by a later pass.

The stub is thin: the two rejection tests in `tests/test_ledger.py`, red on the stub. The build writes the logic.

Retires: "not read" as the label for "nothing to read here" and "read elsewhere"; the screen deriving a record file from a diff.

## Claims

- attavo/ledger.py
- tests/test_ledger.py

## Criteria

- [structural] the-rejection-tests-exist.sh: tests/test_ledger.py carries test_each_ledger_row_names_what_its_commit_carries and test_the_merge_row_says_not_read_when_the_store_does_not_answer
- [behavioural] each-row-names-what-its-commit-carries.sh: on a ledger_base tenant with a marker commit, a person's transport commit and a store holding the merge's statement, attavo ledger --json gives each row its carries (the spec file, each record file, the .started marker, the executor's touched file, nothing for the transport) and the merge row its statement's predicate type and subject digest
