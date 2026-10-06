from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from attavo import clock
from attavo.attest import check_attest_chain, item_accounting
from attavo.build import build_is_empty, driver_identity
from attavo.common import (
    MANIFEST,
    Proof,
    STAGES,
    fixture_item_id,
    git,
    product_version,
    read_manifest,
)
from attavo.events import stage_events_dir
from attavo.records import RECORD_RE, load_records, parse_attempts
from attavo.spec import parse_spec
from attavo.stops import (
    STOP_CLASSES,
    STOP_PROVENANCES,
    STOP_REDERIVABLE_FROM,
    STOP_STAGES,
    gate_stop,
    is_recovery_stop,
    recovery_fault,
    rederive_stop,
)
from attavo import Command

# --- doctor's three proofs ---

def proof_every_gate_rejects(root: Path, m: dict[str, Any] | None) -> Proof:
    p = Proof("every-gate-rejects", "every gate rejects its fixture")
    if m is None:
        return p.fail(f"no {MANIFEST} at {root}; no gate is registered")
    gates = m.get("gates") or {}
    if not gates:
        return p.fail("[gates] is empty; a gate that has rejected nothing does not exist")
    fixtures = root / m.get("paths", {}).get("rejections", "")
    # Proposal, not yet ruled on (issue #54): a gate that retires nothing
    # only adds, so proof one reports it here rather than waiting on the
    # schema alone -- read_manifest never validates against the manifest
    # schema at runtime, so a schema requirement is not an enforced one.
    no_retires = [name for name, gate in sorted(gates.items()) if not gate.get("retires")]
    missing, accepted = [], []
    for name, gate in sorted(gates.items()):
        fixture = fixtures / name
        if not fixture.is_dir():
            missing.append(name)
            continue
        env = dict(os.environ)
        item = fixture_item_id(fixture)
        if item is not None:
            env["ATTAVO_ITEM"] = item
        run = subprocess.run(gate["command"], shell=True, cwd=str(fixture),
                             capture_output=True, text=True, check=False, env=env)
        if run.returncode == 0:
            accepted.append(name)
    if missing:
        return p.fail(f"gate(s) with no rejection fixture: {', '.join(missing)}")
    if accepted:
        return p.fail(f"gate(s) that accepted their rejection fixture: {', '.join(accepted)}")
    if no_retires:
        return p.fail(f"gate(s) with no retires: {', '.join(no_retires)}")
    return p.hold(f"{len(gates)} gate(s) rejected their fixture")


def _merges_reachable(root: Path, ref: str | None = None) -> tuple[list[dict[str, Any]], str | None]:
    """(merges, refusal): every merge commit on the default branch's own
    first-parent line -- sha, parents, and its own first-parent diff --
    read by one process rather than the git log plus one git diff per
    merge merge_introducing_records used to pay walking toward the one
    it wants (issue #153). refs/remotes/origin/HEAD names the default
    branch without hard-coding "main", the same primitive
    diff_since_approval already uses (issue #52); walking from it rather
    than from HEAD is what keeps a transport merge on an item branch, a
    pull_request checkout's own synthetic test merge, and a merge_group's
    temporary merge from ever being mistaken for an item's merge (issue
    #169) -- none of those is reachable from the default branch's first-
    parent line by definition. When refs/remotes/origin/HEAD is not set,
    this refuses by name rather than guess, exactly as diff_since_approval
    does.

    The walk starts at HEAD itself when the current checkout is literally
    on the branch refs/remotes/origin/HEAD names (an ordinary checkout of
    the default branch, on push or on a developer's own machine) and at
    the remote-tracking ref's own value otherwise (any other branch, or
    the detached HEAD a pull_request or merge_group checkout leaves
    behind) -- HEAD only ever *is* the default branch's first-parent line
    in the former case; in the latter, only the remote-tracking ref names
    it. --diff-merges=first-parent gives each merge's diff against its
    first parent in the same pass git log --merges already makes to list
    them, in the same order, newest first."""
    symref = subprocess.run(
        ["git", "-C", str(root), "symbolic-ref", "-q", "refs/remotes/origin/HEAD"],
        capture_output=True, text=True, check=False)
    if symref.returncode != 0:
        return [], "refs/remotes/origin/HEAD is not set; run `git remote set-head origin -a`"
    default_remote_ref = symref.stdout.strip()
    default_branch = default_remote_ref.rsplit("/", 1)[-1]
    current_branch = subprocess.run(
        ["git", "-C", str(root), "symbolic-ref", "-q", "--short", "HEAD"],
        capture_output=True, text=True, check=False)
    default_ref = ref or ("HEAD" if current_branch.returncode == 0
                          and current_branch.stdout.strip() == default_branch
                          else default_remote_ref)
    try:
        out = git(root, "log", default_ref, "--first-parent", "--merges",
                 "--diff-merges=first-parent", "--name-only",
                 "--format=\x01%H\x1f%P")
    except RuntimeError:
        return [], None
    merges = []
    for block in out.split("\x01")[1:]:
        header, _, rest = block.partition("\n")
        sha, parents = header.split("\x1f")
        merges.append({"sha": sha, "parents": parents.split(),
                       "touched": [line for line in rest.splitlines() if line.strip()]})
    return merges, None


def merge_introducing_records(root: Path, records_rel: str, item: str,
                              stages: tuple[str, ...],
                              merges: list[dict[str, Any]] | None = None) -> tuple[str | None, str | None]:
    """(merge_sha, refusal): the merge commit on the default branch's own
    first-parent line whose first-parent diff introduces a record for
    every one of stages, under records_rel/item/ -- the same derivation
    attest itself uses (derive_attest_items), read here so proof two
    requires an item's stage records to have arrived by an actual merge
    on the default branch, not merely to sit in the tree, and not by a
    transport merge, a pull_request checkout's synthetic test merge, or a
    merge_group's temporary merge (issue #69 acceptance row 4; issue
    #169). merge_sha is None with no refusal when no such merge exists
    yet -- the item is not merged, not that the walk failed.

    merges, when given, is _merges_reachable's own result -- ledger's
    cmd_ledger computes it once and passes it to every item rather than
    walking the default branch's merges again for each (issue #153); a
    caller that already has merges has also already handled
    _merges_reachable's refusal, so this never re-derives one. Left
    None, a caller with just the one item to ask (doctor's proof two)
    gets merges computed here, refusal and all."""
    if merges is None:
        merges, refusal = _merges_reachable(root)
        if refusal:
            return None, refusal
    prefix = f"{records_rel.rstrip('/')}/{item}/"
    for merge in merges:
        found: set[str] = set()
        for p in merge["touched"]:
            if p.startswith(prefix):
                m = RECORD_RE.match(Path(p).name)
                if m:
                    found.add(m.group(1))
        if all(s in found for s in stages):
            return merge["sha"], None
    return None, None


