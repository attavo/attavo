#!/bin/sh
grep -rq 'def test_doctor_proof_six_fails_on_an_unsealed_merge' tests/ && grep -rq 'def test_doctor_proof_six_names_an_unmerged_item' tests/ && grep -q 'attestation_command' attavo.toml && test -x .attavo/bin/forge-attestation
