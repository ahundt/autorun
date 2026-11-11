#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright 2025 Andrew Hundt <ATHundt@gmail.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""The shared bridge says who wrote a prompt, and the daemon acts on it.

Pi and Prime Agent: the ``input`` handler records the event's ``source``
(``interactive``, ``rpc`` or ``extension``) before its prefix test and sends
it as the frame field ``prompt_author`` (``interactive`` is ``user``; ``rpc``
and ``extension`` are ``automation``), both from its own command call and
from the ``before_agent_start`` frame that carries every prompt. The
registered ``/ar`` command is given no source by Pi, so it stays the user's:
that limit is pinned here so a change to it is noticed.

OpenCode: the model-callable ``autorun`` tool sends the session id its tool
context provides and ``prompt_author: "model"``, and its description offers
no guarded command.

Each case drives the real extension or shim against a synthetic daemon that
records the frames (the harness ``tests/test_pi_bridge.py`` uses), then sends
those exact frames through ``AutorunDaemon.handle_client`` to see what they
grant.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import uuid
from pathlib import Path

import pytest

from autorun.core import AutorunDaemon, app
from test_pi_bridge import JAVASCRIPT_RUNTIME, _javascript_command, _run_pi_adapter_driver, _SyntheticDaemon
from test_prompt_author_guard import REFUSAL_LEAD, send_frame, session_allows, text_of

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SHIM_SOURCE = PLUGIN_ROOT / "src" / "autorun" / "opencode_template" / "plugin" / "autorun.js"
BRIDGE_SOURCE = PLUGIN_ROOT / "src" / "autorun" / "bridge_template" / "daemon-client.mjs"

needs_js = pytest.mark.skipif(JAVASCRIPT_RUNTIME is None, reason="Bun or Node.js is required for bridge tests")


@pytest.fixture
def daemon(monkeypatch):
    monkeypatch.delenv("AUTORUN_SESSION_ID", raising=False)
    monkeypatch.delenv("AUTORUN_USE_IDENTITY", raising=False)
    return AutorunDaemon(app)


_PI_DRIVER = '''import extension from "__EXTENSION__";
const handlers = new Map();
const commands = new Map();
const pi = {
  registerCommand(name, value) { commands.set(name, value); },
  registerTool() {}, on(name, handler) { handlers.set(name, handler); },
  getActiveTools() { return []; }, sendMessage() {}, sendUserMessage() {},
};
extension(pi);
const ctx = {
  cwd: "/sandbox", mode: "rpc", isIdle: () => true, hasPendingMessages: () => false,
  ui: { notify() {} },
  sessionManager: {
    getSessionId: () => "__SESSION__", getSessionFile: () => undefined,
    getBranch: () => [], buildSessionContext: () => ({ messages: [] }),
  },
};
const results = [];
__STEPS__
console.log(JSON.stringify(results));
'''

_COMMAND_REPLY = {"systemMessage": "reply", "_autorun_bridge": {"starts_agent_turn": False}}


def _drive_pi(tmp_path, cli_type, steps, responses, session_id):
    script = _PI_DRIVER.replace("__SESSION__", session_id).replace("__STEPS__", steps)
    return _run_pi_adapter_driver(tmp_path, responses, script, cli_type=cli_type)


def _prompt_frames(frames):
    return [frame for frame in frames if frame.get("hook_event_name") == "UserPromptSubmit"]


@needs_js
@pytest.mark.parametrize("cli_type", ["pi", "prime"])
@pytest.mark.parametrize("source", ["rpc", "extension"])
def test_colon_command_from_rpc_or_extension_through_input_grants_nothing(tmp_path, daemon, cli_type, source):
    session_id = f"c8-pi-input-{source}-{uuid.uuid4().hex[:8]}"
    _result, frames = _drive_pi(
        tmp_path,
        cli_type,
        f'results.push(await handlers.get("input")({{ text: "ar:ok \'git push\'", source: "{source}" }}, ctx));',
        [_COMMAND_REPLY],
        session_id,
    )
    (frame,) = _prompt_frames(frames)
    assert frame.get("prompt_author") == "automation", f"input from {source!r} sent {frame.get('prompt_author')!r}"

    response = send_frame(daemon, frame)
    assert session_allows(daemon, session_id, cli_type) == [], f"{source} input granted 'git push': {response}"
    assert REFUSAL_LEAD in text_of(response), response


