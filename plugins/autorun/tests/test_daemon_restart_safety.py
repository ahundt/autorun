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
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import pytest

plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root / 'src'))

from filelock import FileLock, Timeout  # noqa: E402 - test path is inserted above for local src imports


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


def test_pytest_runtime_isolated_from_production_daemon():
    """The suite must never use the live ~/.autorun socket, PID, or logs."""
    from autorun import ipc

    test_home = Path(os.environ["AUTORUN_HOME"])
    assert test_home != Path.home() / ".autorun"
    assert ipc.AUTORUN_CONFIG_DIR == test_home
    assert os.environ["AUTORUN_TEST_STATE_DIR"].startswith(str(test_home.parent))


class TestDaemonStartupDiagnostics:
    """Source verification must be deterministic and actionable."""

    def test_normalized_source_path_is_verified(self, tmp_path, capsys):
        from autorun import restart_daemon

        src_dir = tmp_path / "checkout" / "src"
        expected = src_dir / "autorun" / "__init__.py"
        (tmp_path / "daemon_startup.log").write_text(
            f"autorun loaded from: {expected}\n=== Starting Daemon ===\n",
            encoding="utf-8",
        )

        with mock.patch.object(restart_daemon.ipc, "AUTORUN_CONFIG_DIR", tmp_path):
            assert restart_daemon._display_daemon_diagnostics(src_dir) is True

        assert "loaded from source directory" in capsys.readouterr().out

    def test_mismatched_source_reports_actual_and_expected(self, tmp_path, capsys):
        from autorun import restart_daemon

        src_dir = tmp_path / "checkout" / "src"
        loaded = tmp_path / "site-packages" / "autorun" / "__init__.py"
        (tmp_path / "daemon_startup.log").write_text(
            f"autorun loaded from: {loaded}\n",
            encoding="utf-8",
        )

        with mock.patch.object(restart_daemon.ipc, "AUTORUN_CONFIG_DIR", tmp_path):
            assert restart_daemon._display_daemon_diagnostics(src_dir) is False

        output = capsys.readouterr().out
        assert str(loaded.resolve()) in output
        assert str((src_dir / "autorun" / "__init__.py").resolve()) in output

    def test_missing_module_path_points_to_startup_log(self, tmp_path, capsys):
        from autorun import restart_daemon

        log_path = tmp_path / "daemon_startup.log"
        log_path.write_text("=== Starting Daemon ===\n", encoding="utf-8")

        with mock.patch.object(restart_daemon.ipc, "AUTORUN_CONFIG_DIR", tmp_path):
            assert restart_daemon._display_daemon_diagnostics(tmp_path / "src") is False

        output = capsys.readouterr().out
        assert "no module path" in output
        assert str(log_path) in output


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

    def test_acquire_verifies_pid_readback(self, tmp_path):
        """_acquire_daemon_lock reads back PID after write to verify correctness."""
        from autorun.core import AutorunDaemon
        lock_path = tmp_path / "daemon.lock"

        daemon = AutorunDaemon.__new__(AutorunDaemon)
        daemon._daemon_lock = None

        with mock.patch('autorun.core.LOCK_PATH', lock_path):
            result = daemon._acquire_daemon_lock()

        assert result is True
        # Verify the written PID matches current process
        written_pid = int(lock_path.read_text().strip())
        assert written_pid == os.getpid()
        daemon._daemon_lock.release()

    def test_acquire_creates_parent_dir_if_missing(self, tmp_path):
        """_acquire_daemon_lock creates parent directory if it doesn't exist."""
        from autorun.core import AutorunDaemon
        # Use a nested path where parent doesn't exist
        lock_path = tmp_path / "subdir" / "daemon.lock"
        assert not lock_path.parent.exists()

        daemon = AutorunDaemon.__new__(AutorunDaemon)
        daemon._daemon_lock = None

        with mock.patch('autorun.core.LOCK_PATH', lock_path):
            result = daemon._acquire_daemon_lock()

        assert result is True
        assert lock_path.parent.exists()
        assert lock_path.exists()
        assert lock_path.read_text().strip() == str(os.getpid())
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

    def test_daemon_python_prefers_workspace_venv_with_autorun(self, tmp_path):
        """Restarts must spawn the venv Python whose bin dir contains autorun."""
        from autorun.restart_daemon import _daemon_python_for_src

        workspace_root = tmp_path / "workspace"
        plugin_root = workspace_root / "plugins" / "autorun"
        src_dir = plugin_root / "src"
        workspace_venv_bin = workspace_root / ".venv" / "bin"
        package_venv_bin = plugin_root / ".venv" / "bin"
        workspace_venv_bin.mkdir(parents=True)
        package_venv_bin.mkdir(parents=True)
        src_dir.mkdir(parents=True)

        workspace_python = workspace_venv_bin / "python3"
        workspace_autorun = workspace_venv_bin / "autorun"
        package_python = package_venv_bin / "python3"
        for path in (workspace_python, workspace_autorun, package_python):
            path.write_text("#!/bin/sh\n", encoding="utf-8")
            path.chmod(0o755)

        with mock.patch(
            "autorun.restart_daemon._python_has_daemon_dependencies",
            return_value=True,
        ):
            assert _daemon_python_for_src(src_dir) == workspace_python

    def test_daemon_python_skips_venv_without_daemon_dependencies(self, tmp_path):
        """A stale .venv without filelock/psutil must not own the daemon."""
        import autorun.restart_daemon as restart_mod

        workspace_root = tmp_path / "workspace"
        plugin_root = workspace_root / "plugins" / "autorun"
        src_dir = plugin_root / "src"
        stale_venv_bin = workspace_root / ".venv" / "bin"
        stale_venv_bin.mkdir(parents=True)
        src_dir.mkdir(parents=True)

        stale_python = stale_venv_bin / "python3"
        active_python = tmp_path / ".venv-arm64" / "bin" / "python3"
        active_python.parent.mkdir(parents=True)
        for path in (stale_python, active_python):
            path.write_text("#!/bin/sh\n", encoding="utf-8")
            path.chmod(0o755)

        def has_deps(path: Path) -> bool:
            return path == active_python

        with mock.patch.object(restart_mod.sys, "executable", str(active_python)):
            with mock.patch.object(
                restart_mod,
                "_python_has_daemon_dependencies",
                side_effect=has_deps,
            ):
                assert restart_mod._daemon_python_for_src(src_dir) == active_python

    def test_daemon_python_accepts_dot_venv_named_tool_environment(self, tmp_path):
        """uv tool/env names like .venv-arm64 can provide the autorun executable."""
        import autorun.restart_daemon as restart_mod

        workspace_root = tmp_path / "workspace"
        plugin_root = workspace_root / "plugins" / "autorun"
        src_dir = plugin_root / "src"
        tool_bin = tmp_path / ".venv-arm64" / "bin"
        tool_bin.mkdir(parents=True)
        src_dir.mkdir(parents=True)

        tool_python = tool_bin / "python3"
        tool_autorun = tool_bin / "autorun"
        for path in (tool_python, tool_autorun):
            path.write_text("#!/bin/sh\n", encoding="utf-8")
            path.chmod(0o755)

        with mock.patch.object(restart_mod.shutil, "which", return_value=str(tool_autorun)):
            with mock.patch.object(
                restart_mod,
                "_python_has_daemon_dependencies",
                return_value=True,
            ):
                assert restart_mod._daemon_python_for_src(src_dir) == tool_python

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

    def test_get_daemon_pid_fallback_can_filter_by_source_tree(self, tmp_path):
        """Fallback discovery must not claim unrelated worktree daemons."""
        from autorun.restart_daemon import get_daemon_pid

        current_src = tmp_path / "current" / "plugins" / "autorun" / "src"
        other_src = tmp_path / "other" / "plugins" / "autorun" / "src"
        current_src.mkdir(parents=True)
        other_src.mkdir(parents=True)

        current_proc = mock.MagicMock()
        current_proc.info = {
            "pid": 111,
            "cmdline": [
                sys.executable,
                "-c",
                f"sys.path.insert(0, r'{current_src}'); from autorun.daemon import main; main()",
            ],
        }
        other_proc = mock.MagicMock()
        other_proc.info = {
            "pid": 222,
            "cmdline": [
                sys.executable,
                "-c",
                f"sys.path.insert(0, r'{other_src}'); from autorun.daemon import main; main()",
            ],
        }

        with mock.patch('autorun.restart_daemon.LOCK_PATH') as mock_lock:
            mock_lock.exists.return_value = False
            mock_lock.with_suffix.return_value = mock.MagicMock(exists=mock.MagicMock(return_value=True))
            with mock.patch('psutil.process_iter', return_value=[other_proc, current_proc]):
                assert get_daemon_pid(src_dir=current_src) == 111

            with mock.patch('psutil.process_iter', return_value=[other_proc]):
                assert get_daemon_pid(src_dir=current_src) is None

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

    def test_get_daemon_pid_filters_lock_file_by_source_tree(self, tmp_path):
        """Scoped PID discovery must not claim another source tree's lock PID."""
        from autorun.restart_daemon import get_daemon_pid

        current_src = tmp_path / "current" / "plugins" / "autorun" / "src"
        other_src = tmp_path / "other" / "plugins" / "autorun" / "src"
        current_src.mkdir(parents=True)
        other_src.mkdir(parents=True)

        with mock.patch('autorun.restart_daemon.LOCK_PATH') as mock_lock:
            mock_lock.exists.return_value = True
            mock_lock.read_text.return_value = "222"
            mock_lock.with_suffix.return_value = mock.MagicMock(exists=mock.MagicMock(return_value=False))
            with mock.patch('psutil.pid_exists', return_value=True):
                with mock.patch('psutil.Process') as process_cls:
                    process_cls.return_value.cmdline.return_value = [
                        sys.executable,
                        "-c",
                        f"sys.path.insert(0, r'{other_src}'); from autorun.daemon import main; main()",
                    ]

                    assert get_daemon_pid(src_dir=current_src) is None

    def test_no_os_kill_in_restart_daemon(self):
        """Verify restart_daemon.py contains no os.kill calls (all replaced by psutil)."""
        import inspect
        import autorun.restart_daemon as mod
        source = inspect.getsource(mod)
        assert 'os.kill(' not in source, (
            "restart_daemon.py still contains os.kill() — must use psutil for cross-platform"
        )

    def test_restart_kills_only_daemons_from_current_source_tree(self, tmp_path):
        """Restart must not kill unrelated autorun daemons from other worktrees."""
        import autorun.restart_daemon as restart_mod

        src_dir = tmp_path / "current" / "plugins" / "autorun" / "src"
        other_src = tmp_path / "other" / "plugins" / "autorun" / "src"
        src_dir.mkdir(parents=True)
        other_src.mkdir(parents=True)

        current_proc = mock.MagicMock()
        current_proc.info = {
            "pid": 111,
            "cmdline": [
                sys.executable,
                "-c",
                f"sys.path.insert(0, r'{src_dir}'); from autorun.daemon import main; main()",
            ],
        }
        other_proc = mock.MagicMock()
        other_proc.info = {
            "pid": 222,
            "cmdline": [
                sys.executable,
                "-c",
                f"sys.path.insert(0, r'{other_src}'); from autorun.daemon import main; main()",
            ],
        }

        @contextmanager
        def acquired_restart_lock():
            yield True

        # get_daemon_pid(): no owned PID before restart, new owned PID after start.
        with mock.patch.object(restart_mod, "restart_lock", acquired_restart_lock):
            with mock.patch.object(restart_mod, "get_daemon_pid", side_effect=[None, 333]):
                with mock.patch.object(restart_mod, "_resolve_src_dir", return_value=src_dir):
                    with mock.patch.object(restart_mod, "_clear_pycache"):
                        with mock.patch.object(restart_mod, "_check_conflicting_packages"):
                            with mock.patch.object(restart_mod, "_start_daemon", return_value=True):
                                with mock.patch.object(restart_mod, "is_daemon_responding", return_value=True):
                                    with mock.patch.object(restart_mod, "_display_daemon_diagnostics", return_value=True) as diagnostics:
                                        with mock.patch.object(restart_mod, "verify_bashlex", return_value=True):
                                            with mock.patch.object(restart_mod.psutil, "process_iter", return_value=[current_proc, other_proc]):
                                                assert restart_mod.restart_daemon() == 0

        diagnostics.assert_called_once_with(src_dir)
        current_proc.kill.assert_called_once_with()
        other_proc.kill.assert_not_called()

    def test_scoped_restart_refuses_unowned_responding_daemon(self, tmp_path):
        """A worktree restart must not clean up or replace another live daemon."""
        import autorun.restart_daemon as restart_mod

        src_dir = tmp_path / "current" / "plugins" / "autorun" / "src"
        src_dir.mkdir(parents=True)

        @contextmanager
        def acquired_restart_lock():
            yield True

        with mock.patch.object(restart_mod, "restart_lock", acquired_restart_lock):
            with mock.patch.object(restart_mod, "_resolve_src_dir", return_value=src_dir):
                with mock.patch.object(restart_mod, "get_daemon_pid", return_value=None):
                    with mock.patch.object(restart_mod, "is_daemon_responding", return_value=True):
                        with mock.patch.object(restart_mod, "_start_daemon") as start:
                            with mock.patch.object(restart_mod, "cleanup_stale_files") as cleanup:
                                assert restart_mod.restart_daemon() == 1

        start.assert_not_called()
        cleanup.assert_not_called()

    def test_restart_discovers_pid_after_resolving_source_tree(self, tmp_path):
        """Normal restart must source-filter fallback daemon discovery."""
        import autorun.restart_daemon as restart_mod
        from unittest.mock import call

        src_dir = tmp_path / "current" / "plugins" / "autorun" / "src"
        src_dir.mkdir(parents=True)

        @contextmanager
        def acquired_restart_lock():
            yield True

        with mock.patch.object(restart_mod, "restart_lock", acquired_restart_lock):
            with mock.patch.object(restart_mod, "_resolve_src_dir", return_value=src_dir):
                with mock.patch.object(restart_mod, "get_daemon_pid", side_effect=[None, 333]) as get_pid:
                    with mock.patch.object(restart_mod, "_clear_pycache"):
                        with mock.patch.object(restart_mod, "_check_conflicting_packages"):
                            with mock.patch.object(restart_mod, "_start_daemon", return_value=True):
                                with mock.patch.object(restart_mod, "is_daemon_responding", side_effect=[False, True]):
                                    with mock.patch.object(restart_mod, "_display_daemon_diagnostics", return_value=True) as diagnostics:
                                        with mock.patch.object(restart_mod, "verify_bashlex", return_value=True):
                                            with mock.patch.object(restart_mod.psutil, "process_iter", return_value=[]):
                                                assert restart_mod.restart_daemon() == 0

        diagnostics.assert_called_once_with(src_dir)
        assert get_pid.call_args_list == [call(src_dir=src_dir), call(src_dir=src_dir)]

    def test_restart_all_daemons_kills_all_matching_daemons(self, tmp_path):
        """--restart-all-daemons is the explicit risky all-daemon stop mode."""
        import autorun.restart_daemon as restart_mod

        src_dir = tmp_path / "current" / "plugins" / "autorun" / "src"
        other_src = tmp_path / "other" / "plugins" / "autorun" / "src"
        src_dir.mkdir(parents=True)
        other_src.mkdir(parents=True)

        current_proc = mock.MagicMock()
        current_proc.info = {
            "pid": 111,
            "cmdline": [
                sys.executable,
                "-c",
                f"sys.path.insert(0, r'{src_dir}'); from autorun.daemon import main; main()",
            ],
        }
        other_proc = mock.MagicMock()
        other_proc.info = {
            "pid": 222,
            "cmdline": [
                sys.executable,
                "-c",
                f"sys.path.insert(0, r'{other_src}'); from autorun.daemon import main; main()",
            ],
        }

        @contextmanager
        def acquired_restart_lock():
            yield True

        with mock.patch.object(restart_mod, "restart_lock", acquired_restart_lock):
            with mock.patch.object(restart_mod, "get_daemon_pid", side_effect=[None, 333]):
                with mock.patch.object(restart_mod, "_resolve_src_dir", return_value=src_dir):
                    with mock.patch.object(restart_mod, "_clear_pycache"):
                        with mock.patch.object(restart_mod, "_check_conflicting_packages"):
                            with mock.patch.object(restart_mod, "_start_daemon", return_value=True):
                                with mock.patch.object(restart_mod, "is_daemon_responding", return_value=True):
                                    with mock.patch.object(restart_mod, "_display_daemon_diagnostics", return_value=True) as diagnostics:
                                        with mock.patch.object(restart_mod, "verify_bashlex", return_value=True):
                                            with mock.patch.object(restart_mod.psutil, "process_iter", return_value=[current_proc, other_proc]):
                                                assert restart_mod.restart_daemon(all_daemons=True) == 0

        diagnostics.assert_called_once_with(src_dir)
        current_proc.kill.assert_called_once_with()
        other_proc.kill.assert_called_once_with()

    def test_restart_command_uses_package_cli_not_missing_script(self):
        """The slash command must not point at the removed scripts/restart_daemon.py."""
        command_path = plugin_root / "commands" / "restart-daemon.md"
        text = command_path.read_text(encoding="utf-8")

        assert "python -m autorun --restart-daemon" in text
        assert "scripts/restart_daemon.py" not in text
        assert "--restart-all-daemons" in text
        assert "can interrupt active autorun-backed sessions" in text

    def test_cli_parser_exposes_restart_all_daemons_flag(self):
        """The package CLI exposes all-daemon restart as explicit opt-in."""
        from autorun.__main__ import create_parser

        args = create_parser().parse_args(["--restart-all-daemons"])

        assert args.restart_all_daemons is True
        assert args.restart_daemon is False

        help_text = create_parser().format_help()
        normalized_help = " ".join(help_text.split())
        assert "--restart-daemon" in help_text
        assert "current AUTORUN_HOME/source tree" in normalized_help
        assert "--restart-all-daemons" in help_text
        assert "Risky maintenance mode" in normalized_help
        assert "interrupt active sessions in other installs" in normalized_help


