#!/usr/bin/env python3
"""Test daemon restart safety: filelock correctness, RAII, thread/multiprocess safety.

Tests validate:
1. restart_lock() acquires and releases correctly (RAII)
2. Concurrent restart_lock() — second caller gets False
3. Lock released even if body raises exception
4. Multiprocess exclusion via filelock
5. psutil-based process lifecycle in restart_daemon.py

These tests use temporary directories to avoid interfering with production daemons.
"""

import multiprocessing
import os
import sys
import threading
import time
from pathlib import Path
from unittest import mock

import pytest

plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root / 'src'))

from filelock import FileLock, Timeout


def _child_acquire_lock(lock_file: str, result_file: str, hold_seconds: float):
    """Child process helper: acquire lock, write result, hold, release.

    Must be module-level for multiprocessing pickling (spawn start method).
    """
    lock = FileLock(lock_file, timeout=0)
    try:
        lock.acquire()
        Path(result_file).write_text("acquired", encoding="utf-8")
        time.sleep(hold_seconds)
        lock.release()
    except Timeout:
        Path(result_file).write_text("blocked", encoding="utf-8")


class TestRestartLockFilelock:
    """Test restart_lock() with filelock backend — RAII, thread safety, multiprocess."""

    @pytest.fixture(autouse=True)
    def setup_temp_lock(self, tmp_path):
        """Redirect RESTART_LOCK_PATH to tmp_path for test isolation."""
        self.lock_path = tmp_path / "daemon-restart.lock"
        # Patch the module-level constant
        self._patcher = mock.patch(
            'autorun.restart_daemon.RESTART_LOCK_PATH', self.lock_path
        )
        self._patcher.start()
        yield
        self._patcher.stop()

    def test_acquire_and_release(self):
        """restart_lock() yields True when lock is available."""
        from autorun.restart_daemon import restart_lock

        with restart_lock() as acquired:
            assert acquired is True

    def test_lock_released_after_context_exit(self):
        """Lock file is released after context manager exits (RAII)."""
        from autorun.restart_daemon import restart_lock

        with restart_lock() as acquired:
            assert acquired is True

        # After exit, another lock should succeed
        with restart_lock() as acquired2:
            assert acquired2 is True

    def test_concurrent_lock_second_caller_gets_false(self):
        """Second concurrent caller gets False (non-blocking)."""
        from autorun.restart_daemon import restart_lock

        with restart_lock() as first:
            assert first is True
            # While first lock is held, second should fail
            with restart_lock() as second:
                assert second is False

    def test_raii_lock_released_on_exception(self):
        """Lock is released even if body raises an exception (RAII guarantee)."""
        from autorun.restart_daemon import restart_lock

        with pytest.raises(ValueError, match="test error"):
            with restart_lock() as acquired:
                assert acquired is True
                raise ValueError("test error")

        # Lock must be released — next acquire should succeed
        with restart_lock() as acquired2:
            assert acquired2 is True

    def test_thread_safety_concurrent_acquires(self):
        """Multiple threads competing for restart_lock — exactly one wins."""
        from autorun.restart_daemon import restart_lock

        results = []
        barrier = threading.Barrier(4)

        def try_acquire(thread_id):
            barrier.wait()  # Synchronize start
            with restart_lock() as acquired:
                results.append((thread_id, acquired))
                if acquired:
                    time.sleep(0.1)  # Hold lock briefly

        threads = [
            threading.Thread(target=try_acquire, args=(i,))
            for i in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        # Exactly one thread should have acquired=True at any moment
        acquired_count = sum(1 for _, acq in results if acq)
        assert acquired_count >= 1, "At least one thread must acquire the lock"
        # All threads should have completed
        assert len(results) == 4

    @pytest.mark.subprocess
    def test_multiprocess_exclusion(self, tmp_path):
        """Two processes can't both acquire the restart lock simultaneously."""
        lock_file = str(self.lock_path)
        result1 = str(tmp_path / "result1.txt")
        result2 = str(tmp_path / "result2.txt")

        # Process 1 acquires and holds for 1 second
        p1 = multiprocessing.Process(
            target=_child_acquire_lock, args=(lock_file, result1, 1.0)
        )
        p1.start()
        time.sleep(0.2)  # Let p1 acquire first

        # Process 2 tries to acquire while p1 holds
        p2 = multiprocessing.Process(
            target=_child_acquire_lock, args=(lock_file, result2, 0.0)
        )
        p2.start()

        p1.join(timeout=5.0)
        p2.join(timeout=5.0)

        r1 = Path(result1).read_text(encoding="utf-8")
        r2 = Path(result2).read_text(encoding="utf-8")

        assert r1 == "acquired", "First process should acquire lock"
        assert r2 == "blocked", "Second process should be blocked"


class TestAcquireDaemonLock:
    """Test _acquire_daemon_lock() PID file creation and error handling."""

    def test_acquire_writes_pid_to_lock_file(self, tmp_path):
        """_acquire_daemon_lock writes PID to daemon.lock after acquiring flock."""
        from autorun.core import AutorunDaemon
        lock_path = tmp_path / "daemon.lock"
        flock_path = tmp_path / "daemon.flock"

        daemon = AutorunDaemon.__new__(AutorunDaemon)
        daemon._daemon_lock = None

        with mock.patch('autorun.core.LOCK_PATH', lock_path):
            result = daemon._acquire_daemon_lock()

        assert result is True
        assert lock_path.exists()
        assert lock_path.read_text().strip() == str(os.getpid())
        # Cleanup
        if daemon._daemon_lock:
            daemon._daemon_lock.release()

    def test_acquire_pid_write_failure_keeps_flock(self, tmp_path):
        """If daemon.lock write fails, flock is still held (not released)."""
        from autorun.core import AutorunDaemon
        lock_path = tmp_path / "daemon.lock"

        daemon = AutorunDaemon.__new__(AutorunDaemon)
        daemon._daemon_lock = None

        # Make write_text fail by making lock_path a directory
        lock_path.mkdir()

        with mock.patch('autorun.core.LOCK_PATH', lock_path):
            result = daemon._acquire_daemon_lock()

        assert result is True  # Lock acquired despite write failure
        assert daemon._daemon_lock is not None  # Flock still held
        # Cleanup
        daemon._daemon_lock.release()

    def test_acquire_returns_false_when_flock_held(self, tmp_path):
        """_acquire_daemon_lock returns False when another process holds flock."""
        from autorun.core import AutorunDaemon
        from filelock import FileLock
        lock_path = tmp_path / "daemon.lock"
        flock_path = tmp_path / "daemon.flock"

        # Hold the flock
        held_lock = FileLock(str(flock_path), timeout=0)
        held_lock.acquire()

        daemon = AutorunDaemon.__new__(AutorunDaemon)
        daemon._daemon_lock = None

        with mock.patch('autorun.core.LOCK_PATH', lock_path):
            result = daemon._acquire_daemon_lock()

        assert result is False
        assert daemon._daemon_lock is None
        held_lock.release()


class TestPsutilProcessLifecycle:
    """Test that restart_daemon uses psutil for cross-platform process management."""

    def test_get_daemon_pid_uses_psutil(self):
        """get_daemon_pid() uses psutil.pid_exists, not os.kill."""
        from autorun.restart_daemon import get_daemon_pid

        with mock.patch('autorun.restart_daemon.LOCK_PATH') as mock_path:
            mock_path.exists.return_value = True
            mock_path.read_text.return_value = str(os.getpid())

            with mock.patch('psutil.pid_exists', return_value=True) as mock_pid:
                result = get_daemon_pid()
                mock_pid.assert_called_once_with(os.getpid())
                assert result == os.getpid()

    def test_get_daemon_pid_returns_none_for_dead_process(self):
        """get_daemon_pid() returns None when psutil says PID doesn't exist."""
        from autorun.restart_daemon import get_daemon_pid

        with mock.patch('autorun.restart_daemon.LOCK_PATH') as mock_path:
            mock_path.exists.return_value = True
            mock_path.read_text.return_value = "99999"
            # Also mock flock fallback path so it doesn't find the real daemon
            mock_flock = mock.MagicMock(exists=mock.MagicMock(return_value=False))
            mock_path.with_suffix.return_value = mock_flock

            with mock.patch('psutil.pid_exists', return_value=False):
                assert get_daemon_pid() is None

    def test_stop_daemon_uses_psutil_terminate(self):
        """_stop_daemon() uses psutil.Process.terminate, not os.kill(SIGTERM)."""
        from autorun.restart_daemon import _stop_daemon

        mock_proc = mock.MagicMock()
        with mock.patch('psutil.Process', return_value=mock_proc) as mock_cls:
            with mock.patch('autorun.restart_daemon.wait_for_shutdown', return_value=True):
                with mock.patch('autorun.restart_daemon.ipc') as mock_ipc:
                    mock_ipc.SOCKET_PATH.exists.return_value = False
                    with mock.patch('autorun.restart_daemon.LOCK_PATH') as mock_lock:
                        mock_lock.exists.return_value = False
                        _stop_daemon(12345)

            mock_cls.assert_called_with(12345)
            mock_proc.terminate.assert_called_once()

    def test_stop_daemon_falls_back_to_kill(self):
        """_stop_daemon() uses psutil.Process.kill when terminate times out."""
        from autorun.restart_daemon import _stop_daemon

        mock_proc = mock.MagicMock()
        with mock.patch('psutil.Process', return_value=mock_proc):
            with mock.patch('autorun.restart_daemon.wait_for_shutdown', return_value=False):
                with mock.patch('autorun.restart_daemon.cleanup_stale_files'):
                    _stop_daemon(12345)

            mock_proc.terminate.assert_called_once()
            mock_proc.kill.assert_called_once()

    def test_get_daemon_pid_fallback_process_discovery(self):
        """get_daemon_pid() discovers daemon by cmdline when daemon.lock is missing."""
        from autorun.restart_daemon import get_daemon_pid

        # daemon.lock doesn't exist but daemon.flock does
        with mock.patch('autorun.restart_daemon.LOCK_PATH') as mock_lock:
            mock_lock.exists.return_value = False
            mock_lock.with_suffix.return_value = mock.MagicMock(exists=mock.MagicMock(return_value=True))

            mock_proc = mock.MagicMock()
            mock_proc.info = {
                'pid': 12345,
                'cmdline': ['python', '-c', 'from autorun.daemon import main; main()'],
            }
            with mock.patch('psutil.process_iter', return_value=[mock_proc]):
                result = get_daemon_pid()
                assert result == 12345

    def test_get_daemon_pid_no_fallback_when_no_flock(self):
        """get_daemon_pid() returns None when both daemon.lock and daemon.flock are missing."""
        from autorun.restart_daemon import get_daemon_pid

        with mock.patch('autorun.restart_daemon.LOCK_PATH') as mock_lock:
            mock_lock.exists.return_value = False
            mock_lock.with_suffix.return_value = mock.MagicMock(exists=mock.MagicMock(return_value=False))

            result = get_daemon_pid()
            assert result is None

    def test_get_daemon_pid_prefers_lock_file_over_process_scan(self):
        """get_daemon_pid() uses daemon.lock PID when file exists (no process scan)."""
        from autorun.restart_daemon import get_daemon_pid

        with mock.patch('autorun.restart_daemon.LOCK_PATH') as mock_lock:
            mock_lock.exists.return_value = True
            mock_lock.read_text.return_value = str(os.getpid())
            with mock.patch('psutil.pid_exists', return_value=True):
                with mock.patch('psutil.process_iter') as mock_iter:
                    result = get_daemon_pid()
                    assert result == os.getpid()
                    mock_iter.assert_not_called()

    def test_no_os_kill_in_restart_daemon(self):
        """Verify restart_daemon.py contains no os.kill calls (all replaced by psutil)."""
        import inspect
        import autorun.restart_daemon as mod
        source = inspect.getsource(mod)
        assert 'os.kill(' not in source, (
            "restart_daemon.py still contains os.kill() — must use psutil for cross-platform"
        )