INVOCATION_MARKER_SUFFIX = ".started"


def invocation_marker_path(records_dir: Path, stage: str, nn: int) -> Path:
    return records_dir / f"{stage}-{nn:02d}{INVOCATION_MARKER_SUFFIX}"


def heartbeat_s() -> float:
    return float(os.environ.get("ATTAVO_HEARTBEAT_S", 15))


def heartbeat_path(root: Path, stage: str, nn: int) -> Path:
    return stage_events_dir(root) / f"{stage}-{nn:02d}.heartbeat"


def holder_heartbeat_path(root: Path, info: dict[str, Any], stage: str, nn: int) -> Path | None:
    """Where the holder of an invocation writes its heartbeat, as far as
    this host can read it: the directory its marker names, when the
    marker is from this host and names one; this checkout's own, for a
    marker from this host written before markers named it; None for a
    marker from another host, whose heartbeat no file here holds."""
    import socket
    if info.get("host") != socket.gethostname():
        return None
    events = info.get("events")
    if isinstance(events, str) and events:
        return Path(events) / f"{stage}-{nn:02d}.heartbeat"
    return heartbeat_path(root, stage, nn)


def heartbeat_pid(path: Path) -> int | None:
    """The pid the worker wrote as its heartbeat file's one line on its
    first beat -- the holder's own, the only pid anyone signals. None when
    the file is absent, unreadable, empty (a worker from before this
    release touched it and wrote nothing), or not a positive integer."""
    try:
        text = path.read_text().strip()
    except OSError:
        return None
    if not text.isdigit() or int(text) <= 0:
        return None
    return int(text)


def heartbeat_age_s(path: Path) -> float | None:
    """Seconds since the heartbeat file was last touched; None when there
    is no such file."""
    try:
        return clock.now() - path.stat().st_mtime
    except OSError:
        return None


def write_invocation_marker(root: Path, records_dir: Path, stage: str, nn: int,
                            again_by: str | None = None, events_dir: Path | None = None) -> Path:
    """The fact that an invocation began, written before it does: at,
    host, the sha it starts on, and the product that started it. No pid:
    the writer is `attavo drive`, which returns seconds later, and the
    process that holds the stage is the worker, whose own heartbeat is
    the fact of holding it. Its record's arrival is what retires a
    marker; a marker with no record is an invocation that died or hangs,
    and proof five names it. again_by is the login that pressed `--again`
    to reset a stage whose budget was spent (or whose last two signatures
    matched) -- absent on every ordinary marker, never a bare false.
    events names the directory the holder's heartbeat will be written in
    (the checkout's own <git-dir>/attavo, events_dir when the marker is
    written from a detached worktree for another checkout): two checkouts
    on one host each keep their own, so the host alone never says where
    the holder's heartbeat can be read."""
    import socket
    from datetime import datetime, timezone
    records_dir.mkdir(parents=True, exist_ok=True)
    path = invocation_marker_path(records_dir, stage, nn)
    sha = git(root, "rev-parse", "HEAD").strip()
    info: dict[str, Any] = {"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "host": socket.gethostname(), "sha": sha,
                            "product_version": product_version(),
                            "events": str(events_dir or stage_events_dir(root))}
    if again_by:
        info["again_by"] = again_by
    path.write_text(json.dumps(info, indent=2) + "\n")
    return path


def invocation_age_s(info: dict[str, Any]) -> float | None:
    """Seconds since the marker's own `at` timestamp -- how long ago the
    invocation it names began, whatever host wrote it. None when `at` is
    missing or does not parse."""
    from datetime import datetime, timezone
    at = info.get("at")
    if not isinstance(at, str):
        return None
    try:
        stamp = datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return clock.now() - stamp.timestamp()


