from __future__ import annotations

import fnmatch
import contextlib
import json
import shutil
import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterator, cast

from attavo.approval import approval_status
from attavo.common import (
    MANIFEST,
    PRODUCT_ROOT,
    ShallowCheckoutError,
    derive_item_id,
    git,
    last_commit_touching,
    now_iso,
    read_manifest,
)
from attavo.events import append_event, stage_events_dir
from attavo.records import latest_record, next_attempt_number, parse_attempts, write_record
from attavo.spec import parse_spec, run_criterion
from attavo.stops import _log_last_line, derive_stop, signature_of
from attavo.testtool import run_bounded, without_records

# --- build: adapter, claims boundary, criteria, and the tenant's tests ---

STAGE_NAMES = ("build", "verify", "counsel")


def merge_adapter_config(m: dict[str, Any], adapter_name: str, stage: str) -> dict[str, Any]:
    """[executor.<adapter>] merged with [executor.<adapter>.<stage>], the
    stage table shadowing the parent for that stage only. Both build and
    verify call this before setting ATTAVO_ADAPTER_CONFIG, and the
    record's adapter_config is the merged result."""
    parent = (m.get("executor") or {}).get(adapter_name) or {}
    if not isinstance(parent, dict):
        return {}
    merged = {k: v for k, v in parent.items() if k not in STAGE_NAMES}
    stage_table = parent.get(stage)
    if isinstance(stage_table, dict):
        merged.update(stage_table)
    return merged


def resolve_adapter(root: Path, name: str) -> Path | None:
    """adapters/<name>/run, resolved first under the tenant's own
    attavo/adapters/, then under this product's adapters/."""
    for base in (root / "attavo" / "adapters", PRODUCT_ROOT / "adapters"):
        candidate = base / name / "run"
        if candidate.is_file():
            return candidate
    return None


def adapter_reports(adapter_path: Path) -> list[str]:
    """Field names adapters/<name>/conformance.json declares this adapter
    can supply with a real value -- vendor independence (2026-09-09
    ruling): the record carries the declaration, so an adapter claiming
    a field and returning null for it fails the conformance suite
    instead of writing a silent, uncomparable null. [] when the
    resolved adapter (a tenant override included) declares no
    conformance.json at all -- an absent declaration, not a refusal."""
    manifest_path = adapter_path.parent / "conformance.json"
    if not manifest_path.is_file():
        return []
    try:
        manifest = json.loads(manifest_path.read_text())
    except json.JSONDecodeError:
        return []
    reports: list[str] = manifest.get("reports", [])
    return reports


def changed_paths(root: Path, start_sha: str, records_rel: str) -> list[str]:
    """Paths changed between start_sha and HEAD, other than paths.records --
    the records directory is where build itself writes, not a claim."""
    return without_records(git(root, "diff", "--name-only", start_sha, "HEAD").splitlines(),
                           records_rel)


def matches_a_claim(path: str, claims: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in claims)


def build_is_empty(root: Path, approval_sha: str, sha: str, claims: list[str]) -> bool | None:
    """True when sha's tree, restricted to the paths the spec claims, is
    identical to approval_sha's tree there: no path the spec claims has
    changed since approval (item 227's own build). Compared against the
    approval, not a particular attempt's start_sha (item 229), so a
    retried attempt whose head already carries a prior attempt's valid
    commits owes no commit of its own; an attempt that ends on the
    approved tree, whatever it left in the working directory, is empty
    regardless of how it got there. Scoped to claims rather than
    "everything outside paths.records", so a commit that lands between
    approval and this attempt's start but touches no claimed path --
    a tenant installing an adapter, say -- never rescues or defeats the
    comparison on its own account; every commit an executor's own loop
    ever lands is already confined to claims (this module's own boundary
    check above), so a real prior attempt's work is always visible here.
    None when either commit does not resolve in this repository -- not
    known, not asserted."""
    for commit in (approval_sha, sha):
        resolved = subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{commit}^{{commit}}"],
                                  capture_output=True, text=True, check=False)
        if resolved.returncode != 0:
            return None
    pathspec = claims if claims else ["."]
    out = subprocess.run(["git", "-C", str(root), "diff", "--quiet", approval_sha, sha,
                          "--", *pathspec], capture_output=True, text=True, check=False)
    if out.returncode not in (0, 1):
        return None
    return out.returncode == 0


