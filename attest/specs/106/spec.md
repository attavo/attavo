## Problem

Two facts on the attestation of tenant zero's item #665 (merged `6ac6021`) are wrong or unreadable, and both are the product's. First, the two record commits `attavo drive` made (`e7d9860`, `f572d73`) are authored and committed as the operator's own git identity, because `commit_record` calls `git commit --only` with no identity while `executor_identity` already sets one for the executor's commits; a read by author cannot tell a driver's transport commit from a person's, so the driven clause of Phase 3 is provable only from the attestation's `acts` and not from the log. Second, the attestation carries `ci_runs: 0` on an item whose three pushes each ran six checks: the field is `int(os.environ.get("ATTAVO_CI_RUNS") or 0)`, set by no template, so it reports a wrong number rather than none.

This item gives the driver its own identity and removes `ci_runs`. `commit_record` authors and commits every record commit as `attavo driver <driver@attavo.invalid>` from a `driver_identity()` beside `executor_identity()`, set by the product and never the tenant's git config, so `git log --author='attavo driver'` on a driven item lists exactly the driver's transport commits; the executor's identity is unchanged. A test in `tests/test_drive.py` sets the fixture tenant's `user.name` to another value, drives to a record commit, and asserts author and committer name and email on it; it must fail against the current `commit_record`. `ci_runs` leaves the attestation statement, the schema's `required` list and `properties`, and `ATTAVO_CI_RUNS` is read nowhere; `tests/test_attest.py::test_partial_does_not_read_ci_runs` is retired with it, and a test asserts the statement `attavo attest` prints carries no `ci_runs` key. The attestation schema's `$id` moves from `/attestation/v1` to `/attestation/v2`, since a required field left the shape. No other behaviour changes; a forge-read producer for CI runs is Phase 4's, if a decision ever needs the number.

Retires: the by-author read as something the attestation's `acts` field had to stand in for; `ATTAVO_CI_RUNS` and the test that guarded it; one wrong number on every attestation.

## Claims

- bin/attavo
- schema/attestation.schema.json
- tests/test_drive.py
- tests/test_attest.py

## Criteria

- no-ci-runs.sh: neither bin/attavo nor the attestation schema mentions ci_runs or ATTAVO_CI_RUNS
- attestation-schema-v2.sh: the attestation schema's $id is /attestation/v2
- driver-identity.sh: bin/attavo declares driver_identity with the driver@attavo.invalid address
