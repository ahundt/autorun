"""A daemon reply that is not JSON never reaches a harness's stdout as text.

Gemini CLI, Qwen Code and Antigravity parse hook stdout as JSON; the client
printed an unparseable reply to a non-tool event verbatim, which those
harnesses report as a hook error. Tool gates already failed closed.
"""

from __future__ import annotations

import io
import json
import sys

import pytest


@pytest.mark.parametrize("cli_type", ["gemini", "qwen", "antigravity", "claude"])
def test_an_unparseable_reply_is_answered_as_unhandled(monkeypatch, tmp_path, capsys, cli_type):
    from autorun import client, ipc

    payload = {"hook_event_name": "SessionStart", "session_id": "s", "cli_type": cli_type}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(ipc, "AUTORUN_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(ipc, "AUTORUN_LOCK_PATH", tmp_path / "daemon.pid")

    class Reader:
        async def readuntil(self, _separator):
            return b"Traceback (most recent call last): oops\n"

    class Writer:
        def write(self, _data):
            pass

        async def drain(self):
            pass

        def close(self):
            pass

        async def wait_closed(self):
            pass

    async def connect(**_kwargs):
        return Reader(), Writer()

    monkeypatch.setattr(ipc, "connect", connect)
    try:
        code = client.run_client()
    except SystemExit as exited:
        code = exited.code
    out = capsys.readouterr().out

    assert code == 0
    assert "Traceback" not in out
    for line in filter(None, out.splitlines()):
        json.loads(line)


@pytest.mark.parametrize("failing_call", ["drain", "readuntil"])
def test_a_unix_socket_that_never_connected_is_tried_again(monkeypatch, tmp_path, capsys, failing_call):
    """Linux reports a stream socket that never connected as EINVAL on its first use.

    A full listen backlog makes a non-blocking AF_UNIX connect return EAGAIN and
    asyncio then treats the socket as connected. The request was never
    delivered, so sending it again cannot run the hook twice.
    """
    import errno
    from types import SimpleNamespace

    from filelock import FileLock

    from autorun import client, ipc

    payload = {"hook_event_name": "SessionStart", "session_id": "s", "cli_type": "claude"}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(ipc, "AUTORUN_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(ipc, "AUTORUN_LOCK_PATH", tmp_path / "daemon.pid")

    def forbidden_spawn(*args, **kwargs):
        raise AssertionError("client tried to start a daemon while one holds the lock")

    monkeypatch.setattr(client, "subprocess", SimpleNamespace(
        Popen=forbidden_spawn, DEVNULL=client.subprocess.DEVNULL))
    connections = []

    class Reader:
        def __init__(self, broken):
            self.broken = broken

        async def readuntil(self, _separator):
            if self.broken and failing_call == "readuntil":
                raise OSError(errno.EINVAL, "Invalid argument")
            return b'{"continue": true, "systemMessage": "from the daemon"}\n'

    class Writer:
        def __init__(self, broken):
            self.broken = broken

        def write(self, _data):
            pass

        async def drain(self):
            if self.broken and failing_call == "drain":
                raise OSError(errno.EINVAL, "Invalid argument")

        def close(self):
            pass

        async def wait_closed(self):
            pass

    async def connect(**_kwargs):
        connections.append(None)
        broken = len(connections) == 1
        return Reader(broken), Writer(broken)

    monkeypatch.setattr(ipc, "connect", connect)
    # A live daemon holds this lock, so the client waits and reconnects.
    with FileLock(str(ipc.daemon_flock_path())):
        try:
            code = client.run_client()
        except SystemExit as exited:
            code = exited.code
    out = capsys.readouterr().out

    assert code == 0
    assert len(connections) == 2, "the request was not sent again"
    assert "from the daemon" in out, out