def protected_path_touched(root: Path, start_sha: str, records_rel: str,
                           specs_rel: str) -> str | None:
    """The first path an executor's own commits touched under
    paths.records or paths.specs since start_sha, or None (issue #140).
    Neither directory is a claim a spec can make -- paths.records is
    where the product itself writes, paths.specs is the definition of
    done -- yet changed_paths above excludes paths.records from the
    ordinary claims check entirely, so a path there never even reached
    matches_a_claim to be refused: tenant zero's own item #688 committed
    its own events file this way, under its records directory, on a
    build that then passed. Checked directly against the raw diff, not
    changed_paths's already-filtered list."""
    touched = git(root, "diff", "--name-only", start_sha, "HEAD").splitlines()
    protected = []
    for rel in (records_rel, specs_rel):
        stripped = rel.rstrip("/")
        protected.append((stripped, stripped + "/"))
    for p in touched:
        if not p:
            continue
        for exact, prefix in protected:
            if p == exact or p.startswith(prefix):
                return p
    return None


def executor_identity(adapter_name: str) -> dict[str, Any]:
    """The executor's identity (issue #33): set by the product, never the
    tenant's git config, so an executor commit is never indistinguishable
    from an operator's own -- the specimen is attavo/code-factory@e22fa85.
    A reserved, undeliverable domain (.invalid) so no mailbox is implied.
    Distinct from the records app identity: the app says the product
    pushed this, this says an invocation authored this -- different
    facts, neither borrows the other's name."""
    return {"name": f"attavo executor ({adapter_name})",
            "email": f"executor+{adapter_name}@attavo.invalid"}


def driver_identity() -> dict[str, Any]:
    """The driver's identity: set by the product, never the tenant's git
    config, so a driver's own transport commit -- a record commit
    `commit_record` makes on the driver's behalf -- is never
    indistinguishable from an operator's own; `git log --author='attavo
    driver'` on a driven item lists exactly those commits. Distinct from
    executor_identity: that names an adapter invocation's own commits,
    this names the transport that carries a stage's record."""
    return {"name": "attavo driver", "email": "driver@attavo.invalid"}


def commits_since(root: Path, start_sha: str, end: str = "HEAD") -> list[dict[str, Any]]:
    """(sha, name, email) for every commit after start_sha up to and
    including end, first-parent only, oldest first. cmd_build's own call
    (start_sha at the invocation, end left at the default HEAD) never sees
    a merge in that window, so --first-parent changes nothing there;
    issue #128's ledger is the caller that needs it, to walk one branch's
    own line without a concurrent merge to main expanding into every
    commit main gained meanwhile."""
    out = git(root, "log", "--first-parent", "--reverse",
             "--format=%H\x1f%an\x1f%ae", f"{start_sha}..{end}")
    commits = []
    for line in out.splitlines():
        if not line.strip():
            continue
        sha, name, email = line.split("\x1f")
        commits.append({"sha": sha, "name": name, "email": email})
    return commits


def commits_with_diffs(root: Path, start_sha: str, end: str = "HEAD") -> list[dict[str, Any]]:
    """commits_since's own (sha, name, email), each with its own
    first-parent diff already attached as "touched" -- one process for the
    whole range rather than commits_since's list plus a git diff per
    commit (issue #153, the cost item_ledger's own walk paid per commit)."""
    out = git(root, "log", "--first-parent", "--reverse", "--diff-merges=first-parent",
             "--name-only", "--format=\x01%H\x1f%an\x1f%ae", f"{start_sha}..{end}")
    commits = []
    for block in out.split("\x01")[1:]:
        header, _, rest = block.partition("\n")
        sha, name, email = header.split("\x1f")
        commits.append({"sha": sha, "name": name, "email": email,
                        "touched": [line for line in rest.splitlines() if line.strip()]})
    return commits


