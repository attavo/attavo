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


def test_help_lists_attest_and_record_only() -> None:
    run = subprocess.run([sys.executable, str(ATTAVO), "--help"],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 0
    assert "attavo record-only" in run.stdout
    assert "attavo attest" in run.stdout


def test_help_and_the_unknown_subcommand_message_name_test() -> None:
    """Issue #212: attavo test is the executor's one test command."""
    run = subprocess.run([sys.executable, str(ATTAVO), "--help"],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 0
    assert "attavo test [N]" in run.stdout
    unknown = subprocess.run([sys.executable, str(ATTAVO), "not-a-real-subcommand"],
                             capture_output=True, text=True, check=False)
    assert "stops, test exist" in unknown.stderr


def test_help_names_all_four_doctor_proofs() -> None:
    """Issue #94, acceptance row 5: the doctor's own help text names all
    four proofs, no-stop-lacks-cause included -- a proof the help text
    forgets is one a reader would never know to expect from doctor: done."""
    run = subprocess.run([sys.executable, str(ATTAVO), "--help"],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 0
    assert "every-gate-rejects" in run.stdout
    assert "one-item-every-stage" in run.stdout
    assert "no-commit-lacks-record" in run.stdout
    assert "no-stop-lacks-cause" in run.stdout


def test_help_names_the_drive_report_and_the_states_it_prints() -> None:
    run = subprocess.run([sys.executable, str(ATTAVO), "--help"],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 0
    assert "attavo drive [N] [--until-ready] [--again] [--report]" in run.stdout
    assert "schema/drive-report.schema.json" in run.stdout
    assert "(yours, waits, acts, done)" in " ".join(run.stdout.split())


def test_unknown_subcommand_message_names_attest_and_record_only() -> None:
    run = subprocess.run([sys.executable, str(ATTAVO), "not-a-real-subcommand"],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 2
    assert "record-only" in run.stderr
    assert "attest" in run.stderr


def test_no_vendor_name_in_bin_attavo() -> None:
    """Acceptance 11. The four pre-existing code-factory hits this check
    once had to carve out lived in test docstrings, not in bin/attavo
    itself; this check's own scope is bin/attavo alone, so it stays a
    plain empty-output assertion."""
    run = subprocess.run(
        ["git", "grep", "-n", "-i", "-E", "github|sigstore|in-toto", "--", "bin/attavo"],
        cwd=str(ROOT), capture_output=True, text=True, check=False)
    assert run.returncode == 1, run.stdout
    assert run.stdout == ""


def test_help_names_the_test_modes_the_cache_and_the_transport_refusal() -> None:
    """The test tool as the executor's only way to run tests (v0.28.0's
    first item): the help names the three modes, --fresh and the cache,
    exclude_in_executor, and that the transport refuses inside an
    executor. Red on the tree and on the stub: the words are the build's."""
    run = subprocess.run([sys.executable, str(ATTAVO), "--help"],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 0
    assert "attavo test [N] [--all | <test>...] [--fresh]" in run.stdout, run.stdout
    for phrase in ("--fresh", "unchanged", "exclude_in_executor", "ATTAVO_STAGE", "refuse"):
        assert phrase in run.stdout, phrase


def test_help_names_the_seventh_proof_and_the_empty_stop() -> None:
    """Item 227: the doctor's seventh proof and the empty stop class. Red on
    the tree and on the stub: the words are the build's."""
    run = subprocess.run([sys.executable, str(ATTAVO), "--help"],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 0
    for phrase in ("no attested item is empty", "built_since", "committed nothing", "reads the commit"):
        assert phrase in run.stdout, phrase
