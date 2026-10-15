#!/bin/sh
# Depends only on the product, tests/helpers.py and the standard library.
# exit 2: the scenario could not be set up; exit 1: a row does not name what its commit carries.
exec python3 - "$(pwd)" <<'PY'
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
here = Path(sys.argv[1])
sys.path.insert(0, str(here / "tests"))
tmp = Path(tempfile.mkdtemp(prefix="attavo-350-"))
PERSON = {"name": "Alice Author", "email": "alice@example.com"}
APP = {"name": "attavo-bot[bot]", "email": "123+attavo-bot[bot]@users.noreply.github.com"}
DRIVER = {"name": "attavo driver", "email": "driver@attavo.invalid"}
EXECUTOR = {"name": "attavo executor (fixture)", "email": "executor+fixture@attavo.invalid"}
def commit_as(root, who, msg, paths):
    subprocess.run(["git", "-C", str(root), "add", *paths], check=True)
    env = dict(os.environ, GIT_AUTHOR_NAME=who["name"], GIT_COMMITTER_NAME=who["name"],
               GIT_AUTHOR_EMAIL=who["email"], GIT_COMMITTER_EMAIL=who["email"])
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", msg], check=True, env=env)
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
def record(root, stage, n, body):
    p = root / "records" / "1" / f"{stage}-{n:02d}.json"; p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(body)); return str(p.relative_to(root))
try:
    from helpers import git, make_tenant
    root = make_tenant(tmp, "ledger_base")
    (root / "attavo.toml").write_text((root / "attavo.toml").read_text() + '\n[forge]\nattestation_command = "sh ./fake-store"\n')
    (root / "fake-store").write_text('#!/bin/sh\nread sha\ntest -f "statements/$sha.json" && cat "statements/$sha.json"\n')
    commit_as(root, PERSON, "install the store read", ["attavo.toml", "fake-store"])
    git(root, "checkout", "-b", "item")
    (root / "specs" / "1").mkdir(parents=True); (root / "specs" / "1" / "spec.md").write_text("## Problem\n\nsomething.\n")
    s = {}
    s["spec"] = commit_as(root, PERSON, "spec: item 1", ["specs/1"])
    s["spec_record"] = commit_as(root, APP, "spec-gate record", [record(root, "spec", 1, {"status": "pass", "spec_sha": s["spec"], "product_version": "v0.19.1"})])
    s["approve"] = commit_as(root, APP, "approval record", [record(root, "approval", 1, {"approved_by": "alice", "sha": s["spec"], "spec_sha": s["spec"], "product_version": "v0.19.1"})])
    (root / "NOTES.md").write_text("mid-run\n"); s["transport"] = commit_as(root, PERSON, "notes", ["NOTES.md"])
    m = root / "records" / "1" / "build-01.started"; m.write_text(json.dumps({"at": "2026-01-01T00:00:00Z", "host": "h", "sha": s["spec"], "product_version": "v0.19.1"}))
    s["marker"] = commit_as(root, DRIVER, "marker", ["records/1/build-01.started"])
    (root / "built.txt").write_text("built\n"); s["change"] = commit_as(root, EXECUTOR, "build", ["built.txt"])
    s["build"] = commit_as(root, DRIVER, "build record", [record(root, "build", 1, {"status": "pass", "product_version": "v0.19.1"})])
    s["verify"] = commit_as(root, DRIVER, "verify record", [record(root, "verify", 1, {"status": "pass", "product_version": "v0.19.1"})])
    git(root, "checkout", "main")
    subprocess.run(["git", "-C", str(root), "merge", "-q", "--no-ff", "item", "-m", "Merge pull request #1 from x/item"], check=True)
    s["merge"] = git(root, "rev-parse", "HEAD").strip()
    (root / "statements").mkdir()
    (root / "statements" / (s["merge"] + ".json")).write_text(json.dumps({"_type": "https://in-toto.io/Statement/v1", "predicateType": "https://attavo.dev/attestation/v6", "subject": [{"name": "x", "digest": {"sha256": "ab" * 32}}], "predicate": {"item": "1"}}))
    run = subprocess.run([sys.executable, str(here / "bin" / "attavo"), "ledger", "1", "--json"], cwd=str(root), capture_output=True, text=True, check=False)
    if run.returncode != 0:
        raise RuntimeError("ledger: " + run.stdout + run.stderr)
    rows = {r["sha"]: r for r in json.loads(run.stdout)["rows"]}
except Exception as exc:
    print("SETUP: %r" % (exc,)); shutil.rmtree(tmp, ignore_errors=True); sys.exit(2)
shutil.rmtree(tmp, ignore_errors=True)
want = {"spec": ["specs/1/spec.md"], "spec_record": ["records/1/spec-01.json"], "approve": ["records/1/approval-01.json"],
        "transport": [], "marker": ["records/1/build-01.started"], "change": ["built.txt"], "build": ["records/1/build-01.json"], "merge": []}
for role, paths in want.items():
    got = rows.get(s[role], {}).get("carries")
    if got != paths:
        print("ASSERT: the %s row carries %r, want %r" % (role, got, paths)); sys.exit(1)
if rows[s["merge"]].get("statement") != {"predicate_type": "https://attavo.dev/attestation/v6", "subject": "ab" * 32}:
    print("ASSERT: the merge row's statement is %r" % (rows[s["merge"]].get("statement"),)); sys.exit(1)
print("ok")
PY
