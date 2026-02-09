#!/usr/bin/env python3
"""Graceful daemon restart with comprehensive verification."""
import os
import sys
import time
import socket
import subprocess
import fcntl
import errno
from pathlib import Path

HOME_DIR = Path.home() / ".clautorun"
SOCKET_PATH = HOME_DIR / "daemon.sock"
LOCK_PATH = HOME_DIR / "daemon.lock"
RESTART_LOCK_PATH = HOME_DIR / "daemon-restart.lock"

def get_daemon_pid():
    """Get daemon PID from lock file (None if not running)."""
    if not LOCK_PATH.exists():
        return None
    try:
        pid = int(LOCK_PATH.read_text().strip())
        # Verify process exists
        os.kill(pid, 0)
        return pid
    except (ValueError, OSError):
        return None

def is_daemon_responding():
    """Test if daemon accepts connections."""
    if not SOCKET_PATH.exists():
        return False
    try:
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(1.0)
        s.connect(str(SOCKET_PATH))
        s.close()
        return True
    except:
        return False

def wait_for_shutdown(max_wait=5.0):
    """Poll for daemon shutdown with progress dots.

    CRITICAL: Waits for BOTH PID to exit AND socket to close.
    This ensures daemon has fully released all resources before
    we attempt cleanup or start new daemon.
    """
    print("  Waiting for shutdown", end="", flush=True)
    start = time.time()
    while time.time() - start < max_wait:
        # Both conditions must be true for clean shutdown
        pid_gone = not get_daemon_pid()
        socket_closed = not is_daemon_responding()

        if pid_gone and socket_closed:
            print(" ✓")
            return True

        if int((time.time() - start) * 2) % 2:
            print(".", end="", flush=True)
        time.sleep(0.1)
    print(" timeout")
    return False

def cleanup_stale_files():
    """Remove stale socket and lock files (ONLY after failed shutdown).

    IMPORTANT: Only call this if daemon failed to clean up after itself.
    Normal shutdown should NOT need this - daemon cleans up in async_stop().
    """
    removed = []
    for path in [SOCKET_PATH, LOCK_PATH]:
        if path.exists():
            path.unlink()
            removed.append(path.name)
    if removed:
        print(f"  Cleaned up stale files: {', '.join(removed)}")

def verify_bashlex():
    """Check if bashlex available in daemon."""
    try:
        plugin_root = Path(__file__).parent.parent
        sys.path.insert(0, str(plugin_root / "src"))
        from clautorun.command_detection import BASHLEX_AVAILABLE
        return BASHLEX_AVAILABLE
    except:
        return False

def acquire_restart_lock():
    """Acquire exclusive restart lock to prevent concurrent restarts.

    Returns:
        file descriptor if lock acquired, None otherwise
    """
    try:
        # Create restart lock file
        lock_fd = open(RESTART_LOCK_PATH, 'w')
        # Try to acquire exclusive, non-blocking lock
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fd.write(f"{os.getpid()}\n")
        lock_fd.flush()
        return lock_fd
    except (IOError, OSError) as e:
        if hasattr(e, 'errno') and e.errno == errno.EAGAIN:
            return None  # Another restart in progress
        raise

def release_restart_lock(lock_fd):
    """Release restart lock and cleanup lock file."""
    if lock_fd:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
            lock_fd.close()
        except (IOError, OSError):
            pass
        try:
            RESTART_LOCK_PATH.unlink()
        except OSError:
            pass

