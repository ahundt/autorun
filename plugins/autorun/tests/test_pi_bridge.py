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

    home = _install_pi(tmp_path, monkeypatch)
    extension_root = home / ".pi" / "agent" / "extensions"
    installed = extension_root / "ar"
    stranger = extension_root / "mine.ts"
    stranger.write_text("export default () => {}\n", encoding="utf-8")

    assert (installed / "index.ts").is_file()
    assert (installed / "daemon-client.mjs").is_file()
    assert (installed / ".autorun-owned").is_file()

    assert entrypoint.install_plugins("ar", conductor=False, tool=False) == 0
    assert stranger.is_file()

    assert entrypoint.uninstall_plugins("ar") == 0
    assert not installed.exists()
    assert stranger.is_file()


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


def test_pi_extension_uses_native_events_without_duplicating_policy():
    source = (PI_TEMPLATE / "extensions" / "autorun" / "index.ts").read_text(encoding="utf-8")

    for event in (
        'pi.on("session_start"',
        'pi.on("before_agent_start"',
        'pi.on("tool_call"',
        'pi.on("tool_result"',
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
