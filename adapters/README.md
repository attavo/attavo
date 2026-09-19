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
- `ATTAVO_STAGE` — `"build"`, `"verify"`, or `"counsel"` (issue #100), which invocation this is. An adapter that supports every stage branches on it; one that only builds may ignore it.
- `ATTAVO_EVENTS` — build and verify only: a file path an adapter may append events to while it runs (issue #26). Absent on a stage that does not set it (counsel does not); an adapter that never checks for it breaks nothing.
- `ATTAVO_DIFF` — verify only: a file holding the diff since the build's approval.
- `ATTAVO_BUILD_RECORD` — verify only: the path to the `build-NN.json` being verified.
- `ATTAVO_COUNSEL_RECORD` — counsel only: the path to the non-pass `build-NN.json`/`verify-NN.json` being counseled.
- `ATTAVO_COUNSEL_LOG` — counsel only: the path to that record's own `.log`, empty when it has none.
- `ATTAVO_COUNSEL_DIFF` — counsel only: a file holding the item's diff since approval (the same diff verify itself would compute).
- `ATTAVO_MANIFEST` — counsel only: the path to the tenant's own `attavo.toml`.
- `ATTAVO_PRODUCT_ROOT` — counsel only: the path to this product's own root (`bin/attavo`, `adapters/`, `schema/`, `templates/`), so a stop whose cause is the product's own mechanism can be read at it.
- `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`, `GIT_COMMITTER_NAME`, `GIT_COMMITTER_EMAIL` — build only: the executor's identity (issue #33), set by the product and never the tenant's git config. An adapter must not override these for the commits it makes; a commit build finds not carrying this identity is refused.

### The executor sees none of it (issue #198)

The variables above are the adapter's contract, not the executor's. An
adapter launches its executor with every `ATTAVO_*` name removed from the
child's environment and keeps reading its own. The executor's tools would
otherwise inherit `ATTAVO_EVENTS`, the item's live events file, and write
to it: a test suite the executor runs that runs `adapters/fixture/run`
appended 26 of 83 lines to one item's events file (attavo/attavo#194,
build-01). The adapters here pass `executor_env()` from
`adapters/executor_env.py` as `env=` to the process that runs the
executor; a tenant's own adapter does the same, by that helper's rule
(drop every name starting `ATTAVO_`, keep the rest). Situations 20 and 21
of `tests/test_executor_conformance.py` check it through the tool binary
each `conformance.json` names. Probes of the tool itself (`--version`)
are not the executor.

## Output

The adapter commits its work on the current branch itself. It prints one
JSON object on its last stdout line:

```json
{"status": "done" | "question", "question": null | "<text>",
 "model": "<id>", "model_version": "<id or null>",
 "adapter_version": "<tool version>",
 "tokens": {"input": n, "output": n}, "cost_usd": n | null}
```

A `"question"` line may also carry `"class": "non-question"` (see below);
`attavo build` reads only `status`/`question` off this line and does not
know about the extra field.

Anything else on stdout is the adapter's transcript and is saved beside the
build record as `build-NN.log`, never inside the record.

An adapter never writes a record. `attavo build` writes the one
`build-NN.json` for the whole invocation. What it must do with a tool
error, an absent report, or a bad question is `tests/test_executor_conformance.py`,
run against every `adapters/<name>/conformance.json` it declares.

## Events (issue #26)

`ATTAVO_EVENTS`, when set, names a file the adapter may append events to
while it runs, mapping its own executor's stream into one JSON object
per line, flushed on every write:

```json
{"at": "<iso8601, seconds>", "kind": "message" | "tool_call" | "tool_result",
 "text": "<one line, capped at 400 characters>"}
```

`text` is the executor's own words for `message`, the tool's name and its
first argument for `tool_call`, and the tool's result's first line for
`tool_result`. The product appends its own `iteration` and `gate` events
to the same file (each iteration's start, and each criterion's exit,
from the loop that already runs them) and, for `attavo counsel`, never
opens the file at all -- events belong to a running stage, not a
read-only judgment of one already finished.

The file is never a record and is never committed: it is not among the
paths `commit_record` names, and a tenant's `.gitignore` is not touched
for it, since an uncommitted file in an item's own worktree is already
invisible to the ledger and the doctor. An adapter that never writes to
it breaks nothing -- the file is simply absent, and the screen renders
the feed it has without one.

## Counsel (issue #100)

With `ATTAVO_STAGE=counsel`, the report line carries `class`, `verdict`,
`citations`, and `proposal` instead of `status`/`question`:

```json
{"class": "<stop class, or \"unclassed\">",
 "verdict": "answer" | "patch" | "person",
 "citations": [{"kind": "instruction" | "mechanism" | "guard",
                "path": "<path>" | null, "symbol": "<symbol>" | null,
                "ruling_id": "<id>" | null}, ...],
 "proposal": {"ruling_text": "<text>" | null,
              "patch": {"file": "<path>", "symbol": "<symbol>",
                        "rejection_test": "<test>", "diff": "<unified diff>"} | null,
              "question": "<one line>" | null},
 "model": "<id>", "model_version": "<id or null>",
 "adapter_version": "<tool version>",
 "tokens": {"input": n, "output": n}, "cost_usd": n | null}
```

Counsel is read-only -- the same sandbox verify runs under -- and it
never acts: `proposal` carries a ruling's text or a patch's diff, never
a write or a commit. `adapters/<name>/conformance.json` declares which
stages an adapter supports as `"stages"`; an adapter naming no
`"counsel"` there has not been pointed at this stage.

## Two stops (issue #88)

A `"question"` is not always a question. A status report landing in the
question channel ("I've traced the spec's 5 criteria and dispatched an
implementation agent…") halts an item to a person exactly like a real
question does, but the person has nothing to rule on. Every adapter
therefore treats a `"question"` whose text clears the length gate but
carries no `"?"` as a report, not a question: it re-invokes the executor
once more, in the same invocation, appending "your last answer was a
report, not a question; either continue to done, or ask one question
ending in a question mark that names the spec line or guard you cannot
resolve" to the same instruction. A proper question or a `"done"` on the
retry ends the invocation normally, at the cost of one extra executor
turn; a second report in a row still halts (someone must look), but its
report line carries `"class": "non-question"` and both raw results
already reached the transcript above it. No record field changes: the
class lives in the transcript and the report line, not in
`build-NN.json`.

The report line's own `tokens` and `cost_usd` sum both calls, whatever
the retry's outcome — the record's cost matches the two executor turns
actually spent, not just the second one; only `model`/`model_version`/
`adapter_version` come from the second call.

Work can also outlive the invocation that started it: a tool that spawns
its own background subagents can report `"done"` and exit while those
subagents are still running or still being torn down, so the checks
`attavo build` runs right after `"done"` land on a tree something else
is still writing. An adapter whose tool can do this must refuse the run
by name -- exit 1, with the counts -- rather than report `"done"` on it;
`adapters/<name>/conformance.json` names this ability as
`"subagent_guard": {"supported": true, "situation": "<name>"}` or, when
the tool has no such concept to check at all, `{"supported": false,
"reason": "<why>"}` -- an adapter naming neither is an undeclared blind
spot, not a legitimate absence (`tests/test_executor_conformance.py`
enforces this the same way it enforces `"reports"`).
