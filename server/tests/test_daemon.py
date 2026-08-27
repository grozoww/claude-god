"""Server pidfile handling. No processes are spawned here — that is covered by
the live check in install/CI; these pin the logic that decides running vs stale.
"""

from __future__ import annotations

import os

import pytest
from ccgod import daemon


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CCGOD_HOME", str(tmp_path / "home"))
    return tmp_path


def test_no_pidfile_means_stopped():
    assert daemon.read() is None
    assert daemon.status() == (None, "stopped")
    ok, msg = daemon.stop()
    assert ok is False
    assert "nothing to stop" in msg


def test_write_then_read_roundtrip():
    daemon.write("127.0.0.1", 8799)
    s = daemon.read()
    assert s and s.pid == os.getpid()
    assert s.url == "http://127.0.0.1:8799"


def test_live_pid_that_does_not_answer_is_stale(monkeypatch):
    # This process is alive but is not an HTTP server, so health must fail.
    daemon.write("127.0.0.1", 8799)
    monkeypatch.setattr(daemon, "responding", lambda s, timeout=1.0: False)
    s, state = daemon.status()
    assert s and state == "stale"


def test_live_pid_that_answers_is_running(monkeypatch):
    daemon.write("127.0.0.1", 8799)
    monkeypatch.setattr(daemon, "responding", lambda s, timeout=1.0: True)
    _, state = daemon.status()
    assert state == "running"


def test_dead_pid_is_stale_and_stop_clears_it():
    daemon.write("127.0.0.1", 8799)
    p = daemon.pid_path()
    p.write_text(p.read_text().replace(f'"pid": {os.getpid()}', '"pid": 999999999'))
    s, state = daemon.status()
    assert s and state == "stale"

    ok, msg = daemon.stop()
    assert ok is False
    assert "stale" in msg
    assert not p.exists()          # cleaned up rather than left to confuse later runs


def test_corrupt_pidfile_is_treated_as_absent():
    daemon.pid_path().parent.mkdir(parents=True, exist_ok=True)
    daemon.pid_path().write_text("{not json")
    assert daemon.read() is None
    assert daemon.status()[1] == "stopped"


def test_clear_is_idempotent():
    daemon.write("127.0.0.1", 8799)
    daemon.clear()
    daemon.clear()
    assert daemon.read() is None
