## Problem

`attavo doctor` reported done on this repository on 2026-09-17 while none of its three traversals (#106, #148, #153) had a statement in the attestation store: `main` had classic protection and no merge queue, every merge was direct, the coherence workflow's `merge_group` trigger never fired, and proof two holds on the four stage records with the chain held without asking whether the merge was sealed. The store answers 404 for all three; the operator screen rendered the missing statements as `not read`.

Retires: the doctor's `done` on an unattested traversal; `not read` as the rendering of a statement that does not exist.

## Claims

- bin/attavo
- schema/manifest.schema.json
- attavo.toml
- .attavo/bin/forge-attestation
- .github/workflows/ci.yml
- tests/test_doctor_sealed.py
- tests/test_doctor.py
- tests/test_record_schemas.py

## Criteria

- doctor-fails-on-an-unsealed-merge.sh: on a fixture tenant with two merged items whose [forge].attestation_command answers for one merge and not the other, attavo doctor --json fails proof every-attested-item-is-sealed naming item 2 and its merge sha, listing sealed and unsealed, and holds once the command answers for both
- doctor-fails-without-attestation-command.sh: a manifest with no [forge].attestation_command fails the proof naming the key, and the manifest schema declares the key
- the-proof-has-its-test.sh: a test named test_doctor_proof_six_fails_on_an_unsealed_merge exists under tests/, this repository's manifest names its attestation command, and .attavo/bin/forge-attestation is executable
