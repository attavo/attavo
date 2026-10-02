from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from attavo.common import MANIFEST, git, now_iso, product_version, read_manifest
from attavo.records import latest_attempt_path, latest_record, parse_attempts
from attavo.types import (ApprovalRecord, AttestRecord, BuildRecord, ItemCost, ItemTime,
                          ItemTokens, SpecRecord, StageTime, StageTokens, VerifyRecord)

# --- attest: the signed envelope over one item's whole traversal ---

def derive_attest_items(root: Path, records_rel: str) -> tuple[set[str], str | None]:
    """The item ids from the merge commit's own first-parent diff -- record
    paths under records_rel/<N>/, not a branch name, which a squash merge
    discards and which names a forge's convention the product does not
    know. Returns (items, None), else (empty set, reason) only when the
    diff itself could not be read -- zero items found is not an error
    here (issue #58): not every merge is an item merge, and the caller
    decides what zero, one, or more than one means."""
    try:
        changed = git(root, "diff", "--name-only", "HEAD^1", "HEAD").splitlines()
    except RuntimeError as exc:
        return set(), f"git: {exc}"
    prefix = records_rel.rstrip("/") + "/"
    items: set[str] = set()
    for p in changed:
        if p.startswith(prefix):
            rest = p[len(prefix):]
            item = rest.split("/", 1)[0]
            if item:
                items.add(item)
    return items, None


INVOCATION_STAGES = ("build", "verify", "counsel")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def item_tokens(records_dir: Path) -> ItemTokens:
    """The item's tokens, summed from the records attest already reads
    (issue #309): every build, verify, and counsel attempt's input and
    output, per stage and for the item, never the final attempt alone. A
    record whose tokens are not two numbers is named in not_read and makes
    each sum it belongs to partial -- absent is never zero."""
    stages: dict[str, StageTokens] = {}
    not_read: list[str] = []
    for stage in INVOCATION_STAGES:
        total_in = total_out = 0
        partial = False
        for n, rec in sorted(parse_attempts(records_dir, stage).items()):
            tok = rec.get("tokens")
            tok = tok if isinstance(tok, dict) else {}
            read_in, read_out = tok.get("input"), tok.get("output")
            if isinstance(read_in, (int, float)) and isinstance(read_out, (int, float)) \
                    and not isinstance(read_in, bool) and not isinstance(read_out, bool):
                total_in += int(read_in)
                total_out += int(read_out)
            else:
                partial = True
                not_read.append(f"{stage}-{n:02d}")
        stages[stage] = {"input": total_in, "output": total_out, "partial": partial}
    return {
        "build": stages["build"], "verify": stages["verify"], "counsel": stages["counsel"],
        "input": sum(st["input"] for st in stages.values()),
        "output": sum(st["output"] for st in stages.values()),
        "partial": bool(not_read),
        "not_read": not_read,
    }


def item_accounting(records_dir: Path, sealed_at_epoch: int | None
                    ) -> tuple[ItemTime, ItemCost, ItemTokens]:
    """The item's `time`, `cost` and `tokens`: the statement's whole
    accounting, from one call doctor's sixth proof repeats."""
    item_time, item_cost = item_time_and_cost(records_dir, sealed_at_epoch)
    return item_time, item_cost, item_tokens(records_dir)


