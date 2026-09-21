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
    attavo drive [N] [--until-ready | --report]
    attavo ledger [N] [--json]
    attavo stops [N] [--json]

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
file under paths.records/N/ and never overwrites one already written. See
the spec contract in the project's Phase 1 issue for the full rules each
one enforces.

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
whole-tree check, from the tenant root. pass requires every criterion,
the test run, and the tenant's checks (if declared) to exit 0; any
non-zero exit iterates, with the failure as feedback, up to a fixed
budget. [checks] is optional; a tenant that declares none is not
refused. It writes the next build-NN.json under paths.records/N/, with
the adapter's transcript -- each iteration's gate output (criteria
exits, the selected tests' stdout, the tenant's checks' tail) appended
under an "--- attavo gates, iteration N ---" heading, issue #89 -- beside
it as build-NN.log. Every record whose status is not pass carries stop:
{class, provenance, detail}, derived from the refusal, halted, and log
already on hand (issue #89); a pass record carries stop: null. See the
build contract in the project's Phase 2 issue for the full rules it
enforces.

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
read-only question about it: the record, its log, the spec (problem,
claims, criteria, stub, including stub/.removed), paths.rulings, the
item's diff since approval, the tenant manifest, and this product's own
root (bin/attavo, adapters/, schema/, templates/) by path, under the
same sandbox verify runs under. The adapter answers with class (the
stop enumeration, or unclassed), verdict (answer: a ruling text with
citations; patch: a product defect's file, symbol, rejection test, and
diff; person: a one-line question no rule covers), citations
(instruction, mechanism, guard, each a path and symbol or a ruling id),
and proposal (the ruling text, the patch, or the question). It writes
the next counsel-NN.json under paths.records/N/, with the adapter's
transcript beside it as counsel-NN.log, and commits nothing -- the
caller commits, as with every stage. It refuses (exit 1, nothing
written) when the item carries no non-pass build, verify, or spec-gate
record to counsel. See issue #100's lane for the full rules it
enforces, and issue #133's for the spec-gate fallback.

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

drive reads an item's latest records and a forge's own check conclusions
and performs the next mechanical step, and only that step, then stops: no
valid approval refuses by name and runs nothing; no passing build bound to
that approval runs build; a passing build with the forge green on the
branch head (polled for a bound, not the manifest) runs verify; a passing
verify with the forge still green prints that the item is ready to merge
and stops there -- the merge is never drive's own act, and it enqueues
nothing. A record build or verify writes that is not pass is counseled (counsel,
issue #100) and filed (file-stops, issue #89) before it is transported,
so its .filed marker exists in time to be committed alongside it; drive
then commits the stage record and the counsel record together -- each
with its own log and marker -- with `git commit --only`, never sweeping
the index, and pushes; a push a divergent remote rejects
is retried once after `git pull --no-rebase --no-edit`, never a rebase,
since the record already names the commit's sha. drive writes no record
of its own: a second run in the same state repeats the same step or the
same stop, reading exactly what the prior run left behind -- the marker
included, so a stop already filed is never filed again, from this
worktree or a fresh one. See issue #84's lane for the full rules it
enforces.

--until-ready (issue #100's own followups) repeats step-and-wait --
step, the forge poll already inside a step, step again -- in the same
process, rather than making the caller invoke drive again after every
passing step, until the item is ready to merge or a step stops on its
own account (a refusal, a halted or failed or refused record, or the
forge never going green); a step that stops still prints exactly what
the same step would print without the flag. Without --until-ready,
drive is unchanged: one step, then stop, exactly as before.

--report (reconciler R1) prints, as JSON against schema/drive-report.schema.json,
what item N is waiting on: its state, whose move it is (yours, waits, acts,
done), and one sentence in the operator's words. It reads what drive reads --
the item's records, its invocation markers, one read of the forge -- and does
nothing else: it writes no record, starts nothing, terminates nothing, and
does not poll, so a concluded red check reads as blocked-on-checks for as long
as it is red. It exits 0 on every state. See issue #206's lane for the states.

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

import sys

from attavo.approval import cmd_approval_status, cmd_approve
from attavo.attest import cmd_attest
from attavo.build import cmd_build
from attavo.counsel import cmd_counsel
from attavo.doctor import cmd_doctor
from attavo.drive import cmd_drive
from attavo.filing import cmd_file_findings, cmd_file_stops
from attavo.ledger import cmd_ledger
from attavo.record_only import cmd_record_only
from attavo.spec import cmd_spec_gate
from attavo.stops import cmd_stops
from attavo.testtool import cmd_test
from attavo.verify import cmd_verify


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0 if argv else 2
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
    if argv[0] == "ledger":
        return cmd_ledger(argv[1:])
    if argv[0] == "stops":
        return cmd_stops(argv[1:])
    if argv[0] == "test":
        return cmd_test(argv[1:])
    print(f"attavo: unknown subcommand {argv[0]!r}; only doctor, spec-gate, approve, "
          f"approval-status, build, verify, file-findings, file-stops, counsel, record-only, "
          f"attest, drive, ledger, stops, test exist at this commit", file=sys.stderr)
    return 2
