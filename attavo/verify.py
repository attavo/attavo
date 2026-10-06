from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, cast

from attavo.build import (
    adapter_reports,
    build_is_empty,
    merge_adapter_config,
    resolve_adapter,
    split_adapter_stdout,
)
from attavo.common import MANIFEST, derive_item_id, git, now_iso, read_manifest
from attavo.testtool import run_bounded
from attavo.events import stage_events_dir
from attavo.records import (
    latest_attempt_path,
    latest_record,
    next_attempt_number,
    parse_attempts,
    write_record,
)
from attavo.spec import parse_spec
# The forge constants moved to attavo.stops with the stop derivation that reads them;
# report.py still imports them from here.
from attavo.stops import (
    FORGE_OK_CONCLUSIONS as FORGE_OK_CONCLUSIONS,
    FORGE_UNCONCLUDED_STATUSES as FORGE_UNCONCLUDED_STATUSES,
    blocking_titles,
    counsel_record_for,
    derive_stop,
    returned_stop,
    signature_of,
)
from attavo import Command

# --- verify: the forge's own read first, a judge only once it is green ---

def read_forge_checks(root: Path, checks_command: str, sha: str) -> tuple[dict[str, Any], bool]:
    """Runs the tenant's [forge].checks_command on sha; the product does
    not know what forge that command reads. One line per check as
    "<name> <conclusion>"; a command that fails to run, or any conclusion
    other than success/neutral/skipped, is not green."""
    run = subprocess.run(f"{checks_command} {shlex.quote(sha)}", shell=True, cwd=str(root),
                         capture_output=True, text=True, check=False)
    conclusions: list[dict[str, str]] = []
    for line in run.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.rsplit(" ", 1)
        if len(parts) == 2:
            conclusions.append({"name": parts[0], "conclusion": parts[1]})
    # A commit the forge has no record of is not green -- it is unread.
    # The executor's own commit is exactly this: the forge attaches
    # checks to a pull request's head, and that commit stops being the
    # head the moment a record commit lands on top of it. all() over an
    # empty list is True, which is why this is spelled out rather than
    # implied by the loop above.
    ok = run.returncode == 0 and bool(conclusions) and all(
        c["conclusion"] in FORGE_OK_CONCLUSIONS for c in conclusions)
    return {"command": checks_command, "sha": sha, "conclusions": conclusions}, ok


def diff_since_approval(root: Path, approval_sha: str | None) -> tuple[str, str | None]:
    """(diff_text, refusal): the item's own work since approval, not
    everything merged onto the branch since then (issue #52) -- a
    three-dot diff against approval_sha itself is not enough, since
    approval_sha is already an ancestor of HEAD in the normal flow and
    merge-base(approval_sha, HEAD) is just approval_sha again (verified
    empirically, see issue #52). Diffing from the merge-base of HEAD and
    the remote's default branch instead excludes content that arrived by
    merging that branch in, because that content is already present on
    both sides of the merge base. refs/remotes/origin/HEAD is what names
    the default branch without hard-coding "main"; when it is not set,
    there is nothing to diff against and this refuses rather than guess.
    ("", None) when approval_sha is falsy -- nothing to diff from at all.
    Shared by cmd_verify and cmd_counsel, which both hand an adapter this
    same diff."""
    if not approval_sha:
        return "", None
    symref = subprocess.run(
        ["git", "-C", str(root), "symbolic-ref", "-q", "refs/remotes/origin/HEAD"],
        capture_output=True, text=True, check=False)
    if symref.returncode != 0:
        return "", "refs/remotes/origin/HEAD is not set; run `git remote set-head origin -a`"
    diff_base = git(root, "merge-base", symref.stdout.strip(), "HEAD").strip()
    return git(root, "diff", diff_base, "HEAD"), None


STUB_TEST_SELECT_TIMEOUT_S = 60


def stub_test_paths(root: Path, stub_dir: Path, select_command: str | None
                    ) -> frozenset[str] | None:
    """The stub's files that are tests, by the tenant's own definition: a
    stub path is a test when [tests].select, given that path alone on
    stdin, selects it (the path itself, or an identifier under it such as
    path::name). The product never names a test directory or a file
    pattern. None when it cannot be known -- no selector declared, or the
    selector exits non-zero or outlasts its bound -- which the ratio
    reports as not measured."""
    if not stub_dir.is_dir():
        return frozenset()
    if not select_command:
        return None
    tests: set[str] = set()
    for f in sorted(stub_dir.rglob("*")):
        if not f.is_file() or f.name == ".removed":
            continue
        rel = f.relative_to(stub_dir).as_posix()
        code, out, _err, _timed_out = run_bounded(select_command, root, rel + "\n",
                                                  STUB_TEST_SELECT_TIMEOUT_S)
        if code != 0:
            return None
        if any(line.strip().split("::")[0] == rel for line in out.splitlines()):
            tests.add(rel)
    return frozenset(tests)


