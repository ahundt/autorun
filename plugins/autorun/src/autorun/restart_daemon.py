#!/usr/bin/env python3
"""Graceful daemon restart with comprehensive verification.

Performs a full stop-cleanup-start cycle with:
- Exclusive restart lock (prevents concurrent restarts)
- SIGTERM graceful shutdown with SIGKILL fallback
- Stale file cleanup (socket, lock)
- __pycache__ purge (prevents stale bytecode)
- Conflicting package detection
- Startup diagnostics and source verification
- bashlex availability check

Usage:
    # Normal scoped restart for the currently running autorun install:
    autorun --restart-daemon

    # Risky maintenance restart that stops every matching autorun daemon:
    autorun --restart-all-daemons

    # As an importable function from package code:
    from autorun.restart_daemon import restart_daemon
    exit_code = restart_daemon(all_daemons=False)

Normal restarts are source-tree scoped so updates in a worktree do not kill
active sessions using another installed autorun. The all-daemons mode is an
explicit recovery tool for stale or mixed-version daemons; it can interrupt
active autorun-backed sessions and should not be used as a routine code reload.
"""
import os
import shutil
import sys
import time
import subprocess
from contextlib import contextmanager
from pathlib import Path

import psutil
from filelock import FileLock, Timeout

from . import ipc

LOCK_PATH = ipc.AUTORUN_LOCK_PATH
RESTART_LOCK_PATH = ipc.AUTORUN_CONFIG_DIR / "daemon-restart.lock"


def _pid_matches_src(pid: int, src_dir: Path) -> bool:
    """Return whether a live daemon PID was launched from this source tree."""
    try:
        cmdline_str = ' '.join(psutil.Process(pid).cmdline())
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False
    return 'from autorun.daemon import main' in cmdline_str and str(src_dir) in cmdline_str


def get_daemon_pid(*, src_dir: Path | None = None) -> int | None:
    """Get daemon PID from lock file, with process discovery fallback.

    Primary: Read PID from daemon.lock (written by _acquire_daemon_lock).
    Fallback: If daemon.lock missing but daemon.flock exists, search for
    daemon process by cmdline pattern. This handles the case where the
    daemon acquired the flock but failed to write daemon.lock (OSError,
    race condition, or Gemini extension venv mismatch).

    Args:
        src_dir: Optional source-tree filter for fallback discovery. Normal
                 restarts pass this so an unrelated worktree/live daemon is
                 reported as unowned instead of stopped.
    """
    if LOCK_PATH.exists():
        try:
            pid = int(LOCK_PATH.read_text().strip())
            if psutil.pid_exists(pid):
                if src_dir is not None and not _pid_matches_src(pid, src_dir):
                    return None
                return pid
        except (ValueError, OSError):
            pass

    # Fallback: daemon.flock exists but daemon.lock missing — find by cmdline
    flock_path = LOCK_PATH.with_suffix('.flock')
    if flock_path.exists():
        for proc in psutil.process_iter(['pid', 'cmdline']):
            try:
                cmdline_str = ' '.join(proc.info.get('cmdline') or [])
                if 'from autorun.daemon import main' not in cmdline_str:
                    continue
                if src_dir is not None and str(src_dir) not in cmdline_str:
                    continue
                return proc.info['pid']
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass

    return None


def is_daemon_responding() -> bool:
    """Test if daemon accepts connections (delegates to ipc module)."""
    return ipc.is_responding()


def wait_for_shutdown(max_wait: float = 5.0, *, src_dir: Path | None = None) -> bool:
    """Poll for daemon shutdown with progress dots.

    CRITICAL: Waits for BOTH PID to exit AND socket to close.
    This ensures daemon has fully released all resources before
    we attempt cleanup or start new daemon.
    """
    print("  Waiting for shutdown", end="", flush=True)
    start = time.time()
    while time.time() - start < max_wait:
        # Both conditions must be true for clean shutdown
        pid_gone = not get_daemon_pid(src_dir=src_dir)
        socket_closed = not is_daemon_responding()

        if pid_gone and socket_closed:
            print(" ✓")
            return True

        if int((time.time() - start) * 2) % 2:
            print(".", end="", flush=True)
        time.sleep(0.1)
    print(" timeout")
    return False