@needs_js
@pytest.mark.parametrize("cli_type", ["pi", "prime"])
@pytest.mark.parametrize("source", ["rpc", "extension"])
def test_spaced_command_from_rpc_or_extension_through_before_agent_start_grants_nothing(
    tmp_path, daemon, cli_type, source
):
    """``ar ok`` fails the input handler's prefix test and returns early, so it
    reaches autorun only through ``before_agent_start``; the source recorded
    by ``input`` has to travel with it."""
    session_id = f"c8-pi-bas-{source}-{uuid.uuid4().hex[:8]}"
    text = "ar ok 'git push'"
    _result, frames = _drive_pi(
        tmp_path,
        cli_type,
        f'results.push(await handlers.get("input")({{ text: {json.dumps(text)}, source: "{source}" }}, ctx));\n'
        f'results.push(await handlers.get("before_agent_start")({{ prompt: {json.dumps(text)} }}, ctx));',
        [{}],
        session_id,
    )
    (frame,) = _prompt_frames(frames)
    assert frame["prompt"] == text
    assert frame.get("prompt_author") == "automation", f"before_agent_start after {source!r} sent {frame.get('prompt_author')!r}"

    response = send_frame(daemon, frame)
    assert session_allows(daemon, session_id, cli_type) == [], f"{source} prompt granted 'git push': {response}"


@needs_js
@pytest.mark.parametrize("cli_type", ["pi", "prime"])
def test_the_users_routes_grant_and_the_registered_command_stays_the_users(tmp_path, daemon, cli_type):
    """Typed ``ar:ok`` (source ``interactive``) is the user's and says so.

    The registered ``/ar ok`` is the stated limit: Pi runs it before any
    ``input`` handler and gives it no source, over RPC too, so it grants. If
    Pi starts passing a source this pin fails and the README limit can go.
    """
    session_id = f"c8-pi-user-{uuid.uuid4().hex[:8]}"
    _result, frames = _drive_pi(
        tmp_path,
        cli_type,
        'results.push(await handlers.get("input")({ text: "ar:ok \'git push\'", source: "interactive" }, ctx));\n'
        'results.push(await commands.get("ar").handler("ok \'git push\'", ctx));',
        [_COMMAND_REPLY, _COMMAND_REPLY],
        session_id,
    )
    typed, registered = _prompt_frames(frames)
    assert typed.get("prompt_author") == "user", f"interactive input sent {typed.get('prompt_author')!r}"
    assert registered.get("prompt_author") in (None, "user"), registered

    for label, frame in (("typed", typed), ("registered", registered)):
        frame = {**frame, "session_id": f"{session_id}-{label}"}
        response = send_frame(daemon, frame)
        assert session_allows(daemon, frame["session_id"], cli_type) == ["git push"], f"{label}: {response}"


# ─── OpenCode's model-callable tool ───────────────────────────────────────

# A stand-in for @opencode-ai/plugin, which only a real OpenCode process
# provides: `tool(definition)` returns the definition, as the real helper does
# for the fields used here.
_PLUGIN_STUB = '''export function tool(definition) { return definition }
const chain = { describe() { return chain }, optional() { return chain } }
tool.schema = { string() { return chain } }
'''

_OPENCODE_DRIVER = '''import { AutorunPlugin } from "./autorun.js"
const hooks = await AutorunPlugin({ serverUrl: "http://127.0.0.1:1/", directory: "/sandbox" })
const autorun = hooks.tool?.autorun
if (!autorun) { console.log(JSON.stringify({ error: "no autorun tool registered" })); process.exit(0) }
const out = await autorun.execute(
  { command: "ok git push" },
  { sessionID: "__SESSION__", messageID: "m1", agent: "build", abort: new AbortController().signal },
)
console.log(JSON.stringify({ description: autorun.description, out }))
'''