def stub_ratio(diff_text: str, stub_dir: Path, excluded: tuple[str, ...],
               tests: frozenset[str] | None = frozenset()) -> float | None:
    """The share of the lines diff_text adds that are a line of the stub's
    file at the same path: near 1 the build shipped the spec author's stub,
    near 0 the builder wrote the change. Lines under the excluded
    directories (the specs and records, where the stub itself and the
    item's records are committed) and blank lines are not counted. None
    when there is no stub or the diff adds no counted line -- nothing was
    measured, which is not 0.0.

    The stub's own test files (tests, from stub_test_paths) are not
    counted on either side: the build is expected to ship them as they
    are, so copying them is not the build choosing the stub's code. None
    for tests means the tests could not be told apart, and the ratio is
    not measured."""
    if not stub_dir.is_dir():
        return None
    if tests is None:
        return None
    skipped = tuple(e.strip("/") for e in excluded)
    stub_lines: dict[str, set[str]] = {}
    path: str | None = None
    in_hunk = False
    added = shipped = 0
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            path, in_hunk = None, False
        elif in_hunk:
            if line.startswith("+") and line[1:].strip() and path is not None:
                added += 1
                shipped += line[1:].rstrip() in stub_lines[path]
        elif line.startswith("+++ "):
            name = line[4:].split("\t")[0]
            path = name[2:] if name.startswith("b/") else None
            if path is not None and (any(path == e or path.startswith(e + "/") for e in skipped)
                                     or path in tests):
                path = None
            if path is not None and path not in stub_lines:
                stub_file = stub_dir / path
                stub_lines[path] = (
                    {text.rstrip() for text in stub_file.read_text(errors="replace").splitlines()}
                    if stub_file.is_file() else set())
        elif line.startswith("@@"):
            in_hunk = True
    return shipped / added if added else None


def _verify_retry_seed(records_dir: Path, build_sha: str) -> tuple[str | None, str | None]:
    """(retry_of, steer): a plain retry of verify itself -- died, hung,
    transient, or any other non-returned stop counsel answered -- names
    the prior verify attempt it retries, the same way build's own retry
    does. A returned review is never this: its own fix is a new build,
    not a new verify, so build.py's _retry_seed is what names it as
    retry_of instead."""
    attempts = parse_attempts(records_dir, "verify")
    if not attempts:
        return None, None
    nn = max(attempts)
    prior = attempts[nn]
    stop = cast("dict[str, Any]", prior.get("stop") or {})
    if prior.get("status") == "pass" or prior.get("build_sha") != build_sha or stop.get("class") == "returned":
        return None, None
    prior_stem = f"verify-{nn:02d}"
    counsel = counsel_record_for(records_dir, "verify", nn)
    steer = None
    if counsel is not None and counsel.get("verdict") == "answer":
        counsel_attempts = parse_attempts(records_dir, "counsel")
        matches = [n for n, rec in counsel_attempts.items() if rec.get("record") == prior_stem]
        if matches:
            steer = f"counsel-{max(matches):02d}"
    return prior_stem, steer


