# #299 spike: what `attavo draft` should write, and what the build then does

Spike, 2026-10-05. Branch `lane/299-spike`, base `ab2115b`. No product file changed; every prototype ran in a scratch clone (`~/Projects/attest-spike-299`, origin pointed at a local bare copy) and is described below. Tags: **[M]** a measurement (the command or record is given), **[P]** a proposal, **[I]** an inference from measurements.

## Read this first: what limits these numbers

- **n is 1 to 3 everywhere.** Three real issues, one thin draft, one vague draft, two builds, two verifies. Nothing here has a variance.
- **Neither build passed.** Both ended without a `pass` record, so H2's build half and H3's after-build verify rest on work the product did not accept (below). I used my two builds; I could not retry.
- **The machine was shared by my own runs.** Five drafts ran concurrently, then two builds concurrently. Wall times are contended.
- **The builds' failures may be environmental.** The thin build's `attavo test 3` reported 6 failures and 116 of 829 tests not run within the 240 s bound. I did not show the six fail on the base commit.
- **The drafter was given a lot.** Its instruction named `attest/specs/289` as an exemplar and `attavo/spec.py` as the gate's definition. A product drafter would have both, but H1 is partly "a model that has read the gate passes the gate". I told it not to run `spec-gate`; it did run its own criteria in scratch copies under `/tmp`.
- **The verify judge is codex (`gpt-5.6-sol`), the build executor Claude Code (`claude-sonnet-5`), per `attavo.toml`.** The drafter ran `claude-sonnet-5` too.
- **Forge checks were canned** (`ATTAVO_FORGE_CHECKS`, all `success`) for both verifies; the scratch has no forge. The comparison is fair, the checks are not real.

## Summary

| H | Finding | Tag |
|---|---|---|
| H1 | Full-stub drafts of #3, #304 and #328: **3 of 3 passed `spec-gate` on first filing.** The one thin-stub draft was refused on first filing, twice for reasons that are not the drafter's judgment (below). | M |
| H1 | A passing gate says the criteria are reachable, not that they are adequate. #304's criteria grep for the drafter's own function name and phrase. #3's draft added a `ci.yml` line the issue does not ask for. | M, I |
| H2 | Under A the gate can tell the two kinds apart without help: criteria differ in `(on_tree, on_stub)`. A 20-line scratch change let a thin spec pass. | M |
| H2 | The build did not copy anything: it never read the stub. Stub ratio on the claimed paths: **0.00 (thin build), 0.17 (full-stub build).** | M |
| H2 | The recent ratio of about 1.0 did **not** reproduce with drafted stubs. | M, I |
| H3 | Not settled. One verify found a real defect; the other found only an artifact I introduced. | M |
| H4 | The under-specified issue yielded a structured question and wrote nothing. 1 of 1. | M |
| H5 | A full draft costs **1.7 to 3.2 times** a build; a thin draft **1.4 times**. Draft wall time is **0.2 to 1.0 times** a build's. | M |

## Setup [M]

```
git clone -q ~/Projects/attest ~/Projects/attest-spike-299
git clone -q --bare ~/Projects/attest /tmp/spike-299-origin.git
git -C ~/Projects/attest-spike-299 remote set-url origin /tmp/spike-299-origin.git
```

`spike/draft.py` (scratch only, not in this PR) invokes `claude` the way `adapters/claude-code/run` does: same `--settings` sandbox, same `--disallowedTools`, `--output-format stream-json --json-schema`, model `claude-sonnet-5`. `Edit` and `Write` are allowed under `attest/specs/<N>/**` only. Its instruction holds the issue, the spec contract, and a mode paragraph (FULL: the complete change in `stub/`; THIN: red tests and bare signatures only, criteria labelled `[structural]` or `[behavioural]`). Each invocation's usage, cost, seconds and tool calls are in `draft-<N>-<mode>.json`, in `/tmp/spike-299-work/logs/` (kept after the spike; the stream transcripts sit beside them).

## H1. A drafter writes a spec that passes the gate on first filing

