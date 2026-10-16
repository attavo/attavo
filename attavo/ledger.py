from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from attavo.build import commits_with_diffs, driver_identity
from attavo.common import MANIFEST, STAGES, git, read_manifest, git_env
from attavo.doctor import (INVOCATION_MARKER_SUFFIX, _merges_reachable, attestation_command,
                           merge_introducing_records, statement_for)
from attavo.records import RECORD_RE, _load_record
from attavo.stops import item_stops
from attavo.types import StatementIntervention
from attavo import Command

# --- ledger: every act on an item by identity and class (issue #128) ---

# operator acts (a person's own decisions and transport) vs. product acts
# (the driver's or the records app's own automated commits) vs. the
# executor's own code changes -- the three totals the plan's status table
# and every reviewing session used to count by hand. record-or-launch
# (below) contributes to neither: an honest count never guesses which
# side of that line a pre-v0.18.0 commit falls on.
LEDGER_OPERATOR_CLASSES = ("approve", "amend", "launch", "transport", "merge")

LEDGER_V018_CUTOFF = (0, 18, 0)


def _semver_tuple(version: str | None) -> tuple[int, int, int] | None:
    m = re.match(r"^v(\d+)\.(\d+)\.(\d+)", version or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _is_records_app(name: str) -> bool:
    """The forge's own app-bot login -- exactly the identity the
    attavo-spec-gate and attavo-approve templates commit spec-gate and
    approval records as (an app-token step mints it at run time from the
    tenant's own app credentials): "<app-slug>[bot]". Never a name this
    product can name in advance, so the one fact it can check is the
    shape the forge itself gives every app identity, not a specific one."""
    return name.endswith("[bot]")


def _carries(touched: list[str], specs_prefix: str, records_prefix: str) -> list[str]:
    """What a row names as its commit's own: the touched paths under
    paths.specs or paths.records for this item -- the ledger's own
    nameable record, never a guess at the rest of the tree a commit
    happened to touch alongside it."""
    return [p for p in touched if p.startswith(specs_prefix) or p.startswith(records_prefix)]


def _statement_fields(statement: dict[str, Any]) -> dict[str, Any] | None:
    """{predicate_type, subject}: the envelope's own predicateType and its
    first subject's sha256 digest -- the two fields a ledger row names,
    never the statement's own predicate (attest's own business, not a
    row's)."""
    predicate_type = statement.get("predicateType")
    subject = statement.get("subject")
    if not isinstance(predicate_type, str):
        return None
    if not (isinstance(subject, list) and subject and isinstance(subject[0], dict)):
        return None
    digest = subject[0].get("digest")
    sha256 = digest.get("sha256") if isinstance(digest, dict) else None
    if not isinstance(sha256, str):
        return None
    return {"predicate_type": predicate_type, "subject": sha256}


def _merge_statement(root: Path, m: dict[str, Any], merge_sha: str) -> tuple[dict[str, Any] | None, str | None]:
    """(statement, note): the merge's own record, read through
    [forge].attestation_command with the merge sha -- the same read
    doctor's proof six already makes (statement_for). None and a
    "not read: ..." note naming which source did not answer when the
    manifest names no command, the command cannot read the store, or the
    store holds nothing shaped like a statement for this sha -- never a
    guess at what the merge carried."""
    cmd = attestation_command(m)
    if cmd is None:
        return None, "not read: no [forge].attestation_command in the manifest"
    statement, exit_code = statement_for(root, cmd, merge_sha)
    if statement is None:
        if exit_code != 0:
            return None, f"not read: the store could not be read; {cmd!r} exited {exit_code}"
        return None, f"not read: the store holds no statement for {merge_sha[:12]}"
    fields = _statement_fields(statement)
    if fields is None:
        return None, f"not read: the store's statement for {merge_sha[:12]} has no subject digest"
    return fields, None


def first_commit_touching(root: Path, relpath: str) -> str | None:
    """The earliest commit touching relpath -- the mirror of
    last_commit_touching, which answers the latest. Ledger's own walk
    starts here: the item's first spec commit."""
    out = git(root, "log", "--reverse", "--format=%H", "--", relpath)
    lines = out.split()
    return lines[0] if lines else None


def _single_commit_with_diff(root: Path, sha: str) -> dict[str, Any]:
    """commits_with_diffs's own shape, for one commit read directly rather
    than by a range -- the one case `_commits_from` needs it for: a
    commit with no parent, which `^` cannot name."""
    out = git(root, "show", "--diff-merges=first-parent", "--name-only",
             "--format=\x01%H\x1f%an\x1f%ae", sha)
    header, _, rest = out.split("\x01")[1].partition("\n")
    c_sha, name, email = header.split("\x1f")
    return {"sha": c_sha, "name": name, "email": email,
           "touched": [line for line in rest.splitlines() if line.strip()]}


def _commits_from(root: Path, first_sha: str, end: str) -> list[dict[str, Any]]:
    """commits_with_diffs's own range, from first_sha up to and including
    end: first_sha^..end, the ordinary case. `^` cannot name the parent of
    a commit that has none -- the repository's own root commit, which a
    fixture or a squash merge can leave as an item's first spec commit --
    and `A..B` always excludes A regardless, root or not; here, first_sha's
    own diff is read directly and put first."""
    try:
        git(root, "rev-parse", "--verify", f"{first_sha}^")
    except RuntimeError:
        return [_single_commit_with_diff(root, first_sha)] + commits_with_diffs(root, first_sha, end=end)
    return commits_with_diffs(root, f"{first_sha}^", end=end)


def _record_blobs_at(root: Path, specs: list[tuple[str, str]], item_id: str) -> dict[tuple[str, str], dict[str, Any]]:
    """{(sha, relpath): record dict} for every (sha, relpath) pair, read by
    one `git cat-file --batch` process for the whole list rather than one
    `git show` per record (issue #153, ledger's only need for a record at a
    past commit rather than the working tree -- issue #128). Each blob is
    still spilled to a throwaway file and read through _load_record (issue
    #60, acceptance 6: the one place a record's bytes become a dict),
    rather than a second json.loads of its own. A pair git cannot resolve,
    or whose blob is not valid JSON, is simply absent from the result --
    the same silence _record_at once gave a missing blob."""
    if not specs:
        return {}
    stdin = "".join(f"{sha}:{relpath}\n" for sha, relpath in specs)
    out = subprocess.run(["git", "-C", str(root), "cat-file", "--batch"],
                         input=stdin.encode(), capture_output=True, check=False, env=git_env())
    data = out.stdout
    pos = 0
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for key in specs:
        newline = data.index(b"\n", pos)
        header = data[pos:newline].decode()
        pos = newline + 1
        parts = header.split(" ")
        if parts[-1] == "missing":
            continue
        size = int(parts[-1])
        content = data[pos:pos + size]
        pos += size + 1
        with tempfile.NamedTemporaryFile("wb", suffix=".json", delete=False) as fh:
            fh.write(content)
            blob_path = Path(fh.name)
        try:
            result[key] = _load_record(blob_path, item_id)
        except json.JSONDecodeError:
            continue
        finally:
            blob_path.unlink(missing_ok=True)
    return result


def item_ledger(root: Path, m: dict[str, Any], item_id: str, merges: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """{"item", "rows", "summary"}: one row per act between item_id's first
    spec commit and its merge (merge_introducing_records, the same
    derivation doctor's proof two and attest both already read) or HEAD
    when the item has not merged yet.

    merges, when given, is _merges_reachable's own result -- cmd_ledger
    computes it once and passes it here for every item, rather than each
    item walking HEAD's merges again (issue #153).

    Each commit gets exactly one class, in this order: the merge commit
    itself is "merge"; a commit authored by the executor identity
    (executor_identity) is "change"; a commit introducing an approval
    record is "approve", whoever committed it -- an operator's decision,
    however it reached git; a commit introducing a spec-gate, build, or
    verify record, authored by the driver identity (driver_identity) or
    the records app, is "record"; the same, authored by a person, is
    "launch" for build or verify (spec-gate records the app always
    commits, in every template this product ships) -- unless that
    record's own product_version predates v0.18.0, when driver_identity
    did not yet exist to tell the two apart, and the row is
    "record-or-launch" with a note, counted in neither summary total
    rather than guessed; a person's commit touching the spec is "spec"
    before the first approval record and "amend" after; anything else by
    a person is "transport" (a merge of main among them)."""
    specs_rel = m["paths"]["specs"]
    records_rel = m["paths"]["records"]
    specs_prefix = f"{specs_rel.rstrip('/')}/{item_id}/"
    records_prefix = f"{records_rel.rstrip('/')}/{item_id}/"
    empty_summary = {"operator_acts": 0, "product_acts": 0, "executor_commits": 0,
                     "stops": 0, "since": None}

    first_spec_sha = first_commit_touching(root, str(Path(specs_rel) / item_id))
    if first_spec_sha is None:
        return {"item": item_id, "rows": [], "summary": empty_summary}

    if merges is None:
        merges, refusal = _merges_reachable(root)
        if refusal:
            return {"item": item_id, "rows": [], "summary": empty_summary}
    merge_sha, _ = merge_introducing_records(root, records_rel, item_id, STAGES, merges=merges)
    if merge_sha is not None:
        branch_tip = next(mrg["parents"][1] for mrg in merges if mrg["sha"] == merge_sha)
        commits = _commits_from(root, first_spec_sha, branch_tip)
        commits += commits_with_diffs(root, f"{merge_sha}^1", end=merge_sha)
    else:
        commits = _commits_from(root, first_spec_sha, "HEAD")

    driver = driver_identity()

    # Every record a walked commit touches, read by one batched process
    # for the whole item rather than one `git show` per record (issue
    # #153): a first pass over the already-fetched diffs finds which
    # commits touch a record, then _record_blobs_at fetches them all.
    record_touches: dict[str, tuple[str, str]] = {}
    for c in commits:
        if merge_sha is not None and c["sha"] == merge_sha:
            continue
        if c["email"].startswith("executor+") and c["email"].endswith("@attavo.invalid"):
            continue
        for p in c["touched"]:
            if not p.startswith(records_prefix):
                continue
            rm = RECORD_RE.match(Path(p).name)
            if rm and rm.group(1) in STAGES:
                record_touches[c["sha"]] = (rm.group(1), p)
                break
    record_content = _record_blobs_at(
        root, [(sha, relpath) for sha, (_, relpath) in record_touches.items()], item_id)

    rows: list[dict[str, Any]] = []
    operator_acts = product_acts = executor_commits = 0
    since = None
    approval_seen = False

    for c in commits:
        sha, name, email = c["sha"], c["name"], c["email"]

        if merge_sha is not None and sha == merge_sha:
            statement, note = _merge_statement(root, m, merge_sha)
            row = {"sha": sha, "identity": name, "class": "merge", "carries": [],
                  "statement": statement}
            if note is not None:
                row["note"] = note
            rows.append(row)
            operator_acts += 1
            continue

        if email.startswith("executor+") and email.endswith("@attavo.invalid"):
            rows.append({"sha": sha, "identity": name, "class": "change",
                        "carries": list(c["touched"])})
            executor_commits += 1
            continue

        record_touch = record_touches.get(sha)

        if record_touch is not None:
            stage, relpath = record_touch
            content = record_content.get((sha, relpath), {})
            if since is None:
                since = content.get("product_version")
            carries = _carries(c["touched"], specs_prefix, records_prefix)

            if stage == "approval":
                rows.append({"sha": sha, "identity": name, "class": "approve", "carries": carries})
                operator_acts += 1
                approval_seen = True
                continue

            is_driver = name == driver["name"] and email == driver["email"]
            is_records_app = _is_records_app(name)
            if stage == "spec" or is_driver or is_records_app:
                rows.append({"sha": sha, "identity": name, "class": "record", "carries": carries})
                product_acts += 1
                continue

            version = _semver_tuple(content.get("product_version"))
            if version is not None and version < LEDGER_V018_CUTOFF:
                rows.append({"sha": sha, "identity": name, "class": "record-or-launch",
                            "note": "not distinguishable before v0.18.0", "carries": carries})
                continue

            rows.append({"sha": sha, "identity": name, "class": "launch", "carries": carries})
            operator_acts += 1
            continue

        # An invocation marker's own commit (proof five): the product's
        # act, never the operator's.
        if any(p.startswith(records_prefix) and p.endswith(INVOCATION_MARKER_SUFFIX)
               for p in c["touched"]):
            rows.append({"sha": sha, "identity": name, "class": "record",
                        "carries": _carries(c["touched"], specs_prefix, records_prefix)})
            product_acts += 1
            continue

        if any(p.startswith(specs_prefix) for p in c["touched"]):
            cls = "amend" if approval_seen else "spec"
            rows.append({"sha": sha, "identity": name, "class": cls,
                        "carries": _carries(c["touched"], specs_prefix, records_prefix)})
            if cls == "amend":
                operator_acts += 1
            continue

        # A person's transport commit: nothing to name, by design (#350).
        rows.append({"sha": sha, "identity": name, "class": "transport", "carries": []})
        operator_acts += 1

    # issue #137: the same set attavo stops counts, from the same reader
    # (item_stops) -- not a second, independent count kept in sync by
    # hand. One non-pass record per row, whatever its stage; a gate
    # refusal is included the same as a non-pass build or verify record.
    stops = len(item_stops(root, m, item_id)["rows"])

    summary = {"operator_acts": operator_acts, "product_acts": product_acts,
              "executor_commits": executor_commits, "stops": stops, "since": since}
    return {"item": item_id, "rows": rows, "summary": summary}


# The statement's own two lists (issue #349): acts is the operator's ruled
# acts (approve, merge); rule stays in the schema's enum for the ruling
# record (#276) and is never produced here. interventions is every other
# operator row -- amend, launch, transport.
ACT_CLASSES = ("approve", "merge")
INTERVENTION_CLASSES = ("amend", "launch", "transport")


def _committer_time_iso(root: Path, shas: list[str]) -> dict[str, str]:
    """Each sha's own committer time, in the same shape attest's own `at`
    fields already carry -- one `git show` process for every sha an
    item's acts and interventions need, not one git call per row."""
    if not shas:
        return {}
    out = git(root, "show", "-s", "--format=%H\x1f%ct", *shas)
    times: dict[str, str] = {}
    for line in out.splitlines():
        if not line.strip():
            continue
        sha, epoch = line.split("\x1f")
        times[sha] = datetime.fromtimestamp(int(epoch), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return times


def item_acts_and_interventions(root: Path, m: dict[str, Any], item_id: str,
                                merges: list[dict[str, Any]] | None = None
                                ) -> tuple[list[dict[str, Any]], list[StatementIntervention]]:
    """(acts, interventions): the statement's own two lists, read from
    item_ledger's own rows between the item's first spec commit and its
    merge -- each commit classed exactly once there, never classified
    twice. acts carries every approve and merge row as {act, sha, by, at};
    interventions carries every amend, launch, and transport row as
    {class, sha, by, at}. `by` is the commit's own author name
    (item_ledger's `identity`); `at` is its committer time."""
    rows = item_ledger(root, m, item_id, merges=merges)["rows"]
    act_rows = [r for r in rows if r["class"] in ACT_CLASSES]
    intervention_rows = [r for r in rows if r["class"] in INTERVENTION_CLASSES]
    times = _committer_time_iso(root, [r["sha"] for r in act_rows + intervention_rows])
    acts = [{"act": r["class"], "sha": r["sha"], "by": r["identity"], "at": times[r["sha"]]}
           for r in act_rows]
    interventions: list[StatementIntervention] = [
        {"class": r["class"], "sha": r["sha"], "by": r["identity"], "at": times[r["sha"]]}
        for r in intervention_rows]
    return acts, interventions


def cmd_ledger(argv: list[str]) -> int:
    as_json = "--json" in argv
    positional = [a for a in argv if a != "--json"]
    root = Path(".").resolve()
    m = read_manifest(root)
    if m is None:
        print(f"attavo ledger: no {MANIFEST} at {root}", file=sys.stderr)
        return 2

    records_rel = m["paths"]["records"]
    if positional:
        item_ids = [positional[0]]
    else:
        records_root = root / records_rel
        item_ids = (sorted((p.name for p in records_root.iterdir() if p.is_dir()))
                   if records_root.is_dir() else [])

    # Computed once for every item in this read, not once per item (issue
    # #153): the default branch's merges do not depend on which item is
    # being asked about.
    merges, refusal = _merges_reachable(root)
    if refusal:
        print(f"attavo ledger: refused: {refusal}", file=sys.stderr)
        return 2
    ledgers = [item_ledger(root, m, item_id, merges=merges) for item_id in item_ids]

    if as_json:
        if positional:
            print(json.dumps(ledgers[0], indent=2))
        else:
            print(json.dumps({"items": ledgers}, indent=2))
        return 0

    for entry in ledgers:
        print(f"attavo ledger: item {entry['item']}")
        for row in entry["rows"]:
            if row["class"] == "merge":
                statement = row["statement"]
                carries = (f"statement {statement['predicate_type']} {statement['subject'][:12]}"
                          if statement else "no statement")
            else:
                carries = ", ".join(row["carries"]) if row["carries"] else "nothing"
            note = f"  ({row['note']})" if "note" in row else ""
            print(f"  {row['sha'][:12]}  {row['identity']:<32}  {row['class']:<16}  {carries}{note}")
        s = entry["summary"]
        print(f"  operator acts: {s['operator_acts']}  product acts: {s['product_acts']}  "
             f"executor commits: {s['executor_commits']}  stops: {s['stops']}  "
             f"since: {s['since']}")
    if not ledgers:
        print(f"attavo ledger: no item found under {records_rel}")
    return 0


COMMANDS = (
    Command(
        name='ledger',
        run=cmd_ledger,
        usage=('attavo ledger [N] [--json]',),
        rank=140,
        help=(
            (250, """\
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
rules it enforces."""),
        ),
    ),
)
