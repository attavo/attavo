## Problem

Class: core

The Plan's order has no home the product can read. Epics, the items under them, their order and the edges between them live in `claude/plan/roadmap.json`, in a Cowork project that neither the product nor the screen can read. A handoff copy of it is kept on the operator's machine by device commits and md5 checks, and on 2026-09-28 it was stale when a lane read it, three times. Only one session can write it safely: two sessions editing it would silently overwrite each other. Priority lives there and in session prose, and changing it means editing a file by hand.

The storage ruling (2026-09-25) puts the Plan's working documents in the product's datastore, versioned and keyed to the tenant, and out of git, because the attestation binds to none of them. The operator ruled on 2026-09-29 to build that datastore now. The issue is the unit of work, and every issue belongs to an epic, Backlog being the catch-all. Priority is fluid and must never go through a commit or a merge in the planning stage.

This item adds the datastore and the plan in it.

- **`attavo/store.py`** keeps versioned documents under the clone's git common dir, `<common>/attavo/store/<name>/<n>.json`. Every worktree of a clone reads the same store, and nothing in it can be staged. `ATTAVO_STORE` overrides the location.
  - Each version is an envelope `{name, version, base, sha256, by, at, body}`. The sha256 is taken over the canonical JSON of the body.
  - A write names the version it read. It is refused when that version is not the latest, and the refusal names the latest version and its author. Otherwise the write lands as `<base+1>.json` through a hard link from a finished temporary file.
  - A hard link fails when its name exists. So of two writers racing from one base exactly one lands, the other is refused naming the winner, and a reader never sees half a file. There is no database and no lock.
  - It is per clone and per machine. Sharing across people is the multi-user step, when a hosted engine replaces this module without changing a caller of `get`, `put` or `log`.
- **`attavo/plan.py`** holds one document, `plan`, whose contract is `schema/plan.schema.json`:
  - the epics in rank order, each with its issues in rank order;
  - `depends` and `ruled` edges between issues, where a ruled edge names who ruled it and when;
  - policy.

  It holds no issue text, state or claims, because the forge and git hold those. It checks the shape with the standard library, as `records.py` checks records, because `bin/attavo` takes no runtime dependency. It also refuses what a schema cannot say: an issue in two epics, or an edge from an issue to itself. A Backlog epic must exist. The global order is epic rank, then issue rank within the epic.
- **`attavo plan`**:
  - `get [--version V]` prints the body on stdout and its version, author and time on stderr, so `get > f`, an edit, and `put f --base V` round-trip.
  - `put FILE --base V --by LOGIN` writes the next version.
  - `log` lists the versions.
  - `rank <EPIC|#N> (--before <EPIC|#M> | --last) --by LOGIN` moves one epic, or one issue within its epic.
  - `move #N --epic EPIC --by LOGIN` places an issue last in an epic and takes it out of the epic that held it.

  Every write names its author. Nothing here writes to git: a re-rank is a new version of a document, not a commit.

Two lines outside the new modules:

- The selector maps `attavo/plan.py`, `attavo/store.py` and `schema/plan.schema.json` to their tests.
- `tests/test_record_schemas.py` counts the `json.loads(` sites under `attavo/` and names each one. The two new ones are a store version and the file `plan put` is given; neither is a record. The count moves from 9 to 11, and its docstring names them.

The waves, the forge's issues and epic progress are later items, which read this store. The seed, today's roadmap converted and put as version 1, is run once after the merge and is not code.

The stub under `stub/` is the change on `d4c0bc1`: the two modules, the schema, `attavo/cli.py` (usage lines, a help paragraph, dispatch, and `plan` in the unknown-subcommand list, which keeps its `stops, test` ending), the selector, the count, and sixteen tests in `tests/test_store.py` and `tests/test_plan.py`, all red on `d4c0bc1` because the modules do not exist there. `python3 -m mypy` exits 0 on the change. The suite passes on it, except four doctor proofs that read this repository's own history, which a scratch copy cannot supply; they fail identically on `d4c0bc1` there.

Retires: `roadmap.json` in the Cowork project and the handoff folder as the home of the Plan's order (both are frozen after the seed); the device-commit and md5 step for every roadmap edit; priority kept in session prose and a `rank_intake` list.

## Claims

- attavo/store.py
- attavo/plan.py
- attavo/cli.py
- schema/plan.schema.json
- .attavo/bin/select-tests
- tests/test_store.py
- tests/test_plan.py
- tests/test_record_schemas.py

## Criteria

- the-plan-store-keeps-versions-outside-git-and-refuses-a-stale-base.sh: on the fixture tenant, `attavo plan put` with base 0 writes version 1 by alice under the git common dir; `plan rank "#4" --before "#3"` writes version 2 by bob, and `plan get` returns the new order with "plan version 2 by bob"; a put with base 1 is refused naming version 2 and bob; `plan log` lists two versions; `git status --porcelain` is empty; and `tests/test_store.py` and `tests/test_plan.py` carry the sixteen tests named