def split_adapter_stdout(stdout: str) -> tuple[str, dict[str, Any] | None]:
    """(transcript, payload): the adapter's last non-empty stdout line is
    its JSON report, and everything before it is transcript, only when
    that last line actually parses as JSON. When it does not -- a tool's
    own error line printed last on a non-zero exit (issue #86: tenant
    zero attest/records/659/verify-01.log, codex's own "Input exceeds..."
    line, dropped because it was mistaken for an attempted payload) --
    nothing here is a report, and the whole stdout is the transcript, so
    that last line is not silently lost.
    """
    lines = stdout.splitlines()
    non_empty = [i for i, l in enumerate(lines) if l.strip()]
    if not non_empty:
        return stdout, None
    last = non_empty[-1]
    try:
        payload = json.loads(lines[last])
    except ValueError:
        return stdout, None
    transcript = "\n".join(lines[:last])
    return transcript, payload


BUILD_ITERATION_BUDGET = 3

BUILD_HALT_EXIT = 3

CHECKS_FEEDBACK_TAIL_LINES = 200


def _retry_seed(root: Path, m: dict[str, Any], records_dir: Path
                ) -> tuple[str | None, str | None, str]:
    """(retry_of, steer, feedback): what a retried build's first iteration
    is handed (R3), in this order -- the prior attempt's own class and
    signature; the evidence a returned review carries (a red required
    check's log, read through [forge].check_log_command, or a review's
    own blocking findings); and the newest steer newer than the blocking
    record (counsel's answer text, named by the record it came from). A
    build retries the latest build record that is not a pass, or --
    retry_of still naming that same build attempt, the one a required
    check rejected or a review returned -- the latest verify record that
    returned it (class returned, its own build_sha matching the latest
    build's): the two never coexist, since a returned review is read
    only once its own build has already passed. (None, None, "") on a
    first attempt -- nothing to retry, nothing to seed."""
    latest_build = cast("dict[str, Any] | None", latest_record(records_dir, "build"))
    latest_verify = cast("dict[str, Any] | None", latest_record(records_dir, "verify"))
    prior_stem = f"build-{next_attempt_number(records_dir, 'build') - 1:02d}"
    prior: dict[str, Any] | None = None
    counsel_stage, counsel_n = "build", next_attempt_number(records_dir, "build") - 1
    if (latest_verify and (latest_verify.get("stop") or {}).get("class") == "returned"
            and latest_build and latest_verify.get("build_sha") == latest_build.get("sha")):
        prior = latest_verify
        verify_attempts = parse_attempts(records_dir, "verify")
        counsel_stage = "verify"
        counsel_n = max(verify_attempts) if verify_attempts else 0
    elif latest_build and latest_build.get("status") != "pass":
        prior = latest_build
    if prior is None:
        return None, None, ""

    stop: dict[str, Any] = prior.get("stop") or {}
    lines = [f"attempt {prior_stem} stopped as {stop.get('class')}: {stop.get('detail') or ''}",
            f"signature: {stop.get('signature') or ''}", ""]

    forge = m.get("forge") or {}
    if stop.get("class") == "returned":
        findings = [f for f in (prior.get("findings") or [])
                   if str(f.get("severity")) == "blocking"]
        if findings:
            lines.append("blocking findings:")
            for finding in findings:
                lines.append(f"- {finding.get('title')}: {finding.get('specimen')} "
                             f"(retires: {finding.get('retires')})")
            lines.append("")
        elif forge.get("check_log_command"):
            for c in (prior.get("forge_checks") or {}).get("conclusions") or []:
                if c.get("conclusion") in ("success", "neutral", "skipped"):
                    continue
                run = subprocess.run(
                    f"{forge['check_log_command']} {shlex.quote(str(prior.get('sha')))} "
                    f"{shlex.quote(c['name'])}", shell=True, cwd=str(root),
                    capture_output=True, text=True, check=False)
                lines += [f"--- check {c['name']} {c['conclusion']} ---",
                         run.stdout.strip(), ""]

    steer_stem: str | None = None
    if counsel_n:
        target = f"{counsel_stage}-{counsel_n:02d}"
        counsel_attempts = parse_attempts(records_dir, "counsel")
        matches = [(n, rec) for n, rec in sorted(counsel_attempts.items())
                  if rec.get("record") == target]
        if matches:
            nn, counsel = matches[-1]
            if counsel.get("verdict") == "answer":
                steer_stem = f"counsel-{nn:02d}"
                answer = (counsel.get("proposal") or {}).get("ruling_text")
                if answer:
                    lines += [f"steer from {steer_stem}:", answer.strip(), ""]

    return prior_stem, steer_stem, "\n".join(lines) + "\n"


