"""Issue #210: a records-only commit inherits its checks and never assumes
them. scripts/ci_inherit.py decides, for `test` and `typecheck` on a pull
request, whether the head can take a concluded success from an earlier
commit whose code is identical; every doubt is run-in-full.

Three layers, each exercised here: decide (a pure function of the event,
the chain of commits, and a check-runs reader), history (the walk over a
real git repository), and main (the script, with `gh` faked on PATH).
The last section reads ci.yml as text, as the other workflow tests do:
configured is not enforcing.

Run: python3 -m pytest tests -q
"""
from __future__ import annotations

import json
import os
import stat
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from helpers import clone_shallow, commit_all, git, init_repo, job_if_condition, step_block
from scripts import ci_inherit
from scripts.ci_inherit import Commit, Run

ROOT = Path(__file__).resolve().parent.parent
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"
RECORDS = "attest/records"
SUCCESS = [Run("completed", "success")]


def reader(answers: Mapping[str, Sequence[Run]]) -> tuple[ci_inherit.ReadRuns, list[str]]:
    """A fake check-runs reader: the canned answer per sha, and the list of
    shas it was asked about, in order."""
    asked: list[str] = []

    def read(sha: str) -> Sequence[Run]:
        asked.append(sha)
        return answers.get(sha, [])

    return read, asked


def three_records_after_code() -> list[Commit]:
    return [Commit("r3", True), Commit("r2", True), Commit("r1", True), Commit("code", False)]


# --- decide: the way in ------------------------------------------------------


def test_three_records_only_commits_after_a_green_code_commit_inherit_from_it() -> None:
    read, asked = reader({"code": SUCCESS})
    decision = ci_inherit.decide("pull_request", three_records_after_code(), read)
    assert decision.inherit_from == "code", decision
    assert asked == ["r2", "r1", "code"], "nearest first, one read per commit, never the head's own"


def test_the_nearest_concluded_success_is_the_one_inherited() -> None:
    read, asked = reader({"r1": SUCCESS, "code": SUCCESS})
    decision = ci_inherit.decide("pull_request", three_records_after_code(), read)
    assert decision.inherit_from == "r1", decision
    assert asked == ["r2", "r1"]


def test_the_heads_own_run_is_never_read() -> None:
    """The head's own run is this one, in progress; reading it would make
    every inherit a refusal, and a rerun's earlier result is not evidence
    about a different run."""
    read, asked = reader({"r3": [Run("in_progress", None)], "code": SUCCESS})
    decision = ci_inherit.decide("pull_request", three_records_after_code(), read)
    assert decision.inherit_from == "code", decision
    assert "r3" not in asked


def test_a_commit_with_two_runs_of_the_name_needs_both_to_have_succeeded() -> None:
    read, _ = reader({"code": SUCCESS + [Run("completed", "failure")]})
    assert not ci_inherit.decide("pull_request", three_records_after_code(), read).inherits


# --- decide: every way to turn a red tree green by skipping ------------------


def test_a_head_that_touches_code_runs_in_full() -> None:
    chain = [Commit("head", False)]
    read, asked = reader({"head": SUCCESS})
    assert not ci_inherit.decide("pull_request", chain, read).inherits
    assert asked == []


def test_an_empty_chain_runs_in_full() -> None:
    read, _ = reader({})
    assert not ci_inherit.decide("pull_request", [], read).inherits


def test_no_commit_in_the_chain_has_a_concluded_run_runs_in_full() -> None:
    read, asked = reader({})
    decision = ci_inherit.decide("pull_request", three_records_after_code(), read)
    assert not decision.inherits
    assert asked == ["r2", "r1", "code"]


def test_a_build_commit_and_its_record_pushed_together_run_in_full() -> None:
    """The product pushes a build's code commit and its record together
    (#206): the code commit never had a run of its own, so there is
    nothing to inherit, and the walk does not look past it."""
    chain = [Commit("record", True), Commit("build", False), Commit("earlier", False)]
    read, asked = reader({"earlier": SUCCESS})
    decision = ci_inherit.decide("pull_request", chain, read)
    assert not decision.inherits
    assert "earlier" not in asked


