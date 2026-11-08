"""Cross-platform IPC abstraction for daemon communication.

Unix: AF_UNIX socket at ~/.autorun/daemon.sock (fast, no port conflicts)
Windows: TCP 127.0.0.1 on an OS-assigned port, published in daemon.port beside
where the socket would be (AF_UNIX unavailable in Python on Windows). The port
was once derived from the username, which gave every daemon on the machine the
same one and let only the first AUTORUN_HOME hold an endpoint.

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
import hmac
import os
import secrets
import socket
import subprocess
from pathlib import Path

from .durable_io import atomic_write_text, unlink

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
# The loopback listener's credential. A Unix socket in the 0700 config
# directory reaches only its owner; 127.0.0.1 reaches every local process, so
# a TCP client proves it can read this owner-only file before it is served.
AUTORUN_TOKEN_FILE = AUTORUN_CONFIG_DIR / "daemon.token"
AUTORUN_LOCK_PATH = AUTORUN_CONFIG_DIR / "daemon.lock"
AUTORUN_LOG_FILE = AUTORUN_CONFIG_DIR / "daemon.log"


# Functions rather than constants: both are derived from the paths above, and a
# redirected home (tests, sandboxes) patches those, so a frozen copy would
# point the flock and the PID record at different directories.
def daemon_flock_path(lock_path: Path | None = None) -> Path:
    """The lock a live daemon holds for its lifetime, beside its PID record."""
    return (lock_path or AUTORUN_LOCK_PATH).with_suffix(".flock")


def daemon_restart_lock_path() -> Path:
    """The lock held while `autorun --restart-daemon` replaces the daemon."""
    return AUTORUN_CONFIG_DIR / "daemon-restart.lock"
PROCESS_BIRTH_UNITS_PER_SECOND = 1_000_000

# Backward-compatible aliases (used internally by ipc functions below)
SOCKET_PATH = AUTORUN_SOCKET_PATH
PORT_FILE = AUTORUN_PORT_FILE
TOKEN_FILE = AUTORUN_TOKEN_FILE

#: Random bytes in a token (hex-encoded on disk and on the wire).
TOKEN_BYTES = 32
#: How long a TCP client has to send its token line before it is dropped.
TOKEN_READ_TIMEOUT_SECONDS = 5.0


def process_start_units(pid: int) -> int:
    """Return a process birth time in stable integer units, or zero."""
    try:
        import psutil

        return round(
            psutil.Process(pid).create_time() * PROCESS_BIRTH_UNITS_PER_SECOND
        )
    except Exception:
        return 0


def daemon_record_pid(path: Path) -> int | None:
    """Return the live PID named by a ``<pid> <birth>`` daemon record."""
    try:
        pid_token, birth_token, *_ = path.read_text(encoding="utf-8").split()
        pid = int(pid_token)
        recorded_birth = int(birth_token)
    except (OSError, ValueError):
        return None
    actual_birth = process_start_units(pid)
    if recorded_birth <= 0 or actual_birth != recorded_birth:
        return None
    return pid

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


#: Connections the kernel may queue before refusing. asyncio defaults to 100,
#: and a hundred sessions firing hooks at once overflowed it, so every extra
#: connect waited out a client retry. The kernel clamps this to its own limit
#: (macOS kern.ipc.somaxconn, 128 by default; Linux net.core.somaxconn, 4096
#: since 5.4), so raise that sysctl for a deeper queue.
LISTEN_BACKLOG = socket.SOMAXCONN


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
            client_handler, str(SOCKET_PATH), limit=limit, backlog=LISTEN_BACKLOG
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
        # The token is published before the port: a client that finds the
        # port can always find the token that goes with it.
        # atomic_write_text stages with mkstemp (owner-only, 0600) but keeps an
        # existing file's mode, so an earlier token is removed first.
        token = secrets.token_hex(TOKEN_BYTES)
        unlink(TOKEN_FILE, missing_ok=True)
        atomic_write_text(TOKEN_FILE, token)
        server = await asyncio.start_server(
            _require_token(client_handler, token.encode("ascii")),
            "127.0.0.1",
            0,
            limit=limit,
            backlog=LISTEN_BACKLOG,
        )
        port = server.sockets[0].getsockname()[1]
        # Publish only a complete port value. If publication fails, do not
        # strand a listener that no client can discover.
        try:
            atomic_write_text(PORT_FILE, str(port))
        except OSError:
            server.close()
            await server.wait_closed()
            raise
        return server


def _require_token(client_handler, token: bytes):
    """Serve TCP only after a token preamble, before reading requests.

    At most len(token)+1 bytes are read, so an unauthenticated peer cannot
    make the daemon buffer more; reading stops at the first newline so a short
    wrong line is rejected at once instead of waiting out the timeout.
    """
    preamble_size = len(token) + len(b"\n")

    async def read_preamble(reader) -> bytes:
        line = b""
        while len(line) < preamble_size and not line.endswith(b"\n"):
            chunk = await reader.read(preamble_size - len(line))
            if not chunk:
                break
            line += chunk
        return line

    async def authenticated(reader, writer):
        try:
            line = await asyncio.wait_for(read_preamble(reader), TOKEN_READ_TIMEOUT_SECONDS)
        except (asyncio.TimeoutError, TimeoutError, ConnectionError, ValueError):
            line = b""
        # A connect-only liveness probe sends nothing and lands here too.
        if not hmac.compare_digest(line, token + b"\n"):
            writer.close()
            return
        await client_handler(reader, writer)

    return authenticated


def connection_preamble() -> bytes:
    """What a client sends before its request: the token line on TCP, else nothing.

    Raises:
        FileNotFoundError: the TCP daemon has not published a token.
    """
    if HAS_UNIX_SOCKETS:
        return b""
    return TOKEN_FILE.read_text(encoding="utf-8").strip().encode("ascii") + b"\n"


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
        preamble = connection_preamble()
        reader, writer = await asyncio.open_connection("127.0.0.1", port, limit=limit)
        writer.write(preamble)
        return reader, writer


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


#: A request carrying this key is answered by the daemon's event loop with the
#: same object, before any handler runs. connect() alone succeeds against a
#: wedged daemon, whose listen backlog the kernel keeps accepting into.
ROUND_TRIP_PROBE_KEY = "_autorun_round_trip_probe"


def answers_round_trip(timeout: float | None = None) -> bool:
    """Whether the daemon's event loop answers a request within ``timeout``."""
    return _request_round_trip({ROUND_TRIP_PROBE_KEY: True}, timeout)


def request_shutdown(pid: int) -> bool:
    """Ask the authenticated endpoint to stop only if it owns ``pid``."""
    from .config import CONFIG

    return _request_round_trip({CONFIG["daemon_shutdown_request_key"]: pid})


def _request_round_trip(payload: dict, timeout: float | None = None) -> bool:
    """Send one authenticated JSON line and require an identical reply."""
    import json

    if timeout is None:
        from .config import CONFIG

        timeout = float(CONFIG["daemon_round_trip_probe_seconds"])
    try:
        if HAS_UNIX_SOCKETS:
            family, address = socket.AF_UNIX, str(SOCKET_PATH)
        else:
            family, address = socket.AF_INET, ("127.0.0.1", _read_port())
        with socket.socket(family, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(address)
            s.sendall(
                connection_preamble()
                + json.dumps(payload).encode()
                + b"\n"
            )
            reply = s.makefile("rb").readline()
        return json.loads(reply or b"null") == payload
    except (OSError, ValueError):
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
        for path in (PORT_FILE, TOKEN_FILE):
            try:
                path.unlink(missing_ok=True)
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
