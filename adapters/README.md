# Adapters

An adapter is an executable at `adapters/<name>/run`, resolved first under
the tenant's `attavo/adapters/`, then under this product's `adapters/`.
`attavo build` invokes it with the tenant root as its working directory.

## Environment

- `ATTAVO_SPEC_DIR` — the work item's spec directory (`spec.md`, `criteria/`, `stub/`).
- `ATTAVO_RECORDS_DIR` — the work item's own records directory.
- `ATTAVO_RULINGS` — the tenant's rulings file (`paths.rulings`).
- `ATTAVO_BRANCH` — the branch the adapter must commit on.
- `ATTAVO_ITERATION` — the 1-based attempt number within this `attavo build` invocation.
- `ATTAVO_FEEDBACK` — a file holding the previous iteration's failing criteria, test output, and checks output; empty on the first iteration.
- `ATTAVO_ADAPTER_CONFIG` — the tenant manifest's `[executor.<adapter-name>]` table, serialized as JSON (`{}` when the tenant declared none). The product does not know what an adapter's settings mean; each adapter documents its own keys on its own `README.md`. **Never put a secret in this table** — it is copied verbatim into `build-NN.json` as `adapter_config`, and a record is not where secrets live.
- `ATTAVO_CHECKS_COMMAND` — the tenant's `[checks].command`, unset when the tenant declared no `[checks]` table. The product does not know what it checks; the tenant does. An adapter should tell the executor it is part of done when set, and say nothing about checks when it is not.

## Output

The adapter commits its work on the current branch itself. It prints one
JSON object on its last stdout line:

```json
{"status": "done" | "question", "question": null | "<text>",
 "model": "<id>", "model_version": "<id or null>",
 "adapter_version": "<tool version>",
 "tokens": {"input": n, "output": n}, "cost_usd": n | null}
```

Anything else on stdout is the adapter's transcript and is saved beside the
build record as `build-NN.log`, never inside the record.

## Exit status

- `0` with `status: "done"` — the adapter made its attempt; build checks the claims, the criteria, the tests, and the tenant's checks (if declared).
- `0` with `status: "question"` — the adapter cannot proceed without a decision that changes the criteria; build halts at once.
- non-zero — the adapter failed to run; build records the attempt as `failed`.

An adapter never writes a record. `attavo build` writes the one
`build-NN.json` for the whole invocation.
