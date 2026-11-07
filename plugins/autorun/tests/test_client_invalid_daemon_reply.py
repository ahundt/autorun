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
