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

Facts these tests encode, measured against OpenCode 1.18.31:
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

from autorun.core import AutorunDaemon, app
from stop_gate_support import Session, awaiting, blocks, gate_env_fixture  # noqa: F401
from test_prompt_author_guard import REFUSAL_LEAD, send_frame, session_allows

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MARKETPLACE_ROOT = Path(__file__).resolve().parents[3]
SHIM_SOURCE = PLUGIN_ROOT / "src" / "autorun" / "opencode_template" / "plugin" / "autorun.js"
DAEMON_CLIENT_SOURCE = PLUGIN_ROOT / "src" / "autorun" / "bridge_template" / "daemon-client.mjs"


class TestShimSourceIsSelfContained:
    """The adapter and shared transport are plain modules with no build step."""

    def test_shim_ships_with_the_plugin(self):
        assert SHIM_SOURCE.is_file(), f"{SHIM_SOURCE} missing"
        assert DAEMON_CLIENT_SOURCE.is_file(), f"{DAEMON_CLIENT_SOURCE} missing"

    def test_shim_resolves_nothing_through_the_host_path(self):
        text = SHIM_SOURCE.read_text(encoding="utf-8")
        assert "__AUTORUN_SOCKET__" in text, "socket path must be substituted at install"
        assert "child_process" not in text, "no subprocess on the veto hot path"

    def test_shim_registers_the_veto_hook_and_the_command_tool(self):
        text = SHIM_SOURCE.read_text(encoding="utf-8")
        assert "tool.execute.before" in text
        assert '"chat.message"' in text, "typed prompts must reach UserPromptSubmit"
        assert 'event?.type === "session.idle"' in text, "idle must reach the Stop gate"
        assert '"experimental.session.compacting"' in text, "compaction must reach PreCompact"
        assert 'event?.type === "session.compacted"' in text, "successful compaction must reach PostCompact"
        assert 'event: async ({ event }) => {\n      if (event?.type !== "todo.updated")' not in text, (
            "the event bridge must route more than todo updates"
        )
        assert "if (!properties.sessionID" in text, (
            "a todo frame without a session id must be dropped, not sent as "
            "'' — the daemon would file those todos under a per-process key "
            "shared by every session in the OpenCode server"
        )
        assert "OpenCodeAttach" in text, "attach hands the daemon the serverUrl"
        assert "runArCommand" in text, "the command tool's executor must exist"
        assert '@opencode-ai/plugin' in text, "the tool registers through the plugin helper"
        assert "await import(" in text, (
            "the helper must load dynamically: bare Bun (these tests) has no "
            "node_modules, so a static import would crash the whole shim"
        )


class TestInstallerPlacesTheShim:
    def _install(self, tmp_path, monkeypatch):
        from autorun.installer import entrypoint

        # Both names: Path.home() resolves through os.path.expanduser, which reads
        # USERPROFILE on Windows and HOME elsewhere and never consults the other,
        # so setting one isolates this test on one platform and lets it write the
        # real home on the other.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        monkeypatch.delenv("OPENCODE_CONFIG_DIR", raising=False)
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.setattr(entrypoint, "_marketplace_root", lambda: MARKETPLACE_ROOT)
        monkeypatch.setattr(
            entrypoint.shutil,
            "which",
            lambda name: "/bin/opencode" if name == "opencode" else None,
        )
        monkeypatch.setattr(
            entrypoint,
            "_run",
            lambda argv: subprocess.CompletedProcess(argv, 0, "", ""),
        )
        assert entrypoint.install_plugins("ar", conductor=False, tool=False) == 0
        return tmp_path / ".config" / "opencode"

    def test_shim_lands_in_the_singular_plugin_directory(self, tmp_path, monkeypatch):
        base = self._install(tmp_path, monkeypatch)
        assert (base / "plugin" / "autorun.js").is_file()
        assert (base / "plugin" / "daemon-client.mjs").is_file()

    def test_installed_shim_carries_an_absolute_socket_path(self, tmp_path, monkeypatch):
        base = self._install(tmp_path, monkeypatch)
        text = (base / "plugin" / "autorun.js").read_text(encoding="utf-8")
        assert "__AUTORUN_SOCKET__" not in text, "placeholder was not substituted"
        assert "daemon.sock" in text
        # The loopback endpoint is substituted the same way. An unsubstituted
        # placeholder is not a crash: the shim would read a file literally
        # named __AUTORUN_PORT_FILE__, find nothing, and report the daemon
        # unreachable on exactly the platform that needs this path.
        assert "__AUTORUN_PORT_FILE__" not in text, "port placeholder was not substituted"
        assert "daemon.port" in text
        assert not any(
            line.strip().startswith(("const SOCKET", "const PORT_FILE")) and "~" in line
            for line in text.splitlines()
        ), "daemon paths must be absolute, not tilde-relative"
        # The substituted values must be complete JS literals. A raw Windows
        # path would leave backslash escapes that change or break the string.
        for name in ("socketPath: ", "portFile: "):
            line = next(row.strip() for row in text.splitlines() if row.strip().startswith(name))
            assert json.loads(line[len(name):].rstrip(",")), line

    def test_uninstall_removes_the_shim_it_owns(self, tmp_path, monkeypatch):
        from autorun.installer import entrypoint

        base = self._install(tmp_path, monkeypatch)
        stranger = base / "plugin" / "someone-elses.js"
        stranger.write_text("export const Other = async () => ({})\n", encoding="utf-8")

        assert entrypoint.uninstall_plugins("ar") == 0

        assert not (base / "plugin" / "autorun.js").exists()
        assert stranger.is_file(), "uninstall removed a plugin autorun does not own"


