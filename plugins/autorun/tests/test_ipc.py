#!/usr/bin/env python3
"""Tests for cross-platform IPC abstraction (autorun.ipc).

Validates:
1. Platform detection (HAS_UNIX_SOCKETS)
2. TCP port determinism and range
3. Server/client round-trip on current platform
4. Health check (is_responding)
5. Socket connect test
6. Cleanup
7. Address formatting
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture
def short_tmp_path():
    """Short temp path for AF_UNIX sockets (108 char limit on macOS)."""
    with tempfile.TemporaryDirectory(prefix="ipc") as d:
        yield Path(d)


class TestPlatformDetection:
    """Verify HAS_UNIX_SOCKETS matches platform capabilities."""

    def test_has_unix_sockets_matches_platform(self):
        """HAS_UNIX_SOCKETS should be True on Unix, may vary on Windows."""
        import socket as socket_mod
        from autorun.ipc import HAS_UNIX_SOCKETS
        assert HAS_UNIX_SOCKETS == hasattr(socket_mod, "AF_UNIX")


class TestPublishedPort:
    """Where the port comes from, now that nothing derives it.

    It used to be a hash of the username, which made every daemon on the
    machine choose the same one; the client always read the port from the
    file, so the value never needed to be predictable, and being predictable
    is what made two homes collide. The daemon now binds port 0 and publishes
    what the OS gave it.
    """

    def test_a_published_port_is_read_back(self, tmp_path, monkeypatch):
        from autorun import ipc

        monkeypatch.setattr(ipc, "PORT_FILE", tmp_path / "daemon.port")
        (tmp_path / "daemon.port").write_text("51234\n", encoding="utf-8")
        assert ipc._published_port() == 51234

    def test_no_file_means_no_daemon_rather_than_a_guess(self, tmp_path, monkeypatch):
        """Guessing is what produced a port nothing was listening on."""
        from autorun import ipc

        monkeypatch.setattr(ipc, "PORT_FILE", tmp_path / "absent.port")
        assert ipc._published_port() is None

    def test_unreadable_contents_mean_no_daemon(self, tmp_path, monkeypatch):
        from autorun import ipc

        monkeypatch.setattr(ipc, "PORT_FILE", tmp_path / "daemon.port")
        (tmp_path / "daemon.port").write_text("not a port", encoding="utf-8")
        assert ipc._published_port() is None


class TestGetAddress:
    """Test human-readable address formatting."""

    def test_unix_address_format(self):
        """Unix address includes socket path."""
        from autorun.ipc import get_address, HAS_UNIX_SOCKETS
        addr = get_address()
        if HAS_UNIX_SOCKETS:
            assert addr.startswith("unix:")
            assert "daemon.sock" in addr
        else:
            assert addr.startswith("tcp:127.0.0.1:")


class TestServerClientRoundTrip:
    """Integration test: start server, connect client, exchange data."""

    @pytest.mark.asyncio
    async def test_round_trip(self, short_tmp_path):
        """Server receives data from client and responds."""
        from autorun import ipc

        # Use short path for AF_UNIX socket (108 char limit on macOS)
        test_sock = short_tmp_path / "d.sock"
        test_port_file = short_tmp_path / "d.port"

        with mock.patch.object(ipc, "SOCKET_PATH", test_sock), \
             mock.patch.object(ipc, "PORT_FILE", test_port_file), \
             mock.patch.object(ipc, "AUTORUN_CONFIG_DIR", short_tmp_path):

            received = []

            async def handler(reader, writer):
                data = await reader.readline()
                received.append(data.decode().strip())
                writer.write(b"ok\n")
                await writer.drain()
                writer.close()
                await writer.wait_closed()

            server = await ipc.start_server(handler)

            try:
                reader, writer = await ipc.connect()
                writer.write(b"hello\n")
                await writer.drain()
                resp = await reader.readline()
                writer.close()
                await writer.wait_closed()

                assert received == ["hello"]
                assert resp.strip() == b"ok"
            finally:
                server.close()
                await server.wait_closed()

    @pytest.mark.asyncio
    async def test_health_check_true_when_running(self, short_tmp_path):
        """is_responding() returns True when server is running."""
        from autorun import ipc

        test_sock = short_tmp_path / "d.sock"
        test_port_file = short_tmp_path / "d.port"

        with mock.patch.object(ipc, "SOCKET_PATH", test_sock), \
             mock.patch.object(ipc, "PORT_FILE", test_port_file), \
             mock.patch.object(ipc, "AUTORUN_CONFIG_DIR", short_tmp_path):

            async def handler(reader, writer):
                writer.close()
                await writer.wait_closed()

            server = await ipc.start_server(handler)
            try:
                assert ipc.is_responding() is True
            finally:
                server.close()
                await server.wait_closed()

    def test_health_check_false_when_not_running(self, tmp_path):
        """is_responding() returns False when no server is running."""
        from autorun import ipc

        test_sock = tmp_path / "no_such.sock"
        test_port_file = tmp_path / "no_such.port"

        with mock.patch.object(ipc, "SOCKET_PATH", test_sock), \
             mock.patch.object(ipc, "PORT_FILE", test_port_file):
            assert ipc.is_responding() is False


class TestSocketConnectTest:
    """Test socket_connect_test() (inverse logic: True=no daemon)."""

    def test_no_daemon_returns_true(self, tmp_path):
        """socket_connect_test() returns True when no daemon running."""
        from autorun import ipc

        test_sock = tmp_path / "no_such.sock"
        test_port_file = tmp_path / "no_such.port"

        with mock.patch.object(ipc, "SOCKET_PATH", test_sock), \
             mock.patch.object(ipc, "PORT_FILE", test_port_file):
            assert ipc.socket_connect_test() is True

    @pytest.mark.asyncio
    async def test_daemon_running_returns_false(self, short_tmp_path):
        """socket_connect_test() returns False when daemon is running."""
        from autorun import ipc

        test_sock = short_tmp_path / "d.sock"
        test_port_file = short_tmp_path / "d.port"

        with mock.patch.object(ipc, "SOCKET_PATH", test_sock), \
             mock.patch.object(ipc, "PORT_FILE", test_port_file), \
             mock.patch.object(ipc, "AUTORUN_CONFIG_DIR", short_tmp_path):

            async def handler(reader, writer):
                writer.close()
                await writer.wait_closed()

            server = await ipc.start_server(handler)
            try:
                assert ipc.socket_connect_test() is False
            finally:
                server.close()
                await server.wait_closed()


class TestCleanup:
    """Test cleanup_socket removes socket/port file."""

    def test_cleanup_removes_socket(self, tmp_path):
        """cleanup_socket() removes Unix socket file."""
        from autorun import ipc

        test_sock = tmp_path / "test.sock"
        test_sock.touch()

        with mock.patch.object(ipc, "SOCKET_PATH", test_sock), \
             mock.patch.object(ipc, "HAS_UNIX_SOCKETS", True):
            ipc.cleanup_socket()
            assert not test_sock.exists()

    def test_cleanup_removes_port_file(self, tmp_path):
        """cleanup_socket() removes Windows port file."""
        from autorun import ipc

        test_port = tmp_path / "test.port"
        test_port.write_text("50000", encoding="utf-8")

        with mock.patch.object(ipc, "PORT_FILE", test_port), \
             mock.patch.object(ipc, "HAS_UNIX_SOCKETS", False):
            ipc.cleanup_socket()
            assert not test_port.exists()

    def test_cleanup_noop_when_no_file(self, tmp_path):
        """cleanup_socket() doesn't crash when file doesn't exist."""
        from autorun import ipc

        with mock.patch.object(ipc, "SOCKET_PATH", tmp_path / "nonexistent.sock"), \
             mock.patch.object(ipc, "HAS_UNIX_SOCKETS", True):
            ipc.cleanup_socket()  # Should not raise


class TestIPCCodeQuality:
    """Verify ipc.py code quality constraints."""

    def test_no_os_kill_in_ipc(self):
        """ipc.py must not use os.kill (use psutil instead)."""
        import inspect
        from autorun import ipc
        source = inspect.getsource(ipc)
        assert 'os.kill(' not in source

    def test_af_unix_usage_guarded(self):
        """AF_UNIX in executable code must be inside HAS_UNIX_SOCKETS blocks."""
        import inspect
        from autorun import ipc
        source = inspect.getsource(ipc)
        lines = source.split('\n')
        for i, line in enumerate(lines):
            stripped = line.lstrip()
            # Skip comments and docstrings
            if stripped.startswith('#') or stripped.startswith('"""') or stripped.startswith("'"):
                continue
            # Skip lines that are part of docstrings (indented text without code)
            if 'AF_UNIX' in line and 'socket.AF_UNIX' in line:
                # This is actual code using AF_UNIX — must be guarded
                context = '\n'.join(lines[max(0, i-10):i+1])
                assert 'HAS_UNIX_SOCKETS' in context or 'hasattr' in context, \
                    f"socket.AF_UNIX at line {i+1} not guarded by HAS_UNIX_SOCKETS"

