from __future__ import annotations

import json
import sys
from pathlib import Path

from attavo.common import MANIFEST, git, now_iso, product_version, read_manifest
from attavo.records import latest_attempt_path, latest_record, parse_attempts

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


NOT_YET = object()


def check_attest_chain(records_dir: Path, item_id: str, partial: bool = False):
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

    return (latest_spec, latest_approval, latest_build, latest_verify), None


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
    latest_spec, latest_approval, latest_build, latest_verify = chain

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

    statement = {
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
        "tokens_by_stage": {"build": latest_build.get("tokens"), "verify": latest_verify.get("tokens")},
        # Never summed across stages -- the 2026-09-09 ruling on reporting
        # across vendors: a stage's executor may report no cost at all.
        "cost_by_stage_usd": {"build": latest_build.get("cost_usd"), "verify": latest_verify.get("cost_usd")},
        "wall_clock_by_stage_s": {"build": latest_build.get("wall_clock_s"),
                                  "verify": latest_verify.get("wall_clock_s")},
        "product_version": product_version(),
        "product_pin": (m.get("attavo") or {}).get("version"),
    }
    print(json.dumps(statement, indent=2))
    return 0
