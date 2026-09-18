## Problem

`attavo doctor` reported done on this repository on 2026-09-17 while none of its three traversals (#106, #148, #153) had a statement in the attestation store: `main` had classic protection and no merge queue, every merge was direct, the coherence workflow's `merge_group` trigger never fired, and proof two holds on the four stage records with the chain held without asking whether the merge was sealed. The store answers 404 for all three; the operator screen rendered the missing statements as `not read`.

Retires: the doctor's `done` on an unattested traversal; `not read` as the rendering of a statement that does not exist.

Amended 2026-09-18 after #169 landed on `main` under this item's approved tree: an item's merge is the merge on the default branch's first-parent line (read from `refs/remotes/origin/HEAD`, refused by name when the ref is unset), so the proof names an item with no such merge in `not_merged` rather than skipping or failing it, fails by name on the walk's refusal, and the fixtures set the ref they depend on. The approval bound to the previous spec commit expires with this amendment, as the product requires.

Amended again 2026-09-18 after build-05: the doctor job's pytest step ran `gh` with no token, the attestation command exited 2, and proof six called #151's sealed merge unsealed. Exit 2 from the command is "the store could not be read", a fact the proof now reports as `not read` by item, with the command and its exit in the reason, never as `unsealed`; and the doctor job carries `GH_TOKEN` at the job level so every step that runs the doctor can read the store. The approval expires again with this amendment.

## Claims

- bin/attavo
- schema/manifest.schema.json
- attavo.toml
- .attavo/bin/forge-attestation
- .github/workflows/ci.yml
- tests/test_doctor_sealed.py
- tests/test_doctor.py

## Criteria

- doctor-fails-on-an-unsealed-merge.sh: on a fixture tenant with two merged items whose [forge].attestation_command answers for one merge and not the other, attavo doctor --json fails proof every-attested-item-is-sealed naming item 2 and its merge sha, listing sealed, unsealed, before_window, and not_merged, and holds once the command answers for both
- doctor-fails-without-attestation-command.sh: a manifest with no [forge].attestation_command fails the proof naming the key, and the manifest schema declares the key
- doctor-names-an-unmerged-item.sh: on a fixture tenant with one sealed merged item and a second item whose records sit on a branch under a synthetic merge at HEAD, the proof holds with sealed ["1"], not_merged ["2"], and unsealed []; before refs/remotes/origin/HEAD is set, the proof fails naming that ref
- doctor-says-not-read-when-the-store-cannot-be-read.sh: on a fixture tenant with one merged item whose [forge].attestation_command exits 2, the proof fails with a reason containing "not read", the command, and "exited 2", with not_read ["1"], unsealed [], and sealed []
- the-doctor-job-carries-the-token.sh: in .github/workflows/ci.yml the doctor job sets GH_TOKEN at the job level, declares attestations: read, and its pytest step over tests/test_doctor.py selects every_attested_item_is_sealed
- the-proof-has-its-test.sh: tests named test_doctor_proof_six_fails_on_an_unsealed_merge, test_doctor_proof_six_names_an_unmerged_item, and test_doctor_proof_six_says_not_read_when_the_store_cannot_be_read exist under tests/, this repository's manifest names its attestation command, and .attavo/bin/forge-attestation is executable
