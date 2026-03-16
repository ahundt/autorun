#!/usr/bin/env python3
"""Tests for shelve-to-JSON migration and multi-process/multi-session safety.

Verifies that the persistence migration from Python shelve to filelock+JSON
(session_manager.py) is complete, correct, and safe under concurrent access.

Test groups:
1. Shelve retirement completeness — no shelve references remain in migrated modules
2. ai_monitor persistence migration — monitor_state() uses session_state(), not shelve
3. Multi-process safety — concurrent monitors and daemon threads don't lose data
4. Log rotation — RotatingFileHandler replaces unbounded FileHandler
5. EventContext concurrency documentation — atomicity warning present in docstring

Concurrency scenarios tested:
- Two monitor processes writing to the same session_id simultaneously
- One monitor process + one daemon thread writing to shared JSON concurrently
- Three monitor processes with different session_ids (isolation check)
- Filelock timeout when another thread holds the lock
- Key isolation between monitor namespace and daemon namespace in shared JSON
"""

import ast
import multiprocessing
import os
import sys
import tempfile
import threading
import time

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _source_path(module_name: str) -> str:
    """Return absolute path to a source module in src/autorun/."""
    base = os.path.join(
        os.path.dirname(__file__), "..", "src", "autorun", f"{module_name}.py"
    )
    return os.path.abspath(base)


def _source_text(module_name: str) -> str:
    with open(_source_path(module_name), encoding="utf-8") as f:
        return f.read()


