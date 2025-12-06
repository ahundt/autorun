#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for session_manager.py module to increase coverage.
"""
import pytest
import sys
import os
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch, MagicMock, Mock

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from clautorun.session_manager import (
    SessionStateError,
    SessionTimeoutError,
    SessionBackendError,
    SessionLock,
    SessionBackendManager,
    SessionStateManager,
    session_state,
    shared_session_state,
    get_session_manager,
    DEFAULT_SESSION_TIMEOUT,
    SHARED_ACCESS_TIMEOUT,
)


class TestExceptions:
    """Test exception classes"""

    def test_session_state_error(self):
        """Test SessionStateError exception"""
        error = SessionStateError("test error")
        assert str(error) == "test error"

    def test_session_timeout_error(self):
        """Test SessionTimeoutError inherits from SessionStateError"""
        error = SessionTimeoutError("timeout")
        assert isinstance(error, SessionStateError)
        assert str(error) == "timeout"

    def test_session_backend_error(self):
        """Test SessionBackendError inherits from SessionStateError"""
        error = SessionBackendError("backend error")
        assert isinstance(error, SessionStateError)
        assert str(error) == "backend error"


class TestSessionLock:
    """Test SessionLock class"""

    def test_lock_creation(self):
        """Test SessionLock can be created"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            lock = SessionLock("test_session", 5.0, state_dir)
            assert lock.session_id == "test_session"
            assert lock.timeout == 5.0
            assert lock.state_dir == state_dir

    def test_lock_context_manager(self):
        """Test SessionLock as context manager"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            lock = SessionLock("test_session", 5.0, state_dir)
            with lock as fd:
                assert fd is not None
                assert lock.acquired == True

    def test_lock_cleanup_on_exit(self):
        """Test lock is properly cleaned up"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            lock = SessionLock("test_session", 5.0, state_dir)
            with lock as fd:
                pass
            # After exit, lock should be released
            assert lock.lock_fd is None


class TestSessionBackendManager:
    """Test SessionBackendManager class"""

    def test_backend_manager_creation(self):
        """Test SessionBackendManager can be created"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            manager = SessionBackendManager(state_dir)
            assert manager.state_dir == state_dir

    def test_memory_backend_always_works(self):
        """Test memory backend test returns True"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            manager = SessionBackendManager(state_dir)
            assert manager._test_memory_backend("test") == True

    def test_get_backend_thread_safety(self):
        """Test get_backend is thread-safe"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            manager = SessionBackendManager(state_dir)

            results = []
            def get_backend():
                results.append(manager.get_backend("test_session"))

            threads = [threading.Thread(target=get_backend) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # All results should be the same backend
            assert len(set(results)) == 1


class TestSessionStateManager:
    """Test SessionStateManager class"""

    def test_manager_creation(self):
        """Test SessionStateManager can be created"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            manager = SessionStateManager(state_dir)
            assert manager.state_dir == state_dir

    def test_manager_default_state_dir(self):
        """Test SessionStateManager uses default state dir"""
        manager = SessionStateManager()
        assert manager.state_dir == Path.home() / ".claude" / "sessions"

    def test_session_state_requires_valid_id(self):
        """Test session_state requires non-empty string"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            manager = SessionStateManager(state_dir)

            with pytest.raises(SessionStateError):
                with manager.session_state(""):
                    pass

            with pytest.raises(SessionStateError):
                with manager.session_state(None):
                    pass

    def test_session_state_basic_usage(self):
        """Test basic session state read/write"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            manager = SessionStateManager(state_dir)

            with manager.session_state("test_session") as state:
                state["key"] = "value"

    def test_memory_state_thread_safety(self):
        """Test memory state is thread-safe"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            manager = SessionStateManager(state_dir)

            # Force memory backend
            manager.backend_manager._backends["test_session"] = "memory"

            results = []
            def access_state(value):
                with manager.session_state("test_session") as state:
                    state["value"] = value
                    results.append(state.get("value"))

            threads = [threading.Thread(target=access_state, args=(i,)) for i in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(results) == 5


class TestConvenienceFunctions:
    """Test convenience context manager functions"""

    def test_session_state_function(self):
        """Test session_state convenience function"""
        # This uses the global manager
        with session_state("test_session_func") as state:
            state["test"] = "value"
            assert state["test"] == "value"

    def test_shared_session_state_function(self):
        """Test shared_session_state convenience function"""
        with shared_session_state("test_shared_session") as state:
            state["shared"] = "value"
            assert state["shared"] == "value"

    def test_get_session_manager(self):
        """Test get_session_manager returns singleton"""
        manager1 = get_session_manager()
        manager2 = get_session_manager()
        assert manager1 is manager2


class TestSharedAccessTracking:
    """Test shared access tracking"""

    def test_shared_access_count(self):
        """Test shared access tracking increments and decrements"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            manager = SessionStateManager(state_dir)

            # Force memory backend for easier testing
            manager.backend_manager._backends["test_shared"] = "memory"

            with manager.session_state("test_shared", shared_access=True) as state:
                assert state.get("_shared_access_count", 0) >= 1


class TestConstants:
    """Test module constants"""

    def test_default_timeout(self):
        """Test default timeout value"""
        assert DEFAULT_SESSION_TIMEOUT == 30.0

    def test_shared_access_timeout(self):
        """Test shared access timeout value"""
        assert SHARED_ACCESS_TIMEOUT == 5.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
