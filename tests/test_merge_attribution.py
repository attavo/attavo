"""Issue #169: an item's merge is the merge commit on the default branch's
own first-parent line whose first-parent diff introduces the item's
records -- never a transport merge on an item branch, never a
pull_request checkout's own synthetic test merge, never a merge_group's
temporary merge. Regression tests, written and run red first against
_merges_reachable and merge_introducing_records as they walked every
merge reachable from HEAD.

Run: python3 -m pytest tests -q
"""
import json
import subprocess
import sys
from pathlib import Path

from helpers import commit_all, git, init_repo

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"


def attavo_module():
    """bin/attavo imported in-process, for a direct unit test of
    _merges_reachable and merge_introducing_records -- reproducing the
    misattribution these tests name takes git plumbing no CLI subcommand
    exposes raw."""
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader("attavo_merge_attribution_under_test", str(ATTAVO))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def run_doctor(root: Path) -> dict:
    out = subprocess.run([sys.executable, str(ATTAVO), "doctor", str(root), "--json"],
                         capture_output=True, text=True, check=False)
    report = json.loads(out.stdout)
    assert out.returncode == (0 if report["done"] else 1)
    return {p["proof"]: p for p in report["proofs"]}


def write_item_records(records_dir: Path) -> None:
    records_dir.mkdir(parents=True)
    (records_dir / "spec-01.json").write_text(json.dumps({"status": "pass"}))
    (records_dir / "approval-01.json").write_text(json.dumps({"approved_by": "alice"}))
    (records_dir / "build-01.json").write_text(json.dumps({"status": "pass"}))
    (records_dir / "verify-01.json").write_text(json.dumps({"status": "pass"}))


def test_merge_introducing_records_ignores_a_transport_merge_on_an_item_branch(tmp_path):
    """Acceptance row 1. Item 1's four stage records land on main for
    real, at a first-parent merge M1. Item 2's own branch, still open,
    later runs `git merge main` to stay current -- that transport merge
    T's own first-parent diff shows item 1's records as newly arrived on
    item 2's branch, exactly as c3e9415 did to #153's records on #151's
    branch (T is also newer than M1 and, walked from item 2's branch,
    would have been the first match under the old HEAD-reachable, not
    first-parent, walk). merge_introducing_records, read while item 2's
    branch is checked out, must still name M1 for item 1; item 2, never
    itself merged to main, gets nothing."""
    root = tmp_path / "transport_merge"
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    init_repo(root, "install")
    initial_sha = git(root, "rev-parse", "HEAD").strip()

    subprocess.run(["git", "-C", str(root), "checkout", "-q", "-b", "attavo/1-x"], check=True)
    write_item_records(root / "records" / "1")
    commit_all(root, "item 1's records")
    subprocess.run(["git", "-C", str(root), "checkout", "-q", "main"], check=True)
    subprocess.run(["git", "-C", str(root), "merge", "-q", "--no-ff", "-m",
                    "Merge branch 'attavo/1-x'", "attavo/1-x"], check=True)
    m1_sha = git(root, "rev-parse", "HEAD").strip()
    git(root, "update-ref", "refs/remotes/origin/main", m1_sha)

    subprocess.run(["git", "-C", str(root), "checkout", "-q", "-b", "attavo/2-x", initial_sha],
                   check=True)
    (root / "records" / "2").mkdir(parents=True)
    (root / "records" / "2" / "spec-01.json").write_text(json.dumps({"status": "pass"}))
    commit_all(root, "item 2's own work")
    subprocess.run(["git", "-C", str(root), "merge", "-q", "--no-ff", "-m",
                    "transport: bring main in", "main"], check=True)
    transport_sha = git(root, "rev-parse", "HEAD").strip()
    assert transport_sha != m1_sha  # sanity: a distinct, newer merge exists to be mistaken for M1

    module = attavo_module()
    merge_sha, refusal = module.merge_introducing_records(root, "records", "1", module.STAGES)
    assert refusal is None
    assert merge_sha == m1_sha

    merge_sha2, refusal2 = module.merge_introducing_records(root, "records", "2", module.STAGES)
    assert refusal2 is None
    assert merge_sha2 is None


def test_doctor_proof_two_reports_not_merged_at_a_pull_request_synthetic_merge(tmp_path):
    """Acceptance row 2. HEAD checked out at a merge of the item's own
    branch into main, on a branch of its own -- the shape actions/checkout
    leaves behind for a pull_request event, where the merge itself is
    never pushed to origin. refs/remotes/origin/main still names the
    pre-item commit, so the item has no merge at all; proof two must
    report it as not merged rather than hold on that synthetic commit
    (the old bug: it held on every pull request this way, since the
    synthetic merge is what HEAD-reachable found first)."""
    root = tmp_path / "pull_request_shape"
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    init_repo(root, "install")

    subprocess.run(["git", "-C", str(root), "checkout", "-q", "-b", "attavo/1-x"], check=True)
    write_item_records(root / "records" / "1")
    commit_all(root, "item 1's records")

    subprocess.run(["git", "-C", str(root), "checkout", "-q", "-b", "pr-merge-ref", "main"],
                   check=True)
    subprocess.run(["git", "-C", str(root), "merge", "-q", "--no-ff", "-m",
                    "synthetic pull_request test merge", "attavo/1-x"], check=True)
    subprocess.run(["git", "-C", str(root), "checkout", "-q", "--detach", "HEAD"], check=True)

    p = run_doctor(root)["one-item-every-stage"]
    assert not p["holds"]
    assert "no merge" in p["reason"]


def test_merges_reachable_refuses_without_an_origin_head(tmp_path):
    """Acceptance row 3. With no refs/remotes/origin/HEAD to name the
    default branch, _merges_reachable refuses by name -- telling the
    operator to run `git remote set-head origin -a` -- rather than
    silently walking HEAD and risking exactly the misattribution this
    issue retires. Proof two surfaces the same refusal text rather than
    a re-typed copy of it."""
    root = tmp_path / "no_origin_head"
    root.mkdir()
    (root / "attavo.toml").write_text('[paths]\nrecords = "records"\n')
    init_repo(root, "install")
    write_item_records(root / "records" / "1")
    commit_all(root, "item 1's records, committed straight to main")

    subprocess.run(["git", "-C", str(root), "symbolic-ref", "-d", "refs/remotes/origin/HEAD"],
                   capture_output=True, text=True, check=True)

    module = attavo_module()
    merges, refusal = module._merges_reachable(root)
    assert merges == []
    assert "refs/remotes/origin/HEAD" in refusal
    assert "git remote set-head origin -a" in refusal

    p = run_doctor(root)["one-item-every-stage"]
    assert not p["holds"]
    assert "git remote set-head origin -a" in p["reason"]


def test_doctor_job_sets_origin_head_after_checkout():
    """Acceptance row 3's other half. The doctor job runs on pull_request,
    merge_group, and push (ci.yml's own `if:`), none of which leaves
    refs/remotes/origin/HEAD set by actions/checkout alone; the job must
    set it itself, after the checkout step that fetches the refs it
    names."""
    text = (ROOT / ".github/workflows/ci.yml").read_text()
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if l.strip() == "doctor:")
    end = next((i for i in range(start + 1, len(lines))
               if lines[i] and not lines[i].startswith((" ", "\t"))), len(lines))
    job = "\n".join(lines[start:end])

    checkout_idx = job.index("actions/checkout@v4")
    set_head_idx = job.index("git remote set-head origin -a")
    assert set_head_idx > checkout_idx