def _imports_module(source_text: str, module_name: str) -> bool:
    """Check if source_text has an `import <module_name>` statement (via ast)."""
    tree = ast.parse(source_text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == module_name:
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.module == module_name:
                return True
    return False


# ---------------------------------------------------------------------------
# 1. Shelve retirement — no shelve references remain in migrated modules
# ---------------------------------------------------------------------------

class TestShelveRetirementCompleteness:
    """After migration to filelock+JSON (commit ca0d418, 2026-02-19), the word
    'shelve' must not appear in core.py, plan_export.py, or task_lifecycle.py.
    It may still appear in session_manager.py (documents migration history)
    and in ai_monitor.py (if not yet migrated — tested separately).
    """

    def test_core_py_has_no_shelve_references(self):
        text = _source_text("core")
        assert "shelve" not in text.lower(), (
            "core.py still contains 'shelve' in comments or code. "
            "All references should say 'filelock+JSON' or 'persistent store'. "
            "Run: grep -ni shelve plugins/autorun/src/autorun/core.py"
        )

    def test_plan_export_py_has_no_shelve_references(self):
        text = _source_text("plan_export")
        assert "shelve" not in text.lower(), (
            "plan_export.py still contains 'shelve'. "
            "All references should say 'JSON store' or 'session_state()'. "
            "Run: grep -ni shelve plugins/autorun/src/autorun/plan_export.py"
        )

    def test_task_lifecycle_py_has_no_shelve_references(self):
        text = _source_text("task_lifecycle")
        assert "shelve" not in text.lower(), (
            "task_lifecycle.py still contains 'shelve'. "
            "All references should say 'JSON store' or 'session_state()'. "
            "Run: grep -ni shelve plugins/autorun/src/autorun/task_lifecycle.py"
        )


# ---------------------------------------------------------------------------
# 2. ai_monitor persistence migration — uses session_state(), not shelve
# ---------------------------------------------------------------------------

class TestAiMonitorPersistenceMigration:
    """ai_monitor.py must use session_state() (filelock+JSON) instead of
    shelve.open() for tmux monitor state. The monitor_state() context manager
    must yield a dict-like object with the same API as shelve.Shelf.
    """

    def test_ai_monitor_does_not_import_shelve(self):
        text = _source_text("ai_monitor")
        assert not _imports_module(text, "shelve"), (
            "ai_monitor.py still has 'import shelve'. "
            "Replace shelve.open() with session_state() from session_manager.py. "
            "See: plugins/autorun/src/autorun/ai_monitor.py:17"
        )

    def test_ai_monitor_imports_session_state(self):
        text = _source_text("ai_monitor")
        assert "session_state" in text, (
            "ai_monitor.py does not reference session_state. "
            "monitor_state() should delegate to session_state('monitor_{sid}'). "
            "See: plugins/autorun/src/autorun/session_manager.py:285"
        )

    def test_monitor_state_supports_dict_api(self, tmp_path, monkeypatch):
        """monitor_state() must yield an object supporting [], .get(), .keys(),
        'in', and .update() — the same dict-like API that shelve.Shelf provided.
        """
        monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(tmp_path))
        from autorun import session_manager
        session_manager._store = None
        session_manager._manager = None

        from autorun.ai_monitor import monitor_state

        with monitor_state("test_dict_api") as state:
            state["key1"] = "value1"
            assert state["key1"] == "value1", "[] read-back failed"
            assert state.get("missing", "default") == "default", ".get() with default failed"
            assert "key1" in state, "'in' operator failed for existing key"
            assert "missing" not in state, "'in' operator failed for missing key"
            keys = list(state.keys())
            assert "key1" in keys, ".keys() does not contain written key"
            state.update({"key2": "v2", "key3": "v3"})
            assert state["key2"] == "v2", ".update() did not persist"

    def test_monitor_state_persists_across_reopen(self, tmp_path, monkeypatch):
        """Data written inside monitor_state() must survive closing and reopening
        the context manager (verifies atomic JSON write + re-read on open).
        """
        monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(tmp_path))
        from autorun import session_manager
        session_manager._store = None
        session_manager._manager = None

        from autorun.ai_monitor import monitor_state

        with monitor_state("roundtrip_test") as state:
            state["windows"] = {"0": "pane output here"}
            state["checks"] = 42

        # Force fresh store to simulate process restart
        session_manager._store = None
        session_manager._manager = None

        with monitor_state("roundtrip_test") as state:
            assert state["windows"] == {"0": "pane output here"}, (
                "windows dict not recovered after reopen — atomic write may have failed"
            )
            assert state["checks"] == 42, (
                "integer counter not recovered after reopen"
            )

    def test_monitor_sessions_cannot_see_each_others_keys(self, tmp_path, monkeypatch):
        """monitor_state('session_a') keys must be invisible to monitor_state('session_b').
        Both share the same daemon_state.json but use different key prefixes.
        """
        monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(tmp_path))
        from autorun import session_manager
        session_manager._store = None
        session_manager._manager = None

        from autorun.ai_monitor import monitor_state

        with monitor_state("session_a") as state:
            state["secret"] = "only_for_a"

        with monitor_state("session_b") as state:
            assert state.get("secret") is None, (
                "session_b can read session_a's key 'secret' — "
                "key prefix isolation broken in _StateProxy._k(). "
                "Expected prefix 'monitor_session_a/' vs 'monitor_session_b/'"
            )

    def test_monitor_keys_do_not_collide_with_daemon_keys(self, tmp_path, monkeypatch):
        """monitor_state('s1') and session_state('s1') must use different key
        namespaces in the shared daemon_state.json. monitor_state prefixes with
        'monitor_s1/' while daemon uses 's1/' — no collision possible.
        """
        monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(tmp_path))
        from autorun import session_manager
        session_manager._store = None
        session_manager._manager = None

        from autorun.ai_monitor import monitor_state
        from autorun.session_manager import session_state

        # Daemon writes to session_state("s1")
        with session_state("s1") as daemon_st:
            daemon_st["policy"] = "ALLOW"

        # Monitor writes to monitor_state("s1") → session_state("monitor_s1")
        with monitor_state("s1") as monitor_st:
            monitor_st["windows"] = {"0": "output"}

        # Verify daemon doesn't see monitor's keys
        session_manager._store = None
        session_manager._manager = None
        with session_state("s1") as daemon_st:
            assert daemon_st["policy"] == "ALLOW", "daemon lost its own 'policy' key"
            assert daemon_st.get("windows") is None, (
                "daemon can read monitor's 'windows' key — namespace collision. "
                "monitor_state() must use session_state('monitor_s1'), not session_state('s1')"
            )

        # Verify monitor doesn't see daemon's keys
        session_manager._store = None
        session_manager._manager = None
        with monitor_state("s1") as monitor_st:
            assert monitor_st["windows"] == {"0": "output"}, "monitor lost its own 'windows' key"
            assert monitor_st.get("policy") is None, (
                "monitor can read daemon's 'policy' key — namespace collision"
            )


