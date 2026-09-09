# adapters/codex

`adapters/codex/run` against `codex-cli` (`npm install -g @openai/codex`),
authenticated with `codex login`. See `../README.md` for the environment
and output contract every adapter shares; this file documents only
`[executor.codex]`'s own two keys.

## `[executor.codex]`

- `model` (**required**) -- no model id appears anywhere codex itself
  reports: not the JSONL event stream, not `codex doctor`, not any
  config file (confirmed live 2026-09-08 against codex-cli 0.153.4,
  with no `~/.codex/config.toml` on the machine at all). The adapter
  refuses before invoking codex if this is absent, and reports back
  exactly the id it was given -- never one derived from output.
- `auth` -- `"subscription"` (default) or `"api"`, mirroring
  `[executor.claude-code]`'s own `auth` key. `"subscription"` expects
  the operator's own ChatGPT-plan login and refuses if `OPENAI_API_KEY`
  is set in the environment, since codex would otherwise silently bill
  the API account for a run meant for the plan; `"api"` expects
  `OPENAI_API_KEY` to be set. `cost_usd` is always `null` on this
  adapter regardless of mode -- no dollar figure appears anywhere under
  plan auth, and none is invented for API auth -- so a codex record
  carrying a non-null cost is evidence the wrong credential was used, a
  finding about the credential, not a measurement.

## Why `codex exec review` is never called

`codex exec review --output-schema <FILE>` ignores the schema entirely
-- confirmed live 2026-09-08 with a real authenticated run against a
planted-defect diff: its `-o` output was free-text prose regardless of
the schema passed. Both build and verify instead feed their own
instruction to plain `codex exec -` on stdin (`-p` is codex's
`--profile`, not a prompt flag), with `--output-schema` and
`--output-last-message` naming the structured response -- confirmed
live that this path does honor the schema.

## The two channels

- `--json` prints one JSON object per stdout line (JSONL): `thread.started`,
  `turn.started`, `item.*`, and a terminal `turn.completed` (carrying
  `usage`) or `turn.failed`. codex's own exit code is 1 on `turn.failed`,
  0 on `turn.completed` -- read directly, so this adapter reads the
  terminal event for the reason, not for the verdict.
- `--output-schema <FILE>` plus `--output-last-message <FILE>` writes the
  final response as one JSON object once the turn completes -- this is
  where `findings`/`tests_named` (verify) or `status`/`question` (build)
  come from.

## The sandbox, not just the instruction

`verify`'s instruction says "make no edits, write no files, commit
nothing"; this adapter also passes `--sandbox read-only` for verify (and
`--sandbox workspace-write` for build) -- codex enforces it, rather than
this adapter trusting the instruction alone. Not a tenant-configurable
key: a deliberate choice, not an oversight.

## The two hooks

- `ATTAVO_ADAPTER_DRY_RUN` prints `{"argv", "instruction", "schema"}`
  instead of running. `argv` never carries the instruction -- it goes in
  on stdin -- which is why the envelope names it separately.
- `ATTAVO_ADAPTER_RESPONSE_FILE` feeds a canned
  `{"events": [...JSONL objects...], "final": {...the output file's
  parsed content, or null...}, "exit_code": N}` instead of invoking
  `codex` -- codex has no canned-response mechanism of its own, so this
  adapter provides one, exactly as `adapters/claude-code/run` does with
  its own `ATTAVO_ADAPTER_RESPONSE_FILE`.

## Residuals

- `codex doctor --json`'s `auth.credentials` block (`stored auth mode`,
  `stored API key`) is a cheaper, redacted source for `adapter_version`
  than parsing `codex --version`'s `codex-cli 0.153.4`, and a better
  cross-check for `auth` than `OPENAI_API_KEY` alone -- not implemented
  here, since it would call the live tool from paths the conformance
  suite also exercises through a faked `tool_binary`, entangling a
  diagnostic with the suite's own test seams. `OPENAI_API_KEY` alone is
  deterministic, fully testable, and already closes the concrete risk
  (silent API billing on a plan-auth run).
- Whether `--dangerously-bypass-approvals-and-sandbox` or
  `--approve-for-me` ever belong in `[executor.codex]` is unread; this
  adapter never passes either.