def cleanup_stale_files() -> None:
    """Remove stale socket and lock files (ONLY after failed shutdown).

    IMPORTANT: Only call this if daemon failed to clean up after itself.
    Normal shutdown should NOT need this - daemon cleans up in async_stop().
    """
    removed = []
    # Clean up IPC socket/port file
    ipc.cleanup_socket()
    # Clean up lock file
    for path in [LOCK_PATH]:
        if path.exists():
            path.unlink()
            removed.append(path.name)
    if removed:
        print(f"  Cleaned up stale files: {', '.join(removed)}")


def verify_bashlex() -> bool:
    """Check if bashlex available in daemon."""
    try:
        from autorun.command_detection import BASHLEX_AVAILABLE
        return BASHLEX_AVAILABLE
    except Exception:
        return False


@contextmanager
def restart_lock():
    """Context manager for the restart lock. Yields True if acquired, False otherwise.

    Uses filelock for cross-platform file locking (works on Unix and Windows).
    RAII: lock is only released if it was successfully acquired.

    Usage:
        with restart_lock() as acquired:
            if not acquired:
                print("Another restart in progress")
                return
            # ... do restart work ...
    """
    lock = FileLock(str(RESTART_LOCK_PATH), timeout=0)
    acquired = False
    try:
        lock.acquire()
        acquired = True
    except Timeout:
        pass

    try:
        yield acquired
    finally:
        if acquired:
            lock.release()


def _resolve_src_dir() -> Path | None:
    """Resolve the plugin source directory.

    Uses absolute path to ensure sys.path works regardless of CWD.

    Returns:
        Path to src/ directory, or None with error printed if not found.
    """
    # Now that restart_daemon.py is in src/autorun/, go up two levels to src/
    # src/autorun/restart_daemon.py -> parent -> autorun -> parent -> src
    src_dir = Path(__file__).resolve().parent.parent

    if not src_dir.exists():
        print(f"  ✗ ERROR: Source directory not found: {src_dir}")
        return None
    if not src_dir.is_dir():
        print(f"  ✗ ERROR: Source path is not a directory: {src_dir}")
        return None

    return src_dir


def _clear_pycache(src_dir: Path) -> None:
    """Clear __pycache__ directories within src_dir to prevent stale bytecode.

    Safety: Only deletes __pycache__ dirs within src_dir (not system-wide).
    Rationale: Python may load old .pyc files if timestamps are ambiguous.
    """
    import shutil

    cleared = 0
    failed = 0
    if src_dir.exists() and src_dir.is_dir():
        for pycache in src_dir.rglob("__pycache__"):
            # Safety check: only delete if it's actually a __pycache__ directory
            # and it's within our plugin source (not system Python)
            if pycache.name == "__pycache__" and pycache.is_dir():
                # Extra safety: verify it's within src_dir
                try:
                    pycache.relative_to(src_dir)
                except ValueError:
                    print(f"  ⚠️ Skipping __pycache__ outside src_dir: {pycache}")
                    continue

                try:
                    shutil.rmtree(pycache)
                    cleared += 1
                except (OSError, PermissionError) as e:
                    # Non-fatal: log but continue
                    print(f"  ⚠️ Could not clear {pycache}: {e}")
                    failed += 1

    if cleared:
        print(f"  Cleared {cleared} __pycache__ directories")
    if failed:
        print(f"  ⚠️ Failed to clear {failed} __pycache__ directories")
    if not cleared and not failed:
        print("  No __pycache__ directories to clear")