def item_time_and_cost(records_dir: Path, sealed_at_epoch: int | None) -> tuple[ItemTime, ItemCost]:
    """The item's accounting, summed from the records attest already reads
    (issue: a view showed #158 as 20 minutes, its final build plus its final
    verify, where its nineteen invocations ran 115): every build, verify,
    and counsel attempt's wall_clock_s and cost_usd, per stage and for the
    item, never the final attempt alone. A record that carries no number
    is named in not_read and makes each sum it belongs to partial --
    absent is never zero. lead_s runs from the first approval record's
    `at` to sealed_at_epoch (the committer time of the commit the statement
    is computed at); both it and waiting_s are None when either end cannot
    be read. doctor's sixth proof calls this same function over the same
    records, sealed at the same commit, and fails a sealed statement any
    field of whose time or cost is not what this returns."""
    stages: dict[str, StageTime] = {}
    time_not_read: list[str] = []
    cost_not_read: list[str] = []
    by_stage: dict[str, float] = {}
    by_adapter: dict[str, float] = {}
    for stage in INVOCATION_STAGES:
        attempts = parse_attempts(records_dir, stage)
        seconds = 0.0
        partial = False
        for n, rec in sorted(attempts.items()):
            name = f"{stage}-{n:02d}"
            wall = rec.get("wall_clock_s")
            if _is_number(wall):
                seconds += float(wall)
            else:
                partial = True
                time_not_read.append(name)
            cost = rec.get("cost_usd")
            if _is_number(cost):
                by_stage[stage] = by_stage.get(stage, 0.0) + float(cost)
                adapter = rec.get("adapter")
                key = adapter if isinstance(adapter, str) and adapter else "not read"
                by_adapter[key] = by_adapter.get(key, 0.0) + float(cost)
            else:
                cost_not_read.append(name)
        stages[stage] = {"attempts": len(attempts), "invocation_s": round(seconds, 3),
                         "partial": partial}

    invocation_s = round(sum(st["invocation_s"] for st in stages.values()), 3)
    lead_s: float | None = None
    first_approval = parse_attempts(records_dir, "approval")
    if first_approval and sealed_at_epoch is not None:
        at = first_approval[min(first_approval)].get("at")
        try:
            approved = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
            lead_s = round(sealed_at_epoch - approved.timestamp(), 3)
        except ValueError:
            time_not_read.append(f"approval-{min(first_approval):02d}")
    item_time: ItemTime = {
        "build": stages["build"], "verify": stages["verify"], "counsel": stages["counsel"],
        "invocation_s": invocation_s,
        "lead_s": lead_s,
        "waiting_s": None if lead_s is None else round(lead_s - invocation_s, 3),
        "partial": any(st["partial"] for st in stages.values()),
        "not_read": time_not_read,
    }
    item_cost: ItemCost = {
        "usd": round(sum(by_stage.values()), 6),
        "by_stage_usd": {k: round(v, 6) for k, v in by_stage.items()},
        "by_adapter_usd": {k: round(v, 6) for k, v in by_adapter.items()},
        "partial": bool(cost_not_read),
        "not_read": cost_not_read,
    }
    return item_time, item_cost


NOT_YET = object()

ChainRecords = tuple[SpecRecord, ApprovalRecord, BuildRecord, VerifyRecord]


def check_attest_chain(records_dir: Path, item_id: str, partial: bool = False
                       ) -> tuple[ChainRecords | object | None, str | None]:
    """The two assertions attavo attest refuses on: the latest spec,
    approval, build, and verify records for item_id all exist, and
    build's approval_sha names the latest approval -- a re-approval after
    a build must force a re-run of what changed. Returns (None, message)
    on either of them, the message being the same text attest prints, not
    a copy of it a caller re-types (issue #74: proof two reuses it);
    returns ((spec, approval, build, verify), None) when the chain holds.

    A verify record can only name a build that existed when it was
    written (issue #109), so a latest verify whose build_sha is not the
    latest build's sha is never a corrupt chain -- it is a verify
    superseded by a later build, and is treated as absent: the next
    stage is verify, not a refusal, exactly as if no verify record
    existed yet.

    partial (issue #67) is for a pull request, where a later stage's
    record cannot exist yet because the stage that would produce it is
    itself waiting on this check. Under partial, a missing (or
    superseded) record is not a refusal -- the chain assertion still
    runs over whatever records exist, and (NOT_YET, message) is
    returned, the message naming the item and the stages present and
    absent -- or, for a superseded verify, the superseded record and the
    build it named -- when a stage is still missing once that assertion
    holds."""
    latest_spec = latest_record(records_dir, "spec")
    latest_approval = latest_record(records_dir, "approval")
    latest_build = latest_record(records_dir, "build")
    latest_verify = latest_record(records_dir, "verify")

    # A verify naming a sha other than the latest build's is superseded:
    # treated as absent for the chain, never as a mismatch.
    superseded = None
    if latest_build is not None and latest_verify is not None:
        named_sha = latest_verify.get("build_sha")
        if named_sha != latest_build.get("sha"):
            verify_n = max(parse_attempts(records_dir, "verify"))
            build_attempts = parse_attempts(records_dir, "build")
            named_build_n = next((n for n, rec in build_attempts.items()
                                  if rec.get("sha") == named_sha), None)
            named_build = f"build-{named_build_n:02d}" if named_build_n is not None else named_sha
            superseded = (verify_n, named_sha, named_build, max(build_attempts))
            latest_verify = None

    stages = (("spec", latest_spec), ("approval", latest_approval),
              ("build", latest_build), ("verify", latest_verify))

    if not partial:
        for stage, rec in stages:
            if rec is None:
                if stage == "verify" and superseded is not None:
                    verify_n, named_sha, _, _ = superseded
                    return None, (f"no verify names the latest build "
                                 f"(verify-{verify_n:02d} names {named_sha})")
                return None, f"no {stage} record for item {item_id}"

    if latest_approval is not None and latest_build is not None:
        if latest_build.get("approval_sha") != latest_approval.get("sha"):
            return None, ("build's approval_sha does not name the latest approval; a "
                          "re-approval after the build must force a re-run")

    missing = [stage for stage, rec in stages if rec is None]
    if partial and missing:
        present = [stage for stage, rec in stages if rec is not None]
        if missing == ["verify"] and superseded is not None:
            verify_n, _, named_build, latest_build_n = superseded
            return NOT_YET, (f"item {item_id} not yet (present: {', '.join(present) or 'none'}; "
                             f"verify-{verify_n:02d} names {named_build}, "
                             f"superseded by build-{latest_build_n:02d})")
        return NOT_YET, (f"item {item_id} not yet (present: {', '.join(present) or 'none'}; "
                         f"absent: {', '.join(missing)})")

    # Every record is present here: without partial, the loop above refused on
    # a missing one; with partial, `missing` non-empty returned NOT_YET.
    return cast(ChainRecords, (latest_spec, latest_approval, latest_build, latest_verify)), None


