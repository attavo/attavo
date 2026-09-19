import pytest


@pytest.fixture(autouse=True)
def _short_forge_poll(monkeypatch):
    """The drive's forge poll reads its bound and interval from the environment at call time (issue #84) so a test never waits out the production bound; since #178 verify polls too, and a fixture whose checks_command prints nothing is unconcluded and would wait 600 s. Every test gets a sub-second clock unless it sets its own."""
    monkeypatch.setenv("ATTAVO_DRIVE_POLL_BOUND_S", "0.5")
    monkeypatch.setenv("ATTAVO_DRIVE_POLL_INTERVAL_S", "0.05")