def invocations_without_record(item_records_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Every <stage>-NN.started under one item whose <stage>-NN.json has
    not arrived, with the marker's own dict ({} when unreadable)."""
    out: list[tuple[Path, dict[str, Any]]] = []
    if not item_records_dir.is_dir():
        return out
    for marker in sorted(item_records_dir.glob(f"*{INVOCATION_MARKER_SUFFIX}")):
        if (item_records_dir / f"{marker.stem}.json").is_file():
            continue
        try:
            with marker.open() as fh:
                info = json.load(fh)
        except (OSError, ValueError):
            info = {}
        out.append((marker, info if isinstance(info, dict) else {}))
    return out


def stall_bound_s(m: dict[str, Any]) -> int | None:
    """[drive].stall_after_s: the tenant's own bound past which a live
    invocation with no new event is hung. A bound the tenant did not
    write is a forecast, so there is no default."""
    bound = (m.get("drive") or {}).get("stall_after_s")
    if isinstance(bound, bool) or not isinstance(bound, (int, float)) or bound <= 0:
        return None
    return int(bound)


def ceiling_s(m: dict[str, Any], stage: str) -> int | None:
    """R3: [drive.<stage>].ceiling_s, the wall clock past which a live
    invocation of that stage is hung by its ceiling, whatever event age
    says (or does not say, for an adapter that streams none). No
    default: a ceiling the tenant did not declare is not assumed."""
    value = ((m.get("drive") or {}).get(stage) or {}).get("ceiling_s")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return int(value)


def attempts_budget(m: dict[str, Any]) -> int:
    """R3: [drive].attempts, the attempts a stage may spend -- across a
    plain retry, a returned retry, and a steered one alike -- before the
    item is a person's; 3 when the manifest does not say."""
    value = (m.get("drive") or {}).get("attempts")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return 3
    return value


def retry_after_s(m: dict[str, Any]) -> int:
    """R3: [drive].retry_after_s, the wait a transient stop's record sits
    through before its next attempt starts; 300 when the manifest does
    not say."""
    value = (m.get("drive") or {}).get("retry_after_s")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return 300
    return int(value)


def proof_no_invocation_lacks_record(root: Path, m: dict[str, Any] | None) -> Proof:
    import socket
    p = Proof("no-invocation-lacks-record", "no invocation lacks its record")
    if m is None:
        return p.fail(f"no {MANIFEST} at {root}; no records directory is declared")
    records_rel = m.get("paths", {}).get("records", "")
    records_dir = root / records_rel
    if not records_dir.is_dir():
        return p.fail(f"no records directory under {records_dir}")
    bound = stall_bound_s(m)
    if bound is None:
        return p.fail("no [drive].stall_after_s in the manifest; a hung invocation cannot be "
                      "told from a running one")
    # R3: a stage with no ceiling can run forever unjudged even when
    # stall_after_s is set (an adapter that streams no events trips no
    # stall bound at all) -- refused the same way stall_after_s's own
    # absence is, not folded into it, so the reason names exactly which
    # keys the manifest is missing.
    missing_ceilings = [f"[drive.{stage}].ceiling_s" for stage in ("build", "verify")
                        if ceiling_s(m, stage) is None]
    if missing_ceilings:
        return p.fail(f"no {' or '.join(missing_ceilings)} in the manifest; a stage with no "
                      f"ceiling can run forever unjudged (R3)")
    if (m.get("drive") or {}).get("attempts") is None:
        return p.fail("no [drive].attempts in the manifest; a retryable stop would spend an "
                      "unbounded number of attempts (R3)")
    this_host = socket.gethostname()
    recorded = 0
    running: list[str] = []
    in_flight: list[str] = []
    problems: list[str] = []
    for item_dir in sorted(d for d in records_dir.iterdir() if d.is_dir()):
        for marker in item_dir.glob(f"*{INVOCATION_MARKER_SUFFIX}"):
            if (item_dir / f"{marker.stem}.json").is_file():
                recorded += 1
        for marker, info in invocations_without_record(item_dir):
            name = f"item {item_dir.name} {marker.stem}"
            stage, nn_text = marker.stem.rsplit("-", 1)
            try:
                beat_file = holder_heartbeat_path(root, info, stage, int(nn_text))
                beat_age = heartbeat_age_s(beat_file) if beat_file is not None else None
            except (RuntimeError, ValueError):
                beat_file, beat_age = None, None
            if beat_age is not None:
                # The holder's own fact: fresh within three beats is
                # running, older is a holder that stopped touching it.
                if beat_age <= heartbeat_s() * 3:
                    running.append(f"{name} (heartbeat {int(beat_age)} s old, since {info.get('at')})")
                    continue
            elif info.get("host") != this_host or info.get("events"):
                # No heartbeat readable for a marker from another host, or
                # none yet where a marker from this host says its holder
                # beats (another checkout's worker not yet at its first
                # beat): not yet a failure. An invocation younger than the
                # tenant's own stall bound may simply be running, still
                # writing toward its own record.
                age = invocation_age_s(info)
                if age is not None and age < bound:
                    in_flight.append(f"{name} (host {info.get('host')} since {info.get('at')})")
                    continue
            problems.append(f"{name} started {info.get('at')} on {info.get('host')} "
                            f"and never wrote its record")
    if problems:
        return p.fail("; ".join(problems), unrecorded_invocations=len(problems),
                      running_invocations=running, in_flight=in_flight)
    return p.hold(f"{recorded} invocation(s) recorded, {len(running)} running, "
                  f"{len(in_flight)} in flight elsewhere",
                  unrecorded_invocations=0, running_invocations=running, in_flight=in_flight)


def proof_one_item_every_stage(root: Path, m: dict[str, Any] | None) -> Proof:
    p = Proof("one-item-every-stage", "one item has traversed every stage")
    if m is None:
        return p.fail(f"no {MANIFEST} at {root}; no records directory is declared")
    records_rel = m.get("paths", {}).get("records", "")
    records_dir = root / records_rel
    items = load_records(records_dir)
    if not items:
        return p.fail(f"no work item under {records_dir.relative_to(root)}")
    complete = [item for item, stages in items.items() if all(s in stages for s in STAGES)]
    if not complete:
        best = max(items.items(), key=lambda kv: len(kv[1]))
        lacking = [s for s in STAGES if s not in best[1]]
        return p.fail(f"no item carries every stage; item {best[0]} lacks {', '.join(lacking)}")

    # issue #74: a complete item is counted only when its chain holds --
    # attavo attest refuses a build re-approved after the fact or a
    # verify whose build went stale, and the coherence gate refuses the
    # merge group on exactly that; a proof that never asked would call a
    # traversal the gate itself would not let through. The refusal text
    # is check_attest_chain's own, never re-typed here.
    refused, held, unmerged = [], [], []
    for item in complete:
        chain, message = check_attest_chain(records_dir / item, item)
        if chain is None:
            refused.append(f"item {item}: {message}")
            continue
        merge_sha, merge_refusal = merge_introducing_records(root, records_rel, item, STAGES)
        if merge_refusal:
            return p.fail(merge_refusal)
        if merge_sha:
            held.append((item, merge_sha))
        else:
            unmerged.append(item)

    if held:
        names = ", ".join(item for item, _ in held)
        shas = ", ".join(sha[:12] for _, sha in held)
        return p.hold(f"item(s) {names} carry {', '.join(STAGES)} with the chain held, "
                      f"merged at {shas}")
    if refused:
        return p.fail("; ".join(refused))
    return p.fail(f"item(s) {', '.join(unmerged)} carry every stage with the chain held but no "
                  f"merge commit's first-parent diff introduces all of them")


def commit_subject(root: Path, sha: str) -> str:
    return git(root, "log", "-1", "--format=%s", sha).strip()


def touches_boundary(root: Path, sha: str, boundary_paths: list[str]) -> bool:
    """Whether sha's first-parent diff touches a path [spec.boundary].paths
    declares governed -- an ordinary merge that does is a boundary
    evasion (issue #72): the tenant carried a governed change around the
    path the manifest itself names."""
    diff = git(root, "diff", "--name-only", f"{sha}^1", sha).splitlines()
    return any(fnmatch.fnmatch(path, pattern) for path in diff for pattern in boundary_paths)


class WindowRefused(Exception):
    """The window start cannot be computed; str(exc) is the refusal."""


def proof_window_start(root: Path, m: dict[str, Any]) -> tuple[str, str, list[str]]:
    """(install commit, window start, first-parent chain after the install
    commit): where proofs three and six start reading. The start is
    [doctor].from if the tenant set it, otherwise the commit that
    introduced the manifest (issue #192: proof six reads this too, so the
    two proofs cannot disagree)."""
    try:
        install = git(root, "log", "--diff-filter=A", "--format=%H",
                      "--reverse", "--", MANIFEST).split()
    except RuntimeError as exc:
        raise WindowRefused(f"git: {exc}") from exc
    if not install:
        raise WindowRefused(f"{MANIFEST} is not committed; the tenant has no record boundary")
    install_sha = install[0]
    full_chain = git(root, "rev-list", f"{install_sha}..HEAD", "--first-parent").split()

    # issue #74: [doctor] from lets a tenant declare the doctor's read to
    # start at a later commit on main's first-parent line -- the point
    # after which every push arrived through the queue -- rather than the
    # install commit, which on a repository that adopted the product
    # after its history began is a boundary the doctor cannot otherwise
    # see. Any spelling git resolves is accepted (a short sha, a tag, a
    # ref) -- resolved with rev-parse --verify before the first-parent
    # check runs, so the two failure modes stay distinct: a spelling that
    # names no commit at all, and a commit that names one off the line.
    # Checked, not trusted: neither is silently forgiven back to install.
    declared_from = (m.get("doctor") or {}).get("from")
    window_start = install_sha
    if declared_from is not None:
        try:
            resolved_from = git(root, "rev-parse", "--verify", f"{declared_from}^{{commit}}").strip()
        except RuntimeError:
            raise WindowRefused(f"[doctor] from {declared_from} does not resolve to a commit") from None
        if resolved_from != install_sha and resolved_from not in full_chain:
            raise WindowRefused(f"[doctor] from {declared_from} is not on main's first-parent "
                                f"line after the install commit {install_sha[:12]}")
        window_start = resolved_from
    return install_sha, window_start, full_chain


def proof_no_commit_lacks_record(root: Path, m: dict[str, Any] | None) -> Proof:
    p = Proof("no-commit-lacks-record", "no commit lacks a record")
    if m is None:
        return p.fail(f"no {MANIFEST} at {root}; the tenant has no record boundary")
    try:
        install_sha, window_start, full_chain = proof_window_start(root, m)
    except WindowRefused as exc:
        return p.fail(str(exc))
    if not full_chain:
        return p.fail(f"no commit after the install commit {install_sha[:12]}; nothing has been recorded")

    if window_start == install_sha:
        window_spec = f"{install_sha}..HEAD"
        commits = full_chain
    else:
        window_spec = f"{window_start}^..HEAD"
        commits = git(root, "rev-list", window_spec, "--first-parent").split()
    before_window = len(full_chain) - len(commits)

    records_rel = m.get("paths", {}).get("records", "")
    records_dir = root / records_rel
    named: set[str] = set()
    for stages in load_records(records_dir).values():
        for rec in stages.values():
            sha = rec.get("sha")
            if isinstance(sha, str):
                named.add(sha)
            for c in rec.get("commits", []) or []:
                if isinstance(c, str):
                    named.add(c)
    record_commits = set(git(root, "rev-list", window_spec, "--first-parent",
                             "--", records_rel).split()) if records_rel else set()
    merges = set(git(root, "rev-list", window_spec, "--first-parent", "--merges").split())
    boundary_paths = ((m.get("spec") or {}).get("boundary") or {}).get("paths") or []

    # Every first-parent commit in the window is exactly one of three
    # (issue #72): recorded (a record names it, or its diff touches
    # paths.records), ordinary (a merge that doesn't), or unrecorded
    # (anything else -- on a protected main with a queue, a non-merge
    # commit is the only way a change arrives without the forge).
    recorded, ordinary, unrecorded = [], [], []
    ordinary_commits, unrecorded_commits = [], []
    boundary_evasions = 0
    for c in commits:
        if c in named or c in record_commits:
            recorded.append(c)
        elif c in merges:
            ordinary.append(c)
            evasion = bool(boundary_paths and touches_boundary(root, c, boundary_paths))
            if evasion:
                boundary_evasions += 1
            ordinary_commits.append({"sha": c, "subject": commit_subject(root, c),
                                     "boundary_evasion": evasion})
        else:
            unrecorded.append(c)
            unrecorded_commits.append({"sha": c, "subject": commit_subject(root, c)})

    not_transport = driver_commits_not_transport(root, window_spec, records_rel) if records_rel else []
    counts = f"{len(recorded)} recorded, {len(ordinary)} ordinary, {len(unrecorded)} unrecorded"
    evasions = boundary_evasions if boundary_paths else "n/a"
    extra = {"recorded": len(recorded), "ordinary": len(ordinary),
             "unrecorded": len(unrecorded), "boundary_evasions": evasions,
             "before_window": before_window,
             "ordinary_commits": ordinary_commits, "unrecorded_commits": unrecorded_commits,
             "driver_commits_not_transport": not_transport}
    if not_transport:
        return p.fail(f"a driver commit is not a records transport: {not_transport[0]}; {counts}", **extra)
    if unrecorded:
        return p.fail(f"{unrecorded[-1][:12]} lacks a record; {counts}; boundary evasions: "
                      f"{evasions}; before the window: {before_window}", **extra)
    return p.hold(f"{counts}; boundary evasions: {evasions}; before the window: {before_window}",
                  **extra)


def driver_commits_not_transport(root: Path, window_spec: str, records_rel: str) -> list[str]:
    """Every commit in the window authored as driver_identity() that is not
    a records transport: more than one parent, or a path outside
    <records>/<N>/ for the one item N it touches. The driver's commits
    carry records and nothing else, one parent each (root cause 1: the
    driver makes them in a detached worktree and never merges), so one
    that does either was made by something other than the transport.
    Each entry reads `<sha12> <why>`."""
    email = driver_identity()["email"]
    bad: list[str] = []
    prefix = records_rel.strip("/") + "/"
    out = git(root, "log", window_spec, "--format=%H\x1f%ae\x1f%P")
    for line in out.splitlines():
        sha, author, parents = line.split("\x1f")
        if author != email:
            continue
        if len(parents.split()) > 1:
            bad.append(f"{sha[:12]} is a merge")
            continue
        paths = git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha).splitlines()
        items = {p[len(prefix):].split("/", 1)[0] for p in paths if p.startswith(prefix) and "/" in p[len(prefix):]}
        outside = [p for p in paths if not (p.startswith(prefix) and "/" in p[len(prefix):])]
        if outside:
            bad.append(f"{sha[:12]} touches {outside[0]}, outside its item's records")
        elif len(items) > 1:
            bad.append(f"{sha[:12]} touches the records of items {', '.join(sorted(items))}")
    return bad


