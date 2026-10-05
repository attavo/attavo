"""The reconciler's R3 (claude/reconciler-brief-2026-09-20.md ruling C and
its amendments; claude/r3-rulings-2026-09-25.md): a stage that stops for a
reason that does not touch the definition of done is started again by a
pass, not a person. Four retryable families (died, hung, transient,
returned), one budget of [drive].attempts per stage, an early stop on two
identical failure signatures, an exhaustion report, stage ceilings, and
the prior attempt's evidence handed to the next through the feedback file
the adapter contract already passes.

Every test drives a fixture tenant through `attavo drive N` passes alone
and reads records, never internals. Written red first against d20fdea
(R2 as merged) and against R3's thin stub.

Run: python3 -m pytest tests -q
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from helpers import commit_all, git, make_tenant
from test_drive import (
    GREEN_FORGE,
    add_bare_remote,
    add_forge,
    approve_item,
    latest_record,
    record_files,
    records_dir,
    run_attavo,
)
from helpers import wait_for
from test_pass import FAST, clock_ahead, passes_until, pushed, report

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_RUN = ROOT / "adapters" / "fixture" / "run"

DONE_LINE = ('{"status":"done","question":null,"model":"fixture","model_version":null,'
             '"adapter_version":"fixture-1","tokens":{"input":1,"output":1},"cost_usd":0.0}')
TRANSIENT_LINE = ('{"status":"failed","question":null,"class":"transient",'
                  '"detail":"API error: overloaded, retry after 30s","model":"fixture",'
                  '"model_version":null,"adapter_version":"fixture-1",'
                  '"tokens":{"input":1,"output":1},"cost_usd":0.0}')


def add_drive_table(root: Path, *, attempts: int = 3, retry_after_s: int = 0,
                    build_ceiling_s: int = 600, verify_ceiling_s: int = 600,
                    stall_after_s: int = 60) -> None:
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text() + (
        f"\n[drive]\nstall_after_s = {stall_after_s}\nattempts = {attempts}\n"
        f"retry_after_s = {retry_after_s}\n\n[drive.build]\nceiling_s = {build_ceiling_s}\n\n"
        f"[drive.verify]\nceiling_s = {verify_ceiling_s}\n"))
    commit_all(root, "declare the drive table")


def tenant_adapter(root: Path, build_prelude: str, streams_events: bool | None = None) -> None:
    """A tenant-side build adapter: `build_prelude` runs first on the build
    stage (it may exit), then the product's own fixture adapter. A build
    that is retried on a tree already carrying built.txt rewrites it, so
    the fixture's own commit has something to commit."""
    d = root / "attavo" / "adapters" / "fixture"
    d.mkdir(parents=True, exist_ok=True)
    (d / "run").write_text(
        "#!/bin/sh\n"
        'if [ "${ATTAVO_STAGE:-build}" = build ]; then\n'
        f"{build_prelude}\n"
        "  if [ -f built.txt ]; then\n"
        '    echo "built again $(date +%s%N)" > built.txt\n'
        "    git add built.txt\n"
        "    git commit -q -m 'fixture: write built.txt again'\n"
        f"    echo '{DONE_LINE}'\n"
        "    exit 0\n"
        "  fi\n"
        "fi\n"
        f"exec sh {FIXTURE_RUN}\n")
    (d / "run").chmod(0o755)
    if streams_events is not None:
        (d / "conformance.json").write_text(json.dumps({"streams_events": streams_events}) + "\n")
    commit_all(root, "a tenant-side adapter")


def approved_tenant(tmp_path: Path, build_prelude: str = "", **drive: Any) -> tuple[Path, Path]:
    root = make_tenant(tmp_path, "build_valid")
    add_forge(root, GREEN_FORGE)
    add_drive_table(root, **drive)
    tenant_adapter(root, build_prelude)
    bare = add_bare_remote(root, tmp_path)
    approve_item(root)
    commit_all(root, "approved")
    git(root, "push", "-q", "origin", "HEAD:attavo/1-x")
    return root, bare


