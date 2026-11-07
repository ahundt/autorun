"""The TCP transport (Windows, where CPython has no AF_UNIX) answers only
clients that present the daemon's token.

A Unix socket in ~/.autorun is reachable only by its owner. 127.0.0.1 is
reachable by every local process, including other users' on a shared machine,
so without a token any of them could send /ar:ok grants or read session state.
The token lives beside daemon.port in the user's profile, readable by the user.
"""

from __future__ import annotations

import asyncio
import os
import stat
import sys

import pytest


@pytest.fixture
def tcp_ipc(monkeypatch, tmp_path):
    from autorun import ipc

    monkeypatch.setattr(ipc, "HAS_UNIX_SOCKETS", False)
    monkeypatch.setattr(ipc, "AUTORUN_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(ipc, "PORT_FILE", tmp_path / "daemon.port")
    monkeypatch.setattr(ipc, "TOKEN_FILE", tmp_path / "daemon.token")
    return ipc


async def _echo(reader, writer):
    line = await reader.readline()
    writer.write(b"echo:" + line)
    await writer.drain()
    writer.close()


async def _exchange(reader, writer, line: bytes) -> bytes:
    writer.write(line)
    await writer.drain()
    try:
        return await asyncio.wait_for(reader.read(), timeout=5)
    finally:
        writer.close()


def test_a_client_without_the_token_gets_nothing(tcp_ipc):
    async def body():
        server = await tcp_ipc.start_server(_echo)
        try:
            port = int(tcp_ipc.PORT_FILE.read_text(encoding="utf-8"))
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            return await _exchange(reader, writer, b'{"event": "PreToolUse"}\n')
        finally:
            server.close()
            await server.wait_closed()

    assert asyncio.run(body()) == b""


def test_ipc_connect_presents_the_token_and_is_served(tcp_ipc):
    async def body():
        server = await tcp_ipc.start_server(_echo)
        try:
            reader, writer = await tcp_ipc.connect()
            return await _exchange(reader, writer, b'{"event": "PreToolUse"}\n')
        finally:
            server.close()
            await server.wait_closed()

    assert asyncio.run(body()) == b'echo:{"event": "PreToolUse"}\n'


def test_a_wrong_token_is_refused(tcp_ipc):
    async def body():
        server = await tcp_ipc.start_server(_echo)
        try:
            port = int(tcp_ipc.PORT_FILE.read_text(encoding="utf-8"))
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"not-the-token\n")
            return await _exchange(reader, writer, b'{"event": "PreToolUse"}\n')
        finally:
            server.close()
            await server.wait_closed()

    assert asyncio.run(body()) == b""


def test_each_daemon_start_gets_a_new_token(tcp_ipc):
    async def start_once():
        server = await tcp_ipc.start_server(_echo)
        server.close()
        await server.wait_closed()
        return tcp_ipc.TOKEN_FILE.read_text(encoding="utf-8").strip()

    first, second = asyncio.run(start_once()), asyncio.run(start_once())
    assert first and second and first != second


@pytest.mark.skipif(sys.platform == "win32", reason="Windows protects it by the profile ACL")
def test_the_token_file_is_readable_only_by_its_owner(tcp_ipc):
    """Even over an earlier token someone left world-readable."""
    tcp_ipc.TOKEN_FILE.write_text("stale", encoding="utf-8")
    os.chmod(tcp_ipc.TOKEN_FILE, 0o644)

    async def body():
        server = await tcp_ipc.start_server(_echo)
        server.close()
        await server.wait_closed()

    asyncio.run(body())
    mode = stat.S_IMODE(os.stat(tcp_ipc.TOKEN_FILE).st_mode)
    assert mode & (stat.S_IRWXG | stat.S_IRWXO) == 0, oct(mode)


def test_the_stdlib_hook_wrapper_presents_the_token(tcp_ipc, monkeypatch, tmp_path):
    """hooks/hook_entry.py cannot import ipc, so it reads the token itself.
    Hiding AF_UNIX takes its Windows branch on every OS's CI."""
    import importlib.util
    import json
    import socket
    import threading
    from pathlib import Path

    reply = {"hookSpecificOutput": {"hookEventName": "SessionStart"}}

    async def answer(reader, writer):
        await reader.readline()
        writer.write(json.dumps(reply).encode() + b"\n")
        await writer.drain()
        writer.close()

    loop = asyncio.new_event_loop()
    server = loop.run_until_complete(tcp_ipc.start_server(answer))
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        path = Path(__file__).parents[1] / "hooks" / "hook_entry.py"
        spec = importlib.util.spec_from_file_location("hook_entry_tcp_token", path)
        hook_entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook_entry)
        monkeypatch.setenv("AUTORUN_HOME", str(tmp_path))
        monkeypatch.delattr(socket, "AF_UNIX", raising=False)

        handled, exit_code = hook_entry.try_daemon(
            json.dumps({"hook_event_name": "SessionStart", "session_id": "s"}), "claude"
        )
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join()
        server.close()
        loop.run_until_complete(server.wait_closed())
        loop.close()

    assert (handled, exit_code) == (True, 0)


def _serve_in_thread(tcp_ipc, handler):
    """Run ipc.start_server on its own loop; return a stop() callable."""
    import threading

    loop = asyncio.new_event_loop()
    server = loop.run_until_complete(tcp_ipc.start_server(handler))
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    def stop():
        loop.call_soon_threadsafe(loop.stop)
        thread.join()
        server.close()
        loop.run_until_complete(server.wait_closed())
        loop.close()

    return stop


def test_the_javascript_bridge_presents_the_token(tcp_ipc, tmp_path):
    """Pi, Prime and OpenCode reach the daemon through daemon-client.mjs."""
    import json
    import subprocess

    from test_pi_bridge import BRIDGE_SOURCE, JAVASCRIPT_RUNTIME, _javascript_command

    if JAVASCRIPT_RUNTIME is None:
        pytest.skip("Bun or Node.js is required for the JavaScript bridge")
    deny = {
        "hookSpecificOutput": {
            "permissionDecision": "deny",
            "permissionDecisionReason": "blocked over loopback",
        }
    }

    async def answer(reader, writer):
        await reader.readline()
        writer.write(json.dumps(deny).encode() + b"\n")
        await writer.drain()
        writer.close()

    stop = _serve_in_thread(tcp_ipc, answer)
    try:
        driver = tmp_path / "driver.mjs"
        driver.write_text(
            f"""import {{ createDaemonBridge }} from {json.dumps(BRIDGE_SOURCE.as_uri())};
const bridge = createDaemonBridge({{
  cliType: "pi",
  socketPath: {json.dumps(str(tmp_path / "absent.sock"))},
  portFile: {json.dumps(str(tcp_ipc.PORT_FILE))},
  hookEntryCommand: [],
  timeoutMs: 5000,
}});
console.log(JSON.stringify(await bridge.askDaemon({{
  hook_event_name: "PreToolUse", session_id: "s", tool_name: "bash",
  tool_input: {{ command: "rm x" }},
}})));
""",
            encoding="utf-8",
        )
        result = subprocess.run(
            _javascript_command(driver), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30, check=False,
        )
    finally:
        stop()

    assert result.returncode == 0, result.stderr
    assert "blocked over loopback" in result.stdout
