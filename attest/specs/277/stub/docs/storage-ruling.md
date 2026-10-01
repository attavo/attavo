# The three stores: where a thing lives (ruled 2026-09-25)

One test decides the store: does the attestation bind to it?

1. **Git**: facts and policy the attestation binds to. The spec and its criteria; approval bound to the spec's sha; invocation markers; build, review, counsel and ruling records; the manifest, including policy such as `[operators].approvers` (a claim the attestation makes, so a tenant must be able to prove it with the repository alone). Small, versioned, travels with the branch, survives the product.
2. **The product's datastore**: state and working material the attestation does not bind to. Identity state; indexes over git (ledger, measurements, report caches, legitimate only as caches rebuilt from git); the Plan area's working documents; the per-item conversation. A Plan document a spec's research record or a ruling cites is copied into `docs/` in the tenant at its cited version in the same commit as the citation.
3. **Content-addressed bulk**: bytes a fact points at by hash: build logs, events files, later any executor evidence. The record carries `log_sha256` and a locator; the attestation binds to the hash. The backend is the tenant's declaration in the manifest, offered in this order: git LFS (keeps "verify with the repository alone" true), the tenant's own object store behind a `put`/`get` command pair, GitHub release assets. GitHub Actions artifacts are not a candidate: they expire.

## Consequences

- Records are never moved to a datastore to save repository size; they are not the size.
- First step: every build, verify and counsel record carries `log_sha256`, computed at write time, with the log still in git; one field that turns every later move into a change of locator, not of attestation. Beside it, two measurements at retire: repository size per item and log bytes per attempt; they say when to declare a bulk backend.
- The index over git is built when a doctor or report read is slow enough to move a decision.
- Counsel's corpus belongs in the repository under `docs/`; `[counsel].reads` names it.
- Auth and RBAC: policy in git (the manifest), identity delegated to the forge, state in the datastore.