ADAPTER_EXIT_RE = re.compile(r"^\[adapter exited \d+\]$")

_JSON_LINE_PARSE_FAILED = object()


def _parse_json_line(text: str) -> Any:
    """A line's own parsed JSON value, or the sentinel
    _JSON_LINE_PARSE_FAILED when it does not parse -- the one call site
    in this file for a defensive, line-at-a-time JSON parse, shared by
    proof four's own tool-error check below (any valid JSON satisfies
    it) and an events line's own `at` read (issue #151, which further
    requires a dict). A dedicated sentinel, not None: a line that is
    the literal JSON value null parses successfully and must read as
    such, not as a failure."""
    try:
        return json.loads(text)
    except ValueError:
        return _JSON_LINE_PARSE_FAILED


def _log_owes_tool_error(log: str | None) -> bool:
    """True when a log's last non-empty line is exactly an adapter-exit
    marker and every line before it parses as JSON -- an adapter's own
    event stream, never a tool's own words (issue #94's own specimen,
    tenant zero's attest/records/659/verify-01.log: codex's
    "thread.started" event, then nothing but "[adapter exited 1]" -- the
    tool's real error line dropped before this reached the log, issue
    #86's own bug in split_adapter_stdout). A log that is only the exit
    marker, nothing before it at all, still owes the same line. An
    `error` event with a non-empty string `message` is the tool's own
    error delivered as an event (issue #200: "Selected model is at
    capacity"), so it owes nothing. So is an API error the tool reports as
    its own assistant message -- Claude Code's `"is_api_error_message":
    true` event, whose text is the tool's words ("Failed to authenticate:
    OAuth session expired and could not be refreshed", item 267 build-01
    and build-02, 2026-09-30) -- when that text is non-empty."""
    if not log:
        return False
    lines = [l for l in log.splitlines() if l.strip()]
    if not lines or not ADAPTER_EXIT_RE.match(lines[-1]):
        return False
    for l in lines[:-1]:
        parsed = _parse_json_line(l)
        if parsed is _JSON_LINE_PARSE_FAILED:
            return False
        if isinstance(parsed, dict) and parsed.get("type") == "error":
            message = parsed.get("message")
            if isinstance(message, str) and message:
                return False
        if isinstance(parsed, dict) and parsed.get("is_api_error_message") is True:
            if _api_error_text(parsed):
                return False
    return True