def main():
    print("=== Daemon Restart ===")

    # Step 0: Acquire restart lock (prevent concurrent restarts)
    restart_lock = acquire_restart_lock()
    if not restart_lock:
        print("  ⚠️  Another restart already in progress")
        return 1

    try:
        # Step 1: Current state
        pid = get_daemon_pid()
        if pid:
            print(f"Daemon running (PID {pid})")

            # Step 2: Graceful shutdown
            print(f"  Sending SIGTERM to PID {pid}")
            os.kill(pid, 15)  # SIGTERM

            # Step 3: Wait for FULL shutdown (PID gone AND socket closed)
            shutdown_clean = wait_for_shutdown(max_wait=5.0)

            if not shutdown_clean:
                # Daemon didn't shut down cleanly - force it
                print("  ⚠️ Timeout, forcing shutdown")
                try:
                    os.kill(pid, 9)  # SIGKILL
                    time.sleep(0.5)
                except OSError:
                    pass

                # Only cleanup stale files if daemon failed to clean up
                cleanup_stale_files()
            else:
                # Daemon shut down cleanly - it cleaned up its own files
                # Only cleanup if files still exist (shouldn't happen)
                if SOCKET_PATH.exists() or LOCK_PATH.exists():
                    print("  ⚠️ Stale files remain after clean shutdown")
                    cleanup_stale_files()
        else:
            print("Daemon not running")
            # Cleanup any stale files from crashed daemon
            if SOCKET_PATH.exists() or LOCK_PATH.exists():
                cleanup_stale_files()

        # Step 4: Trigger auto-start
        print("  Starting fresh daemon...")
        try:
            # Use absolute path to ensure sys.path works regardless of CWD
            plugin_root = Path(__file__).resolve().parent.parent
            src_dir = plugin_root / "src"

            # Verify source directory exists
            if not src_dir.exists():
                print(f"  ✗ ERROR: Source directory not found: {src_dir}")
                return 1
            if not src_dir.is_dir():
                print(f"  ✗ ERROR: Source path is not a directory: {src_dir}")
                return 1

            print(f"  Source directory: {src_dir}")

            # Explicitly clear __pycache__ to prevent stale bytecode loading
            # Safety: Only delete __pycache__ dirs within src_dir (not system-wide)
            # Rationale: Python may load old .pyc files if timestamps are ambiguous
            import shutil
            cleared_caches = 0
            failed_clears = 0
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
                            cleared_caches += 1
                        except (OSError, PermissionError) as e:
                            # Non-fatal: log but continue
                            print(f"  ⚠️ Could not clear {pycache}: {e}")
                            failed_clears += 1

            if cleared_caches:
                print(f"  Cleared {cleared_caches} __pycache__ directories")
            if failed_clears:
                print(f"  ⚠️ Failed to clear {failed_clears} __pycache__ directories")
            if not cleared_caches and not failed_clears:
                print(f"  No __pycache__ directories to clear")

            # Check for conflicting installed packages
            import site
            try:
                site_packages_list = site.getsitepackages()
                for site_pkg_dir in site_packages_list:
                    site_packages = Path(site_pkg_dir) / "clautorun"
                    if site_packages.exists():
                        print(f"  ⚠️  WARNING: Installed package found at {site_packages}")
                        print(f"      This may interfere with source directory loading")
                        print(f"      Consider: uv pip uninstall clautorun")
                        break
            except Exception as e:
                # Non-fatal: just log
                print(f"  ⚠️ Could not check for installed packages: {e}")

            # Enhanced daemon startup with detailed logging
            daemon_code = (
                f"import sys; "
                f"sys.path.insert(0, r'{src_dir}'); "
                f"import clautorun; "
                f"print(f'=== Daemon Startup Diagnostics ===', flush=True); "
                f"print(f'clautorun loaded from: {{clautorun.__file__}}', flush=True); "
                f"print(f'sys.path[0]: {{sys.path[0]}}', flush=True); "
                f"print(f'Expected source: {src_dir}', flush=True); "
                # Verify bashlex availability
                f"from clautorun.command_detection import BASHLEX_AVAILABLE; "
                f"print(f'bashlex available: {{BASHLEX_AVAILABLE}}', flush=True); "
                # Verify tool name sets loaded
                f"from clautorun.config import BASH_TOOLS; "
                f"print(f'BASH_TOOLS = {{BASH_TOOLS}}', flush=True); "
                f"print(f'=== Starting Daemon ===', flush=True); "
                f"from clautorun.daemon import main; main()"
            )

            # Redirect stdout/stderr to a log file for debug visibility
            log_path = HOME_DIR / "daemon_startup.log"
            with open(log_path, "w") as startup_log:
                subprocess.Popen(
                    [sys.executable, "-c", daemon_code],
                    stdout=startup_log,
                    stderr=startup_log,
                    start_new_session=True
                )

            # Wait briefly for daemon to log diagnostics
            time.sleep(0.5)

            # Read and display diagnostics
            print("\n=== Daemon Diagnostics ===")
            try:
                with open(log_path) as log:
                    lines = log.readlines()
                    # Show first 10 lines (diagnostics section)
                    for line in lines[:10]:
                        print(f"  {line.rstrip()}")

                # Verify module source matches expected
                log_content = ''.join(lines)
                # Check if clautorun loaded from our source directory
                # (path will contain /src/clautorun/__init__.py)
                src_parent = str(src_dir.parent.parent)  # Go up to repo root
                if src_parent in log_content and '/src/clautorun/__init__.py' in log_content:
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
        except Exception as e:
            print(f"  ✗ ERROR: Failed to start daemon: {e}")
            import traceback
            traceback.print_exc()
            return 1

        time.sleep(0.5)  # Let daemon initialize

        # Step 5: Verify new daemon started
        new_pid = get_daemon_pid()
        if new_pid:
            if new_pid == pid:
                print(f"  ⚠️ Same PID {new_pid} (may not have restarted)")
                return 1
            else:
                print(f"  ✓ New daemon started (PID {new_pid})")
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
        print("head somefile.txt             # Should be BLOCKED")
        return 0

    finally:
        # Always release restart lock
        release_restart_lock(restart_lock)

if __name__ == "__main__":
    sys.exit(main())
