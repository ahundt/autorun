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
    assert pi.task_review_tools == frozenset({"TaskList", "TaskGet"})


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
    # The installer writes str(Path) through json.dumps, so compare the
    # staged JSON form: on Windows the backslashes are escaped in the file
    # and a raw str(Path) comparison never matches.
    assert json.dumps(str(autorun_home / "daemon.sock")) in text
    assert json.dumps(str(autorun_home / "daemon.port")) in text
    assert json.dumps(str(live_home / ".autorun"))[1:-1] not in text
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
    source = source.replace("__AUTORUN_CLI_TYPE__", json.dumps("pi"))
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
def test_ordinary_text_that_merely_starts_like_a_command_reaches_the_ai(tmp_path):
    """A prefix is not a command, and only the daemon can tell them apart.

    ``ar-`` is a registered spelling on every harness, so the guard is right to
    look at it — but it claimed the input on the prefix alone and answered
    ``handled`` whatever came back. A prompt like ``ar-archive the release
    notes`` was swallowed: the model never saw it and the user got a notice
    about an unknown command.

    The daemon already answers this. ``response_projection_v2`` attaches
    ``_autorun_bridge`` only when ``_find_command`` matched, so its absence is
    the "this was just text" signal the guard has to honour.
    """
    result, _frames = _run_pi_adapter_driver(
        tmp_path,
        [
            {"systemMessage": "unknown command"},
            {"systemMessage": "status", "_autorun_bridge": {"starts_agent_turn": False}},
        ],
        '''import extension from "__EXTENSION__";
const handlers = new Map();
const notices = [];
const pi = {
  registerCommand() {}, registerTool() {}, on(name, handler) { handlers.set(name, handler); },
  sendMessage() {}, sendUserMessage() {},
};
extension(pi);
const ctx = {
  cwd: "/sandbox", mode: "interactive",
  isIdle: () => true, hasPendingMessages: () => false,
  ui: { notify(message, level) { notices.push({ message, level }); } },
  sessionManager: { getSessionId: () => "pi-session", getSessionFile: () => undefined },
};
const prose = await handlers.get("input")({ text: "ar-archive the release notes" }, ctx);
const command = await handlers.get("input")({ text: "ar-st" }, ctx);
console.log(JSON.stringify({ prose, command, notices }));
''',
    )

    assert result["prose"]["action"] == "continue", (
        "ordinary prose starting with a command prefix was consumed, so the AI "
        f"never received it: {result['prose']}"
    )
    assert result["command"]["action"] == "handled", (
        f"a real command must still be claimed: {result['command']}"
    )
    assert result["notices"] == [{"message": "status", "level": "info"}], (
        f"only the matched command may report to the user: {result['notices']}"
    )