def _check_conflicting_packages() -> None:
    """Warn if installed site-packages could shadow source directory."""
    import site
    try:
        site_packages_list = site.getsitepackages()
        for site_pkg_dir in site_packages_list:
            site_packages = Path(site_pkg_dir) / "autorun"
            if site_packages.exists():
                print(f"  ⚠️  WARNING: Installed package found at {site_packages}")
                print("      This may interfere with source directory loading")
                print("      Consider: uv pip uninstall autorun")
                break
    except Exception as e:
        # Non-fatal: just log
        print(f"  ⚠️ Could not check for installed packages: {e}")


def _python_in_venv_bin(bin_dir: Path) -> Path | None:
    """Return a Python executable from a virtualenv bin directory."""
    for name in ("python3", "python"):
        candidate = bin_dir / name
        if candidate.exists() and os.access(candidate, os.X_OK):
            return candidate
    return None


def _daemon_python_for_src(src_dir: Path) -> Path:
    """Return the interpreter that should own the daemon for this source tree."""
    plugin_root = src_dir.parent
    workspace_root = plugin_root.parent.parent

    # Match hook_entry.py: prefer venvs that can actually provide the autorun
    # console script. The workspace venv is common for local marketplace installs.
    for root in (workspace_root, plugin_root):
        bin_dir = root / ".venv" / "bin"
        if (bin_dir / "autorun").exists():
            python = _python_in_venv_bin(bin_dir)
            if python:
                return python

    autorun_bin = shutil.which("autorun")
    if autorun_bin:
        bin_dir = Path(autorun_bin).resolve().parent
        if bin_dir.parent.name == ".venv":
            python = _python_in_venv_bin(bin_dir)
            if python:
                return python

    for root in (plugin_root, workspace_root):
        python = _python_in_venv_bin(root / ".venv" / "bin")
        if python:
            return python

    return Path(sys.executable)


def _start_daemon(src_dir: Path) -> bool:
    """Start a fresh daemon process with detailed diagnostic logging.

    Args:
        src_dir: Path to plugin source directory.

    Returns:
        True if daemon process was spawned.

    Note: Called after _stop_daemon() in restart_daemon(), so we assume
    all existing daemons have been killed. No safety check here because
    that would prevent restart when cleanup was incomplete.
    """
    # Enhanced daemon startup with detailed logging
    daemon_code = (
        f"import sys; "
        f"sys.path.insert(0, r'{src_dir}'); "
        f"import autorun; "
        f"print(f'=== Daemon Startup Diagnostics ===', flush=True); "
        f"print(f'autorun loaded from: {{autorun.__file__}}', flush=True); "
        f"print(f'sys.path[0]: {{sys.path[0]}}', flush=True); "
        f"print(f'Expected source: {src_dir}', flush=True); "
        # Verify bashlex availability
        f"from autorun.command_detection import BASHLEX_AVAILABLE; "
        f"print(f'bashlex available: {{BASHLEX_AVAILABLE}}', flush=True); "
        # Verify tool name sets loaded
        f"from autorun.config import BASH_TOOLS; "
        f"print(f'BASH_TOOLS = {{BASH_TOOLS}}', flush=True); "
        f"print(f'=== Starting Daemon ===', flush=True); "
        f"from autorun.daemon import main; main()"
    )

    # Redirect stdout/stderr to a log file for debug visibility
    log_path = ipc.AUTORUN_CONFIG_DIR / "daemon_startup.log"
    daemon_python = _daemon_python_for_src(src_dir)
    with open(log_path, "w", encoding="utf-8") as startup_log:
        subprocess.Popen(
            [str(daemon_python), "-c", daemon_code],
            stdout=startup_log,
            stderr=startup_log,
            start_new_session=True,
        )

    # Wait briefly for daemon to log diagnostics
    time.sleep(0.5)

    # Read and display diagnostics
    print("\n=== Daemon Diagnostics ===")
    try:
        with open(log_path, encoding="utf-8") as log:
            lines = log.readlines()
            # Show first 10 lines (diagnostics section)
            for line in lines[:10]:
                print(f"  {line.rstrip()}")

        # Verify module source matches expected
        log_content = ''.join(lines)
        # Check if autorun loaded from our source directory
        # (path will contain /src/autorun/__init__.py)
        src_parent = str(src_dir.parent.parent)  # Go up to repo root
        if src_parent in log_content and '/src/autorun/__init__.py' in log_content:
            print("  ✓ Daemon loaded from source directory")
        elif '.local/lib' in log_content or 'site-packages' in log_content:
            print("  ✗ WARNING: Daemon loaded from installed package!")
            print(f"  Check {log_path} for details")
        else:
            print(f"  ⚠️ Unknown load location - check {log_path}")
    except FileNotFoundError:
        print(f"  ⚠️ Log file not yet created: {log_path}")
    except Exception as e:
        print(f"  ⚠️ Could not read daemon log: {e}")

    print(f"\n  Full daemon output: {log_path}")
    return True


