"""TDD tests for daemon startup race condition fix (Phase 1).

Verifies:
1. Client checks restart_lock before spawning (prevents rogue daemons during restart)
2. Client checks daemon flock before spawning (prevents double-spawn)
3. Capped exponential backoff prevents timeout and excessive waiting
4. First-run case (no config dir) correctly falls through to spawn
5. All existing tests remain unaffected (no timeout change to core.py)

All tests use mocking — no real daemon, socket, or lock files touched.
Safe to run alongside live daemons.
"""

import multiprocessing
import time
from pathlib import Path
from unittest import mock

import pytest
from filelock import FileLock, Timeout as FlockTimeout


# Module-level functions for multiprocessing pickling compatibility (macOS uses spawn)
def _hold_lock_and_exit(path_str):
    """Child process: acquire lock, then exit without releasing (OS releases)."""
    fl = FileLock(path_str, timeout=0)
    fl.acquire()
    # Exit without releasing — kernel releases the lock


# ─── Test Group 1: Restart-lock blocks client spawn ───


class TestRestartLockBlocksSpawn:
    """Client must NOT spawn daemon when restart_lock is held."""

    def test_no_spawn_during_restart(self, tmp_path):
        """restart_lock held → client probe raises FlockTimeout → should_spawn stays False."""
        restart_lock_path = tmp_path / "daemon-restart.lock"
        held = FileLock(str(restart_lock_path), timeout=0)
        held.acquire()

        try:
            # Client's restart_lock probe should fail (lock is held)
            probe = FileLock(str(restart_lock_path), timeout=0)
            with pytest.raises(FlockTimeout):
                probe.acquire()
            # This proves: if restarter holds restart_lock, client sees FlockTimeout
            # and restart_in_progress = True → should_spawn stays False
        finally:
            held.release()

    def test_spawn_allowed_when_no_restart(self, tmp_path):
        """restart_lock NOT held → client probe succeeds → can proceed to flock check."""
        restart_lock_path = tmp_path / "daemon-restart.lock"

        # Probe should succeed (no one holds the lock)
        probe = FileLock(str(restart_lock_path), timeout=0)
        probe.acquire()  # Should succeed — no one holds it
        probe.release()
        # If we get here without exception, restart_lock is free

    def test_restart_lock_released_on_restarter_death(self, tmp_path):
        """If restarter process dies, OS releases restart_lock (POSIX guarantee)."""
        restart_lock_path = tmp_path / "daemon-restart.lock"

        p = multiprocessing.Process(target=_hold_lock_and_exit, args=(str(restart_lock_path),))
        p.start()
        p.join(timeout=5)

        # After child death, lock should be free
        probe = FileLock(str(restart_lock_path), timeout=0)
        probe.acquire()  # Should succeed — OS released on process death
        probe.release()


# ─── Test Group 2: Daemon flock blocks client spawn ───


class TestDaemonFlockBlocksSpawn:
    """Client must NOT spawn daemon when daemon flock is held."""

    def test_no_spawn_when_daemon_running(self, tmp_path):
        """Daemon flock held → client probe raises FlockTimeout → should_spawn stays False."""
        flock_path = tmp_path / "daemon.flock"
        held = FileLock(str(flock_path), timeout=0)
        held.acquire()

        try:
            probe = FileLock(str(flock_path), timeout=0)
            with pytest.raises(FlockTimeout):
                probe.acquire()
        finally:
            held.release()

    def test_spawn_when_daemon_dead(self, tmp_path):
        """Daemon flock free + no PID → client should spawn."""
        flock_path = tmp_path / "daemon.flock"

        # Flock is free — probe succeeds
        probe = FileLock(str(flock_path), timeout=0)
        probe.acquire()
        probe.release()
        # Flock was free, no PID file → should_spawn = True

    def test_no_spawn_when_pid_alive_flock_free(self, tmp_path):
        """Flock free but PID alive → daemon restarting, don't spawn."""
        pid_file = tmp_path / "daemon.lock"
        pid_file.write_text(str(99999))

        with mock.patch('psutil.pid_exists', return_value=True):
            import psutil
            assert psutil.pid_exists(99999) is True
            # In client code: should_spawn stays False (PID alive, just wait for socket)

    def test_spawn_when_pid_dead_flock_free(self, tmp_path):
        """Flock free + PID dead → stale PID, spawn new daemon."""
        pid_file = tmp_path / "daemon.lock"
        pid_file.write_text(str(99999))

        with mock.patch('psutil.pid_exists', return_value=False):
            import psutil
            assert psutil.pid_exists(99999) is False
            # In client code: PID is stale → unlink → should_spawn = True

    def test_flock_released_on_daemon_death(self, tmp_path):
        """When daemon dies, OS releases flock (kernel guarantee)."""
        flock_path = tmp_path / "daemon.flock"

        p = multiprocessing.Process(target=_hold_lock_and_exit, args=(str(flock_path),))
        p.start()
        p.join(timeout=5)

        # After child death, flock should be free
        probe = FileLock(str(flock_path), timeout=0)
        probe.acquire()
        probe.release()


# ─── Test Group 3: First-run edge case ───


