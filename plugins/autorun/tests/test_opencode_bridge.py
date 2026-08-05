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
"""The OpenCode plugin shim: what it sends, where it lands, how it fails.

OpenCode delivers no external hook events; its hook surface is a JavaScript
plugin loaded in-process. The shim is the per-harness adapter that carries
those events to the same daemon socket every other harness already uses, in
the role `hooks/hook_entry.py` plays elsewhere.

Facts these tests encode, measured against opencode 1.18.13:
  - plugins load from `<config>/plugin/`, singular, at session bootstrap
  - `PluginInput.serverUrl` is the bound loopback address
  - the client exposes no `permission` namespace, so the veto path is
    `tool.execute.before` throwing, not a permission reply

Bun runs the shim because OpenCode is a Bun program and loads it in-process,
so an OpenCode user already has that runtime. It is not a dependency of
autorun: every Bun-requiring test below skips when `bun` is missing, and no
other harness installs JavaScript at all.
"""

import json
import os
import shutil
import socket
import subprocess
import threading
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MARKETPLACE_ROOT = Path(__file__).resolve().parents[3]
SHIM_SOURCE = PLUGIN_ROOT / "src" / "autorun" / "opencode_template" / "plugin" / "autorun.js"


class TestShimSourceIsSelfContained:
    """The shim ships as one plain ES module: OpenCode loads it with no build
    step, so anything it needs must be substituted in at install time."""

    def test_shim_ships_with_the_plugin(self):
        assert SHIM_SOURCE.is_file(), f"{SHIM_SOURCE} missing"

    def test_shim_resolves_nothing_through_the_host_path(self):
        text = SHIM_SOURCE.read_text(encoding="utf-8")
        assert "__AUTORUN_SOCKET__" in text, "socket path must be substituted at install"
        assert "child_process" not in text, "no subprocess on the veto hot path"

    def test_shim_registers_the_veto_hook_and_the_command_tool(self):
        text = SHIM_SOURCE.read_text(encoding="utf-8")
        assert "tool.execute.before" in text
        assert "OpenCodeAttach" in text, "attach hands the daemon the serverUrl"
        assert "runArCommand" in text, "the command tool's executor must exist"
        assert '@opencode-ai/plugin' in text, "the tool registers through the plugin helper"
        assert "await import(" in text, (
            "the helper must load dynamically: bare Bun (these tests) has no "
            "node_modules, so a static import would crash the whole shim"
        )


class TestInstallerPlacesTheShim:
    def _install(self, tmp_path, monkeypatch):
        from autorun.install import _install_for_opencode

        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("OPENCODE_CONFIG_DIR", raising=False)
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        ok, msg = _install_for_opencode(MARKETPLACE_ROOT, ["autorun"])
        assert ok, msg
        return tmp_path / ".config" / "opencode"

    def test_shim_lands_in_the_singular_plugin_directory(self, tmp_path, monkeypatch):
        base = self._install(tmp_path, monkeypatch)
        assert (base / "plugin" / "autorun.js").is_file()

    def test_installed_shim_carries_an_absolute_socket_path(self, tmp_path, monkeypatch):
        base = self._install(tmp_path, monkeypatch)
        text = (base / "plugin" / "autorun.js").read_text(encoding="utf-8")
        assert "__AUTORUN_SOCKET__" not in text, "placeholder was not substituted"
        assert "daemon.sock" in text
        assert not any(
            line.strip().startswith("const SOCKET") and '"~' in line
            for line in text.splitlines()
        ), "socket path must be absolute, not tilde-relative"

    def test_the_real_uninstall_entry_point_reaches_opencode(self):
        """Calling the helper directly proves nothing about the flow users run.

        `_uninstall_harness_commands` walks only ForgeCode's base, so OpenCode
        needs its own call from `uninstall_plugins`; without it the shim
        survives an uninstall.
        """
        import inspect

        from autorun import install

        assert "_uninstall_for_opencode()" in inspect.getsource(install.uninstall_plugins)

    def test_uninstall_removes_the_shim_it_owns(self, tmp_path, monkeypatch):
        from autorun.install import _uninstall_for_opencode

        base = self._install(tmp_path, monkeypatch)
        stranger = base / "plugin" / "someone-elses.js"
        stranger.write_text("export const Other = async () => ({})\n", encoding="utf-8")

        _uninstall_for_opencode()

        assert not (base / "plugin" / "autorun.js").exists()
        assert stranger.is_file(), "uninstall removed a plugin autorun does not own"


@pytest.fixture
def short_socket_dir():
    """A directory short enough to hold a Unix socket path."""
    import tempfile

    directory = Path(tempfile.mkdtemp(prefix="arsock", dir="/tmp"))
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