def cmd_verify(argv: list[str]) -> int:
    root = Path(".").resolve()
    item_id = argv[0] if argv else derive_item_id(root)
    if not item_id:
        print("attavo verify: cannot derive item id; pass N or run on an attavo/N-* branch",
              file=sys.stderr)
        return 2
    m = read_manifest(root)
    if m is None:
        print(f"attavo verify: no {MANIFEST} at {root}", file=sys.stderr)
        return 2

    specs_rel = m["paths"]["specs"]
    records_rel = m["paths"]["records"]
    item_records_dir = root / records_rel / item_id

    latest_build = latest_record(item_records_dir, "build")
    if latest_build is None:
        print(f"attavo verify: refused: no build record for item {item_id}")
        return 1
    if latest_build.get("status") != "pass":
        print(f"attavo verify: refused: latest build record is "
              f"{latest_build.get('status')!r}, not pass")
        return 1
    # A build is empty when its tree, restricted to the spec's own
    # claims, is the approved tree there -- asked against the approval,
    # not start_sha, so a build that adopted a prior attempt's valid
    # commits is not empty just because this attempt owed no commit of
    # its own (item 229). sha == start_sha (the shape a first attempt's
    # own empty build always has, and the shape item 227's own build
    # was) is kept as a fallback for a record with no approval_sha to
    # ask at all.
    build_sha, build_start_sha = latest_build.get("sha"), latest_build.get("start_sha")
    build_approval_sha = latest_build.get("approval_sha")
    spec_dir = root / specs_rel / item_id
    parsed, _ = parse_spec(spec_dir / "spec.md")
    claims = parsed["claims"] if parsed else []
    empty = (build_is_empty(root, build_approval_sha, build_sha, claims)
            if build_approval_sha else build_sha == build_start_sha)
    if empty:
        detail = (f" (sha equals start_sha {str(build_sha)[:12]})"
                  if build_sha == build_start_sha else "")
        print(f"attavo verify: refused: the latest build record names no change{detail}; "
              "nothing to review")
        return 1

    forge_cfg = m.get("forge") or {}
    checks_command = forge_cfg.get("checks_command")
    if not checks_command:
        print("attavo verify: refused: no [forge].checks_command in the manifest")
        return 1

    select_command = (m.get("tests") or {}).get("select")
    build_sha = latest_build["sha"]
    build_record_path = latest_attempt_path(item_records_dir, "build")
    approval_sha = latest_build.get("approval_sha")
    started = time.monotonic()
    head_sha = git(root, "rev-parse", "HEAD").strip()

    diff_text, diff_refusal = diff_since_approval(root, approval_sha)
    if diff_refusal:
        print(f"attavo verify: refused: {diff_refusal}")
        return 1
    # The forge attaches checks to a pull request's head, not to the
    # executor's own commit -- that commit stops being the head the
    # moment a record commit lands on top of it (issue #44). Reading the
    # forge on head_sha is what the forge actually checked and what will
    # be merged; build_sha and forge_checks["sha"] can differ, and both
    # are kept on the record rather than one sha serving two purposes.
    # head_sha is a marker commit just pushed (issue #178): the forge has
    # not attached anything to it yet on the first read, so this polls
    # with the same bound and interval the drive's own guard uses, giving
    # those checks room to conclude before this read judges them.
    given = os.environ.get("ATTAVO_FORGE_CHECKS")
    if given:
        # Reconciler ruling B: the pass read the checks on the head before
        # its marker commit and found them green; a records-only commit
        # cannot change them (#210's proof), so the review takes that read
        # and does not poll a head the forge has not attached anything to.
        forge_checks = json.loads(Path(given).read_text())
        forge_ok = bool(forge_checks.get("conclusions")) and all(
            c["conclusion"] in FORGE_OK_CONCLUSIONS for c in forge_checks["conclusions"])
    else:
        forge_checks, forge_ok = poll_forge_checks(root, checks_command, head_sha)

    if not forge_ok:
        record: dict[str, Any] = {
            "sha": head_sha,
            "build_sha": build_sha,
            "build_record": build_record_path.stem if build_record_path else None,
            "approval_sha": approval_sha,
            "at": now_iso(),
            "adapter": None, "adapter_config": {}, "adapter_version": None,
            "model": None, "model_version": None,
            "reports": [],
            "forge_checks": forge_checks,
            "findings": None,
            "tests_named": [],
            "commits": [],
            "stub_ratio": stub_ratio(diff_text, spec_dir / "stub", (specs_rel, records_rel),
                                  stub_test_paths(root, spec_dir / "stub", select_command)),
            "tokens": {"input": 0, "output": 0}, "cost_usd": None,
            "wall_clock_s": round(time.monotonic() - started, 3),
            "status": "returned",
            "report_class": None,
            "retry_of": None, "steer": None,
            "stop": returned_stop(forge_checks),
        }
        record_path = write_record(item_records_dir, "verify", record, m, item_id)
        print(f"attavo verify: returned ({record_path.name}); the forge checks are not green")
        return 0

    executor = m.get("executor") or {}
    adapter_name = executor.get("verify") or executor.get("build")
    if not adapter_name:
        print("attavo verify: refused: no [executor].verify or [executor].build in the manifest")
        return 1
    adapter_path = resolve_adapter(root, adapter_name)
    if adapter_path is None:
        print(f"attavo verify: refused: no adapter named {adapter_name!r}")
        return 1

    adapter_config = merge_adapter_config(m, adapter_name, "verify")
    reports = adapter_reports(adapter_path)
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()

    events_path = stage_events_dir(root) / f"verify-{next_attempt_number(item_records_dir, 'verify'):02d}.events.jsonl"

    with tempfile.TemporaryDirectory() as tmp:
        diff_path = Path(tmp) / "diff.patch"
        diff_path.write_text(diff_text)

        env = dict(os.environ)
        env["ATTAVO_SPEC_DIR"] = str(spec_dir)
        env["ATTAVO_RECORDS_DIR"] = str(item_records_dir)
        env["ATTAVO_RULINGS"] = str(root / m["paths"]["rulings"])
        env["ATTAVO_BRANCH"] = branch
        env["ATTAVO_STAGE"] = "verify"
        env["ATTAVO_DIFF"] = str(diff_path)
        env["ATTAVO_BUILD_RECORD"] = str(build_record_path) if build_record_path else ""
        env["ATTAVO_ADAPTER_CONFIG"] = json.dumps(adapter_config)
        env["ATTAVO_EVENTS"] = str(events_path)

        run = subprocess.run([str(adapter_path)], cwd=str(root), env=env,
                             capture_output=True, text=True, check=False)
        transcript, payload = split_adapter_stdout(run.stdout)
        if run.returncode != 0:
            # Same guarantee as cmd_build's, issue #53: a non-zero adapter
            # exit always leaves a non-empty log.
            detail = f"[adapter exited {run.returncode}]"
            if run.stderr.strip():
                detail += "\n" + run.stderr.rstrip("\n")
            transcript = (transcript + "\n" if transcript else "") + detail

    # Verify is a report, not a commit (issue #46): the adapter is invoked
    # on the branch head it was handed, and must leave that head alone.
    # Checked by comparing the head before the invocation (head_sha,
    # already read above for the forge) against the head after it.
    post_adapter_head = git(root, "rev-parse", "HEAD").strip()
    commits: list[str] = []
    if post_adapter_head != head_sha:
        commits = git(root, "rev-list", f"{head_sha}..{post_adapter_head}", "--reverse").split()

    status_out = "failed"
    findings = None
    tests_named: list[str] = []
    model = model_version = adapter_version_out = None
    tokens = {"input": 0, "output": 0}
    cost_usd = None

    if run.returncode == 0 and payload is not None and payload.get("model"):
        model = payload.get("model")
        model_version = payload.get("model_version")
        adapter_version_out = payload.get("adapter_version")
        tok = payload.get("tokens") or {}
        tokens = {"input": tok.get("input", 0) or 0, "output": tok.get("output", 0) or 0}
        cost_usd = payload.get("cost_usd")
        # "findings" absent (or explicitly null) is not the same as an
        # empty list: the adapter had no channel to report one at all,
        # which is not a clean bill of health (issue #46). A commit
        # during verify fails the attempt regardless of what it reported.
        if not commits and "findings" in payload and payload["findings"] is not None:
            findings = payload["findings"]
            tests_named = payload.get("tests_named") or []
            status_out = "pass"

    # R3 (ruling C amended): a finding whose severity is "blocking" makes
    # this review's status failed with class returned -- retryable, the
    # next build attempt handed the findings as its evidence -- signed
    # by the sorted titles of the blocking findings themselves. Every
    # other severity leaves the review a pass, filed as today (issue
    # #46's own findings channel, untouched).
    blocking = blocking_titles(findings)
    if blocking:
        status_out = "failed"
        # write_record refuses a "failed" record with no trail at all
        # (issue #53); a review whose own transcript is otherwise empty
        # (a terse adapter that prints only its report line) still owes
        # one -- the blocking findings themselves are the cause.
        if not transcript.strip():
            transcript = "blocking finding(s): " + "; ".join(blocking)

    retry_of, steer = _verify_retry_seed(item_records_dir, build_sha)
    # Issue #254: the class the adapter's report line carried, stored as
    # handed to derive_stop so a later reader re-derives the stop.
    reported = (payload or {}).get("class")
    report_class = reported if isinstance(reported, str) else None
    if blocking:
        stop: dict[str, Any] | None = {"class": "returned", "provenance": "tenant",
                                       "detail": "; ".join(blocking),
                                       "signature": signature_of(" ".join(blocking)), "bound": None}
    else:
        stop = cast("dict[str, Any] | None",
                   derive_stop(status_out, log=transcript, report_class=report_class,
                               gate=None))
        if stop is not None:
            stop_detail: str | None = ((payload or {}).get("detail") if stop["class"] == "transient"
                                       else stop.get("detail"))
            stop = {**stop, "detail": stop_detail, "signature": signature_of(stop_detail), "bound": None}

    record = {
        "sha": head_sha,
        "build_sha": build_sha,
        "build_record": build_record_path.stem if build_record_path else None,
        "approval_sha": approval_sha,
        "at": now_iso(),
        "adapter": adapter_name,
        "adapter_config": adapter_config,
        "adapter_version": adapter_version_out,
        "model": model,
        "model_version": model_version,
        "reports": reports,
        "forge_checks": forge_checks,
        "findings": findings,
        "tests_named": tests_named,
        "commits": commits,
        "stub_ratio": stub_ratio(diff_text, spec_dir / "stub", (specs_rel, records_rel),
                                  stub_test_paths(root, spec_dir / "stub", select_command)),
        "tokens": tokens,
        "cost_usd": cost_usd,
        "wall_clock_s": round(time.monotonic() - started, 3),
        "status": status_out,
        "report_class": report_class,
        "retry_of": retry_of,
        "steer": steer,
        "stop": stop,
    }
    record_path = write_record(item_records_dir, "verify", record, m, item_id, log=transcript)

    print(f"attavo verify: {status_out} ({record_path.name})")
    return 0 if status_out == "pass" else 1