# ---------------------------------------------------------------------------
# 3. Multi-process safety — concurrent access to shared daemon_state.json
# ---------------------------------------------------------------------------

# Use "spawn" context to avoid fork-inherited module state on macOS/Linux.
# Each spawned process imports fresh, reads AUTORUN_TEST_STATE_DIR from env.
_mp_ctx = multiprocessing.get_context("spawn")


def _worker_write_keys(state_dir: str, session_id: str, prefix: str, count: int):
    """Worker function for multiprocessing tests. Runs in a SPAWNED child process.
    Writes {prefix}_{i} = value_{prefix}_{i} for i in range(count).
    """
    os.environ["AUTORUN_TEST_STATE_DIR"] = state_dir

    from autorun import session_manager
    session_manager._store = None

    from autorun.ai_monitor import monitor_state

    for i in range(count):
        with monitor_state(session_id) as state:
            state[f"{prefix}_{i}"] = f"value_{prefix}_{i}"


def _daemon_thread_write_keys(state_dir: str, session_id: str, count: int):
    """Simulates daemon writing to session_state (not monitor_state).
    Writes daemon_{i} = daemon_value_{i} for i in range(count).
    """
    os.environ["AUTORUN_TEST_STATE_DIR"] = state_dir
    from autorun import session_manager
    session_manager._store = None

    from autorun.session_manager import session_state

    for i in range(count):
        with session_state(session_id) as state:
            state[f"daemon_{i}"] = f"daemon_value_{i}"


