## Problem

Class: fix

#349's review (verify-01 at 63c95a1) passed with two findings, each a hole in what #349 added. These are the first review findings to become their own item.

1. `attavo/doctor.py::_accounting_disagrees` returns None when a statement carries neither `time` nor `cost`. A v6 statement that omits both and carries false `acts` or `interventions` is held by proof six without either list being compared.
2. `schema/attestation.schema.json` takes any non-empty string for an intervention's `class`. The producer emits only `amend`, `launch` and `transport`.

Neither is reachable from today's producer. Each lets a statement that did not come from the producer pass the check meant to catch one.

The change: proof six compares a v6 statement's acts and interventions whatever else the statement carries, and a v6 statement missing `time` or `cost` fails proof six by name, as one with a null block already does. The intervention `class` becomes the enum `amend`, `launch`, `transport`. The only v6 statement sealed so far (#349's own) carries classes inside the enum, so the predicate stays v6 unless `scripts/check_record_schema_version.py` refuses the shape change, in which case it moves to v7 and the doctor reads v6 as sealed.

The stub is thin: it adds the two rejection tests verify named to `tests/test_attest_interventions.py`, red on the stub. The build writes the logic.

Retires: proof six holding a statement it never compared.

## Claims

- attavo/doctor.py
- attavo/types.py
- schema/attestation.schema.json
- tests/test_attest_interventions.py
- tests/test_doctor.py
- tests/test_types_mirror_schemas.py

## Criteria

- [structural] the-rejection-tests-exist.sh: tests/test_attest_interventions.py carries test_doctor_proof_six_rejects_a_v6_statement_without_time_or_cost_whose_acts_or_interventions_differ and test_v6_schema_rejects_an_intervention_class_outside_amend_launch_transport
- [behavioural] a-v6-statement-missing-its-accounting-fails-proof-six.sh: a v6 statement sealed with neither time nor cost and forged acts fails proof six, naming the item
- [behavioural] the-schema-refuses-an-unknown-intervention-class.sh: the attestation schema accepts an intervention of class transport and refuses one of class bogus