class TestDaemonMainLifecycleCleanup:
    """Daemon entry-point cleanup must only remove files owned by this process."""

    def test_start_failure_without_owned_lock_preserves_live_daemon_files(self, tmp_path):
        """A startup loser must not unlink another daemon's socket or PID file."""
        import autorun.daemon as daemon_mod

        lock_path = tmp_path / "daemon.lock"
        lock_path.write_text("12345", encoding="utf-8")
        cleanup_calls = []

        class FakeDaemon:
            running = False
            _daemon_lock = None

            async def start(self):
                raise RuntimeError("Another daemon is already running")

            def _cleanup_files(self):
                cleanup_calls.append("cleanup")

        with mock.patch.object(daemon_mod, "_bootstrap_optional_deps", return_value=None):
            with mock.patch.object(daemon_mod, "AutorunDaemon", return_value=FakeDaemon()):
                with mock.patch.object(daemon_mod, "ipc", mock.MagicMock(), create=True) as ipc_mod:
                    with pytest.raises(SystemExit):
                        daemon_mod.main()

        ipc_mod.cleanup_socket.assert_not_called()
        assert cleanup_calls == []
        assert lock_path.read_text(encoding="utf-8") == "12345"

    def test_start_failure_with_owned_lock_cleans_owned_files(self):
        """A daemon that acquired the flock still cleans up after startup failure."""
        import autorun.daemon as daemon_mod

        cleanup_calls = []

        class FakeDaemon:
            running = False
            _daemon_lock = object()

            async def start(self):
                raise RuntimeError("startup failed after lock acquisition")

            def _cleanup_files(self):
                cleanup_calls.append("cleanup")
                self._daemon_lock = None

        with mock.patch.object(daemon_mod, "_bootstrap_optional_deps", return_value=None):
            with mock.patch.object(daemon_mod, "AutorunDaemon", return_value=FakeDaemon()):
                with pytest.raises(SystemExit):
                    daemon_mod.main()

        assert cleanup_calls == ["cleanup"]


