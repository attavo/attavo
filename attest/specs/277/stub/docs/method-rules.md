# Method rules: reads that lie, and what a session owes before it writes

Carried from the pilot's document index on 2026-09-03, stripped of the pilot's specimens. These are the rules the reviewing session kept as habit and broke when tired; in Attest they are the seed of the challenge's answer contract (Phase 3): an answer that does not satisfy them is refused by a script, not by memory. Each rule was earned by at least one specimen in the pilot.

**The one rule that covers all of them: a read taken while something is in flight is stale by construction.** Report it as a timestamped observation, never as state.

1. **Directory listings are served from a cache.**
2. **A read taken while a session is mid-command** describes a moment that has passed.
3. **GitHub's API is eventually consistent.**
4. **A truncated listing is not evidence of absence.**
5. **A file transfer tool's success message is not delivery.**
6. **A ruling recorded in a project doc is not repository state.**
7. **Local `main` is stale after any merge until the operator pulls.** Branch from `origin/main`. An aborted pull leaves it stale for the rest of the session; a three-dot diff against it then attributes another lane's merged work to the branch under review.
8. **A lane branch's internal commits are checked by nobody.** The PR's checks are the truth.
9. **An operator-side project doc is not a citable source for an append-only repo record.** **Nor is a live URL.**
10. **Branch currency is verified at review time, never asserted from memory.**
11. **A required status check satisfied by "skipped" is not a check that ran.**
12. **A failed `git checkout` leaves the next command acting on the wrong ref.**
13. **The absence of a subcommand is not evidence the capability is missing.** Search the repository's scripts, commands, workflows and templates first.
14. **A red check may be an observation about the environment, not a finding about the branch.**
15. **An aggregated API row is not attribution.**
16. **A number without its counting method is not reproducible.**
17. **A local run of a check and CI's run of it are different programs.** Ask which program produced a red before diagnosing it.
18. **A red that appears only locally is a claim about the invocation, not the branch**, and the reverse: a red that appears only in CI is a claim about the environment the check named.
19. **Two workflows sharing a `name:` share `${{ github.workflow }}`.** Key concurrency on the filename; the check name in `gh pr checks` is the only way to tell which failed.
20. **A gate that is configured is not a gate that runs.** Read a check's first ten lines for the early return before citing it as enforcing. **And before authorizing any write, enumerate the sibling guards that read the same artifact.**
21. **A *successful* checkout is the more dangerous one.** It exits zero and moves a running session's HEAD silently. Tagging and merging need no working tree; a second lane gets a worktree.
22. **A CLI message, error or success, is not a statement about state.** `gh pr view <n> --json mergeable,mergeStateStatus,reviewDecision,statusCheckRollup`; for merges, `--json state,mergedAt,mergeCommit`. **A `gh run list --limit 1` after a push returns the run that push started.** Select a run by the sha it ran on, never by "latest."
23. **A gate correct within its own field of view may not reach what it protects** (the seam class: a check that is right about its inputs and blind to the thing next to them).
24. **The file in the tree is not the program the agent is running.**
25. **A grep probe's tokenization is part of its claim.** Zero-hit results from an automated probe are re-checked by hand. **A carrier claim requires the executable line that reads the value.**
26. **A reachability claim is only as good as its fetched history and its stated `merge-base --is-ancestor` argument order.**
27. **A shallow marker in the shared `.git` lies to every session and subagent at once.** Check `git rev-parse --is-shallow-repository` first.
28. **A register candidate is not a citable fact.** A finding number is citable only after the findings register holds it.
29. **A signal field is a reading of its producer, not of the world.** Read the function that computed it.
30. **A pipeline verdict and a definition-of-done verdict are two readers, not one state.**
31. **An issue number recorded in the merged register is a repo read, not a forge read.**
32. **A passing shape check is not evidence the value is usable.** **Before citing a check as proof a field is correct, read the code that consumes the field.**
33. **An API error is not an empty result set.** **Every probe asserts its own success before its result is read**, never `.get()` into a default. And every probe in a chain guards its own value with `test -n` before the next command reads it; a null carried forward exits zero.
34. **An identifier is not a reference until it resolves.** **Before writing an identifier into an artifact another session will act on, resolve it: to an issue, a sha, or a file path.**
35. **A negative search inherits the scope of the corpus searched.** Absence in a subtree is not absence in the repository.
36. **A check's answer is about a commit, not a PR.**
37. **A gate's failure count is not its rejection count.**
38. **A tracker search immediately after a create describes a moment before the create.** The number of a new issue is read from the URL its `create` printed, never from a search for its title.
39. **A command block handed over for pasting contains no placeholder.** Either the value is derived inside the block, or the block is split so the value is read first and typed into the second.
40. **`pytest --deselect` matches by nodeid prefix, not by exact string**, so a deselect line silently disables every test whose name extends the deselected one. The read that catches it is counting "N deselected" against the number of `--deselect` flags actually passed. The fix is renaming the new test, never widening the deselect.
41. **An absence is a claim, and a claim needs its own read.** Reading a writer and its outputs never establishes that no declaration exists; only looking where a declaration would live does.
42. **A fix that moves the fact to where the write is allowed, instead of moving the write to where the fact is known, is the pattern that made the pilot unusable.** Ask first: at what moment is this fact known, and what mechanism already stands there? Four tells, three of them countable: the fix adds a step, run, or commit to every item's traversal (the per-item counts in the records must not rise); it writes a fact after the event it describes, in a second commit; it ships with its own recovery path; its checks can reject nothing. The fourth is prose until the screen makes it a number: the fix has no consumer beyond this item, this tenant, this week. A one-time exception for the item in hand is how the pattern starts, and is refused on the same ground.

**Git object reads against a local ref are trustworthy. Directory listings, anything remote, anything concurrent, and anything aggregated are not.**

## The four the answer contract enforces first

- **Enumerate the sibling guards.** Before authorizing any write, name every mechanism that reads the same artifact: the instruction that asks for it, the mechanism that performs it, the guard that judges it.
- **Resolve every identifier.** An identifier is not a reference until it resolves to an issue, a sha, or a file.
- **A gate's failure count is not its rejection count.** Read what was rejected and on how many distinct pieces of work before calling a gate earned.
- **State the per-item cost of every fix, before and after.** Steps, runs, and commits per traversal, from the records. A fix whose after is larger than its before is not authorized without a ruling that names why (rule 42).