def stop_of(root: Path, stage: str, nn: int) -> dict[str, Any]:
    rec = json.loads((records_dir(root) / f"{stage}-{nn:02d}.json").read_text())
    return dict(rec.get("stop") or {})


def counsel_records(root: Path) -> list[Path]:
    return record_files(root, "counsel")


def test_a_transient_stop_retries_plainly_after_the_wait(tmp_path: Path) -> None:
    """Ruling C: a vendor's capacity, rate, or 5xx refusal is `transient`;
    the next pass starts the next attempt, asks counsel nothing, and
    waits [drive].retry_after_s first (#194's verify-01 paid for a
    counsel run and two presses on exactly this)."""
    prelude = ('  if [ ! -f "$ATTAVO_RECORDS_DIR/build-01.json" ]; then\n'
               f"    echo '{TRANSIENT_LINE}'\n    exit 1\n  fi")
    # a wait an hour long: "inside the wait" holds however slow the machine is, and
    # "past it" is the clock file, never the passing of time
    # (the ceilings and the stall bound are a day, so the clock file moves only the wait)
    root, bare = approved_tenant(tmp_path, prelude, retry_after_s=3600, build_ceiling_s=86400,
                                 verify_ceiling_s=86400, stall_after_s=86400)
    assert run_attavo(root, "drive", "1", env=FAST).returncode == 0
    wait_for(lambda: pushed(bare, "build record for item 1 (build-01)"))
    assert stop_of(root, "build", 1)["class"] == "transient"
    assert stop_of(root, "build", 1)["signature"] == "API error: overloaded, retry after s"

    # inside the wait: a pass reads the level and starts nothing
    early = run_attavo(root, "drive", "1", env=FAST)
    assert early.returncode == 0 and "retrying" in early.stdout.lower(), early.stdout
    assert not (records_dir(root) / "build-02.started").exists()

    # past the wait: the clock says so, and the pass starts the next attempt at once
    # the retry's worker beats on the real clock, so a beat is live for three
    # beats of the controlled one: beats 2500 s apart (a bound of 7500 s) keep it running at 7200
    passes_until(root, lambda: len(record_files(root, "build")) == 2,
                 env={**clock_ahead(tmp_path, 7200), "ATTAVO_HEARTBEAT_S": "2500"})
    rec = latest_record(root, "build")
    assert rec["status"] == "pass" and rec["retry_of"] == "build-01", rec
    assert counsel_records(root) == []


def test_died_and_hung_retry_on_the_next_pass(tmp_path: Path) -> None:
    """A recovered died attempt is retried by the pass after the one that
    recovered it, with no wait and no counsel; the retried record names
    what it retries."""
    prelude = ('  if [ ! -f "$ATTAVO_RECORDS_DIR/build-01.json" ]; then\n'
               "    kill -9 $PPID\n    sleep 3\n  fi")
    root, bare = approved_tenant(tmp_path, prelude)
    assert run_attavo(root, "drive", "1", env=FAST).returncode == 0
    passes_until(root, lambda: (records_dir(root) / "build-01.json").exists())
    assert stop_of(root, "build", 1)["class"] == "died"
    passes_until(root, lambda: len(record_files(root, "build")) == 2)
    rec = latest_record(root, "build")
    assert rec["status"] == "pass" and rec["retry_of"] == "build-01", rec
    assert counsel_records(root) == []


