#!/usr/bin/env python3
"""Decides whether a records-only pull request head inherits a check's
result instead of running it again (issue #210).

An item's branch receives a commit for every fact the product writes, each
touching only the records directory. Its code is the code of the commit
before it, so `test` and `typecheck` cannot come out differently. This
script proves that per check rather than assuming it: the head inherits
only when a commit with identical code has a run of the same check that
concluded `success`. Every other case, and every doubt, is run-in-full:
the script never exits in a way that skips a check by accident. An
exception, a non-zero `gh`, unparseable JSON, a shallow clone, a missing
manifest all print "runs in full" and write inherit=false.

The decision is `decide`, a function of the event, the chain of commits
(each with whether it is records-only), and a check-runs reader.
`history` builds the chain from git; `main` reads the pull request head
from the event payload and asks the forge through `gh api`.

The head's own run is never read: it is the run in progress, and an
earlier run of the same sha is not evidence about this one. Only a
`pull_request` inherits; the merge queue's run is the law and never does.
Known limit, stated not fixed: a pull_request run tests the merge of the
head into main, so if main moved between the inherited run and the
records-only head, the inherited result is about a different merge.

Run: python3 scripts/ci_inherit.py <check-name>
Reads GITHUB_EVENT_NAME, GITHUB_EVENT_PATH, GITHUB_REPOSITORY; writes
inherit=true|false (and from=<sha> on inherit) to GITHUB_OUTPUT.
Requires `gh`, authenticated through GH_TOKEN, when it has anything to ask.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

TIMEOUT_S = 30


@dataclass(frozen=True)
class Commit:
    sha: str
    records_only: bool
    merge: bool = False


@dataclass(frozen=True)
class Run:
    status: str
    conclusion: str | None


@dataclass(frozen=True)
class Decision:
    inherit_from: str | None
    reason: str

    @property
    def inherits(self) -> bool:
        return self.inherit_from is not None


@dataclass(frozen=True)
class History:
    chain: list[Commit]
    shallow: bool


ReadRuns = Callable[[str], Sequence[Run]]


def _full(reason: str) -> Decision:
    return Decision(None, reason)


def decide(event: str, chain: Sequence[Commit], read_runs: ReadRuns,
           shallow: bool = False) -> Decision:
    """Inherit from a named commit, or run in full with the reason.

    `chain` is head first, then first-parent ancestors: the records-only
    commits and, last, the first commit that is not (or a merge commit,
    which ends the walk and is never inherited from). `read_runs(sha)`
    returns that commit's runs of the check by name; any exception it
    raises means run in full."""
    if event != "pull_request":
        return _full(f"event {event or '(none)'} never inherits")
    if shallow:
        return _full("the clone is shallow; the history cannot decide")
    if not chain:
        return _full("no history to read")
    if not chain[0].records_only:
        return _full(f"head {chain[0].sha[:12]} touches more than the records directory")
    try:
        for commit in chain[1:]:
            if commit.merge:
                return _full(f"merge commit {commit.sha[:12]} ends the walk with no concluded run before it")
            runs = read_runs(commit.sha)
            if not runs:
                if not commit.records_only:
                    return _full(f"{commit.sha[:12]} changes code and has no run of this check")
                continue
            not_green = [r for r in runs if r.status != "completed" or r.conclusion != "success"]
            if not_green:
                first = not_green[0]
                return _full(f"the nearest run, on {commit.sha[:12]}, is {first.conclusion or first.status}, "
                             "not a concluded success")
            return Decision(commit.sha, f"{commit.sha[:12]} has a concluded success and the commits "
                                        "since touch only the records directory")
    except Exception as error:  # every doubt is run-in-full
        return _full(f"could not read the check runs: {error}")
    return _full("no commit in the chain has a concluded run of this check")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True, timeout=TIMEOUT_S).stdout


def records_dir_at(repo: Path, head: str) -> str:
    """[paths].records from attavo.toml as it stands at `head`, without a
    leading ./ or trailing /."""
    manifest = tomllib.loads(_git(repo, "show", f"{head}:attavo.toml"))
    records = str(manifest["paths"]["records"]).strip()
    if not records or records.startswith("/") or ".." in records.split("/"):
        raise ValueError(f"[paths].records is {records!r}, not a directory inside the repository")
    return records.removeprefix("./").strip("/")


def _touches_only(repo: Path, parent: str, sha: str, records: str) -> bool:
    names = [n for n in _git(repo, "diff", "--name-only", "--no-renames", "-z", parent, sha).split("\0") if n]
    return bool(names) and all(n.startswith(records + "/") for n in names)


def history(repo: Path, head: str) -> History:
    """The first-parent chain from `head` while each commit's diff against
    its first parent touches only the records directory, then the first
    commit that does not. A merge commit or a root commit ends it."""
    if _git(repo, "rev-parse", "--is-shallow-repository").strip() == "true":
        return History([], True)
    records = records_dir_at(repo, head)
    chain: list[Commit] = []
    sha = head
    while True:
        parents = _git(repo, "rev-list", "--parents", "-n", "1", sha).split()[1:]
        if len(parents) > 1:
            chain.append(Commit(sha, False, merge=True))
            return History(chain, False)
        if not parents:
            chain.append(Commit(sha, False))
            return History(chain, False)
        only = _touches_only(repo, parents[0], sha, records)
        chain.append(Commit(sha, only))
        if not only:
            return History(chain, False)
        sha = parents[0]


def runs_from_json(text: str) -> list[Run]:
    """The check runs in one `gh api .../check-runs` answer; ValueError on
    anything that is not that answer."""
    payload = json.loads(text)
    runs = payload["check_runs"]
    if not isinstance(runs, list):
        raise ValueError("check_runs is not a list")
    return [Run(str(run["status"]), None if run.get("conclusion") is None else str(run["conclusion"]))
            for run in runs]


def gh_reader(repository: str, check_name: str, env: Mapping[str, str]) -> ReadRuns:
    def read(sha: str) -> list[Run]:
        run = subprocess.run(
            ["gh", "api", f"repos/{repository}/commits/{sha}/check-runs"
                          f"?check_name={quote(check_name)}&per_page=100"],
            capture_output=True, text=True, timeout=TIMEOUT_S, check=False, env=dict(env))
        if run.returncode != 0:
            raise RuntimeError(f"gh exited {run.returncode}: {run.stderr.strip()[:200]}")
        return runs_from_json(run.stdout)

    return read


def _write_outputs(env: Mapping[str, str], decision: Decision) -> None:
    path = env.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a") as out:
        out.write(f"inherit={'true' if decision.inherits else 'false'}\n")
        if decision.inherit_from:
            out.write(f"from={decision.inherit_from}\n")


def main(argv: Sequence[str], env: Mapping[str, str] | None = None, repo: Path | None = None,
         read_runs: ReadRuns | None = None) -> int:
    if len(argv) != 1:
        print("usage: ci_inherit.py <check-name>", file=sys.stderr)
        return 2
    check_name = argv[0]
    env = os.environ if env is None else env
    repo = Path.cwd() if repo is None else repo
    event = env.get("GITHUB_EVENT_NAME", "")
    try:
        if event != "pull_request":
            decision = decide(event, [], lambda sha: [])
        else:
            payload = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_text())
            head = str(payload["pull_request"]["head"]["sha"])
            walk = history(repo, head)
            reader = read_runs or gh_reader(env["GITHUB_REPOSITORY"], check_name, env)
            decision = decide(event, walk.chain, reader, shallow=walk.shallow)
    except Exception as error:  # every doubt is run-in-full
        decision = _full(f"could not decide: {type(error).__name__}: {error}")
    verdict = f"inherits from {decision.inherit_from}" if decision.inherits else "runs in full"
    print(f"ci-inherit: {check_name} {verdict}: {decision.reason}")
    _write_outputs(env, decision)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
