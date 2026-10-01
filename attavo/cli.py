"""attavo: the command.

    attavo doctor [TENANT_ROOT] [--json]
    attavo spec-gate [N]
    attavo approve [N] --by LOGIN
    attavo approval-status [N]
    attavo build [N]
    attavo verify [N]
    attavo file-findings [N]
    attavo file-stops [N]
    attavo counsel [N]
    attavo record-only
    attavo attest [--partial] [N]
    attavo drive [N] [--until-ready] [--again --by LOGIN] [--report]
    attavo drive --all
    attavo worker N <build|verify> NN
    attavo ledger [N] [--json]
    attavo claims [N] [--json]
    attavo plan get [--version V] | put FILE --base V --by LOGIN | log
    attavo plan rank <EPIC|#N> (--before <EPIC|#M> | --last) --by LOGIN
    attavo plan move #N --epic EPIC --by LOGIN
    attavo frontier [--json]
    attavo stops [N] [--json]
    attavo test [N] [--all | <test>...] [--fresh]

The doctor is the definition of done. It asserts five proofs against a
tenant root and exits 1 until every proof holds:

  1. every-gate-rejects      every gate in the manifest's [gates] table,
                             run against its rejection fixture, exits non-zero,
                             and carries a non-empty retires
  2. one-item-every-stage    one work item under paths.records carries a
                             record for every stage -- spec, approval,
                             build, verify -- all four introduced by one
                             merge commit reachable from HEAD
  3. no-commit-lacks-record  every commit on the default branch after the
                             manifest was introduced is named by a record's
                             sha or commits list, or itself writes under
                             paths.records (a record is committed with the
                             code it describes; an item's merge commit
                             carries its own record paths this way)
  4. no-stop-lacks-cause     every build or verify record under
                             paths.records at record_version 3 or later,
                             with a status other than pass, carries a stop
                             naming an enumerated class and provenance, a
                             non-empty detail when unclassed, the gates
                             heading when a build failed after running
                             them, and the tool's own error line when an
                             adapter exit ended the log -- records before
                             record_version 3 are counted and reported,
                             never refused
  5. every-attested-item-is-sealed
                             every merged item that carries a record for
                             every stage has a statement in the forge's
                             attestation store for its merge commit, read
                             through [forge].attestation_command -- a
                             merge at or before [forge].sealed_since is
                             named as before the window instead of failed.
                             [forge].attestation_command exiting 2 means
                             the store itself could not be read, reported
                             as not_read by item and never as unsealed
  6. no-attested-item-is-empty
                             no attested item is empty: every merged,
                             fully-staged item's passing build record
                             names a change, sha not equal to start_sha.
                             [doctor].built_since names a commit at or
                             before which merges are named as before the
                             window instead of failed, so the specimen
                             that motivated this proof stands on the
                             record without failing the tenant forever;
                             absent, the window is proof three's. A build
                             record with no start_sha is counted as not
                             readable, never as failed.

Nothing in doctor is a gate. The doctor reads; it never writes and it never
blocks a merge by itself. It is what CI runs to say whether the product
is done.

spec-gate, approve, and approval-status are gates and writers. They run
from the tenant root (the current working directory); N, the work item id,
is read from the ATTAVO_ITEM environment variable when set, else from the
branch (attavo/N-*). The doctor's first proof sets ATTAVO_ITEM for a
rejection fixture's own item, so a gate's own command needs no argument
even while the enclosing repository sits on a real item's attavo/N-*
branch. Every run of spec-gate or approve writes the next numbered attempt
file under paths.records/N/ and never overwrites one already written. A
fix item's spec may name the root cause its change closes as one
"Root-cause: <slug>" line in the problem; spec-gate refuses a slug the
tenant's root-causes.md (beside paths.specs) does not list, or any slug
when that file is absent, before any criterion runs -- a spec with no
root-cause line is untouched. spec-gate also refuses, before any
criterion runs, "stub touches a path the claims do not name: <path>" for
the first path under stub/ (or in stub/.removed) that no claim matches.
See the spec contract in the project's Phase 1 issue for the full rules
each one enforces.

build refuses (exit 1, naming the cause, no record) unless the manifest
declares [executor].build and [tests].select and [tests].run, and unless
approval-status would print valid. It exports the executor's identity
(issue #33) into the adapter's environment as GIT_AUTHOR_NAME/EMAIL and
GIT_COMMITTER_NAME/EMAIL, set by the product and never the tenant's git
config, then invokes the named adapter (adapters/<name>/run) with the
spec, the corpus, and the branch. It refuses (status refused) a commit
between start_sha and HEAD not authored by that identity, checks that
every path touched matches a spec claim, records the adapter's own
declared "reports" -- the fields adapters/<name>/conformance.json says
it can supply with a real value (issue #41), so a field it claims and
never supplies is a defect the conformance suite catches rather than a
silent null -- then runs the criteria, the
tests [tests].select and [tests].run name for the touched paths, and --
when the manifest declares [checks].command -- the tenant's own
whole-tree check, all three run in a detached worktree of HEAD and
never in the executor's own directory: the gate reads the commit, not
the tree, so a path written and never committed cannot pass a
criterion, a test, or a check on the executor's behalf. An iteration
that ends with HEAD unchanged since start_sha is told, in the feed and
the log, that the executor committed nothing and that the gate reads
the commit, and iterates; the last iteration ending that way halts the
build instead of failing it, stop class empty, provenance executor,
detail naming that the executor committed nothing and how many paths
it left modified or untracked. pass requires every criterion,
the test run, and the tenant's checks (if declared) to exit 0, and HEAD
to have moved since start_sha; any non-zero exit iterates, with the
failure as feedback, up to a fixed budget. [checks] is optional; a
tenant that declares none is not refused. Before the adapter is invoked
it asks whether this machine can
run the checks -- of [checks].probe, run once inside [checks].probe_timeout_s
(exit 0 yes; anything else, or the bound, an environment refusal), or,
for a tenant that declares no probe, by running [checks].command whole
(exit 2 no) -- and says both the question and the answer in the feed
(issue #212). The adapter is given ATTAVO_TEST_COMMAND, `attavo test N`,
and ATTAVO_TEST_TIMEOUT_S when [tests].call_timeout_s is declared; it
is not given the tenant's checks. The claude-code adapter tells the
executor the claims at the edit: its Edit and Write are allowed on the
spec's claimed paths only (a trailing-slash claim covers its subtree) and
an edit outside them is refused when tried, and its Bash foreground is
that bound plus 60 s. It writes the next build-NN.json under paths.records/N/, with
the adapter's transcript -- each iteration's gate output (criteria
exits, the selected tests' stdout, the tenant's checks' tail) appended
under an "--- attavo gates, iteration N ---" heading, issue #89 -- beside
it as build-NN.log. Every record whose status is not pass carries stop:
{class, provenance, detail}, derived from the record's own fields (the
adapter's report-line class, a halt's question, the refusal's kind, the
gate's results), never from the transcript: a refusal for a commit the
executor did not author is class foreign-commit, provenance tenant; a
build whose own gate is red is class gate, provenance executor; a stop
with no structure to class it is unclassed, and only then does the log's
last line serve as its detail. A pass record carries stop: null. See the
build contract in the project's Phase 2 issue for the full rules it
enforces.

claims [N] [--json] reads what is in flight from git alone: every branch
under refs/remotes/origin/ not yet on the default branch
(refs/remotes/origin/HEAD), with the paths it changes outside
paths.records and paths.specs and, when it carries an item's spec, that
spec's Claims. Without N it lists only: it never takes the item from the
current branch or from ATTAVO_ITEM. With an item, it compares the item's Claims with every
other branch in flight, names each overlap by branch and path, and exits
1 when there is one. A claim is a glob; two claims overlap when either
matches the other or their literal prefixes nest. It writes nothing and
gates nothing: run it before filing or starting an item.

plan keeps the Plan's order in the product's datastore, never in git:
the epics in rank order, each with its issues in rank order, the edges
between issues, and policy. The store is <git common dir>/attavo/store/
(ATTAVO_STORE overrides it): shared by every worktree of the clone, never
staged, one file per version. get prints the plan and its version; put
writes the next version from a file and refuses a stale --base by naming
the latest version and its author; log lists versions; rank and move
change one position or one membership and write the next version. A
re-rank is a new version, not a commit: priority is working material the
attestation does not bind to.

frontier [--json] builds the waves from three reads and writes nothing:
the plan's latest version in the store (the order: epic rank, then issue
rank; an open issue the plan does not place is Backlog, last), the
tenant's issues through [forge].issues_command (state, class, Claims),
and work in flight from git (wave 0). Wave 1 takes each open issue in
order whose predecessors are closed or placed, whose Claims overlap
nothing held in flight or already in the wave, and nothing an open issue
ranked above it claims (strict opportunism); each later wave does the
same as if the earlier ones had merged. An issue with no Claims is not
placed. Every issue says why it waits. Epic progress is closed over
total. No plan, or no issues command, is "not read", exit 2.

test [N] runs the tests [tests].select names for every path the item has
changed since it left the default branch (refs/remotes/origin/HEAD),
committed or not and untracked included, paths.records aside, through
[tests].run (mode mapped, the default). On this repository [tests].run
excludes, by the pytest.mark.own_records marker and by nothing else, the
tests that read this repository's own records -- the doctor's proofs
among them -- since they belong to the doctor job alone. test N <test>... runs exactly the
named tests instead (mode named), so an executor working on one test runs
one test; test N --all runs the whole suite through [tests].run with
"tests" as the one identifier (mode all), the same bound, event, and
exclusion as the other two. Naming tests and --all together is refused
(exit 2). It prints how many were selected (or that none was, and runs
nothing), exits as the run does, and, when [tests].call_timeout_s is
declared and the run outlasts it, ends the run and everything it started
and exits 124. It exits 2 when it cannot name the changes or the selector
fails. A run that exits 0 is cached against a fingerprint of the tree
(HEAD, the diff against it, and every untracked unignored file's name and
content hash, together with the mode and the identifiers); asked again on
an unchanged tree it says so -- unchanged since the earlier run, not
rerun -- without spending a second on it; --fresh reruns anyway. A
failing run is never cached, since the next question after one is always
whether an edit fixed it. [tests].exclude_in_executor names test
identifiers left out, and said so, when ATTAVO_STAGE is set (inside an
executor); they run outside one and in CI. When ATTAVO_EVENTS is set, one
event of kind test is written per invocation, naming the mode, the count
selected, the exit code, and the seconds spent (or that the answer came
from the cache). It is the one test command an executor is given (issue
#212); the product's own transport -- drive, worker, build, verify,
counsel, file-stops -- refuses to run at all when ATTAVO_STAGE is set,
since the transport is the pass's, not the executor's.

verify refuses (exit 1) unless the item's latest build record has
status pass, and unless the manifest declares [forge].checks_command --
a tenant command, taking a full commit sha on argv, that prints one
line per check as "<name> <conclusion>". verify runs it on the build
record's sha first: any conclusion other than success, neutral, or
skipped writes verify-NN.json with status returned and calls no
adapter, because a judge is never spent on a tree the forge already
called red. With the forge green, verify calls the adapter named by
[executor].verify (defaulting to [executor].build) with the diff since
the build's approval, the spec, and the build record, and records its
findings and its declared "reports" (issue #41). [executor.<adapter>.<stage>] shadows [executor.<adapter>] for
build or verify alone; both merge parent then stage before setting
ATTAVO_ADAPTER_CONFIG, and the record's adapter_config is the merged
result. Like build, every verify record whose status is not pass
carries stop: {class, provenance, detail} (issue #89); a pass record
carries stop: null. file-findings opens one tenant issue per finding in
the item's latest verify record via [forge].file_issue_command, and
refuses (exit 1, nothing filed) a record carrying a finding with no
retires. See the verify contract in the project's Phase 2 issue for the
full rules both enforce.

file-stops opens one tenant issue per stop record of the item with no
issue filed for it yet, through [forge].file_issue_command, naming the
class, the provenance, the record path, and the log's last twenty
lines; a record it has already filed carries a <stage>-NN.filed marker
beside it, holding the filing command's own stdout, so a second run
files nothing new. Like a build or verify record, this command writes
the marker and leaves it uncommitted -- drive commits it, in the same
commit as the record it names (issue #89). It refuses (exit 1,
nothing filed) a stop whose class is unclassed and whose detail is
empty -- a stop the product cannot even describe is not filed as if it
could. See issue #89's lane for the full rules it enforces.

counsel reads the item's latest non-pass build or verify record -- or,
when neither exists, its latest refused spec-gate record, the earliest
point a question can be answered inside the product, before any
approval or executor spend (issue #133) -- and asks the tenant's
[executor].counsel adapter (defaulting to [executor].verify) a
read-only question about it: the record, the spec (problem, claims,
criteria, stub, including stub/.removed), paths.rulings, the item's
diff since approval, the tenant manifest, and this product's own root
(bin/attavo, adapters/, schema/, templates/) by path, under the same
sandbox verify runs under (issue #100, extended by the rulings of
2026-09-25). Counsel's own prior readings of the item come first, as
ATTAVO_COUNSEL_PRIOR -- oldest first, each naming the record it read,
its class, verdict, ruling text, citations and the question it
answered -- so a question about a stop already read is answered as a
follow-up to that reading, not re-derived; the record written carries
answers, the stem of the newest prior counsel record that read the
same stage record, or null for a first reading. The stage's
last events stand in for its log (ATTAVO_COUNSEL_EVENTS, its
events.jsonl's last 200 lines); the stop path hands the log too,
and a --ask only hands it when the question names the word "log". The
tenant's corpus, [counsel].reads -- repository-relative paths to the
method rules, briefs and rulings counsel reads before it answers -- is
handed as ATTAVO_COUNSEL_READS, one absolute path per line; a manifest
naming a path that does not exist is refused (exit 1, no record). The
adapter answers with class (the stop enumeration, or unclassed),
verdict (answer: a ruling text with citations; patch: a product
defect's file, symbol, rejection test, and diff; person: a one-line
question no rule covers), citations (instruction, mechanism, guard,
each a path and symbol or a ruling id), and proposal (the ruling text,
the patch, or the question). It writes the next counsel-NN.json under
paths.records/N/, with the adapter's transcript beside it as
counsel-NN.log, and commits nothing -- the caller commits, as with
every stage. It refuses (exit 1, nothing written) when the item
carries no non-pass build, verify, or spec-gate record to counsel. See
issue #100's lane for the full rules it enforces, issue #133's for the
spec-gate fallback, and the rulings of 2026-09-25 for the follow-up,
the events tail and the corpus.

record-only exits 0 when HEAD, against its first parent, touches only
paths.records; 1 otherwise; 2 with no manifest. Core logic about a git
checkout, not a forge's shell -- a workflow template's push-and-retrigger
guard is one line reading this exit code instead of carrying its own
copy of the check.

attest refuses (exit 1) unless the latest spec, approval, build, and
verify records for an item all exist and chain: the build's approval_sha
names the latest approval, and the verify's build_sha names that build --
a re-approval after a build must force a re-run of what changed. With no
N given it derives the item from the record paths HEAD's merge commit
touched under paths.records/<N>/, not from a branch name, which a squash
merge discards and which names a forge's convention the product does not
know; more than one item derived is a refusal naming what it found. Zero
is not a defect -- not every merge is an item merge -- exit 0, nothing
printed but a line saying so.

attest never writes. On a coherent chain it prints the attestation
statement -- validating against schema/attestation.schema.json -- to
stdout as JSON and exits 0: the item, its four records and their shas,
and the commit the statement was computed at (sha and merge_sha are the
same commit here; on the merge group that commit is the one that lands).
The four stage records already under paths.records are the evidence for
that statement; a forge-specific template signs the printed JSON,
unmodified, as the predicate, against the commit that lands, on the merge
group -- and the signed bundle in the forge's attestation store is the
seal over it. attest itself never signs anything and never talks to a
signing service. --partial (issue #67) is for a pull request, where the
record a complete chain would need is the output of a later stage that is
itself waiting on this check: a missing later record is "not yet" (exit
0, naming the item and the stages present and absent, no statement
printed) rather than a refusal, while the two chain assertions -- build's
approval_sha names the latest approval, verify's build_sha names the
latest build -- still run over whatever records exist. merge_group and a
push to main still demand the whole chain. See issue #69's lane for the
full rules it enforces.

drive N from the tenant's own checkout (any branch but attavo/N-*) works in
the item's working copy, ../attavo-worktree-N, which the pass ensures: made
on origin's one attavo/N-* branch at its tip when absent, pruned and made
again when git lists it but its directory is gone, fast-forwarded when
behind, its executor- or driver-authored unpushed commits pushed as a
fast-forward. A directory git does not list, tracked changes, or a commit
origin lacks by anyone else is refused, and nothing is deleted or reset.
--report there reads origin's branch in a detached worktree removed after.
When the report reads merged, the pass removes the working copy with a plain
`git worktree remove`, never --force, only when it is tracked-clean, no
marker lacks its record, and its HEAD is in origin's default branch; its
<git-dir>/attavo moves to <common git dir>/attavo/archive/<N>/ first, and
anything short of that is reported and the copy stays. `drive --all`, run
there, passes every origin attavo/<N>-* whose item is not merged, and every
existing copy whose item is merged.

drive (reconciler R2) is one short pass: it fast-forwards its working copy
to origin's branch (never while a worker holds it), reads the item's
report (attavo.report, issue #206) exactly as --report would, and acts at
most once -- one of the three states that are the product's own move --
before returning, almost always within seconds. owes-recovery writes the
died or hung record for the one invocation the report named (what
--recover used to do, by hand); next-build and next-review start their
stage. Starting a stage is the lease: drive writes the invocation marker,
commits it, and pushes that one commit as a plain fast-forward, no merge
and no retry -- a rejection that is not a fast-forward, or a ref another
push already holds, means another pass started this attempt first, and
this one says so, resets its own marker commit, and starts nothing; a
push the remote refuses outright is exit 1 and starts nothing either way.
On a pushed marker drive spawns `attavo worker N <build|verify> NN` in
its own session, its stdout and stderr appended to
<git-dir>/attavo/<stage>-NN.worker.log, and returns -- the worker
outlives the pass. Every other level is printed in the report's own
words and left alone, exit 0; exit 1 is a refusal. See issue #217's lane
(the reconciler's R2) for the full rules it enforces.

worker N <build|verify> NN is the stage itself, run in the detached child
a pass just started: build or verify in-process, exactly as the pass used
to run it inline before R2, touching
<git-dir>/attavo/<stage>-NN.heartbeat every ATTAVO_HEARTBEAT_S seconds
(15) while it runs -- what a report or a pass reads first to tell a
running invocation from a died one, falling back to the pid only when
there is no heartbeat file at all. A record the stage writes that is not
pass is counseled (counsel, issue #100) and filed (file-stops, issue #89)
before it is transported, so its .filed marker exists in time to be
committed alongside it; the worker then commits the stage record and the
counsel record together -- each with its own log and marker -- with
`git commit --only`, never sweeping the index, and pushes, with the
record push's own retry: a push a divergent remote rejects is retried
once after `git pull --no-rebase --no-edit`, never a rebase, since the
record already names the commit's sha. On a review, ATTAVO_FORGE_CHECKS
names the file the pass already wrote the forge's own read into (the
checks it saw on the head before its marker, reconciler ruling B); verify
reads that file instead of polling when it is set.

--until-ready repeats the pass in this same process, sleeping
ATTAVO_DRIVE_POLL_INTERVAL_S between passes, until the level is the
operator's or done (exit 0), a concluded check reads red (exit 1), or an
unconcluded one has not concluded within ATTAVO_DRIVE_POLL_BOUND_S (exit
1) -- a stage running in its own worker is not bounded by either
constant, only time spent waiting on the forge is. Without the flag,
drive is one pass, then stop.

--again --by LOGIN starts the next attempt of a stopped stage -- the
operator's own resume, until a later reconciler ruling drives a retry
itself; --by names who pressed it, as approve's does, and the marker
carries it. A stage's liveness is read from the worker's heartbeat, the
file the process holding it writes, never from a pid someone else
recorded. --recover
is gone: `attavo drive --recover` now exits 2 saying so; recovery is
what a pass does on its own, the moment the report owes one.

--all runs one pass, in its own process, in every ../attavo-worktree-N
beside this checkout -- each line either prints is prefixed with the
worktree's own directory name.

--report prints, as JSON against schema/drive-report.schema.json, what
item N is waiting on: its state, whose move it is (yours, waits, acts,
done), and one sentence in the operator's words. It reads exactly what a
pass reads -- the item's records, its invocation markers and heartbeats,
one read of the forge -- and does nothing else: it writes no record,
starts nothing, terminates nothing, and does not poll, so a concluded
red check reads as blocked-on-checks for as long as it is red. It exits
0 on every state. See issue #206's lane for the states.

ledger reads, never writes. For item N (or every item under
paths.records when N is omitted), it walks the commits from the item's
first spec commit to its merge (the same derivation doctor's proof two
and attest use) or to HEAD when the item has not merged, and prints one
row per act: the sha, the identity as git names it, and a class from a
closed list -- spec, amend, approve, launch, record, change, transport,
merge -- derived from who committed it and, for an approval, build, or
verify record, what the record itself says. A summary line follows:
operator acts (approve, amend, launch, transport, merge), product acts
(record), executor commits (change), stops (non-pass records), and
since (the product version on the first record). A build or verify
record's own commit, from before v0.18.0 gave the driver and the
executor their own identities, prints as record-or-launch with a note,
counted toward neither total, rather than guessed. --json prints the
rows and the summary as one object. See issue #128's lane for the full
rules it enforces.

stops reads, never writes. For item N (or every item under
paths.records when N is omitted), one row per non-pass build or verify
record: the record, its stop's class, provenance, and detail, and the
answerer -- "counsel:<verdict>" when a counsel record read this exact
stop, else "person" for a question, else "guard" for a class a guard
alone raised (GUARDED_STOP_CLASSES), else "none". A record too old to
carry stop at all prints the doctor's own phrase for it, "before stop
was recorded", and is counted there rather than by class. Totals per
class (read from the build record schema's own enum, never retyped) and
per answerer, plus the before-stop count, follow the rows. --json prints
the rows and the totals as one object. See issue #129's lane for the
full rules it enforces.
"""