def test_a_red_check_becomes_a_returned_record_with_the_log_as_evidence(tmp_path: Path) -> None:
    """Ruling C amended (#206's build-01): a concluded red required check
    on a passed build's head is a returned review record, class returned,
    the red checks its signature; the next build's first feedback carries
    the check's log read through [forge].check_log_command; no operator
    act between approval and the passing review."""
    # the verdict is a fact about a commit, not about how many reads came before it (which pass or
    # worker reads first is the scheduler's): a head carrying the first build's start and not yet
    # the second build's start is red, every other head is green
    forge = tmp_path / "forge.sh"
    forge.write_text('#!/bin/sh\nlog=$(git log --format=%s "$1")\n'
                     'if printf %s "$log" | grep -q "build invocation 01 started" && '
                     '! printf %s "$log" | grep -q "build invocation 02 started"; '
                     'then printf "typecheck failure\\n"; else printf "typecheck success\\n"; fi\n')
    check_log = tmp_path / "checklog.sh"
    check_log.write_text('#!/bin/sh\nprintf "attavo/x.py:12: error: Incompatible return value type\\n"\n')
    feedback_copy = tmp_path / "feedback"
    prelude = (f'  cp "${{ATTAVO_FEEDBACK:-/dev/null}}" "{feedback_copy}.$(ls "$ATTAVO_RECORDS_DIR" | grep -c "^build-.*\\.started$")" 2>/dev/null || true')
    root = make_tenant(tmp_path, "build_valid")
    toml = root / "attavo.toml"
    toml.write_text(toml.read_text() + f'\n[forge]\nchecks_command = "sh {forge}"\ncheck_log_command = "sh {check_log}"\n')
    commit_all(root, "a forge that reads red once")
    add_drive_table(root)
    tenant_adapter(root, prelude)
    bare = add_bare_remote(root, tmp_path)
    approve_item(root)
    commit_all(root, "approved")
    git(root, "push", "-q", "origin", "HEAD:attavo/1-x")
    approved_at = git(root, "rev-parse", "HEAD").strip()

    passes_until(root, lambda: pushed(bare, "verify record for item 1 (verify-01)"))
    returned = latest_record(root, "verify")
    assert returned["status"] == "returned", returned
    assert returned["stop"]["class"] == "returned" and returned["stop"]["signature"] == "typecheck", returned["stop"]

    passes_until(root, lambda: pushed(bare, "build record for item 1 (build-02)"))
    assert latest_record(root, "build")["retry_of"] == "build-01"
    second = (feedback_copy.parent / (feedback_copy.name + ".2")).read_text()
    assert "typecheck" in second and "Incompatible return value type" in second, second

    passes_until(root, lambda: pushed(bare, "verify record for item 1 (verify-02)"))
    assert latest_record(root, "verify")["status"] == "pass"
    authors = set(git(root, "log", "--format=%an", f"{approved_at}..origin/attavo/1-x").splitlines())
    assert authors <= {"attavo driver", "attavo executor (fixture)"}, authors


def test_a_returned_review_retries_with_its_findings_and_counsels_on_the_second(tmp_path: Path) -> None:
    """A review that returns blocking findings is the returned family: the
    next build is handed the findings, counsel is not asked on the first
    return and is asked on the second identical one."""
    findings = tmp_path / "findings.json"
    findings.write_text(json.dumps([{"title": "the helper is never called", "specimen": "x", "retires": "y",
                                     "rejection_test": "z", "severity": "blocking"}]))
    feedback_copy = tmp_path / "feedback"
    prelude = (f'  cp "${{ATTAVO_FEEDBACK:-/dev/null}}" "{feedback_copy}.$(ls "$ATTAVO_RECORDS_DIR" | grep -c "^build-.*\\.started$")" 2>/dev/null || true')
    root, bare = approved_tenant(tmp_path, prelude)
    d = root / "attavo" / "adapters" / "fixture"
    run = (d / "run").read_text()
    verify_branch = ('if [ "${ATTAVO_STAGE:-build}" = verify ]; then\n'
                     f'  printf \'{{"model":"fixture","model_version":null,"adapter_version":"fixture-1","tokens":{{"input":1,"output":1}},"cost_usd":0.0,"findings":%s,"tests_named":[]}}\\n\' "$(cat {findings})"\n'
                     "  exit 0\nfi\n")
    (d / "run").write_text(run.replace("#!/bin/sh\n", "#!/bin/sh\n" + verify_branch, 1))
    commit_all(root, "a review that returns a blocking finding")
    git(root, "push", "-q", "origin", "HEAD:attavo/1-x")
    manifest = root / "attavo.toml"
    manifest.write_text(manifest.read_text().replace('[executor]\nbuild = "fixture"', '[executor]\nbuild = "fixture"\ncounsel = "fixture"'))
    commit_all(root, "counsel configured")
    git(root, "push", "-q", "origin", "HEAD:attavo/1-x")

    passes_until(root, lambda: pushed(bare, "verify record for item 1 (verify-01)"))
    first = latest_record(root, "verify")
    assert first["status"] != "pass" and first["stop"]["class"] == "returned", first
    assert first["stop"]["signature"] == "the helper is never called"
    assert counsel_records(root) == [], "counsel is not asked on the first return"

    passes_until(root, lambda: pushed(bare, "build record for item 1 (build-02)"))
    assert latest_record(root, "build")["retry_of"] == "build-01"
    assert "the helper is never called" in (feedback_copy.parent / (feedback_copy.name + ".2")).read_text()

    passes_until(root, lambda: pushed(bare, "verify record for item 1 (verify-02)"))
    passes_until(root, lambda: counsel_records(root) != [])
    assert latest_record(root, "counsel")["record"] == "verify-02"