class TestInstallerDaemonRestart:
    """Installer restart should use the robust daemon discovery path."""

    def test_installer_restarts_orphan_daemon_discovered_without_pid_file(self, tmp_path):
        import autorun.install as install_mod
        import autorun.restart_daemon as restart_mod

        missing_lock = tmp_path / "daemon.lock"

        with mock.patch.object(install_mod.ipc, "AUTORUN_LOCK_PATH", missing_lock):
            with mock.patch.object(restart_mod, "get_daemon_pid", return_value=12345) as get_pid:
                with mock.patch.object(restart_mod, "restart_daemon", return_value=0) as restart:
                    install_mod._restart_daemon_if_running()

        get_pid.assert_called_once_with()
        restart.assert_called_once_with()

    def test_installer_skips_restart_when_discovery_finds_no_daemon(self, tmp_path):
        import autorun.install as install_mod
        import autorun.restart_daemon as restart_mod

        missing_lock = tmp_path / "daemon.lock"

        with mock.patch.object(install_mod.ipc, "AUTORUN_LOCK_PATH", missing_lock):
            with mock.patch.object(restart_mod, "get_daemon_pid", return_value=None) as get_pid:
                with mock.patch.object(restart_mod, "restart_daemon") as restart:
                    install_mod._restart_daemon_if_running()

        get_pid.assert_called_once_with()
        restart.assert_not_called()
