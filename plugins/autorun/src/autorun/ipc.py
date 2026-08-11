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
import os
import socket
import subprocess
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

def ensure_config_dir():
    """Create AUTORUN_CONFIG_DIR with owner-only permissions (0o700 on Unix)."""
    AUTORUN_CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)

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



def detached_spawn_kwargs(windows: bool | None = None) -> dict:
    """Popen options that let the daemon outlive the process starting it.

    ``start_new_session`` calls setsid() and is POSIX-only: Python accepts it
    on Windows and does nothing, so the daemon stayed in its parent's console
    and process tree. A CI runner or any launcher that reaps a job object then
    kills the daemon the moment the spawning CLI exits, the next hook finds no
    daemon and spawns another that dies the same way, and every event pays the
    client's retry backoff before failing -- which is what "autorun CLI timed
    out" on Windows actually was. DETACHED_PROCESS plus
    CREATE_NEW_PROCESS_GROUP is the Windows equivalent of setsid().

    Both keys are always returned: creationflags is 0 on POSIX and
    start_new_session is ignored on Windows, so one call site covers both and
    neither platform needs a branch around the spawn itself.

    ``windows`` overrides the platform for tests; production passes nothing.
    """
    if windows is None:
        windows = os.name == "nt"
    creationflags = 0
    if windows:
        # Named rather than imported from subprocess: these attributes do not
        # exist on POSIX, so referencing them directly would break the import.
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
        )
    return {"start_new_session": True, "creationflags": creationflags}

def _published_port() -> int | None:
    """The port the running daemon published, or None if none has."""
    try:
        return int(PORT_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def get_address() -> str:
    """Human-readable address string for logging.

    Returns:
        "unix:~/.autorun/daemon.sock" or "tcp:127.0.0.1:PORT"
    """
    if HAS_UNIX_SOCKETS:
        return f"unix:{SOCKET_PATH}"
    port = _published_port()
    return f"tcp:127.0.0.1:{port}" if port else "tcp:127.0.0.1:<not started>"


async def start_server(client_handler, *, limit: int = 2**16) -> asyncio.AbstractServer:
    """Start the daemon IPC server (platform-appropriate).

    Args:
        client_handler: async callback(reader, writer) for each connection.
        limit: StreamReader buffer limit.

    Returns:
        asyncio.Server instance (caller manages lifecycle).
    """
    ensure_config_dir()

    if HAS_UNIX_SOCKETS:
        return await asyncio.start_unix_server(
            client_handler, str(SOCKET_PATH), limit=limit
        )
    else:
        # Port 0: the OS hands out a free one. The port used to be a hash of
        # the username, which made it the same for every daemon on the machine
        # -- and where there is no AF_UNIX the port *is* the endpoint, so two
        # AUTORUN_HOMEs were one endpoint. The second daemon died with "only
        # one usage of each socket address is normally permitted", and every
        # hook belonging to it fell through to a CLI waiting for a daemon that
        # could not start. Clients read the port from this file and always
        # did, so nothing needed the value to be predictable.
        server = await asyncio.start_server(
            client_handler, "127.0.0.1", 0, limit=limit
        )
        port = server.sockets[0].getsockname()[1]
        # Written after binding, so the file names a port that is listening.
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
