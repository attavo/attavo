"""The one clock every bound the product judges reads (#257): with
ATTAVO_CLOCK_FILE set, the heartbeat's age, the marker's age and the
event's age are read against the file's time; unset, against the
machine's.

Run: python3 -m pytest tests/test_clock.py -q
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from attavo import clock
from attavo.doctor import heartbeat_age_s, invocation_age_s
from attavo.drive import _seconds_since

STAMP = "2026-09-17T13:16:08Z"
STAMP_EPOCH = datetime.strptime(STAMP, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def test_now_reads_the_file_when_the_variable_is_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "clock"
    f.write_text("1234.5\n")
    monkeypatch.setenv("ATTAVO_CLOCK_FILE", str(f))
    assert clock.now() == 1234.5
    f.write_text("99")
    assert clock.now() == 99.0


def test_now_is_the_machines_when_the_variable_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ATTAVO_CLOCK_FILE", raising=False)
    before = time.time()
    assert before <= clock.now() <= time.time()


def test_every_age_reader_reads_the_file_when_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "clock"
    f.write_text(str(STAMP_EPOCH + 100))
    monkeypatch.setenv("ATTAVO_CLOCK_FILE", str(f))
    beat = tmp_path / "beat"
    beat.write_text("")
    os.utime(beat, (STAMP_EPOCH + 40, STAMP_EPOCH + 40))
    assert heartbeat_age_s(beat) == 60.0
    assert invocation_age_s({"at": STAMP}) == 100.0
    assert _seconds_since(STAMP) == 100.0
    assert heartbeat_age_s(tmp_path / "absent") is None


def test_every_age_reader_reads_the_machine_when_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ATTAVO_CLOCK_FILE", raising=False)
    beat = tmp_path / "beat"
    beat.write_text("")
    os.utime(beat, (time.time() - 50, time.time() - 50))
    beat_age = heartbeat_age_s(beat)
    assert beat_age is not None and 50 <= beat_age < 60
    for age in (invocation_age_s({"at": STAMP}), _seconds_since(STAMP)):
        assert age is not None and abs(age - (time.time() - STAMP_EPOCH)) < 5