@pytest.fixture
def policy_daemon(monkeypatch):
    monkeypatch.delenv("AUTORUN_SESSION_ID", raising=False)
    monkeypatch.delenv("AUTORUN_USE_IDENTITY", raising=False)
    return AutorunDaemon(app)


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

    # These three stand the stub daemon up on an AF_UNIX socket, which Windows
    # cannot bind at all: the limitation is in the test's own server, not in
    # the shim. The Windows transport is covered by
    # test_shim_reaches_a_loopback_daemon_when_there_is_no_socket, which serves
    # the same frames over loopback and runs everywhere.
    unix_only = pytest.mark.skipif(
        not hasattr(socket, "AF_UNIX"),
        reason="the stub daemon binds AF_UNIX; the loopback test covers this platform",
    )

    def test_port_file_requires_one_complete_valid_port(self):
        source = DAEMON_CLIENT_SOURCE.read_text(encoding="utf-8")
        assert "Number.parseInt" not in source
        assert "port <= 65535" in source

    @pytest.mark.skipif(shutil.which("bun") is None or not hasattr(socket, "AF_UNIX"), reason="Bun and Unix sockets required")
    @pytest.mark.parametrize("degraded", [True, False])
    def test_tool_allow_notice_reaches_user_and_tool_result(self, tmp_path, short_socket_dir, degraded):
        from autorun import client
        from autorun.platforms import PLATFORMS
        response = (client.degraded_tool_response({"hook_event_name": "PreToolUse", "tool_name": "read"}, "opencode", "daemon_response_timeout")
                    if degraded else PLATFORMS["opencode"].hook_protocol.pretool_response("allow", "healthy hint", "PreToolUse"))
        path = short_socket_dir / "notice.sock"
        frames, thread = self._serve(path, {"PreToolUse": response}, connections=3)
        result = _run_shim(tmp_path, path, "degraded-notice")
        thread.join(timeout=10)
        assert result.returncode == 0 and result.stderr == "", result.stderr
        output = json.loads(result.stdout)
        assert output["output"].startswith("contents")
        if degraded:
            assert client.DEGRADED_NOTICE in output["output"]
            assert len(output["toasts"]) == 1
            assert client.DEGRADED_NOTICE in output["toasts"][0]["body"]["message"]
        else:
            assert output == {"output": "contents", "toasts": []}
        assert [frame["hook_event_name"] for frame in frames] == ["OpenCodeAttach", "PreToolUse", "PostToolUse"]

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

    _TCP_TOKEN = "stub-token"

    def _serve_tcp(self, replies, connections=2):
        """The same stub daemon on loopback, which is how Windows listens.

        Like ipc.start_server it serves a connection only when its first line
        is the token published beside the port file."""
        frames = []
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        port = server.getsockname()[1]
        server.listen(connections)
        server.settimeout(20)

        def run():
            for _ in range(connections):
                try:
                    conn, _addr = server.accept()
                except OSError:
                    break
                with conn:
                    data = b""
                    while data.count(b"\n") < 2:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        data += chunk
                    token, _, request = data.decode("utf-8").partition("\n")
                    if token != self._TCP_TOKEN or not request:
                        continue
                    frame = json.loads(request)
                    frames.append(frame)
                    reply = replies.get(frame.get("hook_event_name"), {})
                    conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
            server.close()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return frames, thread, port

    @pytest.mark.skipif(shutil.which("bun") is None, reason="bun is required to run the shim")
    def test_shim_reaches_a_loopback_daemon_when_there_is_no_socket(self, tmp_path):
        """The Windows transport, exercised anywhere.

        CPython has no AF_UNIX on Windows, so the daemon listens on loopback
        and publishes its port in a file. Pointing the shim at a socket path
        that does not exist makes it take that route on any platform, so the
        transport is covered without a Windows runner.
        """
        frames, thread, port = self._serve_tcp(
            {
                "OpenCodeAttach": {},
                "PreToolUse": {
                    "hookSpecificOutput": {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "blocked over tcp",
                    }
                },
            }
        )
        port_file = tmp_path / "daemon.port"
        port_file.write_text(str(port), encoding="utf-8")
        (tmp_path / "daemon.token").write_text(self._TCP_TOKEN, encoding="utf-8")

        result = _run_shim(
            tmp_path,
            tmp_path / "absent" / "daemon.sock",
            "deny",
            port_file=port_file,
        )
        thread.join(timeout=20)

        assert "blocked over tcp" in result.stdout, result.stdout
        assert [f.get("hook_event_name") for f in frames] == [
            "OpenCodeAttach",
            "PreToolUse",
        ], frames

    @pytest.mark.skipif(shutil.which("bun") is None, reason="bun is required to run the shim")
    @unix_only
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

        assert "denied:" in result.stdout, result.stdout
        assert "autorun" in result.stdout.lower()
        assert "wait and retry" in result.stdout, "an unavailable guard must name the way out"

    @pytest.mark.skipif(shutil.which("bun") is None, reason="bun is required to run the shim")
    @unix_only
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

        assert "unreachable" in result.stdout
        assert "restart-daemon" in result.stdout

    @pytest.mark.skipif(shutil.which("bun") is None, reason="bun is required to run the shim")
    @unix_only
    def test_prompt_author_cache_is_bounded_and_session_delete_evicts(
        self, tmp_path, short_socket_dir
    ):
        socket_path = short_socket_dir / "daemon.sock"
        frames, thread = self._serve(
            socket_path,
            {"OpenCodeAttach": {}, "UserPromptSubmit": {}},
            connections=14,
        )
        result = _run_shim(tmp_path, socket_path, "prompt-cache-lru")
        thread.join(timeout=25)

        assert "session-get-count 12" in result.stdout
        assert len(frames) == 14

    @pytest.mark.skipif(shutil.which("bun") is None, reason="bun is required to run the shim")
    @unix_only
    def test_failed_session_lookup_is_cached_for_three_prompts(
        self, tmp_path, short_socket_dir
    ):
        socket_path = short_socket_dir / "daemon.sock"
        frames, thread = self._serve(
            socket_path,
            {"OpenCodeAttach": {}, "UserPromptSubmit": {}},
            connections=5,
        )
        result = _run_shim(tmp_path, socket_path, "prompt-unknown-repeat")
        thread.join(timeout=25)

        assert "session-get-count 2" in result.stdout
        assert {frame["prompt_author"] for frame in frames[1:]} == {"automation"}

    @pytest.mark.skipif(shutil.which("bun") is None, reason="bun is required to run the shim")
    @unix_only
    @pytest.mark.parametrize(
        ("mode", "expected_author"),
        [
            ("prompt-root", "user"),
            ("prompt-root-repeat", "user"),
            ("prompt-child", "subagent"),
            ("prompt-compaction", "automation"),
            ("prompt-automation", "automation"),
            ("prompt-mixed-synthetic", "automation"),
            ("prompt-unknown-session", "automation"),
        ],
    )
    def test_prompt_author_uses_session_parent_and_synthetic_parts(
        self, tmp_path, short_socket_dir, mode, expected_author
    ):
        """The bridge uses the strongest origin evidence OpenCode exposes."""
        socket_path = short_socket_dir / "daemon.sock"
        prompt_count = 2 if mode == "prompt-root-repeat" else 1
        frames, thread = self._serve(
            socket_path,
            {
                "OpenCodeAttach": {},
                "UserPromptSubmit": {"systemMessage": "Cache expired; review cost before continuing."},
            },
            connections=1 + prompt_count,
        )
        result = _run_shim(tmp_path, socket_path, mode)
        thread.join(timeout=25)

        assert "Cache expired; review cost before continuing." in result.stdout
        prompts = frames[1:]
        assert len(prompts) == prompt_count
        assert {prompt["hook_event_name"] for prompt in prompts} == {"UserPromptSubmit"}
        assert {prompt["session_id"] for prompt in prompts} == {"prompt-session"}
        assert {prompt["prompt_author"] for prompt in prompts} == {expected_author}
        if mode == "prompt-root-repeat":
            assert "session-get-count 1" in result.stdout, (
                "the bridge must cache parentID per session instead of fetching it per prompt"
            )

    @pytest.mark.skipif(shutil.which("bun") is None, reason="bun is required to run the shim")
    @unix_only
    @pytest.mark.parametrize(
        ("mode", "grants"),
        [
            ("prompt-root-command", True),
            ("prompt-child-command", False),
            ("prompt-synthetic-command", False),
        ],
    )
    def test_prompt_classification_controls_user_only_commands(
        self, tmp_path, short_socket_dir, policy_daemon, mode, grants
    ):
        socket_path = short_socket_dir / "daemon.sock"
        frames, thread = self._serve(socket_path, {"OpenCodeAttach": {}, "UserPromptSubmit": {}})
        _run_shim(tmp_path, socket_path, mode)
        thread.join(timeout=25)

        prompt = {**frames[1], "session_id": mode}
        response = send_frame(policy_daemon, prompt)
        allowed = session_allows(policy_daemon, mode, "opencode")
        if grants:
            assert allowed == ["git push"], response
        else:
            assert allowed == []
            assert REFUSAL_LEAD in json.dumps(response), response

    @pytest.mark.skipif(shutil.which("bun") is None, reason="bun is required to run the shim")
    @unix_only
    def test_root_user_reply_restores_awaiting_user_task(
        self, tmp_path, short_socket_dir, gate_env
    ):
        session = Session("prompt-session", "opencode")
        session.seed("7", "Push release-1.0")
        session.stop(awaiting("7", "approve pushing release-1.0"))
        assert session.status("7") == "awaiting-user"

        socket_path = short_socket_dir / "daemon.sock"
        frames, thread = self._serve(socket_path, {"OpenCodeAttach": {}, "UserPromptSubmit": {}})
        _run_shim(tmp_path, socket_path, "prompt-root-reply")
        thread.join(timeout=25)

        prompt = frames[1]
        session.prompt(prompt["prompt"], prompt_author=prompt["prompt_author"])
        assert session.status("7") == "in_progress"
        assert blocks(session.stop("Work remains."), "opencode")

    @pytest.mark.skipif(shutil.which("bun") is None, reason="bun is required to run the shim")
    @unix_only
    def test_idle_stop_block_continues_through_prompt_async(self, tmp_path, short_socket_dir):
        socket_path = short_socket_dir / "daemon.sock"
        frames, thread = self._serve(
            socket_path,
            {
                "OpenCodeAttach": {},
                "Stop": {"decision": "block", "reason": "Finish task #7 before stopping."},
            },
        )
        result = _run_shim(tmp_path, socket_path, "idle")
        thread.join(timeout=25)

        calls = json.loads(result.stdout)
        assert calls == {
            "promptAsyncCalls": [{
                "path": {"id": "idle-session"},
                "query": {"directory": str(tmp_path)},
                "body": {"parts": [{
                    "type": "text",
                    "text": "Finish task #7 before stopping.",
                    "synthetic": True,
                    "metadata": {"source": "autorun-stop-gate"},
                }]},
            }],
            "promptCalls": [],
            "logCalls": [],
        }
        assert [frame["hook_event_name"] for frame in frames] == ["OpenCodeAttach", "Stop"]
        assert frames[1]["session_id"] == "idle-session"

    @pytest.mark.skipif(shutil.which("bun") is None, reason="bun is required to run the shim")
    @unix_only
    def test_idle_stop_block_falls_back_to_prompt(self, tmp_path, short_socket_dir):
        socket_path = short_socket_dir / "daemon.sock"
        frames, thread = self._serve(
            socket_path,
            {
                "OpenCodeAttach": {},
                "Stop": {"decision": "block", "reason": "Finish task #7 before stopping."},
            },
        )
        result = _run_shim(tmp_path, socket_path, "idle-prompt")
        thread.join(timeout=25)

        calls = json.loads(result.stdout)
        assert calls["promptCalls"] == [{
            "path": {"id": "idle-session"},
            "query": {"directory": str(tmp_path)},
            "body": {"parts": [{
                "type": "text",
                "text": "Finish task #7 before stopping.",
                "synthetic": True,
                "metadata": {"source": "autorun-stop-gate"},
            }]},
        }]
        assert calls["toastCalls"] == []
        assert [frame["hook_event_name"] for frame in frames] == ["OpenCodeAttach", "Stop"]

    @pytest.mark.skipif(shutil.which("bun") is None, reason="bun is required to run the shim")
    @unix_only
    def test_idle_handler_returns_while_the_prompt_fallback_turn_is_still_running(self, tmp_path, short_socket_dir):
        """`client.session.prompt` resolves when the turn ends, so the idle handler must not wait for it."""
        socket_path = short_socket_dir / "daemon.sock"
        frames, thread = self._serve(
            socket_path,
            {
                "OpenCodeAttach": {},
                "Stop": {"decision": "block", "reason": "Finish task #7 before stopping."},
            },
        )
        result = _run_shim(tmp_path, socket_path, "idle-prompt-pending")
        thread.join(timeout=25)

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip(), "the idle handler never returned while prompt() was pending"
        assert json.loads(result.stdout) == {"promptCalls": 1, "toastCalls": []}

    @pytest.mark.skipif(shutil.which("bun") is None, reason="bun is required to run the shim")
    @unix_only
    def test_idle_stop_block_logs_prompt_async_rejection_before_prompt_fallback(
        self, tmp_path, short_socket_dir
    ):
        socket_path = short_socket_dir / "daemon.sock"
        frames, thread = self._serve(
            socket_path,
            {
                "OpenCodeAttach": {},
                "Stop": {"decision": "block", "reason": "Finish task #7 before stopping."},
            },
        )
        result = _run_shim(tmp_path, socket_path, "idle-reject")
        thread.join(timeout=25)

        calls = json.loads(result.stdout)
        assert len(calls["promptCalls"]) == 1
        assert calls["logCalls"] == [{
            "query": {"directory": str(tmp_path)},
            "body": {
                "service": "autorun",
                "level": "warn",
                "message": "OpenCode promptAsync continuation failed: rejected promptAsync",
            },
        }]
        assert [frame["hook_event_name"] for frame in frames] == ["OpenCodeAttach", "Stop"]

    @pytest.mark.skipif(shutil.which("bun") is None, reason="bun is required to run the shim")
    @unix_only
    def test_idle_stop_block_surfaces_warning_toast(self, tmp_path, short_socket_dir):
        socket_path = short_socket_dir / "daemon.sock"
        frames, thread = self._serve(
            socket_path,
            {
                "OpenCodeAttach": {},
                "Stop": {"decision": "block", "reason": "Finish task #7 before stopping."},
            },
        )
        result = _run_shim(tmp_path, socket_path, "idle-toast")
        thread.join(timeout=25)

        calls = json.loads(result.stdout)
        assert calls == [{
            "query": {"directory": str(tmp_path)},
            "body": {
                "title": "Autorun could not continue this session",
                "message": (
                    "Finish task #7 before stopping.\n\n"
                    "This OpenCode client cannot enforce the continuation; continue manually."
                ),
                "variant": "warning",
                "duration": 12000,
            },
        }]
        assert [frame["hook_event_name"] for frame in frames] == ["OpenCodeAttach", "Stop"]

    @pytest.mark.skipif(shutil.which("bun") is None, reason="bun is required to run the shim")
    @unix_only
    def test_idle_stop_block_uses_structured_log_without_toast(self, tmp_path, short_socket_dir):
        socket_path = short_socket_dir / "daemon.sock"
        frames, thread = self._serve(
            socket_path,
            {
                "OpenCodeAttach": {},
                "Stop": {"decision": "block", "reason": "Finish task #7 before stopping."},
            },
        )
        result = _run_shim(tmp_path, socket_path, "idle-log")
        thread.join(timeout=25)

        assert json.loads(result.stdout) == [{
            "query": {"directory": str(tmp_path)},
            "body": {
                "service": "autorun",
                "level": "warn",
                "message": (
                    "Stop blocked but this OpenCode client cannot continue the session: "
                    "Finish task #7 before stopping."
                ),
            },
        }]
        assert [frame["hook_event_name"] for frame in frames] == ["OpenCodeAttach", "Stop"]

    @pytest.mark.skipif(shutil.which("bun") is None, reason="bun is required to run the shim")
    @unix_only
    def test_idle_stop_block_without_client_apis_is_silent_and_does_not_throw(
        self, tmp_path, short_socket_dir
    ):
        socket_path = short_socket_dir / "daemon.sock"
        frames, thread = self._serve(
            socket_path,
            {
                "OpenCodeAttach": {},
                "Stop": {"decision": "block", "reason": "Finish task #7 before stopping."},
            },
        )
        result = _run_shim(tmp_path, socket_path, "idle-no-api")
        thread.join(timeout=25)

        assert result.stdout == ""
        assert result.stderr == ""
        assert [frame["hook_event_name"] for frame in frames] == ["OpenCodeAttach", "Stop"]

    @pytest.mark.skipif(shutil.which("bun") is None, reason="bun is required to run the shim")
    def test_idle_with_unreachable_daemon_stays_fail_open_and_silent(self, tmp_path):
        result = _run_shim(tmp_path, tmp_path / "nothing.sock", "idle-no-api")

        assert result.stdout == ""
        assert result.stderr == ""

    @pytest.mark.skipif(shutil.which("bun") is None, reason="bun is required to run the shim")
    @unix_only
    def test_compaction_routes_pre_and_post_events(self, tmp_path, short_socket_dir):
        socket_path = short_socket_dir / "daemon.sock"
        frames, thread = self._serve(
            socket_path,
            {
                "OpenCodeAttach": {},
                "PreCompact": {"systemMessage": "Preserve the task ledger."},
                "PostCompact": {},
            },
            connections=3,
        )
        result = _run_shim(tmp_path, socket_path, "compact")
        thread.join(timeout=25)

        assert "Preserve the task ledger." in result.stdout
        assert [frame["hook_event_name"] for frame in frames] == [
            "OpenCodeAttach", "PreCompact", "PostCompact"
        ]

    def test_ordinary_tool_completion_resets_the_stop_loop(self, tmp_path, gate_env):
        session = Session("tool-session", "opencode")
        session.seed("7", "Finish work")
        for _ in range(3):
            assert blocks(session.stop("Work remains."), "opencode")
        frames, thread, port = self._serve_tcp({"OpenCodeAttach": {}, "PostToolUse": {}})
        port_file = tmp_path / "daemon.port"
        port_file.write_text(str(port), encoding="utf-8")
        (tmp_path / "daemon.token").write_text(self._TCP_TOKEN, encoding="utf-8")
        result = _run_shim(tmp_path, tmp_path / "absent.sock", "tool-after", port_file=port_file)
        thread.join(timeout=25)
        assert result.stderr == ""
        for frame in frames[1:]:
            assert frame["session_id"] == session.session_id
            assert frame["tool_name"] == "read"
            assert "tool_input" not in frame and "tool_result" not in frame
            session.tool(frame["tool_name"], frame.get("tool_input"), frame.get("tool_result", ""))
        assert blocks(session.stop("Still unfinished."), "opencode")
        assert [frame["hook_event_name"] for frame in frames] == ["OpenCodeAttach", "PostToolUse"]
        for _ in range(2):
            assert blocks(session.stop("No more tools."), "opencode")
        assert not blocks(session.stop("No more tools."), "opencode")

    @pytest.mark.skipif(shutil.which("bun") is None, reason="bun is required to run the shim")
    @unix_only
    def test_todo_updated_event_reaches_post_tool_use(self, tmp_path, short_socket_dir):
        """OpenCode's native todo event must feed the shared task lifecycle."""
        socket_path = short_socket_dir / "daemon.sock"
        frames, thread = self._serve(
            socket_path,
            {"OpenCodeAttach": {}, "PostToolUse": {}},
            connections=2,
        )
        result = _run_shim(tmp_path, socket_path, "todo")
        thread.join(timeout=25)

        assert [frame["hook_event_name"] for frame in frames] == [
            "OpenCodeAttach",
            "PostToolUse",
        ], frames
        post_tool = frames[1]
        assert post_tool["session_id"] == "todo-session"
        assert post_tool["tool_name"] == "todowrite"
        assert post_tool["tool_input"]["todos"][0]["id"] == "todo-1"

    @pytest.mark.skipif(shutil.which("bun") is None, reason="bun is required to run the shim")
    @unix_only
    def test_wedged_hook_entry_fallback_times_out_and_blocks(self, tmp_path):
        """A fallback interpreter that never answers must not hang the tool call.

        hook_entry bounds its own socket work, but uv can wedge before Python
        exists (bootstrap lock, cold cache), and OpenCode enforces no hook
        timeout the way Claude's hooks.json "timeout": 10 does. The shim owns
        the bound: TIMEOUT_MS, then a fail-closed deny, and the child is
        killed so a wedged interpreter cannot outlive the call it served.

        ELAPSED TIME IS THE ASSERTION — do not shorten to speed up the suite.
        This test waits out TIMEOUT_MS on purpose; that wait is the bound being
        verified. Lowering it to save the ~5s would leave the shim's real
        timeout untested and a wedged interpreter free to hang a tool call.
        """
        result = _run_shim(
            tmp_path,
            tmp_path / "nothing.sock",
            "veto",
            hook_entry_command='["/bin/sleep", "300"]',
        )

        assert "denied:" in result.stdout, result.stdout
        assert "critical guards could not finish" in result.stdout, result.stdout


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


