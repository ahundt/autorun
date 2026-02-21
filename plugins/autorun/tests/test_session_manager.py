#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for session_manager.py (filelock+JSON backend).
"""
import pytest
import sys
import threading
import time
from pathlib import Path

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from clautorun import session_manager as sm
from clautorun.session_manager import (
    SessionStateError,
    SessionTimeoutError,
    SessionBackendError,
    SessionLock,
    SessionStateManager,
    session_state,
    shared_session_state,
    get_session_manager,
    clear_test_session_state,
    DEFAULT_SESSION_TIMEOUT,
    SHARED_ACCESS_TIMEOUT,
    _reset_for_testing,
)


@pytest.fixture(autouse=True)
def reset_session_manager_singletons():
    """Reset module-level singletons before and after each test for isolation."""
    _reset_for_testing()
    yield
    _reset_for_testing()


class TestExceptions:
    """Test exception classes"""

    def test_session_state_error(self):
        error = SessionStateError("test error")
        assert str(error) == "test error"

    def test_session_timeout_error_inherits(self):
        error = SessionTimeoutError("timeout")
        assert isinstance(error, SessionStateError)
        assert str(error) == "timeout"

    def test_session_backend_error_inherits(self):
        error = SessionBackendError("backend error")
        assert isinstance(error, SessionStateError)
        assert str(error) == "backend error"


class TestSessionLock:
    """SessionLock is a no-op shim — just verifies it doesn't raise."""

    def test_lock_creation_noop(self):
        """SessionLock can be created without raising."""
        lock = SessionLock("test_session", 5.0, "/tmp/fake")
        assert lock is not None

    def test_lock_context_manager_noop(self):
        """SessionLock works as context manager without side effects."""
        with SessionLock("test_session", 5.0) as l:
            assert l is not None

    def test_lock_no_attributes(self):
        """SessionLock does not expose internal attributes from old implementation."""
        lock = SessionLock("test_session", 5.0)
        assert not hasattr(lock, "session_id")
        assert not hasattr(lock, "lock_fd")
        assert not hasattr(lock, "acquired")


class TestFilelockJSONBackend:
    """Tests for the filelock+JSON storage backend."""

    def test_session_state_basic_get_set(self, tmp_path):
        """Basic get/set via session_state() persists across context opens."""
        with session_state("test-session", state_dir=str(tmp_path)) as s:
            s["key"] = "value"
        with session_state("test-session", state_dir=str(tmp_path)) as s:
            assert s.get("key") == "value"

    def test_session_state_default_none(self, tmp_path):
        """get() returns default when key is absent."""
        with session_state("s", state_dir=str(tmp_path)) as s:
            assert s.get("missing") is None
            assert s.get("missing", 42) == 42

    def test_session_state_contains(self, tmp_path):
        """__contains__ works correctly."""
        with session_state("s", state_dir=str(tmp_path)) as s:
            s["x"] = 1
        with session_state("s", state_dir=str(tmp_path)) as s:
            assert "x" in s
            assert "y" not in s

    def test_session_state_delitem(self, tmp_path):
        """__delitem__ removes key and marks store dirty."""
        with session_state("s", state_dir=str(tmp_path)) as s:
            s["x"] = 1
        with session_state("s", state_dir=str(tmp_path)) as s:
            del s["x"]
        with session_state("s", state_dir=str(tmp_path)) as s:
            assert s.get("x") is None

    def test_session_state_persists_across_manager_instances(self, tmp_path):
        """State survives creating new SessionStateManager (simulates restart)."""
        m1 = SessionStateManager(str(tmp_path))
        with m1.session_state("s1") as s:
            s["x"] = 42

        m2 = SessionStateManager(str(tmp_path))
        with m2.session_state("s1") as s:
            assert s.get("x") == 42

    def test_different_sessions_isolated(self, tmp_path):
        """Different session_id values don't share keys."""
        with session_state("session-a", state_dir=str(tmp_path)) as s:
            s["key"] = "a-value"
        with session_state("session-b", state_dir=str(tmp_path)) as s:
            s["key"] = "b-value"
        with session_state("session-a", state_dir=str(tmp_path)) as s:
            assert s.get("key") == "a-value"
        with session_state("session-b", state_dir=str(tmp_path)) as s:
            assert s.get("key") == "b-value"

    def test_clear_test_session_state(self, tmp_path):
        """clear_test_session_state removes only the target session's keys."""
        with session_state("session-a", state_dir=str(tmp_path)) as s:
            s["key"] = "a-value"
        with session_state("session-b", state_dir=str(tmp_path)) as s:
            s["key"] = "b-value"
        clear_test_session_state("session-a", state_dir=str(tmp_path))
        with session_state("session-a", state_dir=str(tmp_path)) as s:
            assert s.get("key") is None
        with session_state("session-b", state_dir=str(tmp_path)) as s:
            assert s.get("key") == "b-value"

    def test_timeout_raises_session_timeout_error(self, tmp_path):
        """FileLock timeout raises SessionTimeoutError."""
        barrier = threading.Event()

        def hold_lock():
            with session_state("s", state_dir=str(tmp_path), timeout=10.0):
                barrier.set()
                time.sleep(2)

        t = threading.Thread(target=hold_lock)
        t.start()
        barrier.wait()
        with pytest.raises(SessionTimeoutError):
            with session_state("s", state_dir=str(tmp_path), timeout=0.1):
                pass
        t.join()

    def test_no_write_on_readonly_access(self, tmp_path):
        """State file not created when no writes happen."""
        state_file = tmp_path / "daemon_state.json"
        with session_state("s", state_dir=str(tmp_path)) as s:
            _ = s.get("x")  # read-only
        assert not state_file.exists()

    def test_concurrent_writes_thread_safe(self, tmp_path):
        """Concurrent writes from threads don't corrupt state."""
        results = []
        errors = []

        def writer(i):
            try:
                with session_state("shared", state_dir=str(tmp_path)) as s:
                    s[f"key_{i}"] = i
                results.append(i)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors in concurrent writes: {errors}"
        assert len(results) == 5

        with session_state("shared", state_dir=str(tmp_path)) as s:
            for i in range(5):
                assert s.get(f"key_{i}") == i

    def test_shared_session_state_function(self, tmp_path):
        """shared_session_state() works identically to session_state()."""
        with shared_session_state("s", state_dir=str(tmp_path)) as s:
            s["shared"] = True
        with session_state("s", state_dir=str(tmp_path)) as s:
            assert s.get("shared") is True


