"""Graceful restart uses the same authenticated IPC lifecycle on both transports."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest import mock

import pytest


@pytest.fixture(params=[False, True], ids=["tcp", "unix"])
def running_daemon(request, monkeypatch):
    from autorun import ipc

    unix = request.param
    if unix and not hasattr(__import__("socket"), "AF_UNIX"):
        pytest.skip("Unix sockets unavailable")
    with tempfile.TemporaryDirectory(prefix="os8-", dir="/tmp" if os.name != "nt" else None) as directory:
        home = Path(directory)
        monkeypatch.setattr(ipc, "HAS_UNIX_SOCKETS", unix)
        monkeypatch.setattr(ipc, "AUTORUN_CONFIG_DIR", home)
        for name, filename in [("SOCKET_PATH", "daemon.sock"), ("PORT_FILE", "daemon.port"),
                               ("TOKEN_FILE", "daemon.token")]:
            monkeypatch.setattr(ipc, name, home / filename)
        env = dict(os.environ, HOME=str(home), USERPROFILE=str(home),
                   AUTORUN_HOME=str(home), AUTORUN_TEST_STATE_DIR=str(home / "state"))
        code = (
            "from autorun import ipc; "
            f"ipc.HAS_UNIX_SOCKETS = {unix!r}; "
            "from autorun.daemon import main; main()"
        )
        with (home / "output.log").open("w") as output:
            # cwd: `python -c` puts it first on sys.path, and CI runs pytest from
            # plugins/autorun, whose autorun.py would shadow the package.
            proc = subprocess.Popen([sys.executable, "-c", code], env=env, cwd=home,
                                    stdout=output, stderr=output)
            try:
                deadline = time.monotonic() + 10
                while not ipc.answers_round_trip(timeout=0.1):
                    assert proc.poll() is None, (home / "output.log").read_text()
                    assert time.monotonic() < deadline, (home / "output.log").read_text()
                    time.sleep(0.01)
                # The PID restart_daemon reads. On Windows proc.pid is the
                # venv's python.exe redirector, a parent of the daemon.
                proc.daemon_pid = ipc.daemon_record_pid(home / "daemon.lock")
                yield ipc, home, proc
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)


def test_shutdown_request_exits_and_removes_lifecycle_files(running_daemon):
    ipc, home, proc = running_daemon
    paths = [home / "daemon.lock", home / "daemon.flock"]
    paths += [ipc.SOCKET_PATH] if ipc.HAS_UNIX_SOCKETS else [ipc.PORT_FILE, ipc.TOKEN_FILE]
    assert all(path.exists() for path in paths)
    request = {"_autorun_shutdown": proc.daemon_pid}

    async def shutdown():
        reader, writer = await ipc.connect()
        try:
            writer.write(json.dumps(request).encode() + b"\n")
            await writer.drain()
            return json.loads(await asyncio.wait_for(reader.readline(), timeout=5))
        finally:
            writer.close()
            await writer.wait_closed()

    assert asyncio.run(shutdown()) == request
    assert proc.wait(timeout=5) == 0
    assert not any(path.exists() for path in paths)


@pytest.mark.parametrize("token", [b"", b"incorrect-token\n"], ids=["missing", "wrong"])
def test_tcp_shutdown_requires_the_existing_token(running_daemon, token):
    ipc, _home, proc = running_daemon
    if ipc.HAS_UNIX_SOCKETS:
        pytest.skip("TCP authentication only")

    async def unauthenticated_shutdown():
        reader, writer = await asyncio.open_connection("127.0.0.1", ipc._read_port())
        try:
            writer.write(token + json.dumps({"_autorun_shutdown": proc.daemon_pid}).encode() + b"\n")
            await writer.drain()
            assert await asyncio.wait_for(reader.read(), timeout=5) == b""
        finally:
            writer.close()
            await writer.wait_closed()

    asyncio.run(unauthenticated_shutdown())
    assert proc.poll() is None
    assert ipc.answers_round_trip()


def test_shutdown_request_cannot_stop_another_pid(running_daemon):
    ipc, _home, proc = running_daemon
    assert not ipc.request_shutdown(os.getpid())
    assert proc.poll() is None
    assert ipc.answers_round_trip()


def test_stop_daemon_exits_without_terminate_or_kill(running_daemon, monkeypatch):
    from autorun import restart_daemon

    ipc, home, proc = running_daemon
    monkeypatch.setattr(restart_daemon, "LOCK_PATH", home / "daemon.lock")
    with mock.patch.object(restart_daemon.psutil.Process, "terminate",
                           side_effect=AssertionError("graceful stop must not terminate")), \
            mock.patch.object(restart_daemon.psutil.Process, "kill",
                              side_effect=AssertionError("graceful stop must not kill")), \
            mock.patch.object(restart_daemon, "cleanup_stale_files",
                              side_effect=AssertionError("daemon must clean up itself")):
        restart_daemon._stop_daemon(proc.daemon_pid)
    assert proc.wait(timeout=5) == 0
    assert not ipc.is_responding()
    assert not (home / "daemon.lock").exists()
    assert not (home / "daemon.flock").exists()


@pytest.mark.parametrize(
    "acknowledged, waits, terminated, killed",
    [(True, [True], False, False), (False, [True], True, False),
     (False, [False], True, True), (True, [False, True], True, False),
     (True, [False, False], True, True)],
    ids=["graceful", "request-failed", "request-failed-kill", "exit-timeout", "exit-timeout-kill"],
)
def test_stop_daemon_uses_ipc_then_terminate_kill_fallback(acknowledged, waits, terminated, killed):
    from autorun import restart_daemon

    with mock.patch.object(restart_daemon.ipc, "request_shutdown",
                           return_value=acknowledged) as shutdown, \
            mock.patch.object(restart_daemon.psutil, "Process") as process, \
            mock.patch.object(restart_daemon, "wait_for_shutdown", side_effect=waits), \
            mock.patch.object(restart_daemon, "cleanup_stale_files"), \
            mock.patch.object(restart_daemon.time, "sleep"):
        restart_daemon._stop_daemon(12345)
    shutdown.assert_called_once_with(12345)
    assert process.return_value.terminate.called is terminated
    assert process.return_value.kill.called is killed


def test_shutdown_request_timeout_returns_false(monkeypatch, tmp_path):
    from autorun import ipc

    monkeypatch.setattr(ipc, "HAS_UNIX_SOCKETS", False)
    monkeypatch.setattr(ipc, "PORT_FILE", tmp_path / "daemon.port")
    monkeypatch.setattr(ipc, "TOKEN_FILE", tmp_path / "daemon.token")
    ipc.PORT_FILE.write_text("12345", encoding="utf-8")
    ipc.TOKEN_FILE.write_text("test-token", encoding="utf-8")
    with mock.patch.object(ipc.socket, "socket") as socket_factory:
        readline = socket_factory.return_value.__enter__.return_value.makefile.return_value.readline
        readline.side_effect = TimeoutError
        assert not ipc.request_shutdown(12345)
        readline.assert_called_once_with()