@pytest.mark.parametrize("conclusion",
                         ["failure", "cancelled", "timed_out", "skipped", "neutral",
                          "action_required", "stale", None])
def test_a_nearest_concluded_run_that_is_not_a_success_runs_in_full(conclusion: str | None) -> None:
    read, _ = reader({"r1": [Run("completed", conclusion)], "code": SUCCESS})
    assert not ci_inherit.decide("pull_request", three_records_after_code(), read).inherits


@pytest.mark.parametrize("status", ["in_progress", "queued", "pending", "waiting"])
def test_a_nearest_run_that_has_not_concluded_runs_in_full(status: str) -> None:
    read, _ = reader({"r1": [Run(status, None)], "code": SUCCESS})
    assert not ci_inherit.decide("pull_request", three_records_after_code(), read).inherits


def test_a_failure_nearer_than_a_success_wins() -> None:
    read, _ = reader({"r2": [Run("completed", "failure")], "code": SUCCESS})
    assert not ci_inherit.decide("pull_request", three_records_after_code(), read).inherits


@pytest.mark.parametrize("failure", [RuntimeError("gh exited 1"), ValueError("not json"),
                                     TimeoutError(), KeyError("check_runs")])
def test_a_check_runs_read_that_fails_runs_in_full(failure: Exception) -> None:
    def broken(sha: str) -> Sequence[Run]:
        raise failure

    assert not ci_inherit.decide("pull_request", three_records_after_code(), broken).inherits


def test_a_shallow_clone_runs_in_full() -> None:
    read, asked = reader({"code": SUCCESS})
    decision = ci_inherit.decide("pull_request", three_records_after_code(), read, shallow=True)
    assert not decision.inherits
    assert asked == []


def test_a_merge_commit_in_the_chain_ends_the_walk_and_is_never_inherited_from() -> None:
    chain = [Commit("r1", True), Commit("merge", False, merge=True), Commit("code", False)]
    read, asked = reader({"merge": SUCCESS, "code": SUCCESS})
    assert not ci_inherit.decide("pull_request", chain, read).inherits
    assert "merge" not in asked and "code" not in asked


@pytest.mark.parametrize("event", ["merge_group", "push", "workflow_dispatch", "", "pull_request_target"])
def test_only_a_pull_request_ever_inherits(event: str) -> None:
    read, asked = reader({"code": SUCCESS})
    assert not ci_inherit.decide(event, three_records_after_code(), read).inherits
    assert asked == [], "the queue's run is the law: nothing else is even read"


# --- history: the walk over real git ----------------------------------------


def commit_files(root: Path, files: Mapping[str, str], message: str) -> str:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    commit_all(root, message)
    return git(root, "rev-parse", "HEAD").strip()


def make_repo(tmp_path: Path, records: str = RECORDS) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "attavo.toml").write_text(f'schema_version = 1\n\n[paths]\nrecords = "{records}"\n')
    (root / "attavo").mkdir()
    (root / "attavo" / "code.py").write_text("x = 0\n")
    init_repo(root)
    return root


def code_commit(root: Path, n: int) -> str:
    return commit_files(root, {"attavo/code.py": f"x = {n}\n"}, f"code {n}")


def record_commit(root: Path, n: int, records: str = RECORDS) -> str:
    return commit_files(root, {f"{records}/7/marker-{n:02d}.json": "{}\n"}, f"record {n}")


