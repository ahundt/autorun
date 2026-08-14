"""Pi's in-process extension routes native events through autorun's daemon.

The tests use redirected homes and synthetic sockets only. They never load the
user's Pi configuration or contact a model provider.
"""

from __future__ import annotations

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
PI_TEMPLATE = PLUGIN_ROOT / "src" / "autorun" / "pi_template"
BRIDGE_SOURCE = PLUGIN_ROOT / "src" / "autorun" / "bridge_template" / "daemon-client.mjs"


def test_pi_platform_declares_native_runtime_contract():
    from autorun.platforms import PLATFORMS

    pi = PLATFORMS["pi"]
    assert pi.binary == "pi"
    assert pi.config_dir == "~/.pi/agent/"
    assert pi.config_dir_env_vars == ("PI_CODING_AGENT_DIR",)
    assert pi.detect_env_vars == ("PI_CODING_AGENT", "PI_SESSION_ID")
    assert pi.standalone_session_env_vars == ("PI_SESSION_ID",)
    assert pi.loads_shared_agents_skills is True
    assert pi.hook_protocol.name == "pi"
    assert pi.tool_names["bash"] == "bash"
    assert pi.command_display_prefix == "/ar "
    assert pi.task_management_style == "task_tools"
    assert pi.task_create_tools == frozenset({"TaskCreate"})
    assert pi.task_update_tools == frozenset({"TaskUpdate"})
    assert pi.task_review_tools == frozenset({"TaskList"})


def test_pi_has_one_installer_step_row_and_dedicated_extension_step():
    from autorun.installer import steps
    from autorun.platforms import PLATFORMS

    assert "pi" in PLATFORMS
    assert "pi" in steps.STEPS
    assert steps.pi_extension_step in steps.STEPS["pi"]
    assert steps.opencode_shim_step not in steps.STEPS["pi"]


def _install_pi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from autorun.installer import entrypoint

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(home / ".pi" / "agent"))
    monkeypatch.setenv("AUTORUN_HOME", f"/tmp/api-{tmp_path.name[-8:]}")
    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", f"/tmp/apis-{tmp_path.name[-8:]}")
    monkeypatch.setattr(entrypoint, "_marketplace_root", lambda: MARKETPLACE_ROOT)
    monkeypatch.setattr(
        entrypoint.shutil,
        "which",
        lambda name: "/opt/homebrew/bin/pi" if name == "pi" else None,
    )
    monkeypatch.setattr(
        entrypoint,
        "_run",
        lambda argv: subprocess.CompletedProcess(argv, 0, "", ""),
    )
    assert entrypoint.install_plugins("ar", conductor=False, tool=False) == 0
    return home


def test_pi_install_is_owned_idempotent_and_preserves_other_extensions(tmp_path, monkeypatch):
    from autorun.installer import entrypoint
    from autorun.installer.fs import TreeManifest

    home = _install_pi(tmp_path, monkeypatch)
    extension_root = home / ".pi" / "agent" / "extensions"
    installed = extension_root / "ar"
    stranger = extension_root / "mine.ts"
    stranger.write_text("export default () => {}\n", encoding="utf-8")

    assert (installed / "index.ts").is_file()
    assert (installed / "daemon-client.mjs").is_file()
    assert (installed / ".autorun-owned").is_file()

    obsolete = installed / "obsolete-owned-runtime.ts"
    obsolete.write_text("stale\n", encoding="utf-8")
    marker = TreeManifest.of(installed, "ar").as_payload()
    (installed / ".autorun-owned").write_text(
        json.dumps(marker, indent=2) + "\n", encoding="utf-8"
    )

    assert entrypoint.install_plugins("ar", conductor=False, tool=False) == 0
    assert not obsolete.exists(), "reinstall must replace the complete owned Pi runtime"
    assert stranger.is_file()

    assert entrypoint.uninstall_plugins("ar") == 0
    assert not installed.exists()
    assert stranger.is_file()


def test_pi_uninstall_preserves_a_user_modified_owned_extension(tmp_path, monkeypatch):
    from autorun.installer import entrypoint

    home = _install_pi(tmp_path, monkeypatch)
    installed = home / ".pi" / "agent" / "extensions" / "ar"
    sibling = installed.parent / "sibling.ts"
    sibling.write_text("export default () => {}\n", encoding="utf-8")
    (installed / "index.ts").write_text(
        "// user customization\n", encoding="utf-8"
    )

    assert entrypoint.uninstall_plugins("ar") == 0
    assert installed.is_dir(), "uninstall must preserve a user-modified owned tree"
    assert (installed / "index.ts").read_text(encoding="utf-8") == (
        "// user customization\n"
    )
    assert sibling.is_file()


