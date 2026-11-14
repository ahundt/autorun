"""Daemon shutdown chooses the graceful first step from platform capabilities."""

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
import psutil


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
            # cwd: `python -c` puts it first on sys.path, so an autorun.py in
            # the caller's directory would shadow the package.
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
                # Terminating a Windows venv redirector leaves its daemon child
                # holding daemon.flock; stop and wait for the recorded owner.
                pid = ipc.daemon_record_pid(home / "daemon.lock")
                if pid is not None:
                    try:
                        daemon = psutil.Process(pid)
                        ipc.request_shutdown(pid)
                        try:
                            daemon.wait(timeout=5)
                        except psutil.TimeoutExpired:
                            daemon.kill()
                            daemon.wait(timeout=5)
                    except psutil.NoSuchProcess:
                        pass
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


def test_posix_stop_terminates_real_daemon_without_ipc(running_daemon, monkeypatch):
    if os.name == "nt":
        pytest.skip("Windows has no POSIX graceful process signal")
    from autorun import restart_daemon

    ipc, home, proc = running_daemon
    monkeypatch.setattr(restart_daemon, "LOCK_PATH", home / "daemon.lock")
    with mock.patch.object(restart_daemon.ipc, "terminate_is_graceful", return_value=True), \
            mock.patch.object(restart_daemon.ipc, "request_shutdown",
                              side_effect=AssertionError("clean SIGTERM must not request IPC")), \
            mock.patch.object(restart_daemon.psutil.Process, "kill",
                              side_effect=AssertionError("graceful stop must not kill")), \
            mock.patch.object(restart_daemon, "cleanup_stale_files",
                              side_effect=AssertionError("daemon must clean up itself")):
        restart_daemon._stop_daemon(proc.daemon_pid)
    assert proc.wait(timeout=5) == 0
    assert not ipc.is_responding()
    assert not (home / "daemon.lock").exists()
    assert not (home / "daemon.flock").exists()


@pytest.mark.parametrize("windows, expected", [(False, True), (True, False)])
def test_terminate_is_graceful_depends_on_platform(windows, expected):
    from autorun import ipc

    assert ipc.terminate_is_graceful(windows=windows) is expected


def _stop_call_order(*, graceful_signal, signal_results, waits, acknowledged):
    from autorun import restart_daemon

    calls = []

    def signal(_pid, *, kill=False):
        calls.append("kill" if kill else "terminate")
        return signal_results.pop(0)

    def request(_pid):
        calls.append("ipc")
        return acknowledged

    def wait(**_kwargs):
        calls.append("wait")
        return waits.pop(0)

    with mock.patch.object(restart_daemon.ipc, "terminate_is_graceful",
                           return_value=graceful_signal), \
            mock.patch.object(restart_daemon.ipc, "request_shutdown", side_effect=request), \
            mock.patch.object(restart_daemon, "_signal_owned_daemon", side_effect=signal), \
            mock.patch.object(restart_daemon, "wait_for_shutdown", side_effect=wait), \
            mock.patch.object(restart_daemon, "cleanup_stale_files"), \
            mock.patch.object(restart_daemon, "_owns_this_runtime", return_value=True), \
            mock.patch.object(restart_daemon.time, "sleep"):
        restart_daemon._stop_daemon(12345)
    return calls


@pytest.mark.parametrize(
    "graceful_signal, signal_results, waits, acknowledged, expected",
    [
        (True, [True], [True], True, ["terminate", "wait"]),
        (True, [True], [False, True], True, ["terminate", "wait", "ipc", "wait"]),
        (True, [False], [True], True, ["terminate", "ipc", "wait"]),
        (False, [], [True], True, ["ipc", "wait"]),
        (False, [True], [True], False, ["ipc", "terminate", "wait"]),
        (False, [True], [False, True], True, ["ipc", "wait", "terminate", "wait"]),
    ],
    ids=[
        "posix-signal-exits",
        "posix-signal-timeout-ipc",
        "posix-signal-denied-ipc",
        "windows-ipc-exits",
        "windows-old-daemon-terminate",
        "windows-ipc-timeout-terminate",
    ],
)
def test_stop_daemon_platform_order(
    graceful_signal, signal_results, waits, acknowledged, expected
):
    assert _stop_call_order(
        graceful_signal=graceful_signal,
        signal_results=list(signal_results),
        waits=list(waits),
        acknowledged=acknowledged,
    ) == expected


def test_stop_daemon_kills_after_both_graceful_paths_fail():
    assert _stop_call_order(
        graceful_signal=True,
        signal_results=[True, True],
        waits=[False],
        acknowledged=False,
    ) == ["terminate", "wait", "ipc", "kill"]


def test_restart_replaces_and_stops_real_sandboxed_daemon(tmp_path, monkeypatch):
    from autorun import ipc, restart_daemon

    # The socket lives in this directory and AF_UNIX paths stop at 104 bytes
    # on macOS; tmp_path under the default TMPDIR is already past that, and
    # the daemon then exits 1 with "AF_UNIX path too long".
    home = Path(tempfile.mkdtemp(prefix="d", dir=os.environ["AUTORUN_TEST_RUNTIME_DIR"]))
    monkeypatch.setattr(ipc, "AUTORUN_CONFIG_DIR", home)
    for name, filename in [
        ("SOCKET_PATH", "daemon.sock"),
        ("PORT_FILE", "daemon.port"),
        ("TOKEN_FILE", "daemon.token"),
    ]:
        monkeypatch.setattr(ipc, name, home / filename)
    monkeypatch.setattr(restart_daemon, "LOCK_PATH", home / "daemon.lock")
    monkeypatch.setattr(restart_daemon, "RESTART_LOCK_PATH", home / "daemon-restart.lock")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("AUTORUN_HOME", str(home))
    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(tmp_path / "state"))

    env = dict(os.environ)
    code = "from autorun.daemon import main; main()"
    with (home / "output.log").open("w") as output:
        proc = subprocess.Popen(
            [sys.executable, "-c", code], env=env, cwd=tmp_path,
            stdout=output, stderr=output,
        )
    try:
        deadline = time.monotonic() + 10
        while not ipc.answers_round_trip(timeout=0.1):
            assert proc.poll() is None, (home / "output.log").read_text()
            assert time.monotonic() < deadline, (home / "output.log").read_text()
            time.sleep(0.01)
        old_pid = ipc.daemon_record_pid(home / "daemon.lock")
        assert old_pid is not None

        assert restart_daemon.restart_daemon(replace_runtime_daemon=True) == 0
        new_pid = ipc.daemon_record_pid(home / "daemon.lock")
        assert new_pid is not None and new_pid != old_pid
        assert proc.wait(timeout=5) == 0
        assert ipc.answers_round_trip(timeout=1)
    finally:
        new_pid = ipc.daemon_record_pid(home / "daemon.lock")
        if new_pid is not None:
            restart_daemon._stop_daemon(new_pid)
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)


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
