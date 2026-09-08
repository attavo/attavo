# adapters/claude-code

Runs `claude -p` against the spec. Configured through `[executor.claude-code]`
in the tenant's manifest (see `../README.md` for how that table reaches the
adapter as `ATTAVO_ADAPTER_CONFIG`, and lands verbatim in `build-NN.json` as
`adapter_config`). **Never put a secret in this table** — it lands in a
record.

## Keys

- `auth` — `"api"` (default) or `"subscription"`.
  - `"api"`: runs with `--bare`. Requires `ANTHROPIC_API_KEY`; usage bills per token.
  - `"subscription"`: omits `--bare`; adds `--setting-sources user` and `--strict-mcp-config` so the tenant's project hooks and MCP servers do not load (`CLAUDE.md` still does). Runs on the operator's subscription login; usage is included in the plan. `ANTHROPIC_API_KEY` is not required.
- `model` — omitted means the executor's default. When set, passed as `--model`.
- `allowed_tools` — list; defaults to `["Read", "Edit", "Write", "Bash(git *)", "Bash(python3 *)"]`.
- `setting_sources` — default `"user"`. Only meaningful in subscription mode; ignored in api mode (`--bare` already excludes everything).
- `permission_mode` — default `"acceptEdits"`.

## The tenant's checks

When `ATTAVO_CHECKS_COMMAND` is set (`../README.md`), the instruction names
it: "the tenant's checks, `<command>`, must exit 0 before you commit." When
it is unset, the instruction says nothing about checks.

## cost_usd is a meter, not always a bill

`build-NN.json`'s `cost_usd` is this adapter's report of the tool's
list-price meter. It is billed only when `adapter_config.auth` (this
table's `auth` key) is `"api"`. Under `auth = "subscription"`, the same
figure is a measure of usage against the operator's plan, not a charge —
that build ran on the subscription login, not an API key.

## The raw result

The whole `--output-format json` result is printed to stdout before this
adapter's own final line, so `attavo build` saves it verbatim as the
transcript in `build-NN.log` — the tool's own account of what happened,
including fields (`num_turns`, `duration_ms`, `is_error`,
`permission_denials`) the final line never carries. `is_error: true`, no
`structured_output` at all, or a `status: "question"` whose `question` is
missing or under 40 characters all make the adapter exit 1 (build records
the attempt `failed`) instead of a halt someone has to rule on — a
question worth a person's judgment is at least a sentence.