def test_history_collects_the_records_only_commits_and_the_first_that_is_not(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    code = code_commit(root, 1)
    r1, r2, r3 = (record_commit(root, n) for n in (1, 2, 3))
    walk = ci_inherit.history(root, r3)
    assert [(c.sha, c.records_only) for c in walk.chain] == [
        (r3, True), (r2, True), (r1, True), (code, False)]
    assert not walk.shallow


def test_history_of_a_head_that_touches_code_is_that_head_alone(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    record_commit(root, 1)
    head = code_commit(root, 2)
    assert [(c.sha, c.records_only) for c in ci_inherit.history(root, head).chain] == [(head, False)]


def test_a_commit_touching_a_record_and_anything_else_is_not_records_only(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    mixed = commit_files(root, {f"{RECORDS}/7/a.json": "{}\n", "attavo/code.py": "x = 9\n"}, "mixed")
    assert not ci_inherit.history(root, mixed).chain[0].records_only


def test_a_sibling_directory_that_shares_the_prefix_is_not_the_records_directory(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    sibling = commit_files(root, {"attest/records-old/7/a.json": "{}\n"}, "lookalike")
    assert not ci_inherit.history(root, sibling).chain[0].records_only


def test_the_records_directory_is_read_from_the_manifest_at_the_head(tmp_path: Path) -> None:
    root = make_repo(tmp_path, records="ledger/facts/")
    code = code_commit(root, 1)
    record = record_commit(root, 1, records="ledger/facts")
    assert [(c.sha, c.records_only) for c in ci_inherit.history(root, record).chain] == [
        (record, True), (code, False)]


def test_a_commit_that_changes_nothing_is_not_records_only(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    git(root, "commit", "-q", "--allow-empty", "-m", "nothing")
    empty = git(root, "rev-parse", "HEAD").strip()
    assert not ci_inherit.history(root, empty).chain[0].records_only


def test_a_merge_commit_ends_the_walk(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    git(root, "checkout", "-q", "-b", "feature")
    record_commit(root, 1)
    git(root, "checkout", "-q", "main")
    code_commit(root, 1)
    git(root, "checkout", "-q", "feature")
    git(root, "merge", "-q", "--no-ff", "-m", "merge main", "main")
    merge = git(root, "rev-parse", "HEAD").strip()
    head = record_commit(root, 2)
    chain = ci_inherit.history(root, head).chain
    assert [(c.sha, c.records_only, c.merge) for c in chain] == [
        (head, True, False), (merge, False, True)]


def test_a_shallow_clone_is_reported_shallow(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    code_commit(root, 1)
    head = record_commit(root, 1)
    clone = tmp_path / "clone"
    clone_shallow(root, clone)
    assert ci_inherit.history(clone, head).shallow


# --- main: the script, with `gh` faked on PATH -------------------------------

NO_RUNS = json.dumps({"total_count": 0, "check_runs": []})


def check_runs_json(*runs: tuple[str, str | None]) -> str:
    return json.dumps({"total_count": len(runs), "check_runs": [
        {"name": "test", "status": status, "conclusion": conclusion} for status, conclusion in runs]})


def fake_gh(tmp_path: Path, by_sha: Mapping[str, str] | None = None, *,
            default: str = NO_RUNS, exit_code: int = 0) -> dict[str, str]:
    """A `gh` on PATH that logs its arguments and prints the canned
    check-runs JSON for the sha its URL names. Returns the environment to
    run the script under."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    lines = ["#!/bin/sh", f'echo "$@" >> "{tmp_path}/gh.log"', 'case "$*" in']
    for sha, body in (by_sha or {}).items():
        lines.append(f"  *{sha}*) cat <<'EOF'\n{body}\nEOF\n  ;;")
    lines += [f"  *) cat <<'EOF'\n{default}\nEOF\n  ;;", "esac", f"exit {exit_code}"]
    gh = bin_dir / "gh"
    gh.write_text("\n".join(lines) + "\n")
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
    return {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "GH_TOKEN": "x"}


def action_env(tmp_path: Path, head: str, event: str = "pull_request",
               gh_env: Mapping[str, str] | None = None) -> dict[str, str]:
    payload = tmp_path / "event.json"
    payload.write_text(json.dumps({"pull_request": {"head": {"sha": head}}}))
    return {**(gh_env or fake_gh(tmp_path)), "GITHUB_EVENT_NAME": event,
            "GITHUB_EVENT_PATH": str(payload), "GITHUB_OUTPUT": str(tmp_path / "output"),
            "GITHUB_REPOSITORY": "attavo/attavo"}


def outputs(tmp_path: Path) -> dict[str, str]:
    path = tmp_path / "output"
    text = path.read_text() if path.exists() else ""
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def test_main_inherits_from_the_code_commit_and_names_it(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = make_repo(tmp_path)
    code = code_commit(root, 1)
    record_commit(root, 1)
    head = record_commit(root, 2)
    env = action_env(tmp_path, head, gh_env=fake_gh(tmp_path, {code: check_runs_json(("completed", "success"))}))
    assert ci_inherit.main(["test"], env=env, repo=root) == 0
    assert outputs(tmp_path) == {"inherit": "true", "from": code}
    assert code in capsys.readouterr().out, "the log names the commit inherited from"
    log = (tmp_path / "gh.log").read_text()
    assert f"repos/attavo/attavo/commits/{code}/check-runs" in log and "check_name=test" in log


def test_main_passes_the_check_name_it_was_given(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    code = code_commit(root, 1)
    head = record_commit(root, 1)
    env = action_env(tmp_path, head,
                     gh_env=fake_gh(tmp_path, {code: check_runs_json(("completed", "success"))}))
    assert ci_inherit.main(["typecheck"], env=env, repo=root) == 0
    assert "check_name=typecheck" in (tmp_path / "gh.log").read_text()


@pytest.mark.parametrize("case", ["gh_exits_nonzero", "gh_prints_garbage", "gh_prints_the_wrong_shape",
                                  "gh_names_no_status", "no_run_of_the_name", "no_event_payload",
                                  "no_manifest", "merge_group", "push"])
def test_main_runs_in_full_on_every_doubt_and_exits_zero(tmp_path: Path, case: str) -> None:
    root = make_repo(tmp_path)
    code = code_commit(root, 1)
    head = record_commit(root, 1)
    green = check_runs_json(("completed", "success"))
    gh_env = {
        "gh_exits_nonzero": lambda: fake_gh(tmp_path, {code: green}, exit_code=1),
        "gh_prints_garbage": lambda: fake_gh(tmp_path, {code: "<html>502</html>"}),
        "gh_prints_the_wrong_shape": lambda: fake_gh(tmp_path, {code: '{"check_runs": "none"}'}),
        "gh_names_no_status": lambda: fake_gh(tmp_path, {code: '{"check_runs": [{"name": "test"}]}'}),
        "no_run_of_the_name": lambda: fake_gh(tmp_path),
    }.get(case, lambda: fake_gh(tmp_path, {code: green}))()
    if case == "no_manifest":
        git(root, "rm", "-q", "attavo.toml")
        commit_all(root, "drop the manifest")
        head = git(root, "rev-parse", "HEAD").strip()
    env = action_env(tmp_path, head, event=case if case in ("merge_group", "push") else "pull_request",
                     gh_env=gh_env)
    if case == "no_event_payload":
        env["GITHUB_EVENT_PATH"] = str(tmp_path / "missing.json")
    assert ci_inherit.main(["test"], env=env, repo=root) == 0
    assert outputs(tmp_path).get("inherit") == "false", outputs(tmp_path)
    assert "from" not in outputs(tmp_path)


def test_main_without_a_check_name_is_a_usage_error(tmp_path: Path) -> None:
    assert ci_inherit.main([], env={}, repo=tmp_path) == 2


# --- ci.yml: configured is not enforcing ------------------------------------

INHERIT_STEP = {"test": "- name: inherit test from a records-only head",
                "typecheck": "- name: inherit typecheck from a records-only head"}
SKIP_WHEN_INHERITED = "if: steps.inherit.outputs.inherit != 'true'"


def job_text(job: str) -> str:
    lines = CI_YML.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line == f"  {job}:")
    body = []
    for line in lines[start + 1:]:
        if line.strip() and len(line) - len(line.lstrip(" ")) < 4:
            break
        body.append(line)
    return "\n".join(body)


def steps_of(job: str) -> list[str]:
    """Each step of a job as its raw text, in order."""
    steps: list[list[str]] = []
    in_steps = False
    for line in job_text(job).splitlines():
        if line.strip() == "steps:":
            in_steps = True
        elif in_steps and line.startswith("      - "):
            steps.append([line])
        elif in_steps and steps and (line.startswith("        ") or not line.strip()):
            steps[-1].append(line)
    return ["\n".join(step) for step in steps]


def test_push_runs_on_main_only_and_the_other_triggers_are_unchanged() -> None:
    lines = CI_YML.read_text().splitlines()
    on_body = lines[lines.index("on:") + 1:lines.index("jobs:")]
    push = on_body.index("  push:")
    assert on_body[push + 1].strip() == "branches: [main]", on_body
    assert "  pull_request:" in on_body
    assert "  merge_group:" in on_body


@pytest.mark.parametrize("job", ["test", "typecheck"])
def test_the_job_walks_history_and_reads_check_runs(job: str) -> None:
    text = job_text(job)
    assert "permissions:" in text and "checks: read" in text and "contents: read" in text
    checkout = step_block(text, "- uses: actions/checkout@v4")
    assert "fetch-depth: 0" in checkout


@pytest.mark.parametrize("job", ["test", "typecheck"])
def test_the_job_calls_the_script_on_pull_request_only(job: str) -> None:
    step = step_block(job_text(job), INHERIT_STEP[job])
    assert "id: inherit" in step
    assert "if: github.event_name == 'pull_request'" in step
    assert f"python3 scripts/ci_inherit.py {job}" in step
    assert "GH_TOKEN: ${{ github.token }}" in step
    assert "merge_group" not in step and "push" not in step


@pytest.mark.parametrize("job", ["test", "typecheck"])
def test_every_step_after_the_inherit_step_is_skipped_on_inherit_but_the_named_few(job: str) -> None:
    steps = steps_of(job)
    at = next(i for i, step in enumerate(steps) if INHERIT_STEP[job] in step)
    allowed_to_run_when_inherited = {
        "test": ("pip install pytest jsonschema",),
        "typecheck": (),
    }[job]
    for step in steps[at + 1:]:
        if any(marker in step for marker in allowed_to_run_when_inherited):
            continue
        assert SKIP_WHEN_INHERITED in step, step


def test_the_test_job_runs_no_pytest_on_its_inherit_path_and_excludes_own_records_by_marker_on_the_full_path() -> None:
    """#237: the inherit path's own copy of `-m own_records` had no `git
    remote set-head origin -a` and no GH_TOKEN, so three of its eight tests
    failed there (run 36351653506) while the doctor job, on the same push
    with both, passed all eight. The doctor job runs the identical set on
    the same events, so the inherit path runs no pytest step at all; only
    the full path's own-records exclusion remains."""
    steps = steps_of("test")
    own = [step for step in steps if "-m own_records" in step]
    assert own == [], own
    full = [step for step in steps if "python3 -m pytest tests -q --durations=5" in step]
    assert len(full) == 1 and SKIP_WHEN_INHERITED in full[0]
    assert "-m 'not own_records'" in full[0], full[0]
    assert "--deselect" not in full[0], "a test named by hand is a list that goes stale (#229 build-04)"


def test_the_queue_has_no_path_to_the_script() -> None:
    """merge_group never inherits: the only step that names the script is
    guarded to pull_request, and the only steps that key on its output
    need it to say 'true'."""
    text = CI_YML.read_text()
    script_lines = [line for line in text.splitlines() if "scripts/ci_inherit.py" in line and "run:" in line]
    assert len(script_lines) == 2, script_lines
    for job in ("test", "typecheck"):
        assert job_if_condition(text, job) is None, "the job itself runs on every event"
    assert "github.event_name == 'merge_group'" not in "\n".join(
        step for job in ("test", "typecheck") for step in steps_of(job) if "inherit" in step)


# --- pyproject.toml: typecheck reads nothing under the records directory -----


def records_directory() -> str:
    manifest = tomllib.loads((ROOT / "attavo.toml").read_text())
    return str(manifest["paths"]["records"]).strip("/")


def test_typecheck_names_no_path_under_the_records_directory() -> None:
    files = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["mypy"]["files"]
    records = records_directory()
    for entry in files:
        entry = str(entry).strip("/")
        assert entry not in (".", "") and not entry.startswith(records + "/") and entry != records, entry
        assert not records.startswith(entry + "/"), f"{entry} contains {records}"