def _daemon_cmdline(proc) -> str:
    """Return a process command line string without raising psutil races."""
    try:
        return ' '.join(proc.info.get('cmdline') or [])
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return ""


def _is_daemon_process_for_src(proc, src_dir: Path) -> bool:
    """Return True for autorun daemon processes started from this source tree."""
    cmdline_str = _daemon_cmdline(proc)
    return (
        'from autorun.daemon import main' in cmdline_str
        and str(src_dir) in cmdline_str
    )


def _daemon_processes_for_src(src_dir: Path) -> list:
    """Find orphan daemon processes that belong to the current source tree."""
    return [
        proc
        for proc in psutil.process_iter(['pid', 'cmdline'])
        if _is_daemon_process_for_src(proc, src_dir)
    ]


def _all_daemon_processes() -> list:
    """Find all autorun daemon processes for explicit all-daemons restart only."""
    return [
        proc
        for proc in psutil.process_iter(['pid', 'cmdline'])
        if 'from autorun.daemon import main' in _daemon_cmdline(proc)
    ]


def _stop_daemon(pid: int, *, src_dir: Path | None = None) -> None:
    """Stop a running daemon gracefully, with SIGKILL fallback.

    Steps:
    1. Send SIGTERM for graceful shutdown
    2. Wait for FULL shutdown (PID gone AND socket closed)
    3. If timeout: SIGKILL force + cleanup stale files
    4. If clean but stale files remain: cleanup
    """
    print(f"  Sending SIGTERM to PID {pid}")
    try:
        proc = psutil.Process(pid)
        proc.terminate()  # SIGTERM on Unix, TerminateProcess on Windows
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass

    # Wait for FULL shutdown (PID gone AND socket closed)
    shutdown_clean = wait_for_shutdown(max_wait=5.0, src_dir=src_dir)

    if not shutdown_clean:
        # Daemon didn't shut down cleanly - force it
        print("  ⚠️ Timeout, forcing shutdown")
        try:
            proc = psutil.Process(pid)
            proc.kill()  # SIGKILL on Unix, TerminateProcess on Windows
            time.sleep(0.5)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        # Only cleanup stale files if daemon failed to clean up
        cleanup_stale_files()
    else:
        # Daemon shut down cleanly - it cleaned up its own files
        # Only cleanup if files still exist (shouldn't happen)
        if ipc.SOCKET_PATH.exists() or LOCK_PATH.exists():
            print("  ⚠️ Stale files remain after clean shutdown")
            cleanup_stale_files()