from __future__ import annotations

import os
import sys

from attavo.approval import cmd_approval_status, cmd_approve
from attavo.attest import cmd_attest
from attavo.build import cmd_build
from attavo.claims import cmd_claims
from attavo.plan import cmd_plan
from attavo.frontier import cmd_frontier
from attavo.counsel import cmd_counsel
from attavo.doctor import cmd_doctor
from attavo.drive import cmd_drive, cmd_worker
from attavo.filing import cmd_file_findings, cmd_file_stops
from attavo.ledger import cmd_ledger
from attavo.record_only import cmd_record_only
from attavo.spec import cmd_spec_gate
from attavo.stops import cmd_stops
from attavo.testtool import cmd_test
from attavo.verify import cmd_verify


TRANSPORT = ("drive", "worker", "build", "verify", "counsel", "file-stops")


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0 if argv else 2
    if argv[0] in TRANSPORT and os.environ.get("ATTAVO_STAGE"):
        # The product's transport is never the executor's tool: a stage
        # runs inside an invocation whose environment names the stage,
        # and nothing under it may start, judge, or record another.
        print(f"attavo {argv[0]}: refused inside an executor (ATTAVO_STAGE is set); "
              f"the transport is the pass's, not the executor's", file=sys.stderr)
        return 2
    if argv[0] == "doctor":
        return cmd_doctor(argv[1:])
    if argv[0] == "spec-gate":
        return cmd_spec_gate(argv[1:])
    if argv[0] == "approve":
        return cmd_approve(argv[1:])
    if argv[0] == "approval-status":
        return cmd_approval_status(argv[1:])
    if argv[0] == "build":
        return cmd_build(argv[1:])
    if argv[0] == "verify":
        return cmd_verify(argv[1:])
    if argv[0] == "file-findings":
        return cmd_file_findings(argv[1:])
    if argv[0] == "file-stops":
        return cmd_file_stops(argv[1:])
    if argv[0] == "counsel":
        return cmd_counsel(argv[1:])
    if argv[0] == "record-only":
        return cmd_record_only(argv[1:])
    if argv[0] == "attest":
        return cmd_attest(argv[1:])
    if argv[0] == "drive":
        return cmd_drive(argv[1:])
    if argv[0] == "worker":
        return cmd_worker(argv[1:])
    if argv[0] == "ledger":
        return cmd_ledger(argv[1:])
    if argv[0] == "claims":
        return cmd_claims(argv[1:])
    if argv[0] == "plan":
        return cmd_plan(argv[1:])
    if argv[0] == "frontier":
        return cmd_frontier(argv[1:])
    if argv[0] == "stops":
        return cmd_stops(argv[1:])
    if argv[0] == "test":
        return cmd_test(argv[1:])
    print(f"attavo: unknown subcommand {argv[0]!r}; only doctor, spec-gate, approve, "
          f"approval-status, build, verify, file-findings, file-stops, counsel, record-only, "
          f"attest, drive, worker, ledger, claims, plan, frontier, stops, test exist at this commit", file=sys.stderr)
    return 2
