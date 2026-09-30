## Problem

Class: core

The issue is the unit of work (ruled 2026-09-29). The plan in the store (#264) orders issues and says nothing else about them: an issue's title, whether it is open, when it closed, its labels, and the paths it claims before it has a spec all live in the forge. The product cannot read any of them. `frontier.py`, session tooling outside the product, reads open issues with `gh` directly. The screen's unclassed queue calls `gh` itself. Epic progress (closed issues over all issues under an epic) and the waves (which open issues can start, given their claims) both need this read, and the product names no vendor.

The product already reads the forge through commands the tenant declares: `checks_command`, `check_log_command`, `file_issue_command` and `attestation_command`. This item adds a fifth, `[forge].issues_command`. It takes nothing and prints the tenant's issues, open and closed, as one JSON list of `{number, title, state, closed_at, labels, body}`.

`attavo/issues.py` runs it, bounded at 60 s by `run_bounded`, and returns the issues in number order. Each issue's `claims` are parsed from its body's `## Claims` section: one `- path` line each, up to the next `## ` heading, with backticks stripped. An issue with no such section has `claims` None, which is not the same as a section that lists nothing. The product parses the claims, not the tenant's command, so every tenant's issues are read by one rule.

A read that cannot be made is "not read" with its reason, never an empty list:

- no command in the manifest;
- a non-zero exit, with the command's last stderr line;
- a run past its bound;
- output that is not JSON;
- an entry of the wrong shape, including a state other than `open` or `closed`, which is how the forge's own `OPEN` would show if a tenant's command passed it through.

This repository declares `issues_command = "python3 .attavo/bin/forge-issues"`. That script calls `gh issue list --state all` and translates the forge's words (upper-case states, label objects) into the shape. It exits non-zero with nothing on stdout when the forge cannot be read, or when the list would be cut at its limit.

The schema declares the new key, and the forge description no longer says there are two keys. `tests/test_record_schemas.py` counts the `json.loads(` sites under `attavo/`: the count moves from 11 to 12, and its docstring names this one (the issues as the command prints them, never a record). The selector maps `attavo/issues.py` and `.attavo/bin/forge-issues` to `tests/test_issues.py`.

Nothing reads `read_issues` yet. `attavo frontier` (FR) is its first reader, and the screen's unclassed queue can move onto it afterwards. FI is filed ahead of FR so FR's stub stands on a merged read.

The stub under `stub/` is the change on `5df6926`: the module, the script, the manifest line, the schema key, the selector lines, the count, and seven tests in `tests/test_issues.py`, all red on `5df6926`. `python3 -m mypy` exits 0 on the change. The suite passes on it, except four doctor proofs that read this repository's own history, which a scratch copy cannot supply; they fail identically on `5df6926` there.

Retires: `gh` calls to read issues outside the product, first `frontier.py --github`'s issue read, once FR reads through this; and an issue's Claims section as prose that nothing in the product parses.

## Claims

- attavo/issues.py
- .attavo/bin/forge-issues
- .attavo/bin/select-tests
- attavo.toml
- schema/manifest.schema.json
- tests/test_issues.py
- tests/test_record_schemas.py

## Criteria

- the-product-reads-issues-and-their-claims-through-the-tenants-command.sh: this repository's `forge-issues`, with a stand-in `gh` on PATH, prints two issues that `read_issues` reads in number order, with states lower-cased, label names, the Claims of the one whose body has a section (backticks stripped, the Acceptance line left out) and None for the other; no command reads "not read: no [forge].issues_command", and a command exiting 4 reads "not read: [forge].issues_command exited 4"; `attavo.toml` declares the command and the schema names the key; and `tests/test_issues.py` carries the seven tests named
