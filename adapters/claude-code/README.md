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
- `model` — omitted means the executor's default. When set, passed as `--model`, and trusted verbatim as the record's `model` rather than picked out of the tool's own usage report (issue #77).
- `setting_sources` — default `"user"`. Only meaningful in subscription mode; ignored in api mode (`--bare` already excludes everything).
- `permission_mode` — default `"acceptEdits"`.
- `allowed_tools` is retired (issue #78): a manifest that still declares `[executor.claude-code].allowed_tools` is refused by name rather than silently accepted or ignored. See below.

## The tenant's checks

`attavo build` already runs `[checks].command` after each commit and feeds
a failure back on the next iteration — the executor is not the runner of
the tenant's checks. When `ATTAVO_CHECKS_COMMAND` is set (`../README.md`),
the instruction says so: "after you commit, the tenant's checks (`<command>`)
are run for you and any failure is returned to you on the next iteration;
run only what you need while editing." When it is unset, the instruction
says nothing about checks.

## The sandbox is the boundary, not an allowed_tools list

Every invocation carries `--settings` with a `sandbox` object
(`sandbox.enabled: true`, `allowUnsandboxedCommands: false`,
`network.allowedDomains: []`) — confirmed live against `claude 2.1.268`:
the sandbox refuses a write outside the working directory and any
outbound connection, at the OS level, before `--allowedTools` is ever
consulted. Verify's `--settings` additionally sets
`filesystem.denyWrite` on the working directory itself, so "make no
edits, write no files" is mechanical rather than merely instructed.
`--allowedTools` is therefore fixed per stage to the tool names the
sandbox does not itself govern — `Read,Edit,Write,Bash` for build,
`Read,Bash` for verify — not a curated `Bash(git *)`-style prefix list a
tenant used to grow one denial at a time; `[executor.claude-code]` no
longer has a key for it. The network stays closed for both stages; an
executor that needs a package index is a tenant fact for a later
manifest key, not this one.

The raw result's `permission_denials` (in `build-NN.log` since `v0.3.4`)
still exists, but a Bash tool call the sandbox will refuse at the OS
level is never denied at the permission layer in the first place — a
refusal there shows up as the underlying shell command's own non-zero
exit in the transcript, not as a `permission_denials` entry.

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

## A report is not a question (issue #88)

A `status: "question"` whose text clears the 40-character gate but
carries no `"?"` is a report, not a question — five of tenant zero's
twelve non-pass build records were exactly this ("I've traced the
spec's 5 criteria against the tree and dispatched an implementation
agent…"), each costing a person a read and a re-launch for nothing to
rule on. This adapter re-invokes `claude` once more on a report,
appending a line asking it to either continue to done or ask a real
question, before ever reporting a halt. A second report in a row still
halts, but its report line carries `"class": "non-question"` and both
raw results already reached the transcript above it — see `../README.md`.

## Work that can outlive the invocation (issue #88)

Confirmed live against `claude 2.1.269`: a prompt encouraging a
subagent produced `subagent_stats.spawned: 1` when the Agent tool was
allowed, and `0` with `"Agent"` alone in `--disallowedTools` — the
tool's own reply named `Workflow` as the other path to background work,
so both are disallowed by name on every invocation, both stages.
Tenant zero's own specimen (`#659` build-01) spawned three with neither
tool in `--allowedTools` at all, so the flag is checked after the fact
too: any response whose `subagent_stats.spawned` is not zero is refused
by name, with the counts, rather than accepted as `done` — a build the
tool's own background work could still be writing to.
