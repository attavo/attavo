#!/bin/sh
grep -rq 'def test_doctor_proof_six_fails_on_an_unsealed_merge' tests/ && grep -q 'attestation_command' attavo.toml && test -x .attavo/bin/forge-attestation