def test_counsels_verdict_routes_the_next_pass(tmp_path: Path) -> None:
    """Counsel steers: `answer` starts the next attempt with the answer as
    its steer (recorded in `steer`); `patch` starts nothing and the
    report reads waiting on the issue; `person` starts nothing."""
    responses = tmp_path / "counsel"
    responses.mkdir()
    for verdict in ("answer", "patch", "person"):
        (responses / f"{verdict}.json").write_text(json.dumps({
            "model": "fixture", "model_version": None, "adapter_version": "fixture-1",
            "tokens": {"input": 1, "output": 1}, "cost_usd": 0.0, "class": "question", "verdict": verdict,
            "citations": [{"kind": "instruction", "path": "specs/1/spec.md", "symbol": "problem"}],
            "proposal": {"ruling_text": "use the base tree" if verdict == "answer" else None,
                         "patch": "the fixture cannot read overlays" if verdict == "patch" else None,
                         "question": None if verdict != "person" else "which overlay wins?"}}))
    for verdict, expect_attempt in (("answer", 2), ("patch", 1), ("person", 1)):
        sub = tmp_path / verdict
        sub.mkdir()
        root, bare = approved_tenant(sub)
        manifest = root / "attavo.toml"
        manifest.write_text(manifest.read_text().replace('[executor]\nbuild = "fixture"', '[executor]\nbuild = "fixture"\ncounsel = "fixture"'))
        commit_all(root, "counsel configured")
        git(root, "push", "-q", "origin", "HEAD:attavo/1-x")
        env = {"ATTAVO_FIXTURE_QUESTION": "which overlay wins?",
               "ATTAVO_FIXTURE_COUNSEL_RESPONSE": str(responses / f"{verdict}.json")}
        # one pass starts the worker; the record's push is the fact to wait on, with no
        # further pass running git beside the worker's own commit
        assert run_attavo(root, "drive", "1", env={**FAST, **env}).returncode == 0
        wait_for(lambda: pushed(bare, "build record for item 1 (build-01)"))
        assert latest_record(root, "counsel")["verdict"] == verdict
        wait_for(lambda: report(root, env)["state"] != "running")
        run_attavo(root, "drive", "1", env={**FAST, **env})
        if expect_attempt == 2:
            passes_until(root, lambda: len(record_files(root, "build")) == 2, env={**env, "ATTAVO_FIXTURE_QUESTION": ""})
            assert latest_record(root, "build")["steer"] == "counsel-01"
        else:
            assert len(record_files(root, "build")) == 1, verdict
            out = report(root)
            assert out["state"] == "stopped", out
            if verdict == "patch":
                assert "waiting on" in out["says"], out["says"]


