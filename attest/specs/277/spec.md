## Problem

Class: core

Counsel reads a stop once and well, and then forgets. On #219 the first reading (`counsel-01`) said "continue the build from the existing working tree". The operator's follow-up, `attavo counsel 219 --ask "why did this build stop?"`, ran 73 s and three model turns re-deriving what `counsel-01` already said. It read the last 30 KB of a 4.8 MB build log to do it. It searched the checkout for the reconciler brief and the R3 rulings and found nothing, because the corpus that governs this repository lived in the operator's project, not in the repository. Each of those is a missing mechanism, not a model failing: nothing handed counsel its own earlier reading, nothing named the corpus, and the log was the only evidence of what the stage did.

The rulings of 2026-09-25 (`docs/counsel-rulings.md` and `docs/storage-ruling.md` after this item) settle each. This item is their product half for counsel's inputs.

The prior readings first. `attavo counsel N`, on either path, is handed every counsel record the item already carries as `ATTAVO_COUNSEL_PRIOR`: a JSON list, oldest first, each entry with the record it read, its class, verdict, ruling text, citations, and the question it answered. The adapter's instruction puts it before everything else: do not re-derive a class an earlier reading states; a question about a stop already read is answered as a follow-up to that reading, citing it. The record written carries `answers`, the stem of the newest prior counsel record that read the same stage record, or null for a first reading. A first reading is handed nothing (the variable is empty).

The events in place of the log. On the stop path counsel is handed the record's log as today. On `--ask` the log is handed only when the question names it (the word `log`, case-insensitive). On both paths the stage's last events are handed as `ATTAVO_COUNSEL_EVENTS`: the last 200 lines of `<git-dir>/attavo/<record>.events.jsonl` when the file exists. A follow-up reads the stop record, its signature and the events, and the transcript only when it asks for it.

The corpus. `[counsel].reads`, a list of repository-relative paths, is what counsel reads before it answers: method rules, briefs, rulings. Counsel refuses (exit 1, no record) a manifest naming a path that does not exist ("[counsel].reads names X, which does not exist"), since a configured corpus nobody can read is prose. The paths are handed as `ATTAVO_COUNSEL_READS`, one absolute path per line, and the instruction lists them under "The tenant's corpus". The manifest schema gains the table. This repository, as its own tenant, gains `docs/` with `method-rules.md`, `counsel-rulings.md`, `r3-rulings.md` and `storage-ruling.md` at their text of 2026-09-26, a `README.md` saying what the directory is, and `[counsel] reads` naming the four.

Schema: counsel/4 adds `answers`, required (every counsel record is new under it). `attavo/types.py` mirrors it.

Not in this item, each its own item: `log_sha256` on build, verify and counsel records (the storage ruling's first step); the conversation table (turns as working material in the product's store, the 2026-09-25 ruling on the conversation with counsel); a citation check from a record into `docs/`; resuming a vendor's thread inside an adapter.

The stub under `stub/` is the change on `2a6fc89`: counsel, the counsel schema and its type, the manifest schema, the fixture adapter echoing what it is handed, the claude-code and codex instructions, `docs/` and the manifest table, and five tests red on `2a6fc89`. One of them, `tests/test_cli_help.py::test_help_names_counsels_corpus_the_follow_up_and_the_events_tail` stays red on the stub, by design: the help text is the build's. `python3 -m mypy` exits 0 on the stub.

Retires: counsel re-deriving its own earlier reading; the whole log as a follow-up's evidence; the operator's project as the place counsel's rules live.

## Claims

- attavo/counsel.py
- attavo/types.py
- attavo/cli.py
- schema/records/counsel.schema.json
- schema/manifest.schema.json
- adapters/fixture/run
- adapters/claude-code/run
- adapters/codex/run
- attavo.toml
- docs/README.md
- docs/method-rules.md
- docs/counsel-rulings.md
- docs/r3-rulings.md
- docs/storage-ruling.md
- tests/test_counsel.py
- tests/test_self_tenancy.py
- tests/test_cli_help.py
- tests/test_types_mirror_schemas.py
- tests/test_adapters.py
- tests/test_adapters_codex.py

## Criteria

- a-follow-up-answers-from-the-prior-reading.sh: on a fixture tenant with a halted build, the first `attavo counsel 1` writes counsel-01 with `answers` null and is handed no prior reading; `attavo counsel 1 --ask` writes counsel-02 with `answers` counsel-01 and the question, handed a prior list naming counsel-01 with its verdict and ruling text; a further `--ask` writes counsel-03 answering counsel-02
- the-events-stand-in-for-the-log-unless-the-log-is-asked-about.sh: the stop path hands the log and the stage's events; a `--ask` that does not name the log is handed the events and no log; a `--ask` that names the log is handed the log
- the-corpus-is-handed-and-a-missing-path-is-refused.sh: a manifest whose `[counsel].reads` names paths that do not exist is refused by name with no record written; once they exist counsel is handed them as absolute paths in order; this repository's manifest names at least three non-empty files under `docs/`, the manifest schema carries the table, and the five tests named exist
