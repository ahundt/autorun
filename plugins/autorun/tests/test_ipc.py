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


class TestTCPPort:
    """Test deterministic TCP port generation."""

    def test_port_in_dynamic_range(self):
        """Port must be in IANA dynamic/private range (49152-65535)."""
        from autorun.ipc import _get_tcp_port
        port = _get_tcp_port()
        assert 49152 <= port <= 65535

    def test_port_deterministic(self):
        """Same user produces same port."""
        from autorun.ipc import _get_tcp_port
        p1 = _get_tcp_port()
        p2 = _get_tcp_port()
        assert p1 == p2

    def test_different_users_different_ports(self):
        """Different usernames produce different ports (with high probability)."""
        from autorun.ipc import _get_tcp_port
        with mock.patch.dict(os.environ, {"USER": "alice", "USERNAME": "alice"}):
            port_alice = _get_tcp_port()
        with mock.patch.dict(os.environ, {"USER": "bob", "USERNAME": "bob"}):
            port_bob = _get_tcp_port()
        # Collision probability is ~1/16383, acceptable to assert inequality
        assert port_alice != port_bob


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
             mock.patch.object(ipc, "HOME_DIR", short_tmp_path):

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
             mock.patch.object(ipc, "HOME_DIR", short_tmp_path):

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
             mock.patch.object(ipc, "HOME_DIR", short_tmp_path):

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