def _run_shim(
    tmp_path, socket_path, mode, hook_entry_command="[]", port_file="__missing__"
):
    """Load the installed shim under real Bun and exercise one hook."""
    shim = tmp_path / "autorun.js"
    shutil.copy2(DAEMON_CLIENT_SOURCE, tmp_path / "daemon-client.mjs")
    # Same substitutions the installer performs; the default empty hook-entry
    # command makes the unreachable-daemon path exercise the last-resort block.
    shim.write_text(
        SHIM_SOURCE.read_text(encoding="utf-8")
        # Same JSON encoding the installer applies: the placeholders carry no
        # quotes of their own, so a raw path would not even be a JS literal.
        .replace("__AUTORUN_SOCKET__", json.dumps(str(socket_path)))
        .replace("__AUTORUN_PORT_FILE__", json.dumps(str(port_file)))
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

let sessionGetCount = 0
const promptAsyncCalls = []
const promptCalls = []
const logCalls = []
const client = {
  app: {
    log: async (options) => logCalls.push(options),
  },
  session: {
    get: async ({ path }) => {
      sessionGetCount += 1
      if (process.argv[2].includes("prompt-unknown")) throw new Error("session missing")
      const child = process.argv[2].includes("child")
      return { data: { id: path.id, ...(child ? { parentID: "parent-session" } : {}) } }
    },
    prompt: async (options) => promptCalls.push(options),
    promptAsync: async (options) => promptAsyncCalls.push(options),
  },
}

if (process.argv[2].startsWith("prompt-")) {
  const hooks = await AutorunPlugin({ serverUrl: "http://127.0.0.1:1/", directory: process.cwd(), client })
  const mode = process.argv[2]
  const makeOutput = (text) => ({
    message: { role: "user" },
    parts: [{ type: "text", text }],
  })
  if (mode === "prompt-cache-lru") {
    for (let index = 0; index < 10; index += 1) {
      await hooks["chat.message"]({ sessionID: `session-${index}` }, makeOutput(`prompt ${index}`))
    }
    await hooks["chat.message"]({ sessionID: "session-9" }, makeOutput("recent"))
    await hooks["chat.message"]({ sessionID: "session-0" }, makeOutput("evicted"))
    await hooks.event({ event: { type: "session.deleted", properties: { sessionID: "session-9" } } })
    await hooks["chat.message"]({ sessionID: "session-9" }, makeOutput("deleted"))
    console.log("session-get-count", sessionGetCount)
    process.exit(0)
  }
  if (mode === "prompt-unknown-repeat") {
    for (let index = 0; index < 4; index += 1) {
      await hooks["chat.message"]({ sessionID: "prompt-session" }, makeOutput(`unknown ${index}`))
    }
    console.log("session-get-count", sessionGetCount)
    process.exit(0)
  }
  const input = { sessionID: "prompt-session" }
  const command = mode.endsWith("-command")
  const reply = mode.endsWith("-reply")
  const output = {
    message: { role: "user" },
    parts: [{ type: "text", text: command ? "ar:ok 'git push'" : reply ? "Approved; continue" : `ordinary ${mode} prompt` }],
  }
  if (mode === "prompt-compaction" || mode === "prompt-synthetic-command") output.parts[0].synthetic = true
  if (mode === "prompt-automation") {
    output.parts[0].synthetic = true
    output.parts[0].metadata = { source: "autorun-stop-gate" }
  }
  if (mode === "prompt-mixed-synthetic") {
    output.parts.push({ type: "text", text: "injected", synthetic: true })
  }
  await hooks["chat.message"](input, output)
  if (mode === "prompt-root-repeat") {
    await hooks["chat.message"](input, {
      message: { role: "user" },
      parts: [{ type: "text", text: "second root prompt" }],
    })
  }
  console.log("session-get-count", sessionGetCount)
  console.log(JSON.stringify(output.parts))
  process.exit(0)
}

if (process.argv[2] === "idle") {
  const hooks = await AutorunPlugin({ serverUrl: "http://127.0.0.1:1/", directory: process.cwd(), client })
  await hooks.event({ event: { type: "session.idle", properties: { sessionID: "idle-session" } } })
  console.log(JSON.stringify({ promptAsyncCalls, promptCalls, logCalls }))
  process.exit(0)
}

if (process.argv[2] === "idle-prompt") {
  const toastCalls = []
  const oldClient = {
    session: { prompt: async (options) => promptCalls.push(options) },
    tui: { showToast: async (options) => toastCalls.push(options) },
  }
  const hooks = await AutorunPlugin({ serverUrl: "http://127.0.0.1:1/", directory: process.cwd(), client: oldClient })
  await hooks.event({ event: { type: "session.idle", properties: { sessionID: "idle-session" } } })
  console.log(JSON.stringify({ promptCalls, toastCalls }))
  process.exit(0)
}

if (process.argv[2] === "idle-prompt-pending") {
  const toastCalls = []
  const oldClient = {
    // A turn that is still running: prompt() resolves only when it ends.
    session: { prompt: (options) => { promptCalls.push(options); return new Promise(() => {}) } },
    tui: { showToast: async (options) => toastCalls.push(options) },
  }
  const hooks = await AutorunPlugin({ serverUrl: "http://127.0.0.1:1/", directory: process.cwd(), client: oldClient })
  await hooks.event({ event: { type: "session.idle", properties: { sessionID: "idle-session" } } })
  console.log(JSON.stringify({ promptCalls: promptCalls.length, toastCalls }))
  process.exit(0)
}

if (process.argv[2] === "idle-reject") {
  const rejectingClient = {
    app: { log: async (options) => logCalls.push(options) },
    session: {
      promptAsync: async () => { throw new Error("rejected promptAsync") },
      prompt: async (options) => promptCalls.push(options),
    },
  }
  const hooks = await AutorunPlugin({ serverUrl: "http://127.0.0.1:1/", directory: process.cwd(), client: rejectingClient })
  await hooks.event({ event: { type: "session.idle", properties: { sessionID: "idle-session" } } })
  console.log(JSON.stringify({ promptCalls, logCalls }))
  process.exit(0)
}

if (process.argv[2] === "idle-toast") {
  const toastCalls = []
  const oldClient = {
    tui: { showToast: async (options) => toastCalls.push(options) },
  }
  const hooks = await AutorunPlugin({ serverUrl: "http://127.0.0.1:1/", directory: process.cwd(), client: oldClient })
  await hooks.event({ event: { type: "session.idle", properties: { sessionID: "idle-session" } } })
  console.log(JSON.stringify(toastCalls))
  process.exit(0)
}

if (process.argv[2] === "idle-log") {
  const logClient = { app: { log: async (options) => logCalls.push(options) } }
  const hooks = await AutorunPlugin({ serverUrl: "http://127.0.0.1:1/", directory: process.cwd(), client: logClient })
  await hooks.event({ event: { type: "session.idle", properties: { sessionID: "idle-session" } } })
  console.log(JSON.stringify(logCalls))
  process.exit(0)
}

if (process.argv[2] === "idle-no-api") {
  const hooks = await AutorunPlugin({ serverUrl: "http://127.0.0.1:1/", directory: process.cwd(), client: {} })
  await hooks.event({ event: { type: "session.idle", properties: { sessionID: "idle-session" } } })
  process.exit(0)
}

if (process.argv[2] === "compact") {
  const hooks = await AutorunPlugin({ serverUrl: "http://127.0.0.1:1/", directory: process.cwd(), client })
  const output = { context: [] }
  await hooks["experimental.session.compacting"]({ sessionID: "compact-session" }, output)
  console.log(JSON.stringify(output.context))
  await hooks.event({ event: { type: "session.compacted", properties: { sessionID: "compact-session" } } })
  process.exit(0)
}

if (process.argv[2] === "degraded-notice") {
  const toasts = []
  client.tui = { showToast: async options => toasts.push(options) }
  const hooks = await AutorunPlugin({ directory: process.cwd(), client })
  const input = { tool: "read", sessionID: "tool-session", callID: "c1" }
  await hooks["tool.execute.before"](input, { args: {} })
  const output = { title: "file", output: "contents", metadata: {} }
  await hooks["tool.execute.after"](input, output)
  console.log(JSON.stringify({output: output.output, toasts}))
  process.exit(0)
}

if (process.argv[2] === "tool-after") {
  const hooks = await AutorunPlugin({ directory: process.cwd(), client })
  await hooks["tool.execute.after"]?.({ tool: "read", sessionID: "tool-session", callID: "c1", args: { filePath: "a.txt" } }, { title: "a.txt", output: "contents", metadata: {} })
  process.exit(0)
}

if (process.argv[2] === "todo") {
  const hooks = await AutorunPlugin({ serverUrl: "http://127.0.0.1:1/", directory: process.cwd(), client })
  await hooks.event({ event: {
    type: "todo.updated",
    properties: {
      sessionID: "todo-session",
      todos: [{ id: "todo-1", content: "Ship parity", status: "in_progress", priority: "high" }],
    },
  } })
  process.exit(0)
}

const hooks = await AutorunPlugin({ serverUrl: "http://127.0.0.1:1/", directory: process.cwd(), client })
try {
  await hooks["tool.execute.before"]({ tool: "bash", sessionID: "s1", callID: "c1" }, { args: { command: "rm -rf /" } })
  console.log("allowed")
} catch (err) {
  console.log("denied:", err.message)
}
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        # --no-install: the shim's optional `import("@opencode-ai/plugin")`
        # made Bun fetch the package from npm, so these tests depended on the
        # network and hung for their whole timeout when it was slow.
        ["bun", "run", "--no-install", str(driver), mode],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
        env={**os.environ, "NO_COLOR": "1"},
    )
    # Every driver path above exits 0, so a non-zero code means Bun itself
    # failed and stdout is empty. Asserting it here rather than in each caller
    # keeps the crash readable: a Bun segfault on Windows once surfaced as
    # `assert 'blocked over tcp' in ''`, which points at the bridge instead of
    # at the interpreter that died. Callers assert on stdout only.
    assert result.returncode == 0, (
        f"bun exited {result.returncode} for mode {mode!r}; stderr:\n{result.stderr}"
    )
    return result