def test_one_typed_command_dispatches_once_even_if_pi_emits_both_events(tmp_path):
    """Two claim paths, one command: at most one of them may act.

    The extension both registers an ``ar`` command and watches raw input, so a
    reasonable worry is that Pi delivers a registered command to the handler
    *and* to the input hook, running one control command twice — two daemon
    frames, two notices, and for a mutating command two state changes.

    It cannot, and the reason is the separator rather than anything Pi does.
    ``registerCommand("ar")`` claims the space-separated spelling `/ar st`,
    while the input guard's `^\\/?ar[:\\-]` requires `:` or `-` immediately
    after `ar`, which that spelling never has. The colon spelling `/ar:st` is
    the mirror image: no command named `ar:st` is registered, so only the input
    hook sees it. Feeding *both* paths both spellings is what makes that
    independent of which events Pi chooses to emit — the property holds even
    under the worst assumption about the host.
    """
    result, frames = _run_pi_adapter_driver(
        tmp_path,
        # One staged response, because one dispatch is the claim. The driver
        # fails if the adapter asks for a second, which is the assertion.
        [{"systemMessage": "status", "_autorun_bridge": {"starts_agent_turn": False}}],
        '''import extension from "__EXTENSION__";
const handlers = new Map();
const commands = new Map();
const notices = [];
const pi = {
  registerCommand(name, value) { commands.set(name, value); },
  registerTool() {}, on(name, handler) { handlers.set(name, handler); },
  sendMessage() {}, sendUserMessage() {},
};
extension(pi);
const ctx = {
  cwd: "/sandbox", mode: "interactive",
  isIdle: () => true, hasPendingMessages: () => false,
  ui: { notify(message, level) { notices.push({ message, level }); } },
  sessionManager: { getSessionId: () => "pi-session", getSessionFile: () => undefined },
};
// The slash spelling Pi routes to the registered command, offered to both.
const spaced = await handlers.get("input")({ text: "/ar st" }, ctx);
// The colon spelling, offered to both: no `ar:st` command exists to run.
const colon = await handlers.get("input")({ text: "/ar:st" }, ctx);
console.log(JSON.stringify({
  spaced, colon, notices, registered: [...commands.keys()],
}));
''',
    )

    assert result["registered"] == ["ar"], result["registered"]
    assert result["spaced"]["action"] == "continue", (
        "the input hook also claimed the spelling registerCommand owns, so a "
        f"single /ar st would run twice: {result['spaced']}"
    )
    assert result["colon"]["action"] == "handled", result["colon"]
    assert len(result["notices"]) == 1, f"one command, two notices: {result['notices']}"
    commands_sent = [f for f in frames if f.get("hook_event_name") == "UserPromptSubmit"]
    assert len(commands_sent) == 1, f"one command reached the daemon twice: {commands_sent}"


def _tool_call_driver(command: str) -> str:
    """Drive one ``tool_call`` callback through the staged adapter."""
    return '''import extension from "__EXTENSION__";
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
const decision = await handlers.get("tool_call")({ toolName: "bash", input: { command: __COMMAND__ } }, ctx);
console.log(JSON.stringify({ decision }));
'''.replace("__COMMAND__", json.dumps(command))


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
        _tool_call_driver("rm sample"),
    )

    assert result["decision"] == {"block": True, "reason": "blocked safely"}
    assert frames[0]["tool_input"] == {"command": "rm sample"}


