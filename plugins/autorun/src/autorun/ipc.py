"""Cross-platform IPC abstraction for daemon communication.

Unix: AF_UNIX socket at ~/.autorun/daemon.sock (fast, no port conflicts)
Windows: TCP 127.0.0.1 on a deterministic port (AF_UNIX unavailable in Python on Windows)

All consumers use this module instead of directly referencing AF_UNIX or TCP.
This keeps platform branching in ONE place (DRY).

Usage:
    # Server side (daemon):
    server = await ipc.start_server(handle_client, limit=READ_BUFFER_LIMIT)

    # Client side:
    reader, writer = await ipc.connect(limit=READ_BUFFER_LIMIT)

    # Health check:
    if ipc.is_responding():
        print("Daemon is up")

    # Cleanup:
    ipc.cleanup_socket()
"""

import asyncio
import hashlib
import os
import socket
from pathlib import Path

def _get_autorun_config_dir() -> Path:
    """Get autorun config/data directory (~/.autorun/).

    Default: ~/.autorun on all platforms (consistent, simple, discoverable).
    Override: set AUTORUN_HOME env var for testing or custom deployments.
    """
    env_home = os.environ.get("AUTORUN_HOME")
    if env_home:
        return Path(env_home)
    return Path.home() / ".autorun"


AUTORUN_CONFIG_DIR = _get_autorun_config_dir()
AUTORUN_SOCKET_PATH = AUTORUN_CONFIG_DIR / "daemon.sock"
AUTORUN_PORT_FILE = AUTORUN_CONFIG_DIR / "daemon.port"
AUTORUN_LOCK_PATH = AUTORUN_CONFIG_DIR / "daemon.lock"
AUTORUN_LOG_FILE = AUTORUN_CONFIG_DIR / "daemon.log"

# Backward-compatible aliases (used internally by ipc functions below)
SOCKET_PATH = AUTORUN_SOCKET_PATH
PORT_FILE = AUTORUN_PORT_FILE

# Whether the platform supports Unix domain sockets in Python's asyncio.
# Windows has kernel AF_UNIX support since Windows 10 1803, but CPython
# does not expose socket.AF_UNIX on Windows (https://github.com/python/cpython/issues/77589).
HAS_UNIX_SOCKETS = hasattr(socket, "AF_UNIX")


def _get_tcp_port() -> int:
    """Deterministic TCP port for this user, avoiding well-known ports.

    Hashes the username to produce a port in range 49152-65535 (dynamic/private ports).
    This ensures different users on the same machine don't collide.
    """
    username = os.getenv("USERNAME", os.getenv("USER", "default"))
    h = int(hashlib.sha256(username.encode()).hexdigest(), 16)
    return 49152 + (h % (65535 - 49152))


def get_address() -> str:
    """Human-readable address string for logging.

    Returns:
        "unix:~/.autorun/daemon.sock" or "tcp:127.0.0.1:PORT"
    """
    if HAS_UNIX_SOCKETS:
        return f"unix:{SOCKET_PATH}"
    return f"tcp:127.0.0.1:{_get_tcp_port()}"


async def start_server(client_handler, *, limit: int = 2**16) -> asyncio.AbstractServer:
    """Start the daemon IPC server (platform-appropriate).

    Args:
        client_handler: async callback(reader, writer) for each connection.
        limit: StreamReader buffer limit.

    Returns:
        asyncio.Server instance (caller manages lifecycle).
    """
    AUTORUN_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    if HAS_UNIX_SOCKETS:
        return await asyncio.start_unix_server(
            client_handler, str(SOCKET_PATH), limit=limit
        )
    else:
        port = _get_tcp_port()
        server = await asyncio.start_server(
            client_handler, "127.0.0.1", port, limit=limit
        )
        # Write port to file so clients know where to connect
        PORT_FILE.write_text(str(port), encoding="utf-8")
        return server


async def connect(*, limit: int = 2**16, timeout: float = 5.0):
    """Connect to the daemon IPC server.

    Args:
        limit: StreamReader buffer limit.
        timeout: Connection timeout in seconds.

    Returns:
        (reader, writer) tuple.

    Raises:
        FileNotFoundError: Daemon not running (Unix socket missing).
        ConnectionRefusedError: Daemon not accepting connections.
        OSError: Other connection errors.
    """
    if HAS_UNIX_SOCKETS:
        return await asyncio.open_unix_connection(
            path=str(SOCKET_PATH), limit=limit
        )
    else:
        port = _read_port()
        return await asyncio.open_connection("127.0.0.1", port, limit=limit)


def is_responding() -> bool:
    """Test if daemon accepts connections (health check).

    Returns:
        True if daemon is accepting connections, False otherwise.
    """
    try:
        if HAS_UNIX_SOCKETS:
            if not SOCKET_PATH.exists():
                return False
            with socket.socket(socket.AF_UNIX) as s:
                s.settimeout(1.0)
                s.connect(str(SOCKET_PATH))
        else:
            port = _read_port()
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1.0)
                s.connect(("127.0.0.1", port))
        return True
    except Exception:
        return False


def socket_connect_test() -> bool:
    """Test if a daemon is running via socket connection.

    Returns:
        True if NO daemon running (safe to start), False if daemon running.
    """
    if HAS_UNIX_SOCKETS:
        if not SOCKET_PATH.exists():
            return True
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(1.0)
                sock.connect(str(SOCKET_PATH))
            return False  # Connected — daemon is running
        except (ConnectionRefusedError, FileNotFoundError, OSError):
            return True  # Can't connect — no daemon
    else:
        try:
            port = _read_port()
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1.0)
                sock.connect(("127.0.0.1", port))
            return False
        except (ConnectionRefusedError, FileNotFoundError, OSError):
            return True


def cleanup_socket():
    """Remove socket file (Unix) or port file (Windows) after daemon stops."""
    if HAS_UNIX_SOCKETS:
        try:
            if SOCKET_PATH.exists():
                SOCKET_PATH.unlink()
        except OSError:
            pass
    else:
        try:
            if PORT_FILE.exists():
                PORT_FILE.unlink()
        except OSError:
            pass


def _read_port() -> int:
    """Read TCP port from port file (Windows only).

    Raises:
        FileNotFoundError: If port file doesn't exist (daemon not started).
    """
    if not PORT_FILE.exists():
        raise FileNotFoundError(f"Daemon port file not found: {PORT_FILE}")
    return int(PORT_FILE.read_text(encoding="utf-8").strip())