class TestMultiProcessConcurrentAccess:
    """Verify that multiple processes (ai_monitor instances, daemon threads)
    can write to the shared daemon_state.json simultaneously without data
    loss or corruption. Uses filelock for cross-process serialization and
    atomic tempfile+rename for crash safety.

    All tests use tmp_path (pytest fixture) via AUTORUN_TEST_STATE_DIR
    to avoid touching real user data at ~/.claude/sessions/.
    """

    @pytest.fixture(autouse=True)
    def _reset_store(self, tmp_path, monkeypatch):
        """Ensure each test gets a clean _JSONStore pointing to its own tmp_path.
        Resets both _store (raw JSONStore) and _manager (SessionStateManager)
        singletons to prevent stale references from earlier tests.
        """
        monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(tmp_path))
        from autorun import session_manager
        session_manager._store = None
        session_manager._manager = None
        yield
        session_manager._store = None
        session_manager._manager = None

    def test_two_monitor_processes_writing_same_session(self, tmp_path):
        """Two separate processes each write 10 keys to the same session_id.
        After both finish, all 20 keys must be present — filelock ensures
        no write is lost due to concurrent JSON read-modify-write.
        """
        state_dir = str(tmp_path)
        sid = "shared_session"
        n = 10

        p1 = _mp_ctx.Process(
            target=_worker_write_keys,
            args=(state_dir, sid, "proc1", n),
        )
        p2 = _mp_ctx.Process(
            target=_worker_write_keys,
            args=(state_dir, sid, "proc2", n),
        )

        p1.start()
        p2.start()
        p1.join(timeout=30)
        p2.join(timeout=30)

        assert p1.exitcode == 0, (
            f"Monitor process 1 crashed (exit {p1.exitcode}). "
            "Check if session_state() import or filelock failed in child process."
        )
        assert p2.exitcode == 0, (
            f"Monitor process 2 crashed (exit {p2.exitcode})."
        )

        # Reset singletons so parent reads fresh from disk
        from autorun import session_manager
        session_manager._store = None
        session_manager._manager = None

        json_path = os.path.join(state_dir, "daemon_state.json")
        assert os.path.exists(json_path), (
            f"daemon_state.json not created at {json_path} — "
            "child processes may not have picked up AUTORUN_TEST_STATE_DIR"
        )

        import json
        with open(json_path) as f:
            raw_data = json.load(f)

        session_manager._store = None
        session_manager._manager = None

        from autorun.ai_monitor import monitor_state

        with monitor_state(sid) as state:
            for i in range(n):
                assert state.get(f"proc1_{i}") == f"value_proc1_{i}", (
                    f"Key proc1_{i} missing after concurrent write. "
                    f"Process 2 may have overwritten process 1's data. "
                    f"Raw JSON keys: {sorted(raw_data.keys())[:10]}..."
                )
                assert state.get(f"proc2_{i}") == f"value_proc2_{i}", (
                    f"Key proc2_{i} missing after concurrent write."
                )

    def test_monitor_process_and_daemon_thread_concurrent(self, tmp_path):
        """One monitor subprocess + one daemon thread write simultaneously.
        Monitor writes to monitor_{sid} namespace, daemon writes to {sid} namespace.
        After both finish, all keys from both namespaces must be present.
        """
        state_dir = str(tmp_path)
        sid = "concurrent_test"
        n = 10

        p = _mp_ctx.Process(
            target=_worker_write_keys,
            args=(state_dir, sid, "monitor", n),
        )

        os.environ["AUTORUN_TEST_STATE_DIR"] = state_dir
        from autorun import session_manager
        session_manager._store = None
        session_manager._manager = None

        t = threading.Thread(
            target=_daemon_thread_write_keys,
            args=(state_dir, sid, n),
        )

        p.start()
        t.start()
        p.join(timeout=30)
        t.join(timeout=30)

        assert p.exitcode == 0, (
            f"Monitor process crashed (exit {p.exitcode}) during concurrent write."
        )

        # Verify monitor keys
        session_manager._store = None
        session_manager._manager = None

        from autorun.ai_monitor import monitor_state
        with monitor_state(sid) as state:
            for i in range(n):
                assert state.get(f"monitor_{i}") == f"value_monitor_{i}", (
                    f"Monitor key monitor_{i} missing — daemon thread may have "
                    f"overwritten monitor data in shared JSON"
                )

        # Verify daemon keys in separate namespace
        session_manager._store = None
        session_manager._manager = None
        from autorun.session_manager import session_state
        with session_state(sid) as state:
            for i in range(n):
                assert state.get(f"daemon_{i}") == f"daemon_value_{i}", (
                    f"Daemon key daemon_{i} missing — monitor process may have "
                    f"overwritten daemon data in shared JSON"
                )

    def test_three_monitors_with_different_sessions_are_isolated(self, tmp_path):
        """Three monitor processes, each with a different session_id, write
        concurrently. Each session's keys must be invisible to the others.
        """
        state_dir = str(tmp_path)
        n = 5

        procs = []
        for j in range(3):
            p = _mp_ctx.Process(
                target=_worker_write_keys,
                args=(state_dir, f"session_{j}", f"proc{j}", n),
            )
            procs.append(p)
            p.start()

        for idx, p in enumerate(procs):
            p.join(timeout=30)
            assert p.exitcode == 0, (
                f"Monitor process {idx} (session_{idx}) crashed (exit {p.exitcode})."
            )

        from autorun import session_manager
        session_manager._store = None
        session_manager._manager = None
        from autorun.ai_monitor import monitor_state

        for j in range(3):
            with monitor_state(f"session_{j}") as state:
                for i in range(n):
                    assert state.get(f"proc{j}_{i}") == f"value_proc{j}_{i}", (
                        f"session_{j} missing its own key proc{j}_{i}"
                    )
                other = (j + 1) % 3
                assert state.get(f"proc{other}_0") is None, (
                    f"session_{j} can see session_{other}'s key proc{other}_0 — "
                    f"key prefix isolation broken between monitor sessions"
                )

    def test_filelock_timeout_raises_not_hangs(self, tmp_path, monkeypatch):
        """When one thread holds the filelock, another thread attempting to
        acquire with a short timeout must raise SessionTimeoutError, not hang.
        This verifies the system doesn't deadlock under contention.
        """
        monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(tmp_path))
        from autorun import session_manager
        session_manager._store = None
        session_manager._manager = None

        from autorun.session_manager import session_state, SessionTimeoutError

        lock_acquired = threading.Event()
        lock_released = threading.Event()

        def hold_lock():
            with session_state("lock_test", timeout=10.0) as state:
                state["holder"] = "thread1"
                lock_acquired.set()
                lock_released.wait(timeout=10.0)

        t = threading.Thread(target=hold_lock)
        t.start()
        lock_acquired.wait(timeout=5.0)

        with pytest.raises(SessionTimeoutError):
            with session_state("lock_test", timeout=0.5) as state:
                state["should_not_reach"] = True

        lock_released.set()
        t.join(timeout=5.0)