def _run_opencode_tool(tmp_path, session_id):
    daemon = _SyntheticDaemon(tmp_path, backlog=2)
    frames = []

    def serve():
        try:
            for reply in ({}, {"systemMessage": "reply"}):
                conn = daemon.accept()
                with conn:
                    data = b""
                    while not data.endswith(b"\n"):
                        data += conn.recv(65536)
                    frames.append(json.loads(data))
                    conn.sendall((json.dumps(reply) + "\n").encode())
        finally:
            daemon.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    plugin_dir = tmp_path / "plugin"
    stub = plugin_dir / "node_modules" / "@opencode-ai" / "plugin"
    stub.mkdir(parents=True)
    (stub / "package.json").write_text(
        json.dumps({"name": "@opencode-ai/plugin", "type": "module", "main": "index.js"}), encoding="utf-8"
    )
    (stub / "index.js").write_text(_PLUGIN_STUB, encoding="utf-8")
    shutil.copy2(BRIDGE_SOURCE, plugin_dir / "daemon-client.mjs")
    (plugin_dir / "autorun.js").write_text(
        SHIM_SOURCE.read_text(encoding="utf-8")
        .replace("__AUTORUN_SOCKET__", json.dumps(daemon.socket_path))
        .replace("__AUTORUN_PORT_FILE__", json.dumps(daemon.port_file))
        .replace("__AUTORUN_HOOK_ENTRY_COMMAND__", "[]"),
        encoding="utf-8",
    )
    driver = plugin_dir / "driver.mjs"
    driver.write_text(_OPENCODE_DRIVER.replace("__SESSION__", session_id), encoding="utf-8")
    result = subprocess.run(
        _javascript_command(driver), capture_output=True, text=True, timeout=30, check=False, cwd=str(plugin_dir)
    )
    thread.join(timeout=10)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1]), frames


@pytest.mark.skipif(shutil.which("bun") is None, reason="bun runs OpenCode's plugin")
def test_opencode_tool_acts_on_its_own_session_as_the_model(tmp_path, daemon):
    session_id = f"c8-opencode-{uuid.uuid4().hex[:8]}"
    result, frames = _run_opencode_tool(tmp_path, session_id)
    assert "error" not in result, result
    (frame,) = _prompt_frames(frames)
    assert frame.get("session_id") == session_id, f"the tool sent session {frame.get('session_id')!r}"
    assert frame.get("prompt_author") == "model", f"the tool sent prompt_author {frame.get('prompt_author')!r}"

    response = send_frame(daemon, frame)
    assert session_allows(daemon, session_id, "opencode") == [], f"the model's tool granted 'git push': {response}"
    assert REFUSAL_LEAD in text_of(response), response


_GUARDED_WORDS = re.compile(
    r"\b(ok|no|clear|file|cache|allow|justify|find|existing-only|globalok|globalno|globalclear|ignore|pause)\b"
)


@pytest.mark.skipif(shutil.which("bun") is None, reason="bun runs OpenCode's plugin")
def test_opencode_tool_description_offers_no_guarded_command(tmp_path):
    result, _frames = _run_opencode_tool(tmp_path, f"c8-opencode-desc-{uuid.uuid4().hex[:8]}")
    assert "error" not in result, result
    description = result["description"]
    source = SHIM_SOURCE.read_text(encoding="utf-8")
    argument_hint = re.search(r"\.describe\(\s*((?:\"[^\"]*\"\s*\+?\s*)+)\)", source)
    offered = description + " " + (argument_hint.group(1) if argument_hint else "")
    assert _GUARDED_WORDS.findall(offered) == [], f"the tool offers guarded commands: {offered!r}"
    for name in ("status", "help", "task"):
        assert re.search(rf"\b{name}\b", description), f"description does not offer {name}: {description!r}"
