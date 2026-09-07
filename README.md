# Attavo

Software teams are about to run more agents than engineers, and every one of those agents will open pull requests faster than a person can read them. The bottleneck of the next decade is not writing code; it is knowing which code to trust. Attavo makes that knowable. It turns a specification into executable acceptance criteria, binds a human approval to a commit, runs the build and the judgment as separate invocations with separate objectives, records every intervention with a class, and merges only what carries an attestation. The operator does three things: approve the spec, rule on the escalations that are theirs alone, and merge. Everything else is the product's, and everything the product does is on the record.

## Definition of done

`bin/attavo doctor <tenant-root>` is the definition of done. It asserts three proofs and exits non-zero until all three hold:

1. Every gate named in the tenant's manifest rejects the fixture it must reject.
2. One work item has traversed every stage: spec, approval, build, verify, attestation.
3. No commit on the tenant's default branch lacks a record.

`tests/test_doctor.py` holds the three proofs as tests. They fail against this repository, which has no manifest, and that is expected: this repository is the product, not a tenant. A phase is done when its proof holds, never when its work is finished.

## The spec contract (Phase 1)

A spec is one artifact with a line in it. Above the line — the problem and the claims — is amendable after approval at the cost of one act. Below the line — the criteria, which execute — is the only thing approval freezes.

- `attavo spec-gate N` runs a work item's criteria against the tree, then against the tree plus its stub overlay, and refuses by name: wrong headings, an empty claim, a criterion that is prose, a criterion that already passes, or one that still fails with the stub applied. Every run writes the next `spec-NN.json`.
- `attavo approve N --by LOGIN` binds approval to the commit that produced the latest passing `spec-NN.json`, and refuses a `LOGIN` not in the manifest's `[operators].approvers`.
- `attavo approval-status N` reports `valid`, `expired`, or `absent` — expired the moment the spec directory moves, above the line or below it alike.

All three run from the tenant root; `N` is read from the branch (`attavo/N-*`) when omitted. The full contract is in the Phase 1 issue.

## Build (Phase 2)

`attavo build N` refuses (exit 1, naming the cause, no record) unless the manifest declares `[executor].build` and `[tests].select` and `[tests].run`, and unless `attavo approval-status` would print `valid`. It invokes the named adapter — `adapters/<name>/run`, resolved first under the tenant's own `attavo/adapters/`, then under this product's `adapters/` — with the spec, the corpus, and the branch. After each return it checks that every path the adapter touched matches a spec claim, then runs the criteria against the tree and the tests `[tests].select` and `[tests].run` name for the touched paths, iterating on failure up to a fixed budget. A `question` from the adapter halts the loop at once (exit 3); nothing answers it in Phase 2. Every run writes the next `build-NN.json`, with the adapter's transcript beside it as `build-NN.log`. Nothing dispatches build; it is typed by the operator. The full contract is in the Phase 2 issue.

## Layout

- `bin/attavo` — the command: `doctor`, `spec-gate`, `approve`, `approval-status`, `build`.
- `schema/manifest.schema.json` — the shape of a tenant's `attavo.toml`.
- `schema/records/` — the shape of each stage's record.
- `adapters/` — the adapter contract (`adapters/README.md`) and two adapters: `claude-code` and `fixture`.
- `action.yml` — pins Attavo at a tag and puts `attavo` on `PATH`.
- `templates/workflows/` — workflows a tenant copies into its own repository; nothing here is vendored or run by this one.
- `tests/` — the doctor's proofs and the spec and build contracts' rejection tests, with their fixtures under `tests/fixtures/`.

This repository contains no tenant name, path, or command string.