def test_pi_development_install_uses_redirected_runtime_roots(tmp_path, monkeypatch):
    live_home = Path.home()
    live_extension = live_home / ".pi" / "agent" / "extensions" / "ar"
    before = {
        path.relative_to(live_extension): path.read_bytes()
        for path in live_extension.rglob("*")
        if path.is_file()
    } if live_extension.is_dir() else None
    home = _install_pi(tmp_path, monkeypatch)
    installed = home / ".pi" / "agent" / "extensions" / "ar" / "index.ts"
    text = installed.read_text(encoding="utf-8")

    autorun_home = Path(os.environ["AUTORUN_HOME"])
    assert str(autorun_home / "daemon.sock") in text
    assert str(autorun_home / "daemon.port") in text
    assert str(live_home / ".autorun") not in text
    after = {
        path.relative_to(live_extension): path.read_bytes()
        for path in live_extension.rglob("*")
        if path.is_file()
    } if live_extension.is_dir() else None
    assert after == before, "isolated development install changed the live Pi extension"


@pytest.mark.skipif(shutil.which("pi") is None, reason="Pi is required for extension dogfood")
def test_pi_print_mode_command_is_visible_without_calling_a_model(tmp_path, monkeypatch):
    home = _install_pi(tmp_path, monkeypatch)
    result = subprocess.run(
        [
            shutil.which("pi"),
            "--no-context-files",
            "--no-skills",
            "--no-prompt-templates",
            "--no-session",
            "-p",
            "/ar st",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env={
            **os.environ,
            "HOME": str(home),
            "USERPROFILE": str(home),
            "PI_CODING_AGENT_DIR": str(home / ".pi" / "agent"),
            "PI_SKIP_VERSION_CHECK": "1",
            "PI_OFFLINE": "1",
        },
    )

    assert result.returncode == 0, result.stderr
    # Pi reserves print-mode stdout for model output and redirects extension
    # writes to stderr. The command must still be visible without a model call.
    assert "autorun daemon unreachable" in result.stdout + result.stderr
    assert "extension error" not in result.stderr.lower()


def _run_pi_adapter_driver(tmp_path: Path, responses: list[dict], script: str) -> tuple[dict, list[dict]]:
    """Execute the staged TypeScript adapter against a synthetic daemon."""
    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("synthetic server uses AF_UNIX")
    socket_path = Path("/tmp") / f"arpicb-{os.getpid()}-{tmp_path.name[-5:]}.sock"
    socket_path.unlink(missing_ok=True)
    frames: list[dict] = []

    def serve():
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(socket_path))
        server.listen(len(responses))
        try:
            for response in responses:
                conn, _ = server.accept()
                with conn:
                    data = b""
                    while not data.endswith(b"\n"):
                        data += conn.recv(65536)
                    frames.append(json.loads(data))
                    conn.sendall((json.dumps(response) + "\n").encode())
        finally:
            server.close()
            socket_path.unlink(missing_ok=True)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    extension_dir = tmp_path / "extension"
    extension_dir.mkdir()
    source = (PI_TEMPLATE / "extensions" / "autorun" / "index.ts").read_text(encoding="utf-8")
    source = source.replace("__AUTORUN_SOCKET__", json.dumps(str(socket_path)))
    source = source.replace("__AUTORUN_PORT_FILE__", json.dumps(str(tmp_path / "none.port")))
    source = source.replace("__AUTORUN_HOOK_ENTRY_COMMAND__", "[]")
    (extension_dir / "index.ts").write_text(source, encoding="utf-8")
    shutil.copy2(BRIDGE_SOURCE, extension_dir / "daemon-client.mjs")
    driver = tmp_path / "driver.mjs"
    driver.write_text(script.replace("__EXTENSION__", (extension_dir / "index.ts").as_uri()), encoding="utf-8")
    result = subprocess.run(
        ["node", "--experimental-strip-types", str(driver)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    thread.join(timeout=10)
    assert result.returncode == 0, result.stderr
    assert not thread.is_alive(), f"adapter sent {len(frames)} of {len(responses)} expected frames"
    return json.loads(result.stdout), frames


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for Pi callback tests")
def test_pi_callbacks_separate_display_work_and_continuation_channels(tmp_path):
    result, frames = _run_pi_adapter_driver(
        tmp_path,
        [
            {"systemMessage": "status", "_autorun_bridge": {"starts_agent_turn": False}},
            {"systemMessage": "start work", "_autorun_bridge": {"starts_agent_turn": True}},
            {"decision": "block", "reason": "continue work"},
        ],
        '''import extension from "__EXTENSION__";
const handlers = new Map();
const commands = new Map();
const sent = [], users = [], notices = [];
const pi = {
  registerCommand(name, value) { commands.set(name, value); },
  registerTool() {}, on(name, handler) { handlers.set(name, handler); },
  sendMessage(message, options) { sent.push({ message, options }); },
  sendUserMessage(message, options) { users.push({ message, options }); },
};
extension(pi);
const ctx = {
  cwd: "/sandbox", mode: "interactive",
  isIdle: () => true, hasPendingMessages: () => false,
  ui: { notify(message, level) { notices.push({ message, level }); } },
  sessionManager: {
    getSessionId: () => "pi-session", getSessionFile: () => undefined,
    buildSessionContext: () => ({ messages: [{ role: "assistant", content: [{ type: "text", text: "done" }] }] }),
  },
};
await commands.get("ar").handler("st", ctx);
await commands.get("ar").handler("go build it", ctx);
await handlers.get("agent_settled")({}, ctx);
console.log(JSON.stringify({ sent, users, notices }));
''',
    )

    assert result["users"] == []
    assert result["notices"] == [{"message": "status", "level": "info"}]
    assert [item["message"]["customType"] for item in result["sent"]] == [
        "autorun-command",
        "autorun-continuation",
    ]
    assert all(item["options"]["triggerTurn"] is True for item in result["sent"])
    assert frames[0]["inprocess_capabilities"] == [
        "response_projection_v2", "task_operations_v1"
    ]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for Pi callback tests")
def test_pi_tool_call_returns_native_block_without_executing_tool(tmp_path):
    result, frames = _run_pi_adapter_driver(
        tmp_path,
        [{
            "hookSpecificOutput": {
                "permissionDecision": "deny",
                "permissionDecisionReason": "blocked safely",
            }
        }],
        '''import extension from "__EXTENSION__";
const handlers = new Map();
const pi = { registerCommand() {}, registerTool() {}, on(name, handler) { handlers.set(name, handler); } };
extension(pi);
const ctx = {
  cwd: "/sandbox", mode: "rpc",
  sessionManager: {
    getSessionId: () => "pi-session", getSessionFile: () => undefined,
    buildSessionContext: () => ({ messages: [] }),
  },
};
const decision = await handlers.get("tool_call")({ toolName: "bash", input: { command: "rm sample" } }, ctx);
console.log(JSON.stringify({ decision }));
''',
    )

    assert result["decision"] == {"block": True, "reason": "blocked safely"}
    assert frames[0]["tool_input"] == {"command": "rm sample"}


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for Pi callback tests")
def test_pi_callbacks_preserve_transcript_and_tool_result_fields(tmp_path):
    result, frames = _run_pi_adapter_driver(
        tmp_path,
        [
            {"systemMessage": "prompt context"},
            {"systemMessage": "tool context"},
            {"decision": "block", "reason": "continue"},
        ],
        '''import extension from "__EXTENSION__";
const handlers = new Map();
const pi = {
  registerCommand() {}, registerTool() {}, on(name, handler) { handlers.set(name, handler); },
  sendMessage() {},
};
extension(pi);
const ctx = {
  cwd: "/sandbox", mode: "rpc", isIdle: () => true, hasPendingMessages: () => false,
  ui: { notify() {} },
  sessionManager: {
    getSessionId: () => "pi-session", getSessionFile: () => "/sandbox/session.jsonl",
    buildSessionContext: () => ({ messages: [
      { role: "user", content: [{ type: "text", text: "earlier" }] },
      { role: "assistant", content: [{ type: "text", text: "AUTORUN_INITIAL_TASKS_COMPLETED" }] },
    ] }),
  },
};
const before = await handlers.get("before_agent_start")({ prompt: "current" }, ctx);
const original = {
  toolName: "read", input: { path: "image.png" },
  content: [{ type: "image", data: "abc", mimeType: "image/png" }],
  details: { source: "disk" }, isError: true, usage: { totalTokens: 12 },
};
const tool = await handlers.get("tool_result")(original, ctx);
await handlers.get("agent_settled")({}, ctx);
console.log(JSON.stringify({ before, tool }));
''',
    )

    assert result["before"]["message"]["content"] == "prompt context"
    assert result["tool"]["content"][0]["type"] == "image"
    assert result["tool"]["content"][-1] == {"type": "text", "text": "tool context"}
    assert result["tool"]["details"] == {"source": "disk"}
    assert result["tool"]["isError"] is True
    assert result["tool"]["usage"] == {"totalTokens": 12}
    assert [frame["hook_event_name"] for frame in frames] == [
        "UserPromptSubmit", "PostToolUse", "Stop"
    ]
    assert frames[0]["prompt"] == "current"
    assert frames[0]["session_transcript"][-1]["role"] == "assistant"
    assert "AUTORUN_INITIAL_TASKS_COMPLETED" in json.dumps(frames[2]["session_transcript"])


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for Pi callback tests")
def test_pi_settled_queues_only_one_continuation_until_next_agent_start(tmp_path):
    result, _frames = _run_pi_adapter_driver(
        tmp_path,
        [
            {"decision": "block", "reason": "first"},
            {"decision": "block", "reason": "next generation"},
        ],
        '''import extension from "__EXTENSION__";
const handlers = new Map(), sent = [];
const pi = {
  registerCommand() {}, registerTool() {}, on(name, handler) { handlers.set(name, handler); },
  sendMessage(message, options) { sent.push({ message, options }); },
};
extension(pi);
const ctx = {
  cwd: "/sandbox", mode: "rpc", isIdle: () => true, hasPendingMessages: () => false,
  ui: { notify() {} },
  sessionManager: {
    getSessionId: () => "pi-session", getSessionFile: () => undefined,
    buildSessionContext: () => ({ messages: [] }),
  },
};
await handlers.get("agent_settled")({}, ctx);
await handlers.get("agent_settled")({}, ctx);
await handlers.get("agent_start")({}, ctx);
await handlers.get("agent_settled")({}, ctx);
console.log(JSON.stringify({ sent }));
''',
    )

    assert [item["message"]["content"] for item in result["sent"]] == [
        "first", "next generation"
    ]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for Pi callback tests")
def test_pi_registers_sequential_task_tools_and_routes_mutations_through_posttool(tmp_path):
    result, frames = _run_pi_adapter_driver(
        tmp_path,
        [
            {
                "_autorun_bridge": {
                    "task_snapshot": {
                        "id": "pi-created", "subject": "Build", "status": "pending",
                        "metadata": {"source": "pi_task_tool"},
                    }
                }
            },
            {
                "_autorun_bridge": {
                    "operation": "task_list_v1",
                    "tasks": [{"id": "existing", "subject": "Existing", "status": "pending"}],
                    "total": 1,
                    "truncated": False,
                }
            },
        ],
        '''import extension from "__EXTENSION__";
const handlers = new Map(), tools = new Map();
const pi = {
  registerCommand() {}, on(name, handler) { handlers.set(name, handler); },
  registerTool(tool) { tools.set(tool.name, tool); }, sendMessage() {},
};
extension(pi);
const ctx = {
  cwd: "/sandbox", mode: "rpc", isIdle: () => true, hasPendingMessages: () => false,
  ui: { notify() {} },
  sessionManager: {
    getSessionId: () => "pi-session", getSessionFile: () => undefined,
    buildSessionContext: () => ({ messages: [] }),
  },
};
const created = await tools.get("TaskCreate").execute(
  "call-create", { subject: "Build", description: "Do it", activeForm: "Building" },
  new AbortController().signal, undefined, ctx,
);
const receipt = await handlers.get("tool_result")({
  toolName: "TaskCreate",
  input: { subject: "Build", description: "Do it", activeForm: "Building" },
  content: created.content, details: created.details, isError: false,
}, ctx);
const listed = await tools.get("TaskList").execute(
  "call-list", {}, new AbortController().signal, undefined, ctx,
);
console.log(JSON.stringify({
  names: [...tools.keys()],
  modes: [...tools.values()].map(tool => tool.executionMode),
  createRequired: tools.get("TaskCreate").parameters.required,
  updateProperties: Object.keys(tools.get("TaskUpdate").parameters.properties),
  created, receipt, listed,
}));
''',
    )

    assert result["names"] == ["TaskCreate", "TaskUpdate", "TaskList"]
    assert result["modes"] == ["sequential", "sequential", "sequential"]
    assert result["createRequired"] == ["subject", "description", "activeForm"]
    assert result["created"]["details"]["task"]["id"].startswith("pi-")
    assert len(result["created"]["details"]["task"]["id"]) >= 20
    assert "addBlockedBy" in result["updateProperties"]
    assert result["created"]["details"]["task"]["id"]
    assert result["receipt"]["details"]["taskSnapshot"]["id"] == "pi-created"
    assert result["listed"]["details"]["tasks"][0]["id"] == "existing"
    assert frames[0]["hook_event_name"] == "PostToolUse"
    assert frames[0]["tool_result"]["task"]["id"] == result["created"]["details"]["task"]["id"]
    assert frames[1]["inprocess_operation"] == "task_list_v1"
    assert "task_operations_v1" in frames[1]["inprocess_capabilities"]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for Pi callback tests")
def test_pi_task_mutation_uses_authoritative_snapshot_in_result(tmp_path):
    result, _frames = _run_pi_adapter_driver(
        tmp_path,
        [{"_autorun_bridge": {"task_snapshot": {
            "id": "pi-1", "subject": "Recovered task", "status": "ignored",
            "blockedBy": [], "metadata": {"ghost_task": True},
        }}}],
        '''import extension from "__EXTENSION__";
const handlers = new Map();
const pi = {
  registerCommand() {}, registerTool() {}, on(name, handler) { handlers.set(name, handler); },
};
extension(pi);
const ctx = {
  cwd: "/sandbox", mode: "rpc",
  sessionManager: {
    getSessionId: () => "pi-session", getSessionFile: () => undefined,
    buildSessionContext: () => ({ messages: [] }),
  },
};
const receipt = await handlers.get("tool_result")({
  toolName: "TaskUpdate", input: { taskId: "pi-1", status: "in_progress" },
  content: [{ type: "text", text: "Updated task pi-1" }],
  details: { taskId: "pi-1", updates: { status: "in_progress" } }, isError: false,
}, ctx);
console.log(JSON.stringify({ receipt }));
''',
    )

    assert result["receipt"]["isError"] is False
    assert result["receipt"]["content"] == [{
        "type": "text",
        "text": "Task pi-1 [ignored] Recovered task",
    }]
    assert result["receipt"]["details"]["taskSnapshot"]["status"] == "ignored"


def test_pi_task_mutation_reports_missing_authoritative_snapshot(tmp_path):
    result, _frames = _run_pi_adapter_driver(
        tmp_path,
        [{}],
        '''import extension from "__EXTENSION__";
const handlers = new Map();
const pi = {
  registerCommand() {}, registerTool() {}, on(name, handler) { handlers.set(name, handler); },
};
extension(pi);
const ctx = {
  cwd: "/sandbox", mode: "rpc",
  sessionManager: {
    getSessionId: () => "pi-session", getSessionFile: () => undefined,
    buildSessionContext: () => ({ messages: [] }),
  },
};
const receipt = await handlers.get("tool_result")({
  toolName: "TaskUpdate", input: { taskId: "pi-1", status: "completed" },
  content: [{ type: "text", text: "Updated task pi-1" }],
  details: { taskId: "pi-1" }, isError: false,
}, ctx);
console.log(JSON.stringify({ receipt }));
''',
    )

    assert result["receipt"]["isError"] is True
    assert "did not confirm TaskUpdate" in result["receipt"]["content"][-1]["text"]
    assert result["receipt"]["details"] == {"taskId": "pi-1"}


def test_pi_session_tree_replays_task_receipts_to_python_projection(tmp_path):
    result, frames = _run_pi_adapter_driver(
        tmp_path,
        [{"_autorun_bridge": {"operation": "task_reproject_v1", "count": 1}}],
        '''import extension from "__EXTENSION__";
const handlers = new Map();
const pi = {
  registerCommand() {}, registerTool() {}, on(name, handler) { handlers.set(name, handler); },
  sendMessage() {},
};
extension(pi);
const ctx = {
  cwd: "/sandbox", mode: "rpc", isIdle: () => true, hasPendingMessages: () => false,
  ui: { notify() {} },
  sessionManager: {
    getSessionId: () => "pi-session", getSessionFile: () => undefined,
    buildSessionContext: () => ({ messages: [] }),
    getBranch: () => [{ type: "message", message: {
      role: "toolResult", toolName: "TaskUpdate",
      details: { taskSnapshot: {
        id: "pi-1", subject: "Restored", description: "", activeForm: "Restoring",
        status: "in_progress", blockedBy: [], blocks: [], metadata: { source: "pi_task_tool" },
      } },
    } }],
  },
};
await handlers.get("session_tree")({}, ctx);
console.log(JSON.stringify({ ok: true }));
''',
    )

    assert result == {"ok": True}
    assert frames[0]["inprocess_operation"] == "task_reproject_v1"
    assert frames[0]["task_records"][0]["id"] == "pi-1"
    assert frames[0]["task_records"][0]["status"] == "in_progress"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for Pi callback tests")
def test_pi_transcript_frame_is_bounded_and_keeps_recent_marker(tmp_path):
    result, frames = _run_pi_adapter_driver(
        tmp_path,
        [{"systemMessage": "context"}],
        '''import extension from "__EXTENSION__";
const handlers = new Map();
const pi = {
  registerCommand() {}, registerTool() {}, on(name, handler) { handlers.set(name, handler); },
};
extension(pi);
const messages = Array.from({ length: 40 }, (_, index) => ({
  role: index === 39 ? "assistant" : "user",
  content: [{ type: "text", text: (index === 39 ? "LATEST_MARKER " : "old ") + "x".repeat(4096) }],
}));
const ctx = {
  cwd: "/sandbox", mode: "rpc",
  sessionManager: {
    getSessionId: () => "pi-session", getSessionFile: () => undefined,
    buildSessionContext: () => ({ messages }),
  },
};
await handlers.get("before_agent_start")({ prompt: "current" }, ctx);
console.log(JSON.stringify({ ok: true }));
''',
    )

    encoded = json.dumps(frames[0]["session_transcript"])
    assert len(encoded.encode()) <= 64 * 1024
    assert "LATEST_MARKER" in encoded
    assert len(frames[0]["session_transcript"]) < 40
    assert result == {"ok": True}


def test_pi_extension_uses_native_events_without_duplicating_policy():
    source = (PI_TEMPLATE / "extensions" / "autorun" / "index.ts").read_text(encoding="utf-8")

    for event in (
        'pi.on("session_start"',
        'pi.on("before_agent_start"',
        'pi.on("tool_call"',
        'pi.on("tool_result"',
        'pi.on("session_before_compact"',
        'pi.on("session_compact"',
        'pi.on("session_tree"',
        'pi.on("agent_start"',
        'pi.on("agent_settled"',
        'pi.on("session_shutdown"',
    ):
        assert event in source
    assert 'pi.registerCommand("ar"' in source
    assert 'createDaemonBridge' in source
    assert "rm -rf" not in source, "the adapter must not become a second policy engine"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for the Pi bridge")
def test_shared_daemon_client_sends_pi_identity_and_reads_a_deny(tmp_path):
    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("synthetic server uses AF_UNIX")

    socket_path = Path("/tmp") / f"arpi-{os.getpid()}-{tmp_path.name[-5:]}.sock"
    socket_path.unlink(missing_ok=True)
    frames: list[dict] = []

    def serve():
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(socket_path))
        server.listen(1)
        try:
            conn, _ = server.accept()
            with conn:
                data = b""
                while not data.endswith(b"\n"):
                    data += conn.recv(4096)
                frames.append(json.loads(data))
                conn.sendall(
                    (json.dumps({
                        "hookSpecificOutput": {
                            "permissionDecision": "deny",
                            "permissionDecisionReason": "blocked by pi bridge test",
                        }
                    }) + "\n").encode()
                )
        finally:
            server.close()
            socket_path.unlink(missing_ok=True)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    driver = tmp_path / "driver.mjs"
    driver.write_text(
        f'''import {{ createDaemonBridge }} from {json.dumps(BRIDGE_SOURCE.as_uri())};
const bridge = createDaemonBridge({{
  cliType: "pi",
  socketPath: {json.dumps(str(socket_path))},
  portFile: {json.dumps(str(tmp_path / "none.port"))},
  hookEntryCommand: [],
  timeoutMs: 2000,
}});
const response = await bridge.askDaemon({{
  hook_event_name: "PreToolUse",
  session_id: "pi-session",
  tool_name: "bash",
  tool_input: {{ command: "rm example" }},
}});
console.log(JSON.stringify(response));
''',
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", str(driver)], capture_output=True, text=True, timeout=10, check=False
    )
    thread.join(timeout=10)

    assert result.returncode == 0, result.stderr
    assert "blocked by pi bridge test" in result.stdout
    assert frames[0]["cli_type"] == "pi"
    assert frames[0]["protocol_version"] == 1
    assert frames[0]["session_id"] == "pi-session"
