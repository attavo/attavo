## Problem

Class: core

The operator ruled on 2026-09-29 that the waves are built by the product: priority order, plus opportunistic work when a lower-priority issue can take an open claim. The product now holds what the waves need. The plan's order is in the store (#264), the tenant's issues come through `[forge].issues_command` (#267), and work in flight comes from git (`attavo claims`, #251). Nothing puts them together. The frontier is still computed by `frontier.py`, session tooling outside the product with its own overlap rule, and the waves in the Plan Map were drawn by hand.

This item adds `attavo frontier [--json]`. It makes three reads and writes nothing.

- **Order.** The order comes from the plan's latest version: epic rank, then issue rank within the epic. An open issue the plan does not place is Backlog, last, by number, so a script-filed issue is always somewhere. The output says which order it used.
- **Wave 0** is the work holding claims now: every branch not on the default branch, with the paths it declares and changes, as `attavo claims` reads them. An item branch also says where it stands, read from the records it carries: spec pushed, spec refused, awaiting approval, approved, building, or in review. The spec record's status is read through `records._load_record`, the one place that parses a record.
- **Wave 1** walks the order and places each open issue that meets all four conditions:
  - its predecessors, its `depends` and `ruled` edges, are closed or placed in an earlier wave;
  - its Claims overlap nothing already placed in the wave;
  - its Claims overlap nothing still held in wave 0;
  - under strict opportunism (ruled 2026-09-29), its Claims overlap nothing that an open issue ranked above it claims and that is not yet placed, whether that issue is ready or not. A lower-ranked issue never takes a path a higher-ranked one wants, and it does take a slot on paths nobody above it wants.
- **Wave k+1** walks what is left as if the earlier waves had merged. When the work in flight blocks everything, wave 1 is empty and wave 2 reads past it.
- **Overlap** is `attavo.claims.overlap`, the product's only rule.
- **Not placed.** An issue with no Claims is not placed, because nothing shows it disjoint from anything; neither is an issue whose predecessors never are. Each is listed with its reason.
- **Why lines.** Every placed issue carries one line per reason it waited: `waits on #281 (depends)`, `waits on branch lane/9-other: y/four.py`, `shares attavo/cli.py with #264 in wave 1`, `yields shared.py to #2, ranked above`. An issue in wave 1 says nothing.
- **Epic progress** is closed issues over all issues under the epic, as the forge reports them.
- **No dates.** The output carries no date or duration, because waves are an order, not a forecast.
- **Refusals.** No plan in the store, or no issues command, is "not read" with exit 2. An unset `refs/remotes/origin/HEAD` refuses by name, as `claims` does.

Each issue also carries its class. `attavo/issues.py` reads the class from the body's `Class:` line, using the spec's own `CLASS_LINE` and `CLASSES`, or from a single `class:core` or `class:fix` label. Otherwise the class is None, the unclassed queue's reading. The screen's Waves view (FV2, #282) renders `attavo frontier --json`. Its shape:

```
{"default", "plan_version", "order", "epics": [{"id", "title", "closed", "total"}],
 "waves": [[{"issue", "branch"?, "title", "epic", "class", "claims", "spec_state", "why"}]],
 "not_placed": [{"issue", "title", "epic", "why"}]}
```

The selector maps `attavo/frontier.py` to `tests/test_frontier.py`, and `attavo/issues.py` now also to that file.

The stub under `stub/` is the change on `7a5cbfb`: `attavo/frontier.py`, the class read in `attavo/issues.py`, `attavo/cli.py` (a usage line, a help paragraph, dispatch, and `frontier` in the unknown-subcommand list, which keeps its `stops, test` ending), the selector, nine tests in `tests/test_frontier.py` and one in `tests/test_issues.py`. All are red on `7a5cbfb`. `python3 -m mypy` exits 0. The suite passes on it, except four doctor proofs that read this repository's own history, which a scratch copy cannot supply; they fail identically on `7a5cbfb` there. None of the paths is a claim of the operator's re-entry run.

Retires: `frontier.py` (session tooling) and its second overlap rule; waves drawn by hand; the "Next" lists in session records as the source of order.

## Claims

- attavo/frontier.py
- attavo/issues.py
- attavo/cli.py
- .attavo/bin/select-tests
- tests/test_frontier.py
- tests/test_issues.py

## Criteria

- the-frontier-builds-waves-from-the-plan-the-issues-and-git.sh: on the fixture tenant with an issues command, `attavo frontier --json` is "not read: no plan in the store" before a plan is put. Once a plan is put and a lane branch changes `y/four.py`: wave 0 is that branch; #1 is in wave 1, #2 (which depends on #1) in wave 2, #4 in wave 2 with the why "waits on branch lane/9-other: y/four.py", and #3 in wave 3 with "yields shared.py to #2, ranked above"; #6 (no Claims) is not placed; epic progress reads E1 one of three and Backlog none of three; the plan version and the order are named; `git status --porcelain` is empty. `tests/test_frontier.py` carries the nine tests named and `tests/test_issues.py` the class test.