def _api_error_text(event: dict[str, Any]) -> str:
    """The text of an API error an adapter's tool delivered as its own
    assistant message: the non-empty text blocks of message.content,
    joined. Empty when there is none, which owes the line still."""
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return ""
    texts = [b.get("text") for b in content
             if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)]
    return " ".join(t.strip() for t in texts if t and t.strip())


def proof_no_stop_lacks_cause(root: Path, m: dict[str, Any] | None) -> Proof:
    p = Proof("no-stop-lacks-cause", "no stop lacks its cause")
    if m is None:
        return p.fail(f"no {MANIFEST} at {root}; no records directory is declared")
    records_rel = m.get("paths", {}).get("records", "")
    records_dir = root / records_rel
    if not records_dir.is_dir():
        return p.fail(f"no records directory under {records_dir}")

    # issue #89's own window lesson, proof three's already: a record
    # before record_version 3 has no stop by construction (schema
    # versions 1 and 2 predate it) -- counted here, never refused.
    older = 0
    checked = 0
    before_rederivable = 0
    problems: list[str] = []
    for item_dir in sorted(d for d in records_dir.iterdir() if d.is_dir()):
        for stage in STOP_STAGES:
            for n, rec in sorted(parse_attempts(item_dir, stage).items()):
                if rec.get("record_version", 1) < 3:
                    older += 1
                    continue
                if rec.get("status") == "pass":
                    continue
                checked += 1
                name = f"{stage}-{n:02d}"
                rederivable = rec.get("record_version", 1) >= STOP_REDERIVABLE_FROM[stage]
                if not rederivable:
                    before_rederivable += 1
                stop = rec.get("stop")
                if not stop:
                    problems.append(f"item {item_dir.name} {name} carries no stop")
                    continue
                cls, provenance = stop.get("class"), stop.get("provenance")
                if cls not in STOP_CLASSES:
                    problems.append(f"item {item_dir.name} {name} stop.class {cls!r} names "
                                    f"no enumerated class")
                    continue
                if provenance not in STOP_PROVENANCES:
                    problems.append(f"item {item_dir.name} {name} stop.provenance "
                                    f"{provenance!r} names no enumerated provenance")
                    continue
                # Issue #254: at the version that stores what the stop was
                # derived from, the stop is that function's value -- the
                # pass's own recovery records by their own rule.
                if rederivable and is_recovery_stop(rec):
                    fault = recovery_fault(rec)
                    if fault:
                        problems.append(f"item {item_dir.name} {name} {fault}")
                        continue
                elif rederivable:
                    derived = rederive_stop(stage, rec)
                    if derived is None or (derived["class"], derived["provenance"]) != (cls, provenance):
                        problems.append(
                            f"item {item_dir.name} {name} stop is {cls}/{provenance} but its "
                            f"own fields derive "
                            f"{'no stop' if derived is None else derived['class'] + '/' + derived['provenance']}")
                        continue
                if cls == "unclassed" and not stop.get("detail"):
                    problems.append(f"item {item_dir.name} {name} is unclassed with no detail")
                    continue
                log_path = item_dir / f"{name}.log"
                log = log_path.read_text() if log_path.is_file() else None
                # Issue #89, rejection test 1's own specimen (tenant
                # zero's #659 build-01): a failed build that actually ran
                # the gates -- selected_tests is set only once cmd_build
                # reaches them -- owes their heading in the log, whatever
                # else stop already says.
                if (stage == "build" and rec.get("status") == "failed"
                        and rec.get("selected_tests") is not None
                        and "--- attavo gates, iteration" not in (log or "")):
                    problems.append(f"item {item_dir.name} {name} failed after the gates ran "
                                    f"but its log carries no gates heading")
                    continue
                if _log_owes_tool_error(log):
                    problems.append(f"item {item_dir.name} {name}'s log ends in an adapter "
                                    f"exit with nothing before it but event lines; the tool's "
                                    f"own error line is owed")

        # issue #137: a refused spec-gate record is a stop too, class
        # "gate" -- synthesized the same way item_stops and
        # cmd_file_stops already read it, since cmd_spec_gate itself
        # carries no stop field to check for one. No record_version
        # gate here: unlike build/verify's own stop field, gate is never
        # persisted, so there is no older shape that predates it.
        for n, rec in sorted(parse_attempts(item_dir, "spec").items()):
            if rec.get("status") != "refused":
                continue
            checked += 1
            name = f"spec-{n:02d}"
            stop = gate_stop(rec.get("refusal"))
            cls, provenance = stop["class"], stop["provenance"]
            if cls not in STOP_CLASSES:
                problems.append(f"item {item_dir.name} {name} stop.class {cls!r} names "
                                f"no enumerated class")
                continue
            if provenance not in STOP_PROVENANCES:
                problems.append(f"item {item_dir.name} {name} stop.provenance "
                                f"{provenance!r} names no enumerated provenance")
                continue
            if not stop.get("detail"):
                problems.append(f"item {item_dir.name} {name} is a gate stop with no cause")

    older_note = (f"{older} record(s) before stop was recorded; {before_rederivable} non-pass "
                 f"record(s) before their stop could be re-derived")
    if problems:
        return p.fail("; ".join(problems), older_records=older,
                     before_rederivable=before_rederivable)
    return p.hold(f"{checked} checked ({older_note})" if checked == 0
                 else f"{checked} non-pass record(s) carry stop with a cause ({older_note})",
                 older_records=older, before_rederivable=before_rederivable)


