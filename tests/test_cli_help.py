"""bin/attavo --help and its unknown-subcommand message name every
subcommand that exists, and the product itself names no vendor. Issue
#54, acceptance 10 and 11.

Run: python3 -m pytest tests -q
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ATTAVO = ROOT / "bin" / "attavo"


def test_help_lists_attest_and_record_only():
    run = subprocess.run([sys.executable, str(ATTAVO), "--help"],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 0
    assert "attavo record-only" in run.stdout
    assert "attavo attest" in run.stdout


def test_unknown_subcommand_message_names_attest_and_record_only():
    run = subprocess.run([sys.executable, str(ATTAVO), "not-a-real-subcommand"],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 2
    assert "record-only" in run.stderr
    assert "attest" in run.stderr


def test_no_vendor_name_in_bin_attavo():
    """Acceptance 11. The four pre-existing code-factory hits this check
    once had to carve out lived in test docstrings, not in bin/attavo
    itself; this check's own scope is bin/attavo alone, so it stays a
    plain empty-output assertion."""
    run = subprocess.run(
        ["git", "grep", "-n", "-i", "-E", "github|sigstore|in-toto", "--", "bin/attavo"],
        cwd=str(ROOT), capture_output=True, text=True, check=False)
    assert run.returncode == 1, run.stdout
    assert run.stdout == ""
