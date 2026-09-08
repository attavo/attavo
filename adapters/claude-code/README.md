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

`attavo build` already runs `[checks].command` after each commit and feeds
a failure back on the next iteration — the executor is not the runner of
the tenant's checks. When `ATTAVO_CHECKS_COMMAND` is set (`../README.md`),
the instruction says so: "after you commit, the tenant's checks (`<command>`)
are run for you and any failure is returned to you on the next iteration;
run only what you need while editing." When it is unset, the instruction
says nothing about checks.

## Allowed tools and permission_denials

The default `allowed_tools` — `Read`, `Edit`, `Write`, `Bash(git *)`,
`Bash(python3 *)` — is deliberately narrow. A tenant whose checks or tests
need more (a shell loop, `gh`, `grep`, a platform command) names them in
`[executor.claude-code].allowed_tools`. The raw result's
`permission_denials` (in `build-NN.log` since `v0.3.4`) is where a tenant
reads what its executor kept reaching for and denied.

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