# ---------------------------------------------------------------------------
# 4. Log rotation — RotatingFileHandler replaces unbounded FileHandler
# ---------------------------------------------------------------------------

class TestLogRotationConfiguration:
    """Verify that logging uses RotatingFileHandler (5MB max, 3 backups)
    instead of unbounded FileHandler. Prevents daemon.log from growing
    indefinitely (observed at 29MB+ before this change).
    """

    def test_get_logger_uses_rotating_file_handler(self, monkeypatch, tmp_path):
        """When debug logging is enabled, get_logger() must configure a
        RotatingFileHandler with maxBytes=5MB and backupCount=3.
        """
        import logging
        from logging.handlers import RotatingFileHandler

        monkeypatch.setattr("autorun.logging_utils.DEBUG_ENABLED", True)
        monkeypatch.setattr(
            "autorun.logging_utils.LOG_FILE",
            str(tmp_path / "test_autorun_rotation.log"),
        )

        import autorun.logging_utils as lu
        logger = lu.get_logger("test_rotation_unique_" + str(time.time()))
        handlers = [h for h in logger.handlers if not isinstance(h, logging.NullHandler)]

        assert len(handlers) >= 1, (
            "No file handlers configured — get_logger() may not be setting up "
            "handlers when DEBUG_ENABLED=True. Check logging_utils.py:47-55"
        )
        assert isinstance(handlers[0], RotatingFileHandler), (
            f"Expected RotatingFileHandler but got {type(handlers[0]).__name__}. "
            "FileHandler causes unbounded log growth. "
            "Fix: logging_utils.py:50 should use RotatingFileHandler"
        )
        assert handlers[0].maxBytes == 5 * 1024 * 1024, (
            f"maxBytes={handlers[0].maxBytes}, expected 5242880 (5MB)"
        )
        assert handlers[0].backupCount == 3, (
            f"backupCount={handlers[0].backupCount}, expected 3"
        )


# ---------------------------------------------------------------------------
# 5. EventContext concurrency documentation
# ---------------------------------------------------------------------------

class TestEventContextConcurrencyDocumentation:
    """Verify that EventContext.__getattr__ documents the atomicity gap for
    concurrent multi-session access. Multiple Claude Code and Gemini CLI
    sessions can hit the daemon simultaneously from the same or different
    working directories.
    """

    def test_docstring_contains_atomicity_warning(self):
        from autorun.core import EventContext
        doc = EventContext.__getattr__.__doc__
        assert doc is not None, (
            "EventContext.__getattr__ has no docstring — "
            "must document the read-modify-write atomicity gap"
        )
        assert "ATOMICITY WARNING" in doc, (
            "EventContext.__getattr__ docstring is missing 'ATOMICITY WARNING'. "
            "This warning documents that ctx.x = ctx.x + 1 is not atomic "
            "across concurrent hook invocations from multiple CLI sessions."
        )

    def test_docstring_documents_concurrent_session_scenarios(self):
        from autorun.core import EventContext
        doc = EventContext.__getattr__.__doc__
        assert "CONCURRENT" in doc, (
            "Docstring must list concurrent scenarios: multiple Claude Code "
            "sessions, Gemini CLI sessions, mixed sessions, same-session "
            "rapid hooks. Search for 'CONCURRENT' in the docstring."
        )

    def test_docstring_prescribes_session_state_for_global_scope(self):
        from autorun.core import EventContext
        doc = EventContext.__getattr__.__doc__
        assert "session_state()" in doc, (
            "Docstring must prescribe session_state() as the safe alternative "
            "for global-scope read-modify-write operations. "
            "ctx magic attributes are NOT safe for global atomic operations."
        )