# issue #84: a constant the lane names, not a manifest key, until a tenant
# disagrees -- how long drive gives a forge's checks room to conclude
# before it gives up and stops rather than call a judge on a tree the
# forge has not yet finished reading. Tenant zero's own checks conclude
# about five minutes after a push; the default bound gives them room to
# finish so a person is not the one re-running drive after CI, which is
# exactly the transport act this lane retires. ATTAVO_DRIVE_POLL_BOUND_S
# and ATTAVO_DRIVE_POLL_INTERVAL_S, read at call time rather than once at
# import, are for a test's own clock, not a tenant's -- there is no
# manifest key for either.
DRIVE_FORGE_POLL_BOUND_DEFAULT_S = 600.0

DRIVE_FORGE_POLL_INTERVAL_DEFAULT_S = 20.0


def drive_forge_poll_bound_s() -> float:
    return float(os.environ.get("ATTAVO_DRIVE_POLL_BOUND_S", DRIVE_FORGE_POLL_BOUND_DEFAULT_S))


def drive_forge_poll_interval_s() -> float:
    return float(os.environ.get("ATTAVO_DRIVE_POLL_INTERVAL_S", DRIVE_FORGE_POLL_INTERVAL_DEFAULT_S))


def forge_checks_unconcluded(forge_checks: dict[str, Any]) -> bool:
    """Whether poll_forge_checks should keep waiting rather than stop at
    once: an empty read (the forge has not attached any check to this sha
    yet) or any one check still carrying a status (queued, in_progress,
    pending, waiting) rather than a concluded conclusion. A concluded
    conclusion that is not green (failure, cancelled, timed_out, and
    anything else read_forge_checks did not recognize as ok) is not this
    -- that check is done, whether or not it passed."""
    conclusions = forge_checks["conclusions"]
    if not conclusions:
        return True
    return any(c["conclusion"] in FORGE_UNCONCLUDED_STATUSES for c in conclusions)