def restart_daemon(*, all_daemons: bool = False) -> int:
    """Restart the autorun daemon.

    Performs a full stop-cleanup-start cycle with locking, verification,
    and diagnostics. Safe to call from any context (script, import, install).
    Normal restarts are scoped to the current AUTORUN_HOME/source tree.
    all-daemons restarts are explicit maintenance operations that stop every
    matching autorun daemon process and may interrupt active sessions in other
    installs.

    Steps:
    0. Acquire restart lock (prevent concurrent restarts)
    1. Check current daemon state
    2. Graceful shutdown (SIGTERM -> wait -> SIGKILL fallback)
    3. Cleanup stale files if needed
    4. Start fresh daemon (pycache clear, conflict check, spawn)
    5. Verify new daemon started (different PID)
    6. Verify bashlex availability

    Returns:
        0 on success, 1 on failure.
    """
    print("=== Daemon Restart ===")

    # Step 0: Acquire restart lock (prevent concurrent restarts)
    with restart_lock() as acquired:
        if not acquired:
            print("  ⚠️  Another restart already in progress")
            return 1

        src_dir = _resolve_src_dir()
        if not src_dir:
            return 1

        # Step 1: Current state. Fallback process discovery is source-scoped so
        # missing daemon.lock files cannot make a normal restart stop another
        # install's daemon.
        pid = get_daemon_pid(src_dir=src_dir)

        # Steps 2-3: Stop the daemon owned by this source tree, if any.
        if pid:
            print(f"Daemon running (PID {pid})")
            _stop_daemon(pid, src_dir=src_dir)
        else:
            print("Daemon not running")

        # Normal restart keeps shared/live daemons outside this source tree.
        # all_daemons is risky maintenance for stale multi-daemon cleanup.
        remaining_pids = _all_daemon_processes() if all_daemons else _daemon_processes_for_src(src_dir)
        if remaining_pids:
            scope = "all matching" if all_daemons else "current source tree"
            print(f"  Killing {len(remaining_pids)} remaining daemon(s) ({scope})")
            for proc in remaining_pids:
                try:
                    proc.kill()  # SIGKILL equivalent
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            time.sleep(0.5)
        elif not pid and not all_daemons and is_daemon_responding():
            print("  ✗ Refusing scoped restart: daemon socket responds, but no")
            print("    daemon from this source tree owns the current lock.")
            print("    Use --restart-all-daemons only for explicit recovery, or")
            print("    set AUTORUN_HOME to isolate this worktree.")
            return 1

        # Cleanup any stale files
        if ipc.SOCKET_PATH.exists() or LOCK_PATH.exists():
            cleanup_stale_files()

        # Step 4: Start fresh daemon
        print("  Starting fresh daemon...")
        print(f"  Source directory: {src_dir}")

        # Explicitly clear __pycache__ to prevent stale bytecode loading
        _clear_pycache(src_dir)
        _check_conflicting_packages()

        try:
            _start_daemon(src_dir)
        except Exception as e:
            print(f"  ✗ ERROR: Failed to start daemon: {e}")
            import traceback
            traceback.print_exc()
            return 1

        # Poll for socket readiness (designs-intended indicator)
        start = time.time()
        ready = False
        while time.time() - start < 5.0:
            if is_daemon_responding():
                ready = True
                break
            time.sleep(0.2)

        # Step 5: Verify new daemon started
        new_pid = get_daemon_pid(src_dir=src_dir)
        if new_pid:
            if new_pid == pid:
                print(f"  ⚠️ Same PID {new_pid} (may not have restarted)")
                return 1
            elif not ready:
                print("  ✗ ERROR: Daemon started but not responding to socket")
                return 1
            else:
                print(f"  ✓ New daemon started (PID {new_pid}) and responding")
        else:
            print("  ✗ ERROR: Daemon did not start")
            return 1

        # Step 6: Verify bashlex
        if verify_bashlex():
            print("  ✓ bashlex available")
        else:
            print("  ✗ bashlex NOT available (using fallback)")

        print("\n=== Test Commands ===")
        print("cargo build 2>&1 | head -50  # Should be ALLOWED")
        print("head somefile.txt             # Claude/Gemini block; Codex allows native reads")
        return 0


def main() -> int:
    """CLI entry point - delegates to restart_daemon()."""
    return restart_daemon()


if __name__ == "__main__":
    sys.exit(main())