def attestation_command(m: dict[str, Any]) -> str | None:
    """[forge].attestation_command: the tenant's own read of the store,
    given a merge sha on stdin, printing the statement as JSON on exit 0
    and nothing on a non-zero exit. Absent is not passing (issue #158:
    the doctor said done while #106, #148, #153 had no statement in the
    store at all -- there was no key to read one through)."""
    cmd = (m.get("forge") or {}).get("attestation_command")
    return cmd if isinstance(cmd, str) and cmd.strip() else None


def statement_for(root: Path, cmd: str, merge_sha: str) -> tuple[dict[str, Any] | None, int]:
    """The statement the forge holds for merge_sha and the command's exit
    code. The statement is None when the command exits non-zero or prints
    anything but a JSON object -- exit 2 means the store itself could not
    be read (issue #158, amended after build-05: distinct from exit 1,
    the store read fine and simply held no statement)."""
    run = subprocess.run(cmd, shell=True, cwd=str(root), input=merge_sha + "\n",
                         capture_output=True, text=True, check=False)
    if run.returncode != 0:
        return None, run.returncode
    try:
        parsed = json.loads(run.stdout)
    except ValueError:
        return None, run.returncode
    return (parsed if isinstance(parsed, dict) else None), run.returncode


def _is_ancestor(root: Path, earlier: str, later: str) -> bool:
    run = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", earlier, later],
                         capture_output=True, check=False)
    return run.returncode == 0


# Maps whose keys are the records' data (a stage or an adapter that cost
# something), not a release's fields: a key missing from one is a difference.
DATA_KEYED = ("by_stage_usd", "by_adapter_usd")


def _same(stated: Any, summed: Any) -> bool:
    """Equal as the accounting means it: a number equals a number of the
    same value (a store may print 40.0 as 40), a bool equals only a bool,
    and a list is equal member by member. No tolerance and no conversion:
    both sides are the same function's rounding of the same records, and an
    int one past 2**53 is not the float beside it."""
    if isinstance(stated, bool) or isinstance(summed, bool):
        return isinstance(stated, bool) and isinstance(summed, bool) and stated == summed
    if isinstance(stated, (int, float)) and isinstance(summed, (int, float)):
        return bool(stated == summed)
    if isinstance(stated, list) and isinstance(summed, list):
        return len(stated) == len(summed) and all(_same(a, b) for a, b in zip(stated, summed))
    return type(stated) is type(summed) and bool(stated == summed)


def _differences(path: str, stated: Any, summed: Any, keyed_by_data: bool = False) -> list[str]:
    """Every field of `stated` that is not what its records sum to, named by
    its path. Only the keys the statement carries are compared, at every
    depth, so a field a later release adds is not demanded of a statement
    sealed before it; a key it carries that the function does not produce
    is a difference."""
    if not (isinstance(stated, dict) and isinstance(summed, dict)):
        return [] if _same(stated, summed) else [f"{path} {stated!r} is not its records' {summed!r}"]
    found: list[str] = []
    for key in sorted(stated):
        if key not in summed:
            found.append(f"{path}.{key} {stated[key]!r} is not a field its records sum to")
        else:
            found += _differences(f"{path}.{key}", stated[key], summed[key], key in DATA_KEYED)
    if keyed_by_data:
        found += [f"{path}.{key} is absent; its records sum to {summed[key]!r}"
                  for key in sorted(summed.keys() - stated.keys())]
    return found