@contextlib.contextmanager
def gate_checkout(root: Path) -> Iterator[Path]:
    """A detached worktree of root's HEAD, removed on exit: the tree the
    build's gate reads. The executor's own directory is not it -- a file
    written there and never committed is not part of the change, and a
    gate that read it would pass work that does not exist on the branch
    (item 227)."""
    parent = Path(tempfile.mkdtemp(prefix="attavo-gate-"))
    tree = parent / "head"
    git(root, "worktree", "add", "--detach", "-q", str(tree), "HEAD")
    try:
        yield tree
    finally:
        try:
            git(root, "worktree", "remove", "--force", str(tree))
        except RuntimeError:
            shutil.rmtree(tree, ignore_errors=True)
            try:
                git(root, "worktree", "prune")
            except RuntimeError:
                pass
        shutil.rmtree(parent, ignore_errors=True)


def cmd_build(argv: list[str]) -> int:
    root = Path(".").resolve()
    item_id = argv[0] if argv else derive_item_id(root)
    if not item_id:
        print("attavo build: cannot derive item id; pass N or run on an attavo/N-* branch",
              file=sys.stderr)
        return 2
    m = read_manifest(root)
    if m is None:
        print(f"attavo build: no {MANIFEST} at {root}", file=sys.stderr)
        return 2

    adapter_name = (m.get("executor") or {}).get("build")
    if not adapter_name:
        print("attavo build: refused: no [executor].build in the manifest")
        return 1

    tests_cfg = m.get("tests") or {}
    if not tests_cfg.get("select") or not tests_cfg.get("run"):
        print("attavo build: refused: no [tests] table in the manifest "
              "([tests].select and [tests].run are both required)")
        return 1

    if (m.get("checks") or {}).get("probe") and not (m.get("checks") or {}).get("probe_timeout_s"):
        print("attavo build: refused: [checks].probe is declared without "
              "[checks].probe_timeout_s (a probe with no bound is not asked)")
        return 1

    try:
        status = approval_status(root, m, item_id)
    except ShallowCheckoutError as exc:
        print(f"attavo build: refused: {exc}", file=sys.stderr)
        return 2
    if status != "valid":
        print(f"attavo build: refused: approval is {status}")
        return 1

    adapter_path = resolve_adapter(root, adapter_name)
    if adapter_path is None:
        print(f"attavo build: refused: no adapter named {adapter_name!r}")
        return 1

    specs_rel = m["paths"]["specs"]
    records_rel = m["paths"]["records"]
    spec_dir = root / specs_rel / item_id
    item_records_dir = root / records_rel / item_id
    spec_sha = last_commit_touching(root, str(Path(specs_rel) / item_id))
    latest_approval = latest_record(item_records_dir, "approval")
    approval_sha = latest_approval.get("sha") if latest_approval else None
    # approval_status already refused above unless status is "valid", which
    # only holds when latest_approval exists and names a sha (attavo/approval.py).
    assert approval_sha is not None, "approval status valid implies a latest approval sha"
    parsed, _ = parse_spec(spec_dir / "spec.md")
    claims = parsed["claims"] if parsed else []
    criteria_names = parsed["criteria"] if parsed else []
    adapter_config = merge_adapter_config(m, adapter_name, "build")
    checks_cfg = m.get("checks") or {}
    checks_command = checks_cfg.get("command")
    probe = checks_cfg.get("probe")
    probe_timeout_s = checks_cfg.get("probe_timeout_s")
    identity = executor_identity(adapter_name)
    reports = adapter_reports(adapter_path)

    start_sha = git(root, "rev-parse", "HEAD").strip()
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    started = time.monotonic()

    events_path = stage_events_dir(root) / f"build-{next_attempt_number(item_records_dir, 'build'):02d}.events.jsonl"

    # Issue #115, then issue #212: whether this machine can run the tenant's
    # checks at all is asked once here, on the approved tree, before the
    # adapter is ever invoked -- a missing import at test collection (item
    # #106's build-01) is not a failing check, and no build could have
    # passed against it. A tenant that declares [checks].probe is asked by
    # the probe, inside [checks].probe_timeout_s: exit 0 yes, anything else
    # or the bound no. A tenant that does not is asked the old way, by
    # running [checks].command whole -- exit 2 no, exit 0 or 1 (the checks
    # ran and passed or failed, the expected states before a build) yes --
    # which is the run #194's build-01 spent 215 s on. Either way the feed
    # says what is being asked and what the answer was.
    if checks_command:
        append_event(events_path, "gate", "checking this machine can run the checks")
        asked = checks_command
        if probe:
            asked = probe
            code, out, err, timed_out = run_bounded(probe, root, "", probe_timeout_s)
            refusal_text = (f"[checks].probe did not finish within {probe_timeout_s} s" if timed_out
                            else f"[checks].probe exited {code} on the approved tree"
                            if code != 0 else None)
        else:
            preflight = subprocess.run(checks_command, shell=True, cwd=str(root),
                                       capture_output=True, text=True, check=False)
            code, out, err = preflight.returncode, preflight.stdout, preflight.stderr
            refusal_text = ("[checks].command exited 2 on the approved tree" if code == 2 else None)
        if refusal_text:
            append_event(events_path, "gate", f"this machine cannot run the checks: {refusal_text}")
            log = out + err
            detail = _log_last_line(out + "\n" + err) or refusal_text
            record = {
                "sha": start_sha,
                "start_sha": start_sha,
                "spec_sha": spec_sha,
                "approval_sha": approval_sha,
                "at": now_iso(),
                "adapter": adapter_name,
                "adapter_config": adapter_config,
                "executor_identity": identity,
                "reports": reports,
                "adapter_version": None,
                "model": None,
                "model_version": None,
                "iterations": 1,
                "criteria": [],
                "selected_tests": None,
                "checks": {"command": asked, "exit": code},
                "tokens": {"input": 0, "output": 0},
                "cost_usd": None,
                "wall_clock_s": round(time.monotonic() - started, 3),
                "status": "refused",
                "refusal": refusal_text,
                "halted": None,
                "retry_of": None,
                "steer": None,
                # R3: environment is never retried by this item (recorded
                # here, acted on by R4's breaker) -- signed anyway for
                # the exhaustion report's own side-by-side, on the
                # remote chance a person retries by hand.
                "stop": {"class": "environment", "provenance": "tenant", "detail": detail,
                        "signature": signature_of(detail), "bound": None},
            }
            record_path = write_record(item_records_dir, "build", record, m, item_id,
                                       log=log or refusal_text + "\n")
            print(f"attavo build: refused ({record_path.name})")
            return 1
        append_event(events_path, "gate", "this machine can run the checks")

    adapter_version = model = model_version = None
    tokens = {"input": 0, "output": 0}
    cost_usd = None
    transcripts: list[str] = []
    criteria_results: list[dict[str, Any]] = []
    selected_tests: dict[str, Any] | None = None
    checks_result: dict[str, Any] | None = None
    status_out = "failed"
    refusal = None
    refusal_provenance = None
    halted = None
    empty_detail: str | None = None
    iterations_used = 0

    with tempfile.TemporaryDirectory() as tmp:
        feedback_path = Path(tmp) / "feedback.txt"
        feedback_path.write_text("")
        # R3: a retried attempt's first iteration starts with the prior
        # attempt's class and signature, its evidence, and the newest
        # steer newer than the blocking record -- the one file the
        # contract already passes, no new adapter input at all.
        retry_of, steer, seed = _retry_seed(root, m, item_records_dir)
        if seed:
            feedback_path.write_text(seed)

        for iteration in range(1, BUILD_ITERATION_BUDGET + 1):
            iterations_used = iteration
            append_event(events_path, "iteration", f"iteration {iteration} of {BUILD_ITERATION_BUDGET}")
            env = dict(os.environ)
            # #212 retired the checks command reaching the executor; an
            # ATTAVO_CHECKS_COMMAND exported by whoever launched `attavo
            # build` must not reach it by inheritance either.
            env.pop("ATTAVO_CHECKS_COMMAND", None)
            env["ATTAVO_SPEC_DIR"] = str(spec_dir)
            env["ATTAVO_RECORDS_DIR"] = str(item_records_dir)
            env["ATTAVO_RULINGS"] = str(root / m["paths"]["rulings"])
            env["ATTAVO_BRANCH"] = branch
            env["ATTAVO_ITERATION"] = str(iteration)
            env["ATTAVO_FEEDBACK"] = str(feedback_path)
            env["ATTAVO_ADAPTER_CONFIG"] = json.dumps(adapter_config)
            env["ATTAVO_STAGE"] = "build"
            env["ATTAVO_EVENTS"] = str(events_path)
            env["ATTAVO_TEST_COMMAND"] = f"python3 {PRODUCT_ROOT / 'bin' / 'attavo'} test {item_id}"
            if tests_cfg.get("call_timeout_s"):
                env["ATTAVO_TEST_TIMEOUT_S"] = str(tests_cfg["call_timeout_s"])
            else:
                env.pop("ATTAVO_TEST_TIMEOUT_S", None)
            env["GIT_AUTHOR_NAME"] = identity["name"]
            env["GIT_AUTHOR_EMAIL"] = identity["email"]
            env["GIT_COMMITTER_NAME"] = identity["name"]
            env["GIT_COMMITTER_EMAIL"] = identity["email"]

            run = subprocess.run([str(adapter_path)], cwd=str(root), env=env,
                                 capture_output=True, text=True, check=False)
            transcript, payload = split_adapter_stdout(run.stdout)
            if run.returncode != 0:
                # The guarantee behind issue #53's refusal: an adapter that
                # exits non-zero always leaves a non-empty log, even one
                # that printed nothing at all on stderr.
                detail = f"[adapter exited {run.returncode}]"
                if run.stderr.strip():
                    detail += "\n" + run.stderr.rstrip("\n")
                transcript = (transcript + "\n" if transcript else "") + detail
            transcripts.append(transcript)

            if run.returncode != 0 or payload is None or not payload.get("model"):
                status_out = "failed"
                break

            adapter_version = payload.get("adapter_version")
            model = payload.get("model")
            model_version = payload.get("model_version")
            tok = payload.get("tokens") or {}
            tokens["input"] += tok.get("input", 0) or 0
            tokens["output"] += tok.get("output", 0) or 0
            iter_cost = payload.get("cost_usd")
            if iter_cost is not None:
                cost_usd = (cost_usd or 0) + iter_cost

            if payload.get("status") == "question":
                status_out = "halted"
                halted = {"question": payload.get("question")}
                break

            foreign = next((c for c in commits_since(root, start_sha)
                           if c["name"] != identity["name"] or c["email"] != identity["email"]),
                          None)
            if foreign:
                status_out = "refused"
                refusal = f"commit {foreign['sha']} not authored by the executor"
                break

            # An executor that committed nothing this iteration built
            # nothing this iteration, whatever it left in the working
            # tree: the gate below reads the commit, so an empty
            # iteration fails its gate on the approved tree and is told
            # why (item 227). Whether the *build* is empty is a separate
            # question, asked against the approval below (item 229): a
            # retried attempt whose head already carries a prior
            # attempt's valid commits owes no commit of its own.
            current_sha = git(root, "rev-parse", "HEAD").strip()
            committed_nothing = current_sha == start_sha
            left_uncommitted = [
                line for line in git(root, "status", "--porcelain", "--untracked-files=all").splitlines()
                if line.strip() and not line[3:].startswith(records_rel.rstrip("/") + "/")]
            empty_line = (f"no commit since {start_sha[:12]}; {len(left_uncommitted)} path(s) "
                          "modified or untracked in the working tree, which the gate does not read"
                          if committed_nothing else None)
            # A build is empty when its tree, restricted to the spec's
            # own claims, is the approved tree there -- compared against
            # the approval, not this attempt's own start_sha, so a build
            # that already carries a prior attempt's valid commits is
            # not empty just because this attempt added none of its own.
            build_empty = build_is_empty(root, approval_sha, current_sha, claims)
            if build_empty is None:
                build_empty = committed_nothing

            protected = protected_path_touched(root, start_sha, records_rel, specs_rel)
            if protected:
                status_out = "refused"
                refusal = protected
                refusal_provenance = "executor"
                break

            touched = sorted(changed_paths(root, start_sha, records_rel))
            bad_path = next((p for p in touched if not matches_a_claim(p, claims)), None)
            if bad_path:
                status_out = "refused"
                refusal = bad_path
                break

            # The gate reads the commit, never the executor's directory: a
            # detached worktree of HEAD, so a file written and not committed
            # cannot pass a criterion, a test or a check on the executor's
            # behalf (item 227). Removed before the iteration ends.
            with gate_checkout(root) as gate_root:
                criteria_results = [
                    {"name": name, "exit": run_criterion(spec_dir / "criteria" / name, gate_root)}
                    for name in criteria_names]
                for c in criteria_results:
                    append_event(events_path, "gate", f"{c['name']}: exit {c['exit']}")

                select_in = "".join(p + "\n" for p in touched)
                select_run = subprocess.run(tests_cfg["select"], shell=True, cwd=str(gate_root),
                                            input=select_in, capture_output=True,
                                            text=True, check=False)
                identifiers = [l for l in select_run.stdout.splitlines() if l.strip()]
                run_in = "".join(i + "\n" for i in identifiers)
                test_run = subprocess.run(tests_cfg["run"], shell=True, cwd=str(gate_root),
                                          input=run_in, capture_output=True,
                                          text=True, check=False)
                selected_tests = {"select": tests_cfg["select"], "run": tests_cfg["run"],
                                  "count": len(identifiers), "exit": test_run.returncode}

                checks_run = None
                if checks_command:
                    checks_run = subprocess.run(checks_command, shell=True, cwd=str(gate_root),
                                                capture_output=True, text=True, check=False)
                    checks_result = {"command": checks_command, "exit": checks_run.returncode}

            # Issue #89, rejection test 1: the gate output that decides pass
            # or fail is appended to the build log itself, under a heading
            # per iteration, before this temporary directory goes away --
            # tenant zero's #576 build-11 and #659 build-01 are a failed
            # record with nothing beside it to say what was red, because
            # this output previously lived only in feedback.txt, in a
            # tempfile.TemporaryDirectory() cmd_build deletes on return.
            gate_lines = [f"criterion {c['name']}: exit {c['exit']}" for c in criteria_results]
            gate_lines.append(f"selected tests ({selected_tests['count']}): exit "
                              f"{selected_tests['exit']}")
            if test_run.stdout.strip():
                gate_lines += ["--- selected tests stdout ---", test_run.stdout.rstrip("\n")]
            if test_run.stderr.strip():
                gate_lines += ["--- selected tests stderr ---", test_run.stderr.rstrip("\n")]
            if checks_run is not None:
                tail = (checks_run.stdout.splitlines() + checks_run.stderr.splitlines())
                tail = tail[-CHECKS_FEEDBACK_TAIL_LINES:]
                gate_lines.append(f"tenant checks: exit {checks_run.returncode}")
                gate_lines += ["--- checks output (tail) ---", *tail]
            transcripts[-1] += (f"\n--- attavo gates, iteration {iteration} ---\n"
                               + "\n".join(gate_lines))

            if empty_line:
                transcripts[-1] += ("\nyou committed nothing: " + empty_line +
                                    "\ncommit your work; the gate reads the commit, not the tree")

            failing = [c for c in criteria_results if c["exit"] != 0]
            checks_ok = checks_run is None or checks_run.returncode == 0
            if not failing and test_run.returncode == 0 and checks_ok and not build_empty:
                status_out = "pass"
                break

            if iteration == BUILD_ITERATION_BUDGET:
                if build_empty:
                    status_out = "halted"
                    halted = {"question": None}
                    empty_detail = (f"the executor committed nothing; {empty_line}" if empty_line else
                                    f"the executor committed nothing; no claimed path has changed "
                                    f"since approval {str(approval_sha)[:12]}; "
                                    f"{len(left_uncommitted)} path(s) modified or untracked in the "
                                    "working tree, which the gate does not read")
                else:
                    status_out = "failed"
                break

            feedback_lines = []
            if empty_line:
                feedback_lines += ["you committed nothing: " + empty_line,
                                   "commit your work; the gate reads the commit, not the tree"]
            if failing:
                feedback_lines.append("criteria failing:")
                feedback_lines += [f"- {c['name']}: exit {c['exit']}" for c in failing]
            if test_run.returncode != 0:
                feedback_lines += [f"test run exit {test_run.returncode}",
                                   "--- stdout ---", test_run.stdout,
                                   "--- stderr ---", test_run.stderr]
            if checks_run is not None and checks_run.returncode != 0:
                tail = (checks_run.stdout.splitlines() + checks_run.stderr.splitlines())
                tail = tail[-CHECKS_FEEDBACK_TAIL_LINES:]
                feedback_lines += [f"checks exit {checks_run.returncode}",
                                   "--- checks output (tail) ---", *tail]
            feedback_path.write_text("\n".join(feedback_lines) + "\n")

    record = {
        "sha": git(root, "rev-parse", "HEAD").strip(),
        "start_sha": start_sha,
        "spec_sha": spec_sha,
        "approval_sha": approval_sha,
        "at": now_iso(),
        "adapter": adapter_name,
        "adapter_config": adapter_config,
        "executor_identity": identity,
        "reports": reports,
        "adapter_version": adapter_version,
        "model": model,
        "model_version": model_version,
        "iterations": iterations_used,
        "criteria": criteria_results,
        "selected_tests": selected_tests,
        "checks": checks_result,
        "tokens": tokens,
        "cost_usd": cost_usd,
        "wall_clock_s": round(time.monotonic() - started, 3),
        "status": status_out,
        "refusal": refusal,
        "halted": halted,
        "retry_of": retry_of,
        "steer": steer,
        "stop": derive_stop(status_out, refusal=refusal, halted=halted,
                            log="\n".join(transcripts),
                            report_class="empty" if empty_detail else (payload or {}).get("class"),
                            refusal_provenance=refusal_provenance),
    }
    if record["stop"] is not None and empty_detail:
        record["stop"] = {**record["stop"], "detail": empty_detail}
    if record["stop"] is not None:
        stop_out: dict[str, Any] = record["stop"]
        # R3: transient's own signature is the adapter's stop detail
        # (payload.get("detail")), not derive_stop's detail (which is
        # None for every class but unclassed/question/environment/gate);
        # every other non-pass class signs off its own detail (None for
        # most, so signature is None too -- the class alone, or the log
        # itself for unclassed, is the specimen's own cause).
        stop_detail: str | None = ((payload or {}).get("detail") if stop_out["class"] == "transient"
                                   else stop_out.get("detail"))
        record["stop"] = {**stop_out, "detail": stop_detail, "signature": signature_of(stop_detail),
                          "bound": None}
    record_path = write_record(item_records_dir, "build", record, m, item_id,
                               log="\n".join(transcripts))

    print(f"attavo build: {status_out} ({record_path.name})")
    if status_out == "halted":
        return BUILD_HALT_EXIT
    return 0 if status_out == "pass" else 1