def _recorded_pi_deny(cwd: Path, command: str) -> dict:
    """Record autorun's own PreToolUse answer for Pi from a real hook process.

    The test above hands the adapter a deny this file wrote by hand, so it can
    only prove the adapter reshapes whatever it is given. autorun is the party
    that actually produces that deny, and it costs nothing to ask: one
    ``hook_entry.py --cli pi`` process, no daemon, no model. Recording the tape
    from the real producer at test time is what keeps it from drifting the way
    a pasted literal does.
    """
    from autorun.platforms import PLATFORMS
    from e2e_support import run_isolated_hook

    completed = run_isolated_hook(
        plugin_root=PLUGIN_ROOT,
        hook_script=PLUGIN_ROOT / "hooks" / "hook_entry.py",
        cli="pi",
        payload={
            "hook_event_name": "PreToolUse",
            "session_id": f"pi-recorded-deny-{os.getpid()}-{cwd.name[-8:]}",
            "cwd": str(cwd),
            "tool_name": PLATFORMS["pi"].tool_names["bash"],
            "tool_input": {"command": command},
        },
        cwd=cwd,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@pytest.mark.timeout(120)
@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for Pi callback tests")
def test_pi_adapter_blocks_with_the_reason_autorun_actually_emits(tmp_path):
    """Replay autorun's recorded deny, not an invented one, through the adapter.

    This is the free half of Pi's ``rm`` guard. It joins the two halves nothing
    else connects: the Python side really denies ``rm`` for ``cli_type=pi``, and
    the TypeScript side really turns that exact payload into Pi's native
    ``{ block, reason }``. What it cannot show is that Pi hands the adapter the
    ``toolName``/``input`` shape assumed here — only a live Pi can, which is
    ``test_pi_e2e_real_money.py``'s job.
    """
    probe = tmp_path / "probe.txt"
    probe.write_text("probe\n", encoding="utf-8")
    command = f"rm {probe}"

    recorded = _recorded_pi_deny(tmp_path, command)
    reason = recorded["hookSpecificOutput"]["permissionDecisionReason"]
    assert recorded["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "trash" in reason
    # Pi spells its commands "/ar ok rm"; Claude's "/ar:ok rm" here would mean
    # the deny was rendered for the wrong harness.
    assert "/ar ok rm" in reason, reason

    result, frames = _run_pi_adapter_driver(tmp_path, [recorded], _tool_call_driver(command))

    assert result["decision"] == {"block": True, "reason": reason}
    assert frames[0]["tool_input"] == {"command": command}
    assert probe.is_file(), "a blocked tool_call must never reach the shell"


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
            {"_autorun_bridge": {"operation": "task_next_id_v1", "task_id": "7"}},
            {
                "_autorun_bridge": {
                    "task_snapshot": {
                        "id": "7", "subject": "Build", "status": "pending",
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

    assert result["names"] == ["TaskCreate", "TaskUpdate", "TaskList", "TaskGet"]
    assert result["modes"] == ["sequential", "sequential", "sequential", "sequential"]
    assert result["createRequired"] == ["subject", "description", "activeForm"]
    # The daemon mints the id, so it is the session's next sequential integer
    # (Claude Code shape: TaskUpdate(taskId=7)), not a 35-character random string.
    assert result["created"]["details"]["task"]["id"] == "7"
    assert result["created"]["content"] == [{"type": "text", "text": "Created task 7: Build"}]
    assert "addBlockedBy" in result["updateProperties"]
    assert result["receipt"]["details"]["taskSnapshot"]["id"] == "7"
    assert result["listed"]["details"]["tasks"][0]["id"] == "existing"
    assert frames[0]["inprocess_operation"] == "task_next_id_v1"
    assert "task_operations_v1" in frames[0]["inprocess_capabilities"]
    # task operations read state only, so they carry no transcript projection
    assert "session_transcript" not in frames[0] and "session_transcript" not in frames[2]
    assert frames[0]["session_id"] == "pi-session" and frames[0]["cwd"] == "/sandbox"
    assert frames[1]["hook_event_name"] == "PostToolUse"
    assert "session_transcript" in frames[1]
    assert frames[1]["tool_result"]["task"]["id"] == "7"
    assert frames[2]["inprocess_operation"] == "task_list_v1"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for Pi callback tests")
def test_pi_task_create_falls_back_to_a_prefixed_random_id_without_a_daemon_mint(tmp_path):
    """A daemon that returns no id must not stop task creation: the extension
    falls back to a harness-prefixed random id that the PostToolUse receipt
    then confirms or flags, exactly as before the daemon minted ids."""
    result, frames = _run_pi_adapter_driver(
        tmp_path,
        [{"systemMessage": "no mint here"}],
        '''import extension from "__EXTENSION__";
const tools = new Map();
const pi = { registerCommand() {}, on() {}, registerTool(tool) { tools.set(tool.name, tool); }, sendMessage() {} };
extension(pi);
const ctx = {
  cwd: "/sandbox", mode: "rpc",
  sessionManager: {
    getSessionId: () => "pi-session", getSessionFile: () => undefined,
    buildSessionContext: () => ({ messages: [] }),
  },
};
const created = await tools.get("TaskCreate").execute(
  "call-create", { subject: "Build", description: "Do it", activeForm: "Building" },
  new AbortController().signal, undefined, ctx,
);
console.log(JSON.stringify({ created }));
''',
    )

    task_id = result["created"]["details"]["task"]["id"]
    assert task_id.startswith("pi-") and len(task_id) >= 20
    assert frames[0]["inprocess_operation"] == "task_next_id_v1"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for Pi callback tests")
def test_pi_task_get_reads_one_authoritative_task(tmp_path):
    result, frames = _run_pi_adapter_driver(
        tmp_path,
        [{"_autorun_bridge": {
            "operation": "task_get_v1",
            "task": {"id": "one", "subject": "One", "status": "completed"},
        }}],
        '''import extension from "__EXTENSION__";
const handlers = new Map(), tools = new Map();
const pi = {
  registerCommand() {}, on(name, handler) { handlers.set(name, handler); },
  registerTool(tool) { tools.set(tool.name, tool); },
};
extension(pi);
const ctx = {
  cwd: "/sandbox", mode: "rpc",
  sessionManager: {
    getSessionId: () => "pi-session", getSessionFile: () => undefined,
    buildSessionContext: () => ({ messages: [] }),
  },
};
const result = await tools.get("TaskGet").execute(
  "get-one", { taskId: "one" }, new AbortController().signal, undefined, ctx,
);
console.log(JSON.stringify({ result, properties: tools.get("TaskGet").parameters.properties }));
''',
    )

    assert result["result"]["details"]["task"]["id"] == "one"
    assert result["result"]["content"] == [{"type": "text", "text": "Task one [completed] One"}]
    assert result["properties"]["taskId"]["type"] == "string"
    assert frames[0]["inprocess_operation"] == "task_get_v1"
    assert frames[0]["task_id"] == "one"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for Pi callback tests")
def test_pi_task_update_declares_and_formats_atomic_bulk_updates(tmp_path):
    result, frames = _run_pi_adapter_driver(
        tmp_path,
        [{"_autorun_bridge": {"task_snapshots": [
            {"id": "one", "subject": "One", "status": "in_progress"},
            {"id": "two", "subject": "Two", "status": "pending", "blockedBy": ["one"]},
        ]}}],
        '''import extension from "__EXTENSION__";
const handlers = new Map(), tools = new Map();
const pi = {
  registerCommand() {}, on(name, handler) { handlers.set(name, handler); },
  registerTool(tool) { tools.set(tool.name, tool); },
};
extension(pi);
const ctx = {
  cwd: "/sandbox", mode: "rpc",
  sessionManager: {
    getSessionId: () => "pi-session", getSessionFile: () => undefined,
    buildSessionContext: () => ({ messages: [] }),
  },
};
const input = { taskUpdates: [
  { taskId: "one", status: "in_progress" },
  { taskId: "two", status: "pending", addBlockedBy: ["one"] },
] };
const created = await tools.get("TaskUpdate").execute(
  "call-bulk", input, new AbortController().signal, undefined, ctx,
);
const receipt = await handlers.get("tool_result")({
  toolName: "TaskUpdate", input, content: created.content,
  details: created.details, isError: false,
}, ctx);
console.log(JSON.stringify({
  required: tools.get("TaskUpdate").parameters.required,
  hasTaskUpdates: tools.get("TaskUpdate").parameters.properties.taskUpdates,
  created, receipt,
}));
''',
    )

    assert result["required"] == []
    assert result["hasTaskUpdates"]["type"] == "array"
    assert result["hasTaskUpdates"]["minItems"] == 1
    assert result["hasTaskUpdates"]["items"]["properties"]["taskId"]["minLength"] == 1
    assert "append" in result["hasTaskUpdates"]["items"]["properties"]["addBlockedBy"]["description"]
    assert result["created"]["details"]["taskUpdates"] == [
        {"taskId": "one", "status": "in_progress"},
        {"taskId": "two", "status": "pending", "addBlockedBy": ["one"]},
    ]
    assert [
        item["text"] for item in result["receipt"]["content"]
    ] == [
        "Task one [in_progress] One",
        "Task two [pending] Two",
    ]
    assert result["receipt"]["details"]["taskSnapshots"][1]["blockedBy"] == ["one"]
    assert frames[0]["tool_input"]["taskUpdates"][0]["taskId"] == "one"


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


def test_pi_extension_task_status_enum_matches_the_registry():
    """The TS tool schema cannot import Python, so pin the one copy it keeps.

    ``TASK_STATUSES`` is what the model is offered for ``TaskUpdate.status``;
    ``Platform.native_task_statuses`` is what the lifecycle accepts. Both Pi
    variants share the template, so one registry entry is the authority.
    """
    import re

    from autorun.platforms import PLATFORMS

    source = (PI_TEMPLATE / "extensions" / "autorun" / "index.ts").read_text(encoding="utf-8")
    match = re.search(r"const TASK_STATUSES = \[(.*?)\];", source, re.S)
    assert match, "index.ts must declare TASK_STATUSES"
    declared = set(re.findall(r'"([a-z_]+)"', match.group(1)))
    assert declared == set(PLATFORMS["pi"].native_task_statuses)
    assert declared == set(PLATFORMS["prime"].native_task_statuses)


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


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for the Pi bridge")
def test_shared_daemon_client_maps_every_bare_ar_spelling_to_the_help_prompt(tmp_path):
    """``/ar`` with no arguments (Pi's registered command, empty args), ``ar:``,
    ``/ar:``, ``ar-`` and ``/ar `` must all reach the daemon as the ``ar:``
    prompt, which the dispatcher answers with help. A regex that requires a
    separator after ``ar`` sends ``/ar`` through as the prompt ``ar:/ar`` and
    the user sees nothing."""
    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("synthetic server uses AF_UNIX")

    spellings = ["/ar", "ar:", "/ar:", "ar-", "/ar ", "/ar st", "ar:st", "ar-st", "AR:ST"]
    socket_path = Path("/tmp") / f"arpi-{os.getpid()}-{tmp_path.name[-5:]}.sock"
    socket_path.unlink(missing_ok=True)
    frames: list[dict] = []

    def serve():
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(socket_path))
        server.listen(8)
        try:
            for _ in spellings:
                conn, _ = server.accept()
                with conn:
                    data = b""
                    while not data.endswith(b"\n"):
                        data += conn.recv(4096)
                    frames.append(json.loads(data))
                    conn.sendall(b'{"systemMessage": "ok"}\n')
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
for (const spelling of {json.dumps(spellings)}) {{
  await bridge.runCommandResponse(spelling, "/work", "pi-session");
}}
''',
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", str(driver)], capture_output=True, text=True, timeout=20, check=False
    )
    thread.join(timeout=10)

    assert result.returncode == 0, result.stderr
    prompts = [frame["prompt"] for frame in frames]
    assert prompts == ["ar:", "ar:", "ar:", "ar:", "ar:", "ar:st", "ar:st", "ar:st", "ar:ST"], prompts


# --- Prime Agent: Pi variant through the same pathway ------------------------
#
# prime-agent is PrimeIntellect's build of the Pi coding agent. The shipped
# 0.7.1 bundle keeps Pi's runtime (its launcher is named __piBundleCreateRequire
# and it still sets PI_CODING_AGENT=true in subprocesses) and rebrands only the
# config dir through pkg.piConfig.configDir = ".prime/agent". autorun therefore
# supports it as a registry entry over the existing Pi template, staging, and
# extension step — no second template, step function, or protocol class.


def test_prime_platform_is_a_pi_variant_with_its_own_identity():
    from autorun.platforms import PLATFORMS

    pi = PLATFORMS["pi"]
    prime = PLATFORMS["prime"]
    # Identity and discovery paths are Prime Agent's own.
    assert prime.display_name == "Prime Agent"
    assert prime.binary == "prime-agent"
    assert prime.config_dir == "~/.prime/agent/"
    assert prime.config_dir_env_vars == ("PRIME_AGENT_CODING_AGENT_DIR",)
    assert prime.detect_path_hints == (".prime/agent",)
    # The shipped Prime bundle sets PI_CODING_AGENT=true (Pi's spelling), so
    # environment signals cannot tell the two harnesses apart. Prime claims
    # none and relies on the extension's explicit cliType plus its transcript
    # paths, which do carry .prime/agent.
    assert prime.detect_env_vars == ()
    assert prime.detect_session_keys == ()
    assert prime.standalone_session_env_vars == ()
    assert prime.hook_protocol.name == "prime"
    assert prime.memory_sentinel_slug == "prime-agents-md"
    assert prime.task_record_source == "prime_task_tool"
    # Unified pathway: every behavioral contract is Pi's, verbatim.
    assert prime.has_hooks is True
    assert prime.tool_names == pi.tool_names
    assert prime.task_management_style == pi.task_management_style
    assert prime.task_create_tools == pi.task_create_tools
    assert prime.autorun_to_harness_cli_events == pi.autorun_to_harness_cli_events
    assert prime.native_hook_events == pi.native_hook_events
    assert prime.installed_hook_events == pi.installed_hook_events
    assert prime.command_display_prefix == pi.command_display_prefix
    assert prime.memory_filename == pi.memory_filename
    assert prime.memory_template == pi.memory_template
    assert prime.skill_invocation_format == pi.skill_invocation_format
    assert prime.loads_shared_agents_skills is True


def test_prime_reuses_the_pi_extension_step_row():
    from autorun.installer import steps

    assert steps.STEPS["prime"] == steps.STEPS["pi"]
    assert steps.pi_family_names() == ("pi", "prime")


def test_staged_pi_family_extensions_carry_their_own_cli_type(tmp_path):
    from autorun.installer import steps

    plugins = {"ar": MARKETPLACE_ROOT / "plugins" / "autorun"}
    for cli_type in ("pi", "prime"):
        staged = steps.stage_pi_extension(
            tmp_path / f"_{cli_type}",
            plugins,
            socket=str(tmp_path / "daemon.sock"),
            port_file="",
            command=("hook_entry.py", "--cli", cli_type),
            cli_type=cli_type,
        )
        text = (staged["ar"] / "index.ts").read_text(encoding="utf-8")
        assert f'cliType: "{cli_type}"' in text
        assert f'"--cli", "{cli_type}"' in text
        assert "__AUTORUN_CLI_TYPE__" not in text


def _install_prime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Mirror ``_install_pi`` with only the prime-agent binary discoverable."""
    from autorun.installer import entrypoint

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv(
        "PRIME_AGENT_CODING_AGENT_DIR", str(home / ".prime" / "agent")
    )
    monkeypatch.setenv("AUTORUN_HOME", f"/tmp/aprh-{tmp_path.name[-8:]}")
    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", f"/tmp/aprs-{tmp_path.name[-8:]}")
    monkeypatch.setattr(entrypoint, "_marketplace_root", lambda: MARKETPLACE_ROOT)
    monkeypatch.setattr(
        entrypoint.shutil,
        "which",
        lambda name: (
            "/opt/homebrew/bin/prime-agent" if name == "prime-agent" else None
        ),
    )
    monkeypatch.setattr(
        entrypoint,
        "_run",
        lambda argv: subprocess.CompletedProcess(argv, 0, "", ""),
    )
    assert entrypoint.install_plugins("ar", conductor=False, tool=False) == 0
    return home


def test_prime_install_lands_in_the_prime_home_with_prime_cli_type(tmp_path, monkeypatch):
    from autorun.installer import entrypoint

    home = _install_prime(tmp_path, monkeypatch)
    installed = home / ".prime" / "agent" / "extensions" / "ar"
    assert (installed / "daemon-client.mjs").is_file()
    assert (installed / ".autorun-owned").is_file()
    text = (installed / "index.ts").read_text(encoding="utf-8")
    assert 'cliType: "prime"' in text
    assert '"--cli", "prime"' in text
    # Nothing may land in Pi's home from a prime-only install.
    assert not (home / ".pi").exists()

    assert entrypoint.uninstall_plugins("ar") == 0
    assert not installed.exists()


def test_prime_backend_declares_an_e2e_contract():
    from autorun.platforms import PLATFORMS
    from e2e_support import BACKEND_E2E_CONTRACTS

    assert "prime" in PLATFORMS
    contract = BACKEND_E2E_CONTRACTS["prime"]
    assert contract.hook_process is PLATFORMS["prime"].has_hooks
    assert contract.isolation