def test_a_persons_stop_is_never_retried(tmp_path: Path) -> None:
    """A question with no counsel configured, a claims-boundary stop, and
    an environment refusal are a person's: three passes over each start
    nothing."""
    cases = {
        "question": ({"ATTAVO_FIXTURE_QUESTION": "which overlay wins?"}, ""),
        "claims": ({"ATTAVO_FIXTURE_WRITE": "outside/claims.txt"}, ""),
    }
    for name, (env, prelude) in cases.items():
        sub = tmp_path / name
        sub.mkdir()
        root, bare = approved_tenant(sub, prelude)
        full = {**FAST, **env}
        run_attavo(root, "drive", "1", env=full)
        wait_for(lambda: pushed(bare, "build record for item 1 (build-01)"))
        first = latest_record(root, "build")
        assert first["status"] != "pass", (name, first)
        for _ in range(3):
            run_attavo(root, "drive", "1", env=full)
        assert len(record_files(root, "build")) == 1, name
        assert not (records_dir(root) / "build-02.started").exists(), name
        assert report(root)["state"] == "stopped"


def test_identical_failures_stop_early_and_the_budget_holds(tmp_path: Path) -> None:
    """Two attempts ending with the identical signature stop the stage at
    two; two different signatures proceed to a third; a fourth never
    starts."""
    same = ('  at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"\n'
            '  printf \'{"at":"%s","kind":"tool_call","text":"fixture-tool waiting on lock 4471"}\\n\' "$at" >> "$ATTAVO_EVENTS"\n'
            "  kill -9 $PPID\n  sleep 3")
    root, bare = approved_tenant(tmp_path / "same", same)
    passes_until(root, lambda: len(record_files(root, "build")) == 2)
    wait_for(lambda: pushed(bare, "build record for item 1 (build-02)"), "build-02's record on origin")
    for _ in range(6):
        run_attavo(root, "drive", "1", env=FAST)
    assert len(record_files(root, "build")) == 2
    assert not (records_dir(root) / "build-03.started").exists()
    assert stop_of(root, "build", 1)["signature"] == stop_of(root, "build", 2)["signature"]

    differ = ('  at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"\n'
              '  n=$(ls "$ATTAVO_RECORDS_DIR" | grep -c "^build-.*\\.started$")\n'
              '  printf \'{"at":"%s","kind":"tool_call","text":"fixture-tool step %s of the plan"}\\n\' "$at" "$(printf "%s" "$n" | tr 0-9 a-j)" >> "$ATTAVO_EVENTS"\n'
              "  kill -9 $PPID\n  sleep 3")
    root2, bare2 = approved_tenant(tmp_path / "differ", differ)
    passes_until(root2, lambda: len(record_files(root2, "build")) == 3)
    wait_for(lambda: pushed(bare2, "build record for item 1 (build-03)"), "build-03's record on origin")
    for _ in range(6):
        run_attavo(root2, "drive", "1", env=FAST)
    assert len(record_files(root2, "build")) == 3
    assert not (records_dir(root2) / "build-04.started").exists()
    out = report(root2)
    assert out["state"] == "stopped" and len(out["attempts"]) == 3, out


def test_every_marker_spends_the_budget(tmp_path: Path) -> None:
    """A recovered attempt counts: with attempts = 2, one died attempt and
    one transient attempt spend the budget and a third never starts,
    though the two signatures differ."""
    prelude = ('  n=$(ls "$ATTAVO_RECORDS_DIR" | grep -c "^build-.*\\.started$")\n'
               '  if [ "$n" = 1 ]; then kill -9 $PPID; sleep 3; fi\n'
               f'  if [ "$n" = 2 ]; then echo \'{TRANSIENT_LINE}\'; exit 1; fi')
    root, bare = approved_tenant(tmp_path, prelude, attempts=2)
    passes_until(root, lambda: len(record_files(root, "build")) == 2)
    # the record is in the copy before the worker has pushed it; a pass in between is refused
    wait_for(lambda: pushed(bare, "build record for item 1 (build-02)"), "build-02's record on origin")
    for _ in range(6):
        run_attavo(root, "drive", "1", env=FAST)
    assert len(record_files(root, "build")) == 2
    assert not (records_dir(root) / "build-03.started").exists()
    out = report(root)
    assert out["state"] == "stopped" and out["signatures_match"] is False, out
    assert any("again" in r for r in out["rulings_available"]), out