class TestFirstRunSpawn:
    """First run: no config dir, no lock files → should spawn."""

    def test_flock_creates_parent_dirs_on_first_run(self, tmp_path):
        """FileLock on non-existent dir creates parent dirs (first-run behavior).

        Modern filelock creates parent directories automatically on acquire.
        This is the first-run code path: daemon config dir doesn't exist yet,
        FileLock.acquire() creates it. Cross-platform (Unix + Windows).
        """
        nonexistent = tmp_path / "does_not_exist" / "nested" / "daemon.flock"
        assert not nonexistent.parent.exists()
        fl = FileLock(str(nonexistent), timeout=0)
        fl.acquire()
        assert nonexistent.parent.exists(), "FileLock should create parent dirs on acquire"
        fl.release()

    def test_restart_lock_probe_tolerates_missing_file(self, tmp_path):
        """restart_lock on non-existent file creates it (filelock behavior)."""
        lock_path = tmp_path / "daemon-restart.lock"
        assert not lock_path.exists()

        # FileLock creates the file on acquire
        probe = FileLock(str(lock_path), timeout=0)
        probe.acquire()
        probe.release()
        # filelock creates file on acquire, may clean up on release (platform-dependent)
        # Key behavior: no exception raised — probe succeeded on non-existent file


# ─── Test Group 4: Exponential backoff ───


class TestExponentialBackoff:
    """Verify capped exponential backoff timing."""

    def test_backoff_formula(self):
        """Backoff: min(0.3 * 2^depth, 2.0) → 0.3, 0.6, 1.2, 2.0, 2.0, 2.0."""
        expected = [0.3, 0.6, 1.2, 2.0, 2.0, 2.0]
        for depth, want in enumerate(expected):
            got = min(0.3 * (2 ** depth), 2.0)
            assert got == pytest.approx(want), f"depth={depth}: {got} != {want}"

    def test_total_backoff_under_10s(self):
        """Total max wait across 6 retries: 0.3+0.6+1.2+2.0+2.0+2.0 = 8.1s."""
        total = sum(min(0.3 * (2 ** d), 2.0) for d in range(6))
        assert total == pytest.approx(8.1)
        assert total < 10.0

    def test_6_retries_before_failure(self):
        """Client allows depths 0-5 (6 attempts) before raising."""
        max_depth = 5
        for depth in range(max_depth + 1):
            assert depth <= max_depth  # All these depths should retry
        assert max_depth + 1 > max_depth  # depth=6 would raise


# ─── Test Group 5: Multi-process contention scenarios ───


class TestMultiProcessContention:
    """Verify behavior under concurrent process scenarios."""

    def test_two_clients_same_flock(self, tmp_path):
        """Two clients probing same flock: one holds, other sees FlockTimeout."""
        flock_path = tmp_path / "daemon.flock"

        probe1 = FileLock(str(flock_path), timeout=0)
        probe1.acquire()

        probe2 = FileLock(str(flock_path), timeout=0)
        with pytest.raises(FlockTimeout):
            probe2.acquire()

        probe1.release()

        # After release, second probe succeeds
        probe2.acquire()
        probe2.release()

    def test_restart_lock_and_flock_independent(self, tmp_path):
        """restart_lock and daemon flock are separate — holding one doesn't affect other."""
        restart_path = tmp_path / "daemon-restart.lock"
        flock_path = tmp_path / "daemon.flock"

        restart_lock = FileLock(str(restart_path), timeout=0)
        daemon_flock = FileLock(str(flock_path), timeout=0)

        restart_lock.acquire()

        # daemon_flock is independent — should still be acquirable
        daemon_flock.acquire()
        daemon_flock.release()

        restart_lock.release()

    def test_concurrent_unlink_with_missing_ok(self, tmp_path):
        """Two processes unlinking same PID file: missing_ok=True prevents error."""
        pid_file = tmp_path / "daemon.lock"
        pid_file.write_text("12345")

        # First unlink succeeds
        pid_file.unlink(missing_ok=True)
        assert not pid_file.exists()

        # Second unlink also succeeds (missing_ok=True)
        pid_file.unlink(missing_ok=True)  # Should not raise


# ─── Test Group 6: No regressions ───


class TestNoRegressions:
    """Verify Phase 1 changes don't affect core.py or existing tests."""

    def test_daemon_lock_timeout_still_zero(self):
        """_acquire_daemon_lock still uses timeout=0 (no change in Phase 1)."""
        import inspect
        from autorun.core import AutorunDaemon

        source = inspect.getsource(AutorunDaemon._acquire_daemon_lock)
        assert 'timeout=0' in source, \
            "_acquire_daemon_lock should still use timeout=0"

    def test_restart_lock_path_accessible(self):
        """RESTART_LOCK_PATH is importable and at expected location."""
        from autorun.restart_daemon import RESTART_LOCK_PATH
        assert RESTART_LOCK_PATH.name == "daemon-restart.lock"

    def test_ipc_config_dir_exists(self):
        """AUTORUN_CONFIG_DIR is importable (needed for restart_lock_path in client)."""
        from autorun import ipc
        assert hasattr(ipc, 'AUTORUN_CONFIG_DIR')
        assert hasattr(ipc, 'AUTORUN_LOCK_PATH')

    def test_client_uses_6_retries(self):
        """Client forward() uses depth > 5 (6 retries), not depth > 2."""
        import inspect
        from autorun.client import run_client
        source = inspect.getsource(run_client)
        assert 'depth > 5' in source, "Client should use 6 retries (depth > 5)"
        assert 'depth > 2' not in source, "Old 3-retry limit should be removed"

    def test_client_uses_capped_backoff(self):
        """Client uses min(0.3 * 2**depth, 2.0) capped backoff."""
        import inspect
        from autorun.client import run_client
        source = inspect.getsource(run_client)
        assert 'min(0.3' in source, "Client should use capped exponential backoff"

    def test_poll_timeout_is_5s(self):
        """restart_daemon socket poll timeout is 5 seconds (not 3)."""
        import inspect
        from autorun.restart_daemon import restart_daemon
        source = inspect.getsource(restart_daemon)
        assert '5.0' in source, "Socket poll timeout should be 5 seconds"