def _carried(predicate: dict[str, Any], key: str) -> str:
    return repr(predicate[key]) if key in predicate else "absent"


V3_ONLY_FIELDS = ("cost_by_stage_usd", "wall_clock_by_stage_s", "tokens_by_stage")


V5_COST_FIELDS = ("reported_usd", "estimated_usd", "basis_by_stage", "rates", "estimate_note")


def _predicate_version(statement: dict[str, Any], predicate: dict[str, Any]) -> int:
    """The attestation version a statement was sealed under (issues #309,
    #307): named by its envelope's predicateType, else known by what it
    carries -- v3 by the final-attempt fields v4 retired, v5 by the rates
    its cost block carries, v4 otherwise. Each is judged by its own rules;
    nothing sealed is rewritten."""
    declared = statement.get("predicateType")
    named = re.search(r"/attestation/v(\d+)$", declared) if isinstance(declared, str) else None
    if named:
        return int(named.group(1))
    if any(key in predicate for key in V3_ONLY_FIELDS):
        return 3
    cost = predicate.get("cost")
    return 5 if isinstance(cost, dict) and "rates" in cost else 4


def _accounting_disagrees(root: Path, item_records_dir: Path, item: str, merge_sha: str,
                          statement: dict[str, Any]) -> str | None:
    """A sealed statement that carries `time` and `cost` (and, from v4,
    `tokens`) says what its records sum to, field by field -- the same function attest summed them
    with, over the same records (none is written after a merge), sealed at
    the same commit (the statement is computed at the merge, so lead_s and
    waiting_s are recomputed from its committer time, not excepted). Exact:
    #202 retired the one-second tolerance. A statement sealed before `time`
    existed carries neither key and is not checked; a key that is present
    holds a block, so a null one, or one block without the other, is a
    disagreement. No new read of the store."""
    inner = statement.get("predicate")
    predicate = inner if isinstance(inner, dict) else statement
    if "time" not in predicate and "cost" not in predicate:
        return None
    stated_time, stated_cost = predicate.get("time"), predicate.get("cost")
    where = f"item {item} merged at {merge_sha[:12]}"
    if not isinstance(stated_time, dict) or not isinstance(stated_cost, dict):
        return (f"{where}: the statement's time and cost are not both blocks "
                f"(time is {_carried(predicate, 'time')}, cost is {_carried(predicate, 'cost')})")
    try:
        sealed_at: int | None = int(git(root, "show", "-s", "--format=%ct", merge_sha).strip())
    except (RuntimeError, ValueError):
        sealed_at = None
    version = _predicate_version(statement, predicate)
    # v5 prices a record by the rates its own cost block carries -- what the
    # tenant's manifest said when it was sealed, which a later edit to the
    # manifest does not unseal; v3 and v4 priced nothing.
    carried = stated_cost.get("rates") if version >= 5 else None
    summed_time, summed_cost, summed_tokens = item_accounting(
        item_records_dir, sealed_at, carried if isinstance(carried, dict) else None)
    cost_now = {k: v for k, v in summed_cost.items() if version >= 5 or k not in V5_COST_FIELDS}
    if version < 5:  # v3 and v4 named the record, never the missing rate
        cost_now["not_read"] = [n.split(" (no [cost.rates]")[0] for n in summed_cost["not_read"]]
    differing = (_differences("time", stated_time, dict(summed_time))
                 + _differences("cost", stated_cost, cost_now))
    if version >= 4:
        stated_tokens = predicate.get("tokens")
        if isinstance(stated_tokens, dict):
            differing += _differences("tokens", stated_tokens, dict(summed_tokens))
        else:
            differing.append(f"tokens is {_carried(predicate, 'tokens')}, not a block "
                             f"its records sum to {dict(summed_tokens)!r}")
    if differing:
        return f"{where}: the statement's " + "; ".join(differing)
    return None


def proof_every_attested_item_is_sealed(root: Path, m: dict[str, Any] | None) -> Proof:
    p = Proof("every-attested-item-is-sealed", "every attested item has its statement in the store")
    if m is None:
        return p.fail(f"no {MANIFEST} at {root}; no records directory is declared")
    cmd = attestation_command(m)
    if cmd is None:
        return p.fail("no [forge].attestation_command in the manifest; the store cannot be read")
    records_rel = m.get("paths", {}).get("records", "")
    records_dir = root / records_rel
    if not records_dir.is_dir():
        return p.fail(f"no records directory under {records_dir}")
    items = load_records(records_dir)
    complete = [item for item, stages in sorted(items.items()) if all(s in stages for s in STAGES)]

    # [forge].sealed_since: merges at or before this commit are before the
    # window -- the tenant could not seal them (no queue, no signing
    # workflow, or a store that held nothing yet). They are named, never
    # failed. Absent, the window starts where proof three's does: the
    # install commit, or [doctor].from (issue #192).
    since = (m.get("forge") or {}).get("sealed_since")
    if not (isinstance(since, str) and since.strip()):
        try:
            since = proof_window_start(root, m)[1]
        except WindowRefused as exc:
            return p.fail(str(exc))
    merges, refusal = _merges_reachable(root)
    if refusal:
        return p.fail(refusal)
    sealed: list[str] = []
    before_window: list[str] = []
    unsealed: list[str] = []
    not_merged: list[str] = []
    not_read: list[str] = []
    reasons: list[str] = []
    for item in complete:
        merge_sha, _ = merge_introducing_records(root, records_rel, item, STAGES, merges=merges)
        if merge_sha is None:
            not_merged.append(item)
            continue
        if _is_ancestor(root, merge_sha, since):
            before_window.append(item)
            continue
        statement, exit_code = statement_for(root, cmd, merge_sha)
        if statement is not None:
            sealed.append(item)
            disagreement = _accounting_disagrees(root, records_dir / item, item, merge_sha,
                                                 statement)
            if disagreement:
                reasons.append(disagreement)
        elif exit_code == 2:
            not_read.append(item)
            reasons.append(f"item {item} merged at {merge_sha[:12]}: not read -- "
                           f"the store could not be read; {cmd!r} exited 2")
        else:
            unsealed.append(item)
            reasons.append(f"item {item} merged at {merge_sha[:12]} has no statement in the store")
    if reasons:
        return p.fail("; ".join(reasons), sealed=sealed, before_window=before_window,
                      unsealed=unsealed, not_merged=not_merged, not_read=not_read)
    window = f" at {since[:12]}" if before_window else ""
    return p.hold(f"{len(sealed)} sealed, {len(before_window)} before the window{window}, "
                 f"{len(not_merged)} not merged",
                 sealed=sealed, before_window=before_window, unsealed=unsealed,
                 not_merged=not_merged, not_read=not_read)