class TestDaemonSocketFrames:
    """The shim speaks the wire the daemon already speaks: one newline-ended
    JSON request, one newline-ended JSON response."""

    def _serve(self, socket_path, replies, connections=2):
        """Answer N frames the way the daemon does: one connection each.

        The shim attaches before it vetoes, so a stub that accepts a single
        connection makes the veto fail open and hides the behavior under test.
        """
        frames = []

        def run():
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(str(socket_path))
            server.listen(connections)
            server.settimeout(20)
            for _ in range(connections):
                try:
                    conn, _addr = server.accept()
                except OSError:
                    break
                with conn:
                    data = b""
                    while not data.endswith(b"\n"):
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        data += chunk
                    if not data:
                        continue
                    frame = json.loads(data.decode("utf-8"))
                    frames.append(frame)
                    reply = replies.get(frame.get("hook_event_name"), {})
                    conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
            server.close()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return frames, thread

    @pytest.mark.skipif(shutil.which("bun") is None, reason="bun is required to run the shim")
    def test_shim_denies_by_throwing_when_the_daemon_says_deny(self, tmp_path, short_socket_dir):
        # AF_UNIX paths cap near 104 bytes, well under pytest's tmp_path depth.
        socket_path = short_socket_dir / "daemon.sock"
        frames, thread = self._serve(
            socket_path,
            {
                "OpenCodeAttach": {},
                "PreToolUse": {
                    "hookSpecificOutput": {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "blocked by autorun test",
                    }
                },
            },
        )
        result = _run_shim(tmp_path, socket_path, "veto")
        thread.join(timeout=25)

        assert result.returncode == 0, result.stderr
        assert "blocked by autorun test" in result.stdout

        events = [frame["hook_event_name"] for frame in frames]
        assert events == ["OpenCodeAttach", "PreToolUse"], events
        attach, veto = frames
        assert attach["server_url"].startswith("http://127.0.0.1")
        assert veto["cli_type"] == "opencode"
        assert veto["protocol_version"] == 1
        assert veto["tool_name"] == "bash"
        assert veto["tool_input"] == {"command": "rm -rf /"}

    @pytest.mark.skipif(shutil.which("bun") is None, reason="bun is required to run the shim")
    def test_unreachable_daemon_blocks_the_tool_instead_of_allowing_it(self, tmp_path):
        """Match `hook_entry.fail_closed_tool_gate`, not a second policy in JS.

        On every other harness, a tool gate autorun cannot evaluate blocks with
        a restart hint. Failing open here would mean a dead daemon silently
        removes every guard on OpenCode and nowhere else.
        """
        result = _run_shim(tmp_path, tmp_path / "nothing.sock", "veto")

        assert result.returncode == 0, result.stderr
        assert "denied:" in result.stdout, result.stdout
        assert "autorun" in result.stdout.lower()
        assert "restart-daemon" in result.stdout, "the block must name the way out"

    @pytest.mark.skipif(shutil.which("bun") is None, reason="bun is required to run the shim")
    def test_command_tool_executor_returns_the_daemon_reply(self, tmp_path, short_socket_dir):
        """`runArCommand("st")` is a UserPromptSubmit frame, answered in text.

        The registered tool is how ar:* commands dispatch on OpenCode beyond
        the six static ar-*.md files. Its executor is a plain exported
        function so this test can drive it under bare Bun; the tool wrapper
        around it only exists when @opencode-ai/plugin resolves, which
        requires a real OpenCode process.
        """
        socket_path = short_socket_dir / "daemon.sock"
        frames, thread = self._serve(
            socket_path,
            {"UserPromptSubmit": {"systemMessage": "AutoFile policy: allow-all"}},
            connections=1,
        )
        result = _run_shim(tmp_path, socket_path, "command")
        thread.join(timeout=25)

        assert result.returncode == 0, result.stderr
        assert "AutoFile policy: allow-all" in result.stdout

        (frame,) = frames
        assert frame["hook_event_name"] == "UserPromptSubmit"
        assert frame["prompt"] == "ar:st", "every input spelling canonicalizes to ar:<cmd>"
        assert frame["cli_type"] == "opencode"

    @pytest.mark.skipif(shutil.which("bun") is None, reason="bun is required to run the shim")
    def test_command_tool_executor_fails_open_when_daemon_is_down(self, tmp_path):
        """Command dispatch gates nothing, so unlike the veto it fails OPEN.

        A dead daemon must not turn `ar:st` into a blocked tool call; the
        reply names the way out instead.
        """
        result = _run_shim(tmp_path, tmp_path / "nothing.sock", "command")

        assert result.returncode == 0, result.stderr
        assert "unreachable" in result.stdout
        assert "restart-daemon" in result.stdout

    @pytest.mark.skipif(shutil.which("bun") is None, reason="bun is required to run the shim")
    def test_wedged_hook_entry_fallback_times_out_and_blocks(self, tmp_path):
        """A fallback interpreter that never answers must not hang the tool call.

        hook_entry bounds its own socket work, but uv can wedge before Python
        exists (bootstrap lock, cold cache), and OpenCode enforces no hook
        timeout the way Claude's hooks.json "timeout": 10 does. The shim owns
        the bound: TIMEOUT_MS, then a fail-closed deny, and the child is
        killed so a wedged interpreter cannot outlive the call it served.
        """
        result = _run_shim(
            tmp_path,
            tmp_path / "nothing.sock",
            "veto",
            hook_entry_command='["/bin/sleep", "300"]',
        )

        assert result.returncode == 0, result.stderr
        assert "denied:" in result.stdout, result.stdout
        assert "timed out" in result.stdout, result.stdout


