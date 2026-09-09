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
- `ATTAVO_STAGE` — `"build"` or `"verify"`, which invocation this is. An adapter that supports both branches on it; one that only builds may ignore it.
- `ATTAVO_DIFF` — verify only: a file holding the diff since the build's approval.
- `ATTAVO_BUILD_RECORD` — verify only: the path to the `build-NN.json` being verified.
- `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`, `GIT_COMMITTER_NAME`, `GIT_COMMITTER_EMAIL` — build only: the executor's identity (issue #33), set by the product and never the tenant's git config. An adapter must not override these for the commits it makes; a commit build finds not carrying this identity is refused.

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

An adapter never writes a record. `attavo build` writes the one
`build-NN.json` for the whole invocation. What it must do with a tool
error, an absent report, or a bad question is `tests/test_executor_conformance.py`,
run against every `adapters/<name>/conformance.json` it declares.