def proof_no_attested_item_is_empty(root: Path, m: dict[str, Any] | None) -> Proof:
    """Proof seven (item 227). Every merged, fully-staged item's passing
    build names a change: its tree, restricted to the item's own spec
    claims, differs from the approved tree there (item 229 -- asked
    against the approval, not the build's own start_sha, so a build
    that adopted a prior attempt's valid commits is not empty just
    because that attempt owed no commit of its own; scoped to claims,
    not "everything outside paths.records", so a commit that lands
    between approval and a later attempt's start but touches no
    claimed path never rescues or defeats the comparison on its own
    account). Item 227 was specified, approved, built by one
    invocation, judged by another, merged and sealed, and its build
    committed nothing -- the executor left the stub in the working tree,
    the gate read the tree, and main received the spec and none of the
    change. Six proofs held over it.

    [doctor].built_since: merges at or before this commit are before the
    window -- named, never failed -- so the specimen stands on every
    doctor run without failing the tenant forever. Absent, the window is
    proof three's. A build record whose approval cannot be resolved
    (no approval_sha at all, or one this repository cannot read) is
    named as not readable, not failed."""
    p = Proof("no-attested-item-is-empty", "every attested item's build names a change")
    if m is None:
        return p.fail(f"no {MANIFEST} at {root}; no records directory is declared")
    records_rel = m.get("paths", {}).get("records", "")
    specs_rel = m.get("paths", {}).get("specs", "")
    records_dir = root / records_rel
    if not records_dir.is_dir():
        return p.fail(f"no records directory under {records_dir}")
    items = load_records(records_dir)
    complete = [item for item, stages in sorted(items.items()) if all(s in stages for s in STAGES)]
    since = (m.get("doctor") or {}).get("built_since")
    if not (isinstance(since, str) and since.strip()):
        try:
            since = proof_window_start(root, m)[1]
        except WindowRefused as exc:
            return p.fail(str(exc))
    else:
        try:
            since = git(root, "rev-parse", "--verify", f"{since}^{{commit}}").strip()
        except RuntimeError:
            return p.fail(f"[doctor] built_since {since} does not resolve to a commit")
    merges, refusal = _merges_reachable(root)
    if refusal:
        return p.fail(refusal)
    built: list[str] = []
    before_window: list[str] = []
    not_merged: list[str] = []
    not_read: list[str] = []
    empty: list[str] = []
    for item in complete:
        merge_sha, _ = merge_introducing_records(root, records_rel, item, STAGES, merges=merges)
        if merge_sha is None:
            not_merged.append(item)
            continue
        if _is_ancestor(root, merge_sha, since):
            before_window.append(item)
            continue
        build = items[item]["build"]
        sha, approval_sha = build.get("sha"), build.get("approval_sha")
        if not sha or not approval_sha:
            not_read.append(item)
            continue
        spec_md = root / specs_rel / item / "spec.md"
        claims: list[str] = []
        if spec_md.is_file():
            parsed, _ = parse_spec(spec_md)
            claims = parsed["claims"] if parsed else []
        is_empty = build_is_empty(root, approval_sha, sha, claims)
        if is_empty is None:
            not_read.append(item)
        elif is_empty:
            empty.append(f"item {item} merged at {merge_sha[:12]}: build names no "
                         "claimed change; nothing was built")
        else:
            built.append(item)
    if empty:
        return p.fail("; ".join(empty), built=built, before_window=before_window,
                      not_merged=not_merged, not_read=not_read)
    window = f" at {since[:12]}" if before_window else ""
    return p.hold(f"{len(built)} built, {len(before_window)} before the window{window}, "
                  f"{len(not_read)} without a readable approval, {len(not_merged)} not merged",
                  built=built, before_window=before_window, not_merged=not_merged,
                  not_read=not_read)


def doctor(root: Path) -> list[Proof]:
    m = read_manifest(root)
    return [proof_every_gate_rejects(root, m),
            proof_one_item_every_stage(root, m),
            proof_no_commit_lacks_record(root, m),
            proof_no_stop_lacks_cause(root, m),
            proof_no_invocation_lacks_record(root, m),
            proof_every_attested_item_is_sealed(root, m),
            proof_no_attested_item_is_empty(root, m)]


def cmd_doctor(argv: list[str]) -> int:
    as_json = "--json" in argv
    args = [a for a in argv if a != "--json"]
    root = Path(args[0] if args else ".").resolve()
    proofs = doctor(root)
    if as_json:
        print(json.dumps({"root": str(root), "done": all(p.holds for p in proofs),
                          "proofs": [p.as_dict() for p in proofs]}, indent=2))
    else:
        for i, p in enumerate(proofs, 1):
            print(f"proof {i} {p.key}: {'HOLDS' if p.holds else 'FAILS'}: {p.reason}")
        print("doctor: done" if all(p.holds for p in proofs)
              else f"doctor: not done ({sum(not p.holds for p in proofs)} of "
                   f"{len(proofs)} proofs fail)")
    return 0 if all(p.holds for p in proofs) else 1


COMMANDS = (
    Command(
        name='doctor',
        run=cmd_doctor,
        usage=('attavo doctor [TENANT_ROOT] [--json]',),
        rank=10,
        help=(
            (20, """\
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
is done."""),
        ),
    ),
)