def cmd_attest(argv: list[str]) -> int:
    partial = "--partial" in argv
    positional = [a for a in argv if not a.startswith("--")]
    unknown = [a for a in argv if a.startswith("--") and a != "--partial"]
    if unknown:
        print(f"attavo attest: unknown argument {unknown[0]!r}", file=sys.stderr)
        return 2

    root = Path(".").resolve()
    m = read_manifest(root)
    if m is None:
        print(f"attavo attest: no {MANIFEST} at {root}", file=sys.stderr)
        return 2

    records_rel = m["paths"]["records"]
    item_id = positional[0] if positional else None
    if item_id is None:
        items, err = derive_attest_items(root, records_rel)
        if err:
            print(f"attavo attest: refused: {err}", file=sys.stderr)
            return 1
        # Not every merge is an item merge -- a merge that carries no item
        # at all is an ordinary fact, not a defect (issue #58); the
        # attest template runs this on every push and has nothing to
        # sign or commit when nothing was attested, and its existing
        # `if:` guards already read this step's own exit code.
        if not items:
            print("attavo attest: no item derived from the merge commit's record paths; "
                  "nothing to attest")
            return 0
        if len(items) > 1:
            print(f"attavo attest: refused: {len(items)} items derived from the merge "
                  f"commit's record paths: {', '.join(sorted(items))}", file=sys.stderr)
            return 1
        item_id = next(iter(items))

    records_dir = root / records_rel / item_id
    chain, message = check_attest_chain(records_dir, item_id, partial=partial)
    if chain is None:
        print(f"attavo attest: refused: {message}", file=sys.stderr)
        return 1
    if chain is NOT_YET:
        print(f"attavo attest --partial: {message}")
        return 0
    latest_spec, latest_approval, latest_build, latest_verify = cast(ChainRecords, chain)

    spec_path = latest_attempt_path(records_dir, "spec")
    approval_path = latest_attempt_path(records_dir, "approval")
    build_path = latest_attempt_path(records_dir, "build")
    verify_path = latest_attempt_path(records_dir, "verify")

    # sha and merge_sha are the same commit here -- the one this statement
    # was computed at. On the merge group that commit is the one main will
    # hold; kept as two fields because a forge-specific template signs
    # this statement against "the commit that lands", naming it merge_sha
    # is what makes that unambiguous to a reader who has never seen attest
    # run anywhere but the merge group.
    head_sha = git(root, "rev-parse", "HEAD").strip()
    try:
        sealed_at: int | None = int(git(root, "show", "-s", "--format=%ct", "HEAD").strip())
    except (RuntimeError, ValueError):
        sealed_at = None
    item_time, item_cost, item_tokens_ = item_accounting(records_dir, sealed_at)

    statement: AttestRecord = {
        "sha": head_sha,
        "merge_sha": head_sha,
        "item": item_id,
        "spec": spec_path.stem if spec_path else None,
        "spec_sha": latest_spec.get("sha"),
        "approval": approval_path.stem if approval_path else None,
        "approval_sha": latest_approval.get("sha"),
        "build": build_path.stem if build_path else None,
        "build_sha": latest_build.get("sha"),
        "verify": verify_path.stem if verify_path else None,
        "verify_sha": latest_verify.get("sha"),
        "at": now_iso(),
        # Neither is populated by anything today -- no ruling or amendment
        # log exists yet for either to read from. Carried as empty rather
        # than omitted, since the schema names them.
        "interventions": [],
        "acts": [],
        # The only accounting: every attempt, summed (the sum across
        # executors was ruled 2026-09-19). v3's final-attempt fields are
        # retired (issue #309).
        "time": item_time,
        "cost": item_cost,
        "tokens": item_tokens_,
        "product_version": product_version(),
        "product_pin": (m.get("attavo") or {}).get("version"),
    }
    print(json.dumps(statement, indent=2))
    return 0
