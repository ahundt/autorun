"""Advisory state has to survive long enough to be read back.

``set_volatile`` and ``update_volatile`` keep values in memory rather than on
disk, which is right while a daemon is holding that memory across every hook
in a session. Without one — ``AUTORUN_USE_DAEMON=0``, and the fallback
whenever the daemon is unreachable — each hook is its own process with its
own empty cache.

A counter kept that way is read as absent, incremented to one, and thrown
away, on every single call. It never reaches any threshold, so whatever it
was counting toward simply never happens: no error, no warning, nothing in
the log. Task-staleness enforcement was dead this way, and the only visible
symptom was five end-to-end tests that had been failing quietly.

So the store is told whether anything outlives the request, and when nothing
does, advisory writes become durable ones.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent
SRC_DIR = PLUGIN_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autorun import session_manager as sm  # noqa: E402
from autorun.core import ThreadSafeDB  # noqa: E402


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    directory = tmp_path / "sessions"
    directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(directory))
    sm._reset_for_testing()
    yield directory
    sm._reset_for_testing()


class TestDaemonBehaviorIsUnchanged:
    """With a daemon holding the cache, advisory means advisory."""

    def test_an_advisory_write_stays_out_of_storage(self, isolated_state):
        db = ThreadSafeDB()
        db.set_volatile("s:counter", 7)

        assert db.get("s:counter") == 7
        fresh = ThreadSafeDB()
        assert fresh.get("s:counter") is None, (
            "An advisory value reached storage, so the daemon is paying for "
            "durability it explicitly opted out of."
        )

    def test_an_advisory_counter_accumulates_in_one_process(self, isolated_state):
        db = ThreadSafeDB()
        for _ in range(5):
            count = db.update_volatile("s:counter", lambda current: (current or 0) + 1, 0)
        assert count == 5


class TestWithoutADaemon:
    """With nothing outliving the request, advisory has to mean durable."""

    def test_an_advisory_write_survives_the_process(self, isolated_state):
        db = ThreadSafeDB(persist_volatile_state=True)
        db.set_volatile("s:counter", 7)

        fresh = ThreadSafeDB(persist_volatile_state=True)
        assert fresh.get("s:counter") == 7, (
            "The value did not survive, so anything counting on it restarts "
            "from nothing on every hook."
        )

    def test_a_counter_advances_across_processes(self, isolated_state):
        """The failure exactly: a fresh store per call, counting to one."""
        counts = []
        for _ in range(5):
            db = ThreadSafeDB(persist_volatile_state=True)
            counts.append(
                db.update_volatile("s:counter", lambda current: (current or 0) + 1, 0)
            )

        assert counts == [1, 2, 3, 4, 5], (
            f"The counter did not advance across stores: {counts}. A threshold "
            "of 3 or 5 would never be reached, and the feature waiting on it "
            "would never run."
        )

    def test_the_durable_value_is_the_one_a_reader_sees(self, isolated_state):
        db = ThreadSafeDB(persist_volatile_state=True)
        db.update_volatile("s:counter", lambda current: (current or 0) + 1, 0)

        with sm.session_state("s") as state:
            assert state["counter"] == 1


_COUNT_PROBE = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, sys.argv[1])
    from autorun.core import ThreadSafeDB

    db = ThreadSafeDB(persist_volatile_state=True)
    print(db.update_volatile("probe:counter", lambda c: (c or 0) + 1, 0))
    """
)


@pytest.mark.subprocess
@pytest.mark.serial
class TestAcrossRealProcesses:
    def test_separate_processes_continue_one_countdown(self, isolated_state):
        """Each hook really is a separate interpreter; prove it there too."""
        env = {**os.environ, "AUTORUN_TEST_STATE_DIR": str(isolated_state)}
        seen = []
        for _ in range(4):
            completed = subprocess.run(
                [sys.executable, "-c", _COUNT_PROBE, str(SRC_DIR)],
                capture_output=True, text=True, timeout=60, env=env,
            )
            assert completed.returncode == 0, completed.stderr
            seen.append(int(completed.stdout.strip().splitlines()[-1]))

        assert seen == [1, 2, 3, 4], f"the countdown restarted: {seen}"


class TestDirectModeSelectsThisBehavior:
    def test_the_no_daemon_entry_point_persists_advisory_state(self):
        """The wiring, not just the capability.

        The store is constructed in one place for direct mode; if that call
        loses the flag, every test above still passes and the feature is
        broken again.
        """
        source = (SRC_DIR / "autorun" / "__main__.py").read_text(encoding="utf-8")
        assert "ThreadSafeDB(persist_volatile_state=True)" in source, (
            "Direct mode no longer asks for durable advisory state, so "
            "counters reset on every hook again."
        )
