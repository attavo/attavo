# Attest

Software teams are about to run more agents than engineers, and every one of those agents will open pull requests faster than a person can read them. The bottleneck of the next decade is not writing code; it is knowing which code to trust. Attest makes that knowable. It turns a specification into executable acceptance criteria, binds a human approval to a commit, runs the build and the judgment as separate invocations with separate objectives, records every intervention with a class, and merges only what carries an attestation. The operator does three things: approve the spec, rule on the escalations that are theirs alone, and merge. Everything else is the product's, and everything the product does is on the record.

## Definition of done

`bin/attest doctor <tenant-root>` is the definition of done. It asserts three proofs and exits non-zero until all three hold:

1. Every gate named in the tenant's manifest rejects the fixture it must reject.
2. One work item has traversed every stage: spec, approval, build, verify, attestation.
3. No commit on the tenant's default branch lacks a record.

`tests/test_doctor.py` holds the three proofs as tests. They are written before the capability and they fail today. A phase is done when its proof holds, never when its work is finished.

## Layout

- `bin/attest` — the command. `doctor` is its only subcommand at this commit.
- `schema/manifest.schema.json` — the shape of a tenant's `attest.toml`.
- `tests/` — the doctor's proofs.

This repository contains no tenant name, path, or command string.