def poll_forge_checks(root: Path, checks_command: str, sha: str) -> tuple[dict[str, Any], bool]:
    """read_forge_checks, retried within drive_forge_poll_bound_s() while
    some check is still unconcluded (forge_checks_unconcluded) -- a check
    still running is not yet red, and drive gives it a little room to
    conclude before it gives up. A read where every check has already
    concluded, green or not, stops here at once: there is nothing left to
    wait on. The bound and interval are constants this lane names, not a
    manifest key, re-read at call time so a test's own clock
    (ATTAVO_DRIVE_POLL_BOUND_S, ATTAVO_DRIVE_POLL_INTERVAL_S) never has to
    survive past when it was set."""
    bound = drive_forge_poll_bound_s()
    interval = drive_forge_poll_interval_s()
    deadline = time.monotonic() + bound
    forge_checks, ok = read_forge_checks(root, checks_command, sha)
    while not ok and forge_checks_unconcluded(forge_checks) and time.monotonic() < deadline:
        time.sleep(interval)
        forge_checks, ok = read_forge_checks(root, checks_command, sha)
    return forge_checks, ok


COMMANDS = (
    Command(
        name='verify',
        run=cmd_verify,
        usage=('attavo verify [N]',),
        rank=60,
        help=(
            (110, """\
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
full rules both enforce."""),
        ),
        transport=True,
    ),
)