class TestDetachedSpawnKwargs:
    """The daemon must survive the process that starts it, on both platforms.

    start_new_session is POSIX-only and silently ignored on Windows, so the
    daemon stayed inside its parent's process tree there and was reaped with
    it. Both keys are always returned so the spawn sites need no branch.
    """

    def test_posix_uses_setsid_and_no_creation_flags(self):
        from autorun import ipc

        options = ipc.detached_spawn_kwargs(windows=False)
        assert options["start_new_session"] is True
        assert options["creationflags"] == 0

    def test_windows_detaches_and_makes_a_new_process_group(self):
        from autorun import ipc

        options = ipc.detached_spawn_kwargs(windows=True)
        assert options["start_new_session"] is True
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        assert options["creationflags"] == 0x00000008 | 0x00000200

    def test_both_platforms_answer_the_same_keys(self):
        """A spawn site passes **kwargs blind; a missing key would be a crash."""
        from autorun import ipc

        assert set(ipc.detached_spawn_kwargs(windows=True)) == set(
            ipc.detached_spawn_kwargs(windows=False)
        )

    def test_the_default_follows_the_running_platform(self, monkeypatch):
        from autorun import ipc

        monkeypatch.setattr(ipc.os, "name", "nt")
        assert ipc.detached_spawn_kwargs()["creationflags"] != 0
        monkeypatch.setattr(ipc.os, "name", "posix")
        assert ipc.detached_spawn_kwargs()["creationflags"] == 0