class TestSessionStateManager:
    """Test SessionStateManager class with new filelock+JSON backend."""

    def test_manager_creation(self, tmp_path):
        """SessionStateManager can be created with explicit state_dir."""
        manager = SessionStateManager(str(tmp_path))
        assert manager._store is not None

    def test_manager_read_write(self, tmp_path):
        """Manager session_state context manager works for read/write."""
        manager = SessionStateManager(str(tmp_path))
        with manager.session_state("test") as s:
            s["val"] = 99
        with manager.session_state("test") as s:
            assert s.get("val") == 99

    def test_shared_session_state(self, tmp_path):
        """shared_session_state is equivalent to session_state."""
        manager = SessionStateManager(str(tmp_path))
        with manager.shared_session_state("test") as s:
            s["v"] = "hi"
        with manager.session_state("test") as s:
            assert s.get("v") == "hi"

    def test_clear_test_session(self, tmp_path):
        """clear_test_session removes only the target session's keys."""
        manager = SessionStateManager(str(tmp_path))
        with manager.session_state("a") as s:
            s["k"] = "A"
        with manager.session_state("b") as s:
            s["k"] = "B"
        manager.clear_test_session("a")
        with manager.session_state("a") as s:
            assert s.get("k") is None
        with manager.session_state("b") as s:
            assert s.get("k") == "B"


class TestConvenienceFunctions:
    """Test module-level convenience functions."""

    def test_session_state_function(self, tmp_path):
        """session_state() module function works."""
        with session_state("func-test", state_dir=str(tmp_path)) as s:
            s["test"] = "value"
            assert s.get("test") == "value"

    def test_get_session_manager_singleton(self, tmp_path):
        """get_session_manager() returns same instance on repeated calls."""
        m1 = get_session_manager(str(tmp_path))
        m2 = get_session_manager(str(tmp_path))
        assert m1 is m2


class TestConstants:
    """Test module constants."""

    def test_default_timeout(self):
        assert DEFAULT_SESSION_TIMEOUT == 30.0

    def test_shared_access_timeout(self):
        assert SHARED_ACCESS_TIMEOUT == 5.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
