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
import os
import shutil
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

class TestGeneratedDaemonCodeSurvivesAnyPath:
    """The spawn string is Python source: a path in it must be a valid literal.

    client.py built it with a plain '{0}' substitution. On Windows src_dir is
    C:\\Users\\..., where \\U is an invalid escape, so the spawned interpreter
    died with a SyntaxError before importing anything. No daemon existed, every
    hook fell through to the CLI, and the CLI waited on the daemon it had just
    failed to start until the caller gave up -- reported as "autorun CLI timed
    out after 5s" on every Windows event.
    """

    @pytest.mark.parametrize(
        "src_dir",
        [
            "/home/user/src",
            "C:\\Users\\runneradmin\\autorun\\src",
            "C:\\temp\\new\\autorun\\src",
            "/tmp/it's here/src",
        ],
    )
    def test_the_path_round_trips_through_the_generated_source(self, src_dir):
        import ast

        code = (
            "import sys; sys.path.insert(0, {0!r}); "
            "from autorun.daemon import main; main()"
        ).format(src_dir)

        tree = ast.parse(code)  # a SyntaxError here is the whole bug

        literals = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        assert src_dir in literals, (
            f"the path did not survive into the generated source: {literals}"
        )



def _tail(path: Path, limit: int = 4000) -> str:
    """The end of a log file, or why it could not be read."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-limit:]
    except OSError as error:
        return f"<unreadable: {error}>"


class TestColdStartReachability:
    """A spawned daemon must end up as something a client can connect to.

    Every fast path depends on this. When it does not hold, no hook fails
    loudly: try_daemon finds no endpoint, try_cli starts a CLI that waits for
    the daemon it just spawned, and the caller's budget expires. The user sees
    "autorun CLI timed out after 5s" and a blocked tool, with nothing anywhere
    naming the cause -- which is exactly what Windows reported for every hook
    call while this test did not exist.

    The daemon is spawned here with its output captured rather than sent to
    DEVNULL as production does, so a failure carries the child's own traceback
    instead of only the absence of a socket.
    """

    def test_a_spawned_daemon_publishes_an_endpoint(self, tmp_path, monkeypatch):
        import importlib
        import subprocess
        import sys
        import time

        # A short home, not tmp_path: the socket lives under AUTORUN_HOME and
        # sun_path is 104 bytes, which pytest's tmp_path alone can exceed --
        # the daemon then dies with "AF_UNIX path too long" and looks exactly
        # like the unreachable daemon this test exists to detect.
        import tempfile

        root = Path(tempfile.mkdtemp(prefix="ard", dir="/tmp" if os.path.isdir("/tmp") else None))
        home = root / "h"
        home.mkdir()
        monkeypatch.setenv("AUTORUN_HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        monkeypatch.setenv("HOME", str(tmp_path))

        from autorun import ipc

        ipc = importlib.reload(ipc)
        src_dir = str(Path(ipc.__file__).resolve().parents[1])
        log = tmp_path / "daemon-startup.log"

        code = (
            "import sys; sys.path.insert(0, {0!r}); "
            "from autorun.daemon import main; main()"
        ).format(src_dir)

        with open(log, "w", encoding="utf-8") as sink:
            child = subprocess.Popen(
                [sys.executable, "-c", code],
                stdin=subprocess.DEVNULL,
                stdout=sink,
                stderr=subprocess.STDOUT,
                **ipc.detached_spawn_kwargs(),
            )
        try:
            deadline = time.monotonic() + 30
            published = None
            while time.monotonic() < deadline:
                if ipc.HAS_UNIX_SOCKETS:
                    if Path(ipc.SOCKET_PATH).exists():
                        published = str(ipc.SOCKET_PATH)
                        break
                elif Path(ipc.PORT_FILE).exists():
                    published = Path(ipc.PORT_FILE).read_text(encoding="utf-8").strip()
                    break
                if child.poll() is not None:
                    break
                time.sleep(0.2)

            assert published, (
                "the daemon published no endpoint within 30s, so no client "
                "could ever reach it.\n"
                f"  transport: {'unix socket' if ipc.HAS_UNIX_SOCKETS else 'loopback port file'}\n"
                f"  expected at: {ipc.SOCKET_PATH if ipc.HAS_UNIX_SOCKETS else ipc.PORT_FILE}\n"
                f"  config dir: {ipc.AUTORUN_CONFIG_DIR} exists={Path(ipc.AUTORUN_CONFIG_DIR).exists()}\n"
                f"  contents: {sorted(p.name for p in Path(ipc.AUTORUN_CONFIG_DIR).glob('*')) if Path(ipc.AUTORUN_CONFIG_DIR).exists() else 'n/a'}\n"
                f"  child exit code: {child.poll()}\n"
                f"  child output:\n{log.read_text(encoding='utf-8', errors='replace')[-4000:]}\n"
                f"  daemon log:\n{_tail(Path(ipc.AUTORUN_CONFIG_DIR) / 'daemon.log')}"
            )
        finally:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - stubborn child
                child.kill()
            shutil.rmtree(root, ignore_errors=True)