def test_a_stage_past_its_ceiling_is_hung_by_the_ceiling(tmp_path: Path) -> None:
    """A worker alive past [drive.build].ceiling_s is terminated and
    recorded hung with bound ceiling; an adapter that declares it does
    not stream events is never judged by event age."""
    root, bare = approved_tenant(tmp_path, "  sleep 20", build_ceiling_s=2)
    d = root / "attavo" / "adapters" / "fixture"
    (d / "conformance.json").write_text(json.dumps({"streams_events": False}) + "\n")
    commit_all(root, "an adapter that does not stream")
    git(root, "push", "-q", "origin", "HEAD:attavo/1-x")
    assert run_attavo(root, "drive", "1", env=FAST).returncode == 0
    wait_for(lambda: report(root)["state"] == "running")
    # the worker is inside its ceiling now; the clock, not a wait, puts it past
    past = clock_ahead(tmp_path, 3)
    out = report(root, past)
    assert out["state"] == "owes-recovery", out
    assert "event" not in out["says"], out["says"]
    passes_until(root, lambda: pushed(bare, "build record for item 1 (build-01)"), env=past)
    stop = stop_of(root, "build", 1)
    assert stop["class"] == "hung" and stop["bound"] == "ceiling", stop


def test_again_resets_a_spent_budget_only(tmp_path: Path) -> None:
    """--again on a stage whose budget is spent starts the next attempt
    and records that in the marker; inside the budget it is a no-op that
    says the pass retries on its own."""
    same = ('  at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"\n'
            '  printf \'{"at":"%s","kind":"tool_call","text":"fixture-tool waiting on lock"}\\n\' "$at" >> "$ATTAVO_EVENTS"\n'
            "  kill -9 $PPID\n  sleep 3")
    root, bare = approved_tenant(tmp_path, same)
    passes_until(root, lambda: len(record_files(root, "build")) == 1)
    inside = run_attavo(root, "drive", "1", "--again", "--by", "alice", env=FAST)
    assert inside.returncode == 0 and "retries on its own" in inside.stdout, inside.stdout
    passes_until(root, lambda: len(record_files(root, "build")) == 2)
    wait_for(lambda: pushed(bare, "build record for item 1 (build-02)"), "build-02's record on origin")
    for _ in range(3):
        run_attavo(root, "drive", "1", env=FAST)
    assert len(record_files(root, "build")) == 2
    again = run_attavo(root, "drive", "1", "--again", "--by", "alice", env=FAST)
    assert again.returncode == 0 and "Build started" in again.stdout, again.stdout
    marker = json.loads((records_dir(root) / "build-03.started").read_text())
    assert marker.get("again_by") == "alice", marker


def test_adapters_class_transient() -> None:
    """The fixture, claude-code and codex adapters put class transient on
    the report line for a capacity, rate, or 5xx refusal: each adapter's
    conformance.json names the situation and the product's own stop
    classes carry it."""
    from attavo.stops import RETRYABLE_CLASSES, STOP_CLASSES
    assert "transient" in STOP_CLASSES and "returned" in STOP_CLASSES
    assert RETRYABLE_CLASSES == ("died", "hung", "transient", "returned", "test-bypass")
    for adapter in ("fixture", "claude-code", "codex"):
        conf = json.loads((ROOT / "adapters" / adapter / "conformance.json").read_text())
        assert "streams_events" in conf, adapter
        assert "transient" in json.dumps(conf.get("situations", {})) or "transient" in json.dumps(conf), adapter
    env = dict(os.environ)
    env["ATTAVO_FIXTURE_TRANSIENT"] = "API error: overloaded"
    env["ATTAVO_STAGE"] = "build"
    run = subprocess.run(["sh", str(FIXTURE_RUN)], capture_output=True, text=True, env=env, cwd=str(ROOT))
    line = [l for l in run.stdout.splitlines() if l.startswith("{")][-1]
    assert json.loads(line).get("class") == "transient", run.stdout


def test_this_file_waits_on_facts_never_on_the_clock() -> None:
    """The bounds the product judges are set through ATTAVO_CLOCK_FILE, and
    every other wait is on a condition: no real sleep in this file (#257)."""
    assert "time.sleep" not in Path(__file__).read_text().replace('"time.sleep"', "")
