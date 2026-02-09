#!/usr/bin/env python3
"""Graceful daemon restart with comprehensive verification."""
import os
import sys
import time
import socket
import subprocess
from pathlib import Path

HOME_DIR = Path.home() / ".clautorun"
SOCKET_PATH = HOME_DIR / "daemon.sock"
LOCK_PATH = HOME_DIR / "daemon.lock"

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
    """Poll for daemon shutdown with progress dots."""
    print("  Waiting for shutdown", end="", flush=True)
    start = time.time()
    while time.time() - start < max_wait:
        if not get_daemon_pid() and not is_daemon_responding():
            print(" ✓")
            return True
        if int((time.time() - start) * 2) % 2:
            print(".", end="", flush=True)
        time.sleep(0.1)
    print(" timeout")
    return False

def cleanup_stale_files():
    """Remove stale socket and lock files."""
    removed = []
    for path in [SOCKET_PATH, LOCK_PATH]:
        if path.exists():
            path.unlink()
            removed.append(path.name)
    if removed:
        print(f"  Cleaned up: {', '.join(removed)}")

def verify_bashlex():
    """Check if bashlex available in daemon."""
    try:
        plugin_root = Path(__file__).parent.parent
        sys.path.insert(0, str(plugin_root / "src"))
        from clautorun.command_detection import BASHLEX_AVAILABLE
        return BASHLEX_AVAILABLE
    except:
        return False

def main():
    print("=== Daemon Restart ===")

    # Step 1: Current state
    pid = get_daemon_pid()
    if pid:
        print(f"Daemon running (PID {pid})")

        # Step 2: Graceful shutdown
        print(f"  Sending SIGTERM to PID {pid}")
        os.kill(pid, 15)  # SIGTERM

        if not wait_for_shutdown(max_wait=5.0):
            print("  ⚠️ Timeout, forcing shutdown")
            try:
                os.kill(pid, 9)  # SIGKILL
                time.sleep(0.5)
            except OSError:
                pass
    else:
        print("Daemon not running")

    # Step 3: Cleanup
    cleanup_stale_files()

    # Step 4: Trigger auto-start
    print("  Starting fresh daemon...")
    try:
        # Use same auto-start mechanism as client.py
        plugin_root = Path(__file__).parent.parent
        src_dir = plugin_root / "src"
        daemon_code = f"import sys; sys.path.insert(0, '{src_dir}'); from clautorun.daemon import main; main()"

        subprocess.Popen(
            [sys.executable, "-c", daemon_code],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
    except Exception as e:
        print(f"  ⚠️ Failed to start daemon: {e}")

    time.sleep(0.5)  # Let daemon initialize

    # Step 5: Verify
    new_pid = get_daemon_pid()
    if new_pid:
        if new_pid == pid:
            print(f"  ⚠️ Same PID {new_pid} (may not have restarted)")
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

if __name__ == "__main__":
    sys.exit(main())