**Measurement [M].** Drafted #3, #304 and #328 in FULL mode, committed each spec as it came, ran `python3 bin/attavo spec-gate N`:

| issue | spec-gate | criteria `(on_tree, on_stub)` |
|---|---|---|
| #3 | pass | 3 criteria, each `(non-zero, 0)` |
| #304 | pass | 3 criteria, each `(non-zero, 0)` |
| #328 | pass | 4 criteria, each `(non-zero, 0)` |

Records: `attest/records/{3,304,328}/spec-01.json` in the scratch clone.

Thin mode on #3 (first filing, today's gate): **refused**, `stub touches a path the claims do not name: tests/__pycache__/test_doctor_gate_runs.cpython-314-pytest-9.1.1.pyc`. The drafter's own collect run left the file in the stub directory. I deleted it and re-ran the gate: **refused**, `criterion fails against the tree plus stub: proof-one-reads-the-gates-exit.sh`. That second refusal is the thin design working as intended (the behavioural criterion cannot pass on a thin stub), not a drafting error.

**What the passes do not show [M, I].**
- #304: `a-pass-carries-a-stranded-record-to-origin-before-it-acts.sh` greps for `carry_stranded_records` and its call before running a test (the drafter's note says so). The criterion is true of the drafter's code, not of the issue's behaviour.
- #3: the stub touches `.github/workflows/ci.yml`, which the issue does not mention; the drafter flagged it as unsure. Its reason: without the line the doctor job's proof one goes red once the proof is correct.
- #328: all four criteria are `python3 -m pytest` one-liners on stub tests. That is a fair shape, but it makes the stub's tests the whole spec.

**Says about A, B, C.** Drafting succeeds; the first-filing gate result does not discriminate between the options for full stubs. **[I]** The thin gate failure shows the gate, not the drafter, is what A has to change.

## H2. Under A: thin-stub criteria, a gate that tells kinds apart, and a build with a low stub ratio

**Thin criteria are red on the tree [M].** `tests-are-present-and-collectable.sh` `(4, 0)`; `proof-one-reads-the-gates-exit.sh` `(1, 1)` (`attest/records/3/spec-02.json`, thin worktree).

**The gate can tell the kinds apart mechanically [M].** `run_criteria` already records both exits. A structural criterion is `(non-zero, 0)`; a behavioural one is `(non-zero, non-zero)`. I prototyped A in 20 lines (`attavo/spec.py` in the thin worktree; the patch is `/tmp/spike-299-work/option-a-gate.patch`): a criterion that `spec.md` labels `[behavioural]` is required to fail on the thin stub; one labelled `[structural]`, or unlabelled, is required to pass on it as today. The thin #3 spec then passed (`spec-03.json`, each criterion carries its `kind`). **[P]** The label is a declaration the gate checks against the measured exits, so a mislabel is refused by name.

**The build [M].** `python3 bin/attavo build 3` in each worktree, both started together, from commit `d0630be` (thin) and `7280948` (full):