class TestLoopbackServerIsolation:
    """Two daemons with different homes must not fight over one port.

    The port was derived from a hash of the username, so every daemon on the
    machine wanted the same one. On POSIX that is invisible: the socket lives
    under AUTORUN_HOME, so separate homes are separate endpoints. Where there
    is no AF_UNIX the port is the endpoint, and the second daemon died with
    "[Errno 10048] only one usage of each socket address is normally
    permitted" -- one project, test, or install per machine, and every hook in
    the others fell through to a CLI that waited for a daemon that could never
    start.

    Everything runs inside one event loop. Starting a server with its own
    asyncio.run leaves it bound to a loop that has already closed, and closing
    it then fails with "'NoneType' object has no attribute '_stop_serving'" on
    the Windows proactor loop.
    """

    def _configure(self, monkeypatch, tmp_path, name):
        """Point the module at a fresh home and force the no-AF_UNIX path.

        Split from _serve because configuration is synchronous and binding is
        not: keeping them together forced every caller into an event loop just
        to redirect a directory.

        Attributes are monkeypatched rather than reloading the module.
        importlib.reload re-runs _get_autorun_config_dir at import time and the
        new value outlives the test, which leaked AUTORUN_CONFIG_DIR into
        test_daemon_restart_safety.
        """
        from autorun import ipc

        home = tmp_path / name
        home.mkdir()
        # Windows has no AF_UNIX in CPython, and that is the branch under test;
        # forcing it here means the assertion runs on every platform's CI.
        monkeypatch.setattr(ipc, "HAS_UNIX_SOCKETS", False)
        monkeypatch.setattr(ipc, "AUTORUN_CONFIG_DIR", home)
        monkeypatch.setattr(ipc, "PORT_FILE", home / "daemon.port")
        return ipc, home

    async def _serve(self, ipc, home):
        """Bind a server and return it with the port it published.

        Async, and awaited by the caller inside a single asyncio.run, so the
        server stays bound to a loop that is still open when it is closed.
        """
        async def handler(_reader, _writer):  # pragma: no cover - never called
            pass

        server = await ipc.start_server(handler)
        # Read the file a client would read, not getsockname(): the contract
        # under test is what the daemon publishes, not what it happens to bind.
        published = int((home / "daemon.port").read_text(encoding="utf-8").strip())
        return server, published

    def test_two_homes_get_two_ports(self, monkeypatch, tmp_path):
        import asyncio

        async def body():
            ipc, home_a = self._configure(monkeypatch, tmp_path, "home-a")
            first, first_port = await self._serve(ipc, home_a)
            try:
                _ipc, home_b = self._configure(monkeypatch, tmp_path, "home-b")
                try:
                    second, second_port = await self._serve(_ipc, home_b)
                except OSError as error:  # pragma: no cover - the bug guarded
                    raise AssertionError(
                        f"the second daemon could not bind: {error}. Two homes "
                        "must not share one port."
                    ) from error
                try:
                    assert first_port != second_port, (
                        f"both homes published port {first_port}"
                    )
                finally:
                    # close() only stops accepting; wait_closed() is what
                    # releases the port before the next test tries to bind.
                    second.close()
                    await second.wait_closed()
            finally:
                first.close()
                await first.wait_closed()

        asyncio.run(body())

    def test_the_published_port_is_the_one_actually_bound(self, monkeypatch, tmp_path):
        import asyncio

        async def body():
            ipc, home = self._configure(monkeypatch, tmp_path, "home-c")
            server, published = await self._serve(ipc, home)
            try:
                bound = server.sockets[0].getsockname()[1]
                assert published == bound, (
                    f"clients read {published} but the daemon listens on {bound}"
                )
                assert published > 0, "port 0 asks the OS for a free port"
            finally:
                server.close()
                await server.wait_closed()

        asyncio.run(body())