class TestDaemonRecordsTheAttachment:
    """The shim hands the daemon the address OpenCode is listening on. Storing
    it is what later lets Python act as the SDK client; refusing a non-loopback
    address is what stops any local process from pointing the daemon at a host
    of its choosing."""

    def _attach(self, session_id, server_url, event="OpenCodeAttach"):
        from autorun import plugins
        from autorun.core import EventContext, ThreadSafeDB

        ctx = EventContext(
            session_id=session_id,
            event=event,
            cli_type="opencode",
            cwd="/tmp/project",
            server_url=server_url,
            store=ThreadSafeDB(),
        )
        plugins.app.dispatch(ctx)
        return ctx

    def test_loopback_attachment_is_recorded(self):
        ctx = self._attach("oc-attach-1", "http://127.0.0.1:7813/")
        attachment = ctx.state_get("opencode_attachment")

        assert attachment, "loopback attachment was not recorded"
        assert attachment["server_url"] == "http://127.0.0.1:7813/"
        assert attachment["cwd"] == "/tmp/project"

    @pytest.mark.parametrize(
        "server_url",
        [
            # RFC 5737 documentation addresses stand in for a LAN host and a
            # link-local metadata-style endpoint; the property under test is
            # that anything non-loopback is refused, whatever its class.
            "http://192.0.2.5:7813/",
            "http://example.com/",
            "https://192.0.2.254/",
            "file:///etc/passwd",
            "",
        ],
    )
    def test_non_loopback_attachment_is_refused(self, server_url):
        ctx = self._attach("oc-attach-refuse", server_url)

        assert not ctx.state_get("opencode_attachment"), (
            f"daemon accepted {server_url!r}, which it would later POST to"
        )

    def test_detach_clears_the_attachment(self):
        ctx = self._attach("oc-attach-2", "http://localhost:7813/")
        assert ctx.state_get("opencode_attachment")

        self._attach("oc-attach-2", "", event="OpenCodeDetach")

        from autorun.core import EventContext, ThreadSafeDB

        after = EventContext(
            session_id="oc-attach-2",
            event="SessionStart",
            cli_type="opencode",
            store=ThreadSafeDB(),
        )
        assert not after.state_get("opencode_attachment")


def _run_shim(tmp_path, socket_path, mode, hook_entry_command="[]"):
    """Load the installed shim under real Bun and exercise one hook."""
    shim = tmp_path / "autorun.js"
    # Same substitutions the installer performs; the default empty hook-entry
    # command makes the unreachable-daemon path exercise the last-resort block.
    shim.write_text(
        SHIM_SOURCE.read_text(encoding="utf-8")
        .replace("__AUTORUN_SOCKET__", str(socket_path))
        .replace("__AUTORUN_HOOK_ENTRY_COMMAND__", hook_entry_command),
        encoding="utf-8",
    )
    driver = tmp_path / "driver.js"
    driver.write_text(
        """
import { AutorunPlugin, runArCommand } from "./autorun.js"

if (process.argv[2] === "command") {
  // The executor stands alone: no plugin init, so no attach frame precedes
  // the command frame and the stub server sees exactly one connection.
  console.log(await runArCommand("/ar:st", process.cwd()))
  process.exit(0)
}

const hooks = await AutorunPlugin({ serverUrl: "http://127.0.0.1:1/", directory: process.cwd() })
try {
  await hooks["tool.execute.before"]({ tool: "bash", sessionID: "s1", callID: "c1" }, { args: { command: "rm -rf /" } })
  console.log("allowed")
} catch (err) {
  console.log("denied:", err.message)
}
""",
        encoding="utf-8",
    )
    return subprocess.run(
        ["bun", "run", str(driver), mode],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
        env={**os.environ, "NO_COLOR": "1"},
    )