| | thin stub (A) | full stub (C or B's draft) |
|---|---|---|
| build record | `halted`, a question: 707 passed, 6 failed, 116 not run at the 240 s bound; the change is uncommitted | `refused`, class `test-bypass`: the executor ran `git stash push -- ... && python3 -m pytest` outside `attavo test` |
| tokens in / out | 471,807 / 12,757 | 751,399 / 12,638 |
| cost, seconds | $0.381, 723.8 s | $0.463, 659.0 s |
| turns / tool calls | 15 | 19 |
| read the stub or the spec directory | no | no |
| `attavo/doctor.py` change | 9 lines | 9 lines |
| the spec's criteria on the built tree | both exit 0 | all three exit 0 |
| the stub's three tests, on the built tree | 3 passed (`pytest tests/test_doctor_gate_runs.py`) | the builder's own test file differs from the stub's |

Records: `attest/records/3/build-01.{json,log}` in each worktree. The builder reported `status: question` (thin) and `status: done` (full); the product's own refusal for the full build is a process rule, not a defect found in the diff.

**Stub ratio [M].** The verify record's `stub_ratio` is polluted by scratch commits in the diff (it counted `spike/draft.py`; 0.023 and 0.136 on the two verify records). I recomputed with the product's own `attavo.verify.stub_ratio` on the claimed paths only (`attavo/doctor.py`, `.github/workflows/ci.yml`, and the stub's test files, which the function excludes as tests):

- thin build, working tree against the approved tree: **0.0**
- full-stub build, `git diff` since approval: **0.1667** (2 of the 12 non-test lines added matched the stub's lines at the same path; I did not check which two)
- the stub applied as-is, same function: **1.0** (a sanity check that the function reads what I think)

**Says about A, B, C [M, I].** The build wrote its own change under both designs. It never opened the stub; it converged on near-identical code because the issue and the criteria pin the change down. So:
- **[I]** the premise that the build "re-types the stub" did not reproduce with a drafted stub on this issue (n=1). The 1.0 on recent items came from hand-written specs, which describe the change in the Problem down to the function names (`attest/specs/289/spec.md` does). That is an inference; I did not measure it.
- **[I]** the stub ratio counts copied *text*. It cannot tell "the builder re-typed the stub" from "the criteria dictate the code". Under A it would read near 0 whether or not the builder authored anything.
- the thin build and the full build produced the same logic. For a nine-line change, thin stubs buy nothing in the code; they save the drafter work (H5).

## H3. Under B: a verify that did not write the change

**Measurement [M].** Two verifies (`attavo verify 3` with `ATTAVO_FORGE_CHECKS` canned), both codex `gpt-5.6-sol`, both returned `pass` with one finding:

| | verify after a real build | verify of the stub applied, no build |
|---|---|---|
| tree | the full build's commit `8197133` | the stub overlaid, commit `4cf74a0` |
| tokens in / out, seconds | 176,591 / 2,488; 137.0 s | 128,679 / 2,416; 132.1 s |
| finding | "Passing build record contains no recorded criteria or checks" | "An accepted gate masks a later gate that could not run" |
| `tests_named` | the build-record test | `test_proof_one_names_both_accepted_and_unable_gates` |

**Why this is not a clean comparison [M].** The build was `refused`, so there was no `pass` record for verify to read. I edited a copy of its record (`status`, `refusal`, `stop`, `refusal_kind` set to pass/null; the original is `build-01.original.json`). The refused record has `criteria: []` and `checks: null`, so verify's one finding is about the record I edited, not about the change. The no-build verify's record was built the same way (the build record copied, status pass, `sha` the stub commit, adapter named `stub-apply`).

**What the no-build finding is [M].** In both builds and in the stub, `proof_every_gate_rejects` still returns on `accepted` before it reports `unrun`. The finding is real, it applies to the builder's code and the stub's alike, and only the no-build verify reported it.

**Says about A, B, C [I].** One verify that did not write the change (and did not follow a build) read the code and found a real defect. That is a lower bound on B: independence of the judge from the author did not cost a finding. It does not say B's verify matches today's, because the comparison arm did not look at the code. **I cannot settle H3 on this data.** A fair run needs a build that passes, a record nobody edited, and a second verify on the same diff.

## H4. A vague issue yields a question, never a guess

**Measurement [M].** Drafted a one-sentence issue I wrote (`#9004: Make the report nicer ...`, not filed) in FULL mode. Result: `status: question`, nothing written under `attest/specs/9004/`, 19.5 s, $0.216, 8 turns. The question names the likely subject (`attavo drive N --report`, `attavo/report.py`) and what is missing: which fields the `says` line or JSON must carry, for which stopped or waiting input, and asks for one case with exact text. It ends with `?`.

**Says about A, B, C [I].** Independent of the option: the drafter's question channel works. Its question carries a guess as the framing (the likeliest report); whether the product should accept that, or require a question that names only what is missing, is a rule to write (decision 4).

## H5. Cost and wall time

**Measurement [M]** (`/tmp/spike-299-work/logs/draft-*.json`, builds from `build-01.json`; every figure is the invocation's own reported cost, `claude-sonnet-5` on a subscription login):

| invocation | tokens in (incl. cache) / out | cost | seconds | turns |
|---|---|---|---|---|
| draft #3, full | 981,049 / 28,703 | $0.792 | 141.7 | 28 |
| draft #328, full | 1,434,166 / 44,719 | $1.136 | 349.1 | 24 |
| draft #304, full | 2,883,060 / 46,001 | $1.493 | 647.7 | 38 |
| draft #3, thin | 571,189 / 16,473 | $0.524 | 82.5 | 13 |
| draft #9004, vague | 164,432 / 2,930 | $0.216 | 19.5 | 8 |
| build #3, full | 751,399 / 12,638 | $0.463 | 659.0 | 21 |
| build #3, thin (halted) | 471,807 / 12,757 | $0.381 | 723.8 | 15 |
| verify, after a build (codex) | 176,591 / 2,488 | n/a | 137.0 | n/a |
| verify, no build (codex) | 128,679 / 2,416 | n/a | 132.1 | n/a |

Codex reports no cost in the record (`cost_usd: null`).

**Says about A, B, C [M, I].**
- A draft is **not within a build's cost**: full drafts are 1.7 to 3.2 times the $0.463 build; the thin draft is 1.4 times the thin build. Within a build's wall time: 0.2 to 1.0 times.
- Same issue, #3: B (full draft, no build) is $0.79 and 142 s. A (thin draft plus build) is $0.52 + $0.38 = $0.90 and 82 s + 724 s = 806 s. On this one item B is cheaper by about 12% and faster by about 5.7 times. C is $0.79 + $0.46 = $1.25: the most expensive way to get the same code.
- The five drafts ran together; their seconds are contended.

## Recommendation

**[P] A, with the gate change, over B; and C stays rejected.** The evidence for B is cost and time on one item; the evidence for A is that the builder authors regardless of stub size, so the build stage is already doing the work A wants measured, and the cheaper thin draft hands it the same change. B is not refuted: its verify arm is the better result in H3, and it is the faster pipeline. If the operator's priority is time per item, B is the choice the numbers favour. I recommend A because it keeps one invocation that authors and a different one that judges, and the spike has no evidence that the builder is a cost worth removing.

C is rejected, with one reason found: C pays for the stub and then a build that does not read it ($1.25 for the code A or B gets for $0.79 to $0.90 here).

## Decisions for the operator

1. **A or B.** The data: B was cheaper and faster on one item; A keeps authorship and judgment apart; neither build passed, and H3 is unsettled. Rule on the principle, or ask for a clean re-run first (one passing build, one unedited record, two verifies on one diff).
2. **How the gate knows a criterion is behavioural.** A declared `[behavioural]` label checked against the measured exits (what I prototyped), or inferred from `(on_tree, on_stub)` alone. The first refuses a mislabel by name; the second cannot tell a behavioural criterion from a broken structural one.
3. **What the stub ratio means after this.** It read 0.0 and 0.17 here, and it cannot separate re-typing from convergence. Keep it as a text-copy measure, retire it, or replace it with something the build did (for example, whether the builder opened `stub/`).
4. **What the drafter may see and what it may do.** The spike gave it the gate's source, an exemplar and a shell. Rule on: the exemplar and the gate's source as fixed inputs; whether its shell may run criteria; whether a question may carry a guess; a cost ceiling per draft (full drafts here ran $0.79 to $1.49).

## What I decided that the brief left open

- Used `claude-sonnet-5` for the drafter, the manifest's build model, so draft and build costs are comparable.
- Drafted #3 twice (full and thin) in separate worktrees of the scratch clone so one issue could carry both arms and the builds.
- Edited the refused build record to `pass` for the verify (disclosed in H3), and built the no-build record by hand.
- A `git push` of a scratch branch to the local bare origin was refused by the session's permission classifier and not retried. `attavo build` does not push, so nothing needed it.
- The scratch clone, its two extra worktrees and `/tmp/spike-299-origin.git` are removed when the PR is open; `/tmp/spike-299-work` (the issue texts, drafter logs and the option-A patch) is kept.
