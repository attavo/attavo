#!/bin/sh
# Depends only on the product, tests/helpers.py and the standard library.
# exit 2: the scenario could not be set up; exit 1: proof six held a statement it should have failed.
exec python3 - "$(pwd)" <<'PY'
import json, shutil, subprocess, sys, tempfile
from pathlib import Path
here = Path(sys.argv[1])
sys.path.insert(0, str(here / "tests"))
tmp = Path(tempfile.mkdtemp(prefix="attavo-353-"))
def run(root, *args):
    return subprocess.run([sys.executable, str(here / "bin" / "attavo"), *args], cwd=str(root),
                          capture_output=True, text=True, check=False)
try:
    from helpers import git
    root = tmp / "tenant"
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main", ".")
    git(root, "config", "user.email", "fixture@attavo.invalid")
    git(root, "config", "user.name", "fixture")
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\nspecs = "specs"\n\n[forge]\nattestation_command = "sh ./fake-store"\n')
    (root / "fake-store").write_text('#!/bin/sh\nread sha\ntest -f "statements/$sha.json" && cat "statements/$sha.json"\n')
    git(root, "add", "-A"); git(root, "commit", "-q", "-m", "install")
    git(root, "checkout", "-q", "-b", "attavo/1-x")
    rec = root / "records" / "1"; rec.mkdir(parents=True)
    def w(name, obj): (rec / name).write_text(json.dumps(obj))
    w("spec-01.json", {"status": "pass", "sha": "s1", "at": "2026-01-01T00:00:00Z"})
    w("approval-01.json", {"sha": "a1", "spec_sha": "s1", "at": "2026-01-01T00:00:00Z"})
    w("build-01.json", {"status": "pass", "sha": "build1", "adapter": "alpha", "approval_sha": "a1", "cost_usd": 0.25,
                        "wall_clock_s": 100.5, "tokens": {"input": 1000, "output": 10}})
    w("verify-01.json", {"status": "pass", "sha": "verify1", "adapter": "beta", "approval_sha": "a1", "cost_usd": None,
                         "wall_clock_s": 10.0, "tokens": {"input": 500, "output": 5}, "build_sha": "build1"})
    git(root, "add", "-A"); git(root, "commit", "-q", "-m", "records for item 1")
    git(root, "checkout", "-q", "main")
    git(root, "merge", "-q", "--no-ff", "-m", "Merge branch 'attavo/1-x'", "attavo/1-x")
    git(root, "update-ref", "refs/remotes/origin/main", "main")
    git(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    merge = git(root, "rev-parse", "HEAD").strip()
    done = run(root, "attest", "1")
    if done.returncode != 0:
        raise RuntimeError("attest: " + done.stdout + done.stderr)
    statement = json.loads(done.stdout)
    forged = json.loads(json.dumps(statement))
    del forged["time"]; del forged["cost"]
    forged["acts"] = [{"act": "approve", "sha": "f" * 40, "by": "nobody", "at": "2026-01-01T00:00:00Z"}]
    store = root / "statements"; store.mkdir(exist_ok=True)
    (store / (merge + ".json")).write_text(json.dumps({"_type": "https://in-toto.io/Statement/v1",
                                                       "predicateType": "https://attavo.dev/attestation/v6", "predicate": forged}))
    out = json.loads(run(root, "doctor", "--json", str(root)).stdout)
    result = [p for p in out["proofs"] if p["proof"] == "every-attested-item-is-sealed"][0]
except Exception as exc:
    print("SETUP: %r" % (exc,))
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(2)
shutil.rmtree(tmp, ignore_errors=True)
if result.get("holds") is not False or "item 1" not in str(result.get("reason", "")):
    print("ASSERT: proof six held a v6 statement with no time or cost and forged acts: %r" % (result,))
    sys.exit(1)
print("ok")
PY
