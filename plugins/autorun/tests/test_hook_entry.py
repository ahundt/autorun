#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for hooks/hook_entry.py and daemon.py bootstrap

TDD-driven tests for hook entry point and daemon bootstrap functionality.
"""
import json
import io
import builtins
import os
import socket
import subprocess
import sys
import importlib.util
import types
from pathlib import Path

import pytest


# =============================================================================
# Paths
# =============================================================================

PLUGIN_ROOT = Path(__file__).parent.parent
HOOK_ENTRY = PLUGIN_ROOT / "hooks" / "hook_entry.py"


def write_fake_cli(
    directory: Path,
    *,
    name: str = "autorun",
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 0,
    sleep_seconds: int = 0,
) -> Path:
    """Write a stub `autorun` the running platform can actually execute.

    POSIX gets a /bin/sh script with the executable bit set. Windows gets a
    .cmd, because chmod is a no-op there and CreateProcess will not run a file
    whose only claim to being executable is a shebang -- try_cli then never
    spawned anything, never reached its SystemExit, and ten tests reported
    "DID NOT RAISE" for a stub that had simply not run.

    This is the only place that difference lives; callers pass behaviour, not
    a script, and never branch on the platform themselves.
    """
    if os.name == "nt":
        path = directory / f"{name}.cmd"
        lines = ["@echo off"]
        if sleep_seconds:
            # No sleep.exe on a bare runner; ping loops back once per second.
            lines.append(f"ping -n {sleep_seconds + 1} 127.0.0.1 >nul")
        if stdout:
            lines.append(f"echo|set /p={_cmd_quote(stdout)}")
        if stderr:
            lines.append(f"echo {_cmd_quote(stderr)} 1>&2")
        lines.append(f"exit /b {exit_code}")
        path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
        return path

    path = directory / name
    lines = ["#!/bin/sh"]
    if sleep_seconds:
        lines.append(f"sleep {sleep_seconds}")
    if stdout:
        lines.append(f"printf '%s' {_sh_quote(stdout)}")
    if stderr:
        lines.append(f"printf '%s\\n' {_sh_quote(stderr)} >&2")
    lines.append(f"exit {exit_code}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _sh_quote(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


def _cmd_quote(text: str) -> str:
    """cmd.exe has no quoting that survives these characters; escape them."""
    for char in "^&<>|":
        text = text.replace(char, "^" + char)
    return text


def _isolated_interpreter(tmp_path: Path) -> str:
    """A python whose bin directory carries no `autorun` sibling.

    get_autorun_bin resolves the binary beside sys.executable first, so a
    subprocess test that stages its own fake `autorun` on PATH must not run
    hook_entry with the workspace venv's interpreter — the real sibling would
    win and the staged fake would never execute. hook_entry is stdlib-only by
    design, so a bare symlink outside any venv is a complete interpreter for
    it, and sys.executable inside the child is the symlink's own path.
    """
    # The interpreter that created this venv already satisfies the
    # requirement and needs no filesystem trick: it lives outside the venv, so
    # no autorun sits beside it. The previous symlink was not portable --
    # Windows resolves an interpreter's home from its own path, so a link in
    # an unrelated directory produced "failed to locate pyvenv.cfg" and three
    # tests failed before reaching what they meant to check.
    base = getattr(sys, "_base_executable", None)
    if base and base != sys.executable:
        base_dir = Path(base).parent
        if not any((base_dir / n).exists() for n in ("autorun", "autorun.exe")):
            return base

    link = tmp_path / Path(sys.executable).name
    link.symlink_to(sys.executable)
    return str(link)
SRC_DIR = PLUGIN_ROOT / "src"
DAEMON_PY = PLUGIN_ROOT / "src" / "autorun" / "daemon.py"


def load_hook_entry_module():
    """Load hook_entry.py as an importable module for direct function tests."""
    spec = importlib.util.spec_from_file_location("autorun_hook_entry_test", HOOK_ENTRY)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


# =============================================================================
# Test: hook_entry.py Structure
# =============================================================================


class TestHookEntryStructure:
    """Verify hook_entry.py has required structure."""

    def test_exists(self):
        """hook_entry.py exists in hooks directory."""
        assert HOOK_ENTRY.exists()

    def test_has_shebang(self):
        """hook_entry.py has Python shebang."""
        content = HOOK_ENTRY.read_text(encoding="utf-8")
        assert content.startswith("#!/usr/bin/env python3")

    def test_has_main_function(self):
        """hook_entry.py has main() function."""
        content = HOOK_ENTRY.read_text(encoding="utf-8")
        assert "def main()" in content

    def test_has_entry_point(self):
        """hook_entry.py has if __name__ == '__main__' block."""
        content = HOOK_ENTRY.read_text(encoding="utf-8")
        assert 'if __name__ == "__main__":' in content

    def test_has_fail_open_function(self):
        """hook_entry.py has fail_open() for error handling."""
        content = HOOK_ENTRY.read_text(encoding="utf-8")
        assert "def fail_open(" in content

    def test_has_try_cli_function(self):
        """hook_entry.py has try_cli() for CLI-first execution."""
        content = HOOK_ENTRY.read_text(encoding="utf-8")
        assert "def try_cli(" in content

    def test_has_run_fallback_function(self):
        """hook_entry.py has run_fallback() for direct import fallback."""
        content = HOOK_ENTRY.read_text(encoding="utf-8")
        assert "def run_fallback(" in content

    def test_has_get_src_dir_fallback(self):
        """hook_entry.py has get_src_dir() for when PLUGIN_ROOT missing."""
        content = HOOK_ENTRY.read_text(encoding="utf-8")
        assert "def get_src_dir(" in content


# =============================================================================
# Test: hook_entry.py Execution Priority
# =============================================================================


class TestHookEntryExecutionPriority:
    """Verify hook_entry.py tries CLI first, then plugin root, then relative."""

    def test_uses_shutil_which_for_cli(self):
        """hook_entry.py uses shutil.which to find CLI."""
        content = HOOK_ENTRY.read_text(encoding="utf-8")
        assert "shutil.which" in content

    def test_cli_checked_before_fallback(self):
        """main() tries CLI before fallback."""
        content = HOOK_ENTRY.read_text(encoding="utf-8")
        # Check order: try_cli should be called first in main()
        main_idx = content.find("def main()")
        assert "try_cli(" in content[main_idx:]

    def test_has_timeout_constant(self):
        """hook_entry.py has HOOK_TIMEOUT constant."""
        content = HOOK_ENTRY.read_text(encoding="utf-8")
        assert "HOOK_TIMEOUT" in content

    def test_main_prefers_direct_daemon_before_cli(self, monkeypatch):
        """A healthy daemon avoids spawning a second Python interpreter."""
        hook_entry = load_hook_entry_module()
        payload = json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "echo ok"},
            }
        )
        monkeypatch.setattr(hook_entry.sys, "stdin", io.StringIO(payload))
        monkeypatch.setattr(hook_entry.sys, "argv", ["hook_entry.py", "--cli", "codex"])
        monkeypatch.setattr(
            hook_entry,
            "get_autorun_bin",
            lambda: Path("/tmp/plugin/.venv/bin/autorun"),
        )
        monkeypatch.setattr(hook_entry, "try_daemon", lambda *_args: (True, 0))
        monkeypatch.setattr(
            hook_entry.subprocess,
            "run",
            lambda *_args, **_kwargs: pytest.fail(
                "healthy daemon fast path must not spawn CLI"
            ),
        )

        with pytest.raises(SystemExit) as exc:
            hook_entry.main()

        assert exc.value.code == 0

    def test_direct_daemon_injects_context_and_emits_response(
        self, tmp_path, monkeypatch, capsys
    ):
        """Fast IPC preserves daemon routing metadata and Codex denial JSON."""
        hook_entry = load_hook_entry_module()
        socket_path = tmp_path / "daemon.sock"
        socket_path.touch()
        sent = bytearray()

        class FakeSocket:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def settimeout(self, timeout):
                assert timeout == hook_entry.hook_timeout_for_cli("codex")

            def connect(self, path):
                assert path == str(socket_path)

            def sendall(self, data):
                sent.extend(data)

            def makefile(self, *_args, **_kwargs):
                response = {
                    "decision": "block",
                    "hookSpecificOutput": {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "blocked by test",
                    },
                }
                return io.StringIO(json.dumps(response) + "\n")

        monkeypatch.setenv("AUTORUN_HOME", str(tmp_path))
        monkeypatch.setattr(hook_entry.socket, "socket", lambda *_args: FakeSocket())
        payload = json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "rm test"},
                "cli_type": "codex",
            }
        )

        handled, exit_code = hook_entry.try_daemon(payload, "codex")

        assert handled is True and exit_code == 0
        forwarded = json.loads(bytes(sent).decode())
        assert forwarded["cli_type"] == "codex"
        assert forwarded["_pid"] == os.getppid()
        assert forwarded["_cwd"] == os.getcwd()
        assert json.loads(capsys.readouterr().out)["decision"] == "block"

    def test_direct_daemon_unavailable_uses_existing_cli_fallback(
        self, tmp_path, monkeypatch
    ):
        """A missing socket is recoverable and leaves bootstrap behavior intact."""
        hook_entry = load_hook_entry_module()
        monkeypatch.setenv("AUTORUN_HOME", str(tmp_path))

        assert hook_entry.try_daemon("{}", "codex") == (False, 0)

    def test_direct_daemon_timeout_fails_closed_without_cli_replay(
        self, tmp_path, monkeypatch, capsys
    ):
        """A connected but stalled permission gate is not sent a second time."""
        hook_entry = load_hook_entry_module()
        (tmp_path / "daemon.sock").touch()

        class TimeoutSocket:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def settimeout(self, _timeout):
                return None

            def connect(self, _path):
                return None

            def sendall(self, _data):
                return None

            def makefile(self, *_args, **_kwargs):
                raise socket.timeout

        monkeypatch.setenv("AUTORUN_HOME", str(tmp_path))
        monkeypatch.setattr(hook_entry.socket, "socket", lambda *_args: TimeoutSocket())
        payload = json.dumps({"hook_event_name": "PreToolUse", "cli_type": "codex"})

        with pytest.raises(SystemExit) as exc:
            hook_entry.try_daemon(payload, "codex")

        assert exc.value.code == 0
        response = json.loads(capsys.readouterr().out)
        assert response["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_connected_daemon_invalid_response_fails_closed(self, tmp_path, monkeypatch, capsys):
        """A processed permission event is never replayed after malformed output."""
        hook_entry = load_hook_entry_module()
        (tmp_path / "daemon.sock").touch()

        class InvalidResponseSocket:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def settimeout(self, _timeout):
                return None

            def connect(self, _path):
                return None

            def sendall(self, _data):
                return None

            def makefile(self, *_args, **_kwargs):
                return io.StringIO("not-json\n")

        monkeypatch.setenv("AUTORUN_HOME", str(tmp_path))
        monkeypatch.setattr(
            hook_entry.socket, "socket", lambda *_args: InvalidResponseSocket()
        )
        payload = json.dumps({"hook_event_name": "PreToolUse", "cli_type": "codex"})

        with pytest.raises(SystemExit) as exc:
            hook_entry.try_daemon(payload, "codex")

        assert exc.value.code == 0
        response = json.loads(capsys.readouterr().out)
        assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "invalid response" in response["reason"]

    @pytest.mark.parametrize(
        ("cli_type", "expected"),
        [
            ("claude", {"continue": True}),
            ("gemini", {"continue": True}),
            ("qwen", {"continue": True}),
            ("codex", {}),
            ("antigravity", {}),
        ],
    )
    def test_connected_daemon_invalid_lifecycle_response_uses_native_noop(
        self, tmp_path, monkeypatch, capsys, cli_type, expected
    ):
        """Malformed lifecycle replies never leak Claude fields to other CLIs."""
        hook_entry = load_hook_entry_module()
        (tmp_path / "daemon.sock").touch()

        class InvalidResponseSocket:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def settimeout(self, _timeout):
                return None

            def connect(self, _path):
                return None

            def sendall(self, _data):
                return None

            def makefile(self, *_args, **_kwargs):
                return io.StringIO("not-json\n")

        monkeypatch.setenv("AUTORUN_HOME", str(tmp_path))
        monkeypatch.setattr(
            hook_entry.socket, "socket", lambda *_args: InvalidResponseSocket()
        )
        payload = json.dumps(
            {"hook_event_name": "SessionStart", "cli_type": cli_type}
        )

        with pytest.raises(SystemExit) as exc:
            hook_entry.try_daemon(payload, cli_type)

        assert exc.value.code == 0
        response = json.loads(capsys.readouterr().out)
        if cli_type == "claude":
            assert response["continue"] is True
        else:
            assert response == expected

    def test_hook_timeout_is_platform_specific(self):
        """Hook wrapper budgets must match CONFIG when autorun imports work."""
        from autorun.config import CONFIG

        hook_entry = load_hook_entry_module()

        for cli_type, timeout in CONFIG["hook_wrapper_timeouts_seconds"].items():
            assert hook_entry.hook_timeout_for_cli(cli_type) == timeout
        assert hook_entry.hook_timeout_for_cli("unknown") == hook_entry.hook_timeout_for_cli("claude")

    def test_hook_timeout_hot_path_does_not_import_autorun(self, monkeypatch):
        """Short-lived wrappers read the contract mirror without package startup."""
        hook_entry = load_hook_entry_module()
        original_import = builtins.__import__

        def reject_autorun(name, *args, **kwargs):
            if name == "autorun" or name.startswith("autorun."):
                pytest.fail(f"hot timeout lookup imported {name}")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", reject_autorun)

        assert hook_entry.hook_timeout_for_cli("codex") == 5.0

    def test_antigravity_is_accepted_cli_type(self):
        """Antigravity hooks must not fall back to the wrong CLI identity."""
        hook_entry = load_hook_entry_module()

        assert "antigravity" in hook_entry._VALID_CLI_TYPES

    def test_antigravity_entry_injects_manifest_event_and_common_aliases(
        self, monkeypatch
    ):
        """Native stdin omits its event; the installed command supplies it."""
        hook_entry = load_hook_entry_module()
        monkeypatch.setattr(
            hook_entry.sys,
            "argv",
            ["hook_entry.py", "--cli", "antigravity", "--event", "PreToolUse"],
        )
        payload = hook_entry.prepare_hook_payload(
            {
                "conversationId": "conversation",
                "workspacePaths": ["/workspace/project"],
                "transcriptPath": "/tmp/transcript.jsonl",
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": "rm -rf build"},
                },
            },
            "antigravity",
        )

        assert payload["hook_event_name"] == "PreToolUse"
        assert payload["cli_type"] == "antigravity"
        assert payload["transcript_path"] == "/tmp/transcript.jsonl"
        assert payload["cwd"] == "/workspace/project"

    def test_autorun_bin_resolves_beside_the_running_interpreter(self, tmp_path, monkeypatch):
        """A venv python running this file must find the autorun beside it.

        Without this tier, resolution depends on the LAUNCHER: outside uv no
        CLAUDE_PLUGIN_ROOT is set, the plugin-local .venv probe misses (the
        source tree keeps one workspace venv at the repo root, not under
        plugins/autorun), and the global ~/.local/bin/autorun wins — which
        `_can_use_direct_daemon` rejects, so every event pays a second
        interpreter: the measured 177 ms cliff against the ~50 ms fast path.
        """
        hook_entry = load_hook_entry_module()
        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python").touch()
        (venv_bin / "autorun").touch()
        # hook_entry imports the real sys module, so this patches (and
        # monkeypatch restores) the interpreter-wide value for the test body.
        monkeypatch.setattr(hook_entry.sys, "executable", str(venv_bin / "python"))

        resolved = hook_entry.get_autorun_bin()

        assert resolved == venv_bin / "autorun"
        assert hook_entry._can_use_direct_daemon(resolved), (
            "the interpreter-sibling binary must qualify for the direct-daemon fast path"
        )

    def test_direct_daemon_fast_path_honors_explicit_disable(
        self, tmp_path, monkeypatch
    ):
        """Isolated hooks must not connect merely because a socket exists."""
        hook_entry = load_hook_entry_module()
        autorun_bin = tmp_path / ".venv" / "bin" / "autorun"
        autorun_bin.parent.mkdir(parents=True)
        autorun_bin.touch()
        monkeypatch.setenv("AUTORUN_USE_DAEMON", "0")

        assert hook_entry._can_use_direct_daemon(autorun_bin) is False

    def test_autorun_bin_falls_back_to_the_workspace_root_venv(self, tmp_path, monkeypatch):
        """The source-tree shape: one uv workspace venv at the repo root.

        hook_entry lives at <repo>/plugins/autorun/hooks/hook_entry.py and the
        workspace venv at <repo>/.venv — two levels above the plugin root.
        Resolving it relative to this file keeps the fast path reachable with
        no launcher environment at all.
        """
        hook_entry = load_hook_entry_module()
        repo = tmp_path / "repo"
        plugin_root = repo / "plugins" / "autorun"
        plugin_root.mkdir(parents=True)
        workspace_bin = repo / ".venv" / "bin"
        workspace_bin.mkdir(parents=True)
        (workspace_bin / "autorun").touch()

        monkeypatch.setattr(hook_entry.sys, "executable", str(tmp_path / "nowhere" / "python"))
        monkeypatch.setattr(hook_entry, "get_plugin_root", lambda: str(plugin_root))

        resolved = hook_entry.get_autorun_bin()

        assert resolved == workspace_bin / "autorun"
        assert hook_entry._can_use_direct_daemon(resolved)

    def test_autorun_bin_resolves_beside_a_windows_interpreter(self, tmp_path, monkeypatch):
        """The same tier-1 rule, in the layout Windows actually produces.

        A Windows venv puts executables in Scripts/ and suffixes them .exe, so
        `Path(sys.executable).with_name("autorun")` named a file that never
        exists there. Tier 1 could not match, and resolution fell through to
        the global binary that _can_use_direct_daemon rejects: every hook on
        Windows paid the second interpreter this tier exists to avoid.
        """
        hook_entry = load_hook_entry_module()
        scripts = tmp_path / ".venv" / "Scripts"
        scripts.mkdir(parents=True)
        (scripts / "python.exe").touch()
        (scripts / "autorun.exe").touch()
        monkeypatch.setattr(hook_entry.sys, "executable", str(scripts / "python.exe"))

        resolved = hook_entry.get_autorun_bin()

        assert resolved == scripts / "autorun.exe"
        assert hook_entry._can_use_direct_daemon(resolved), (
            "a Windows plugin-local venv binary must reach the fast path too"
        )

    def test_autorun_bin_falls_back_to_a_windows_workspace_venv(self, tmp_path, monkeypatch):
        """Tier 3 in the Windows layout: <repo>/.venv/Scripts/autorun.exe."""
        hook_entry = load_hook_entry_module()
        repo = tmp_path / "repo"
        plugin_root = repo / "plugins" / "autorun"
        plugin_root.mkdir(parents=True)
        scripts = repo / ".venv" / "Scripts"
        scripts.mkdir(parents=True)
        (scripts / "autorun.exe").touch()

        monkeypatch.setattr(
            hook_entry.sys, "executable", str(tmp_path / "nowhere" / "python.exe")
        )
        monkeypatch.setattr(hook_entry, "get_plugin_root", lambda: str(plugin_root))

        resolved = hook_entry.get_autorun_bin()

        assert resolved == scripts / "autorun.exe"
        assert hook_entry._can_use_direct_daemon(resolved)

    def test_a_global_binary_is_still_refused_by_the_direct_daemon_check(self, tmp_path):
        """Accepting the Windows shape must not accept everything.

        The fast path is limited to a complete plugin-local installation; a
        global install is exactly what tiers 1-3 exist to beat.
        """
        hook_entry = load_hook_entry_module()
        for candidate in (
            tmp_path / "usr" / "local" / "bin" / "autorun",
            tmp_path / "Program Files" / "autorun" / "autorun.exe",
            tmp_path / ".venv" / "bin" / "autorunner",
        ):
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.touch()
            assert hook_entry._can_use_direct_daemon(candidate) is False, candidate

    def test_standalone_detector_carries_every_registered_platform(self):
        """hook_entry's detection data is a hand copy; this pins it.

        The bootstrap constraint bars hook_entry from importing the package,
        so `_VALID_CLI_TYPES` and the env/path detection literals are copies
        of `platforms.py` registry data. Nothing else forces the copies to
        move when a platform is added — an eighth harness would silently
        misdetect as the fallback identity. Registry-driven detection
        (`config.detect_cli_type`) needs no such pin.
        """
        from autorun.platforms import PLATFORMS

        hook_entry = load_hook_entry_module()
        assert set(hook_entry._VALID_CLI_TYPES) == set(PLATFORMS)

        source = HOOK_ENTRY.read_text(encoding="utf-8")
        missing = [
            f"{platform.name}: {literal}"
            for platform in PLATFORMS.values()
            for literal in (*platform.detect_env_vars, *platform.detect_path_hints)
            if literal not in source
        ]
        assert not missing, (
            "platform detection literals absent from hook_entry.py's standalone "
            f"detector: {missing}"
        )

    @pytest.mark.parametrize(
        ("cli_type", "event_name"),
        [
            ("gemini", "BeforeTool"),
            ("qwen", "PreToolUse"),
            ("antigravity", "PreToolUse"),
            ("codex", "PreToolUse"),
        ],
    )
    def test_tool_gate_fail_closed_matches_canonical_platform_schema(
        self, capsys, cli_type, event_name
    ):
        from autorun.platforms import platform_for

        hook_entry = load_hook_entry_module()

        with pytest.raises(SystemExit) as exc:
            hook_entry.fail_closed_tool_gate(
                "broken hook path",
                cli_type=cli_type,
                event_name=event_name,
            )

        assert exc.value.code == 0
        output = json.loads(capsys.readouterr().out)
        reason = (
            "[autorun] broken hook path. Blocking tool use to avoid fail-open. "
            "Run `autorun --restart-daemon` or `autorun --install --force`, then retry."
        )
        expected = platform_for(cli_type).hook_protocol.fail_closed_pretool_response(
            reason, event_name
        )
        assert output == expected

    def test_qwen_project_dir_precedes_gemini_compat_env(self, monkeypatch):
        """Qwen hooks must not inherit a stale Gemini-compatible project root."""
        hook_entry = load_hook_entry_module()
        monkeypatch.setenv("QWEN_PROJECT_DIR", "/tmp/qwen-project")
        monkeypatch.setenv("GEMINI_PROJECT_DIR", "/tmp/gemini-project")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/tmp/claude-project")

        assert hook_entry.get_project_dir() == "/tmp/qwen-project"

    def test_debug_values_are_truncated(self):
        """Hook debug logging must not store huge subprocess output."""
        hook_entry = load_hook_entry_module()
        value = "a" * 5000

        shortened = hook_entry._short_debug_value(value, limit=100)

        assert len(shortened) < 300
        assert "omitted" in shortened
        assert shortened.startswith("a" * 50)
        assert shortened.endswith("a" * 50)

    def test_debug_log_rotates_before_append(self, tmp_path, monkeypatch):
        """A large hook debug log must be rotated before appending."""
        hook_entry = load_hook_entry_module()
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(hook_entry, "DEBUG_LOG_MAX_BYTES", 100)

        log_dir = tmp_path / ".autorun"
        log_dir.mkdir()
        active_log = log_dir / "hook_entry_debug.log"
        active_log.write_text("x" * 200, encoding="utf-8")

        hook_entry._append_debug_log("new bounded entry")

        rotated_log = log_dir / "hook_entry_debug.log.1"
        assert rotated_log.exists()
        assert rotated_log.read_text(encoding="utf-8") == "x" * 200
        assert active_log.read_text(encoding="utf-8") == "new bounded entry\n"


# =============================================================================
# Test: hook_entry.py Fail-Open Behavior
# =============================================================================


class TestHookEntryFailOpen:
    """Test fail-open behavior - hooks should never crash Claude."""

    def test_missing_everything_fails_open(self):
        """Missing CLI and PLUGIN_ROOT outputs valid JSON and exits 0."""
        env = os.environ.copy()
        env.pop('CLAUDE_PLUGIN_ROOT', None)
        env['PATH'] = '/nonexistent'  # No CLI available

        result = subprocess.run(
            [sys.executable, str(HOOK_ENTRY)],
            capture_output=True,
            text=True,
            env=env,
            cwd='/tmp',  # Run from different dir to test relative path
            timeout=10
        )

        # Should exit 0 (not crash)
        assert result.returncode == 0, f"Exit {result.returncode}, stderr: {result.stderr}"

        # Phase 1B: empty stdout = pass-through = implicit allow.
        # This IS the correct fail-open behavior — Claude Code treats no JSON
        # output as implicit allow, same as {"continue": true}.
        output = json.loads(result.stdout) if result.stdout.strip() else {}

        # Should have continue=True (fail-open) — empty dict defaults to True
        assert output.get("continue", True) is True

    def test_invalid_plugin_root_fails_open(self, tmp_path):
        """Invalid CLAUDE_PLUGIN_ROOT outputs valid JSON and exits 0."""
        env = os.environ.copy()
        env['CLAUDE_PLUGIN_ROOT'] = '/nonexistent/path'
        env['PATH'] = '/nonexistent'

        result = subprocess.run(
            [_isolated_interpreter(tmp_path), str(HOOK_ENTRY)],
            capture_output=True,
            text=True,
            env=env,
            cwd='/tmp',
            timeout=10
        )

        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output.get("continue") is True


# =============================================================================
# Test: hook_entry.py Integration
# =============================================================================


class TestHookEntryIntegration:
    """Integration tests with valid environment."""

    def test_runs_with_valid_plugin_root(self):
        """hook_entry.py runs successfully with valid CLAUDE_PLUGIN_ROOT."""
        env = os.environ.copy()
        env['CLAUDE_PLUGIN_ROOT'] = str(PLUGIN_ROOT)

        result = subprocess.run(
            [sys.executable, str(HOOK_ENTRY)],
            capture_output=True,
            text=True,
            input="{}",
            env=env,
            timeout=10
        )

        assert result.returncode == 0, f"Exit {result.returncode}, stderr: {result.stderr}"

    def test_relative_path_works(self):
        """hook_entry.py can find src via relative path from script location."""
        # Verify the relative path logic works
        hooks_dir = HOOK_ENTRY.parent
        relative_src = hooks_dir.parent / "src"
        assert relative_src.exists(), f"Relative src not found: {relative_src}"
        assert (relative_src / "autorun" / "__main__.py").exists()


# =============================================================================
# Test: try_cli Return Code and Stdin Preservation
# =============================================================================


class TestTryCliRobustness:
    """Test that try_cli checks return codes and stdin is preserved for fallback.

    Bug history:
    - try_cli() returned True even when the subprocess failed (non-zero exit
      code, argparse errors from stale UV tool installs). This
      caused hook_entry.py to exit without printing any JSON → Claude Code
      fail-open → rm/git-reset-hard executed unblocked.
    - hook_entry.py later treated empty successful stdout as failure, but empty
      stdout is the correct implicit-allow response for hooks where no rules
      fired.
    - try_cli() consumed stdin, so if it failed the fallback path
      (run_fallback → run_client → json.load(sys.stdin)) got empty input.
    """

    def test_try_cli_checks_return_code(self):
        """try_cli must check returncode — must not return True on failure."""
        content = HOOK_ENTRY.read_text(encoding="utf-8")
        assert "result.returncode" in content, \
            "try_cli must check subprocess return code. Without this, " \
            "a stale/broken CLI binary silently causes fail-open."

    def test_try_cli_accepts_empty_stdout_success_as_implicit_allow(self, tmp_path, capsys):
        """Empty stdout with exit 0 is a valid hook allow response."""
        fake_autorun = write_fake_cli(tmp_path)

        hook_entry = load_hook_entry_module()

        with pytest.raises(SystemExit) as exc:
            hook_entry.try_cli(fake_autorun, "{}")

        captured = capsys.readouterr()
        assert exc.value.code == 0
        assert captured.out == ""
        assert captured.err == ""

    def test_try_cli_suppresses_child_stderr_on_success(self, tmp_path, capsys):
        """Successful hooks must not leak dependency warnings to harness stderr."""
        fake_autorun = write_fake_cli(
            tmp_path, stdout='{"continue": true}', stderr="uv warning"
        )

        hook_entry = load_hook_entry_module()

        with pytest.raises(SystemExit) as exc:
            hook_entry.try_cli(fake_autorun, "{}", cli_type="codex")

        captured = capsys.readouterr()
        assert exc.value.code == 0
        assert json.loads(captured.out) == {"continue": True}
        assert captured.err == ""

    def test_try_cli_rejects_non_json_success_without_protocol_noise(self, tmp_path, capsys):
        """Invalid child stdout must trigger fallback without reaching the harness."""
        fake_autorun = write_fake_cli(tmp_path, stdout="not hook json")

        hook_entry = load_hook_entry_module()

        assert hook_entry.try_cli(fake_autorun, "{}", cli_type="claude") is False
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_try_cli_forwards_stderr_only_for_exit_two(self, tmp_path, capsys):
        """Claude's exit-2 block path must retain its denial feedback."""
        fake_autorun = write_fake_cli(
            tmp_path,
            stdout='{"decision": "block"}',
            stderr="blocked for safety",
            exit_code=2,
        )

        hook_entry = load_hook_entry_module()

        with pytest.raises(SystemExit) as exc:
            hook_entry.try_cli(fake_autorun, "{}", cli_type="claude")

        captured = capsys.readouterr()
        assert exc.value.code == 2
        assert json.loads(captured.out) == {"decision": "block"}
        assert captured.err == "blocked for safety\n"

    def test_main_does_not_fallback_when_cli_allows_with_empty_stdout(self, tmp_path):
        """Empty successful CLI output must not trigger fallback source lookup."""
        fake_autorun = write_fake_cli(tmp_path)

        extension_root = tmp_path / "gemini-extension-no-src"
        extension_root.mkdir()

        env = os.environ.copy()
        env["PATH"] = str(tmp_path)
        env["AUTORUN_PLUGIN_ROOT"] = str(extension_root)
        env.pop("CLAUDE_PLUGIN_ROOT", None)

        result = subprocess.run(
            [_isolated_interpreter(tmp_path), str(HOOK_ENTRY)],
            input=json.dumps({
                "hook_event_name": "BeforeTool",
                "tool_name": "bash_command",
                "tool_input": {"command": "cargo build 2>&1 | head -50"},
            }),
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )

        assert result.returncode == 0
        assert result.stdout == ""
        assert result.stderr == ""

    def test_tool_gate_fails_closed_when_cli_fails_and_extension_has_no_source(self, tmp_path):
        """A broken CLI fast path must not fail open for Gemini tool gates."""
        fake_autorun = write_fake_cli(tmp_path, exit_code=1)

        extension_root = tmp_path / "gemini-extension-no-src"
        extension_root.mkdir()

        env = os.environ.copy()
        env["PATH"] = str(tmp_path)
        env["AUTORUN_PLUGIN_ROOT"] = str(extension_root)
        env.pop("CLAUDE_PLUGIN_ROOT", None)

        result = subprocess.run(
            [_isolated_interpreter(tmp_path), str(HOOK_ENTRY), "--cli", "gemini"],
            input=json.dumps({
                "hook_event_name": "BeforeTool",
                "tool_name": "bash_command",
                "tool_input": {"command": "rm test"},
            }),
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )

        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output.get("decision") == "deny"
        assert output.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
        assert "Cannot locate plugin source" in output.get("reason", "")

    @pytest.mark.parametrize(
        ("cli_type", "expected"),
        [
            ("claude", {"continue": True}),
            ("gemini", {"continue": True}),
            ("qwen", {"continue": True}),
            ("codex", {}),
            ("antigravity", {}),
        ],
    )
    def test_missing_source_lifecycle_uses_native_noop(
        self, monkeypatch, capsys, cli_type, expected
    ):
        """The no-source branch follows each lifecycle response protocol."""
        hook_entry = load_hook_entry_module()
        monkeypatch.setattr(hook_entry, "get_plugin_root", lambda: None)
        monkeypatch.setattr(hook_entry, "get_src_dir", lambda: None)
        monkeypatch.setattr(sys, "argv", ["hook_entry.py", "--cli", cli_type])
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(json.dumps({"hook_event_name": "SessionStart"})),
        )

        with pytest.raises(SystemExit) as exc:
            hook_entry.run_fallback()

        assert exc.value.code == 0
        response = json.loads(capsys.readouterr().out)
        if cli_type == "claude":
            assert response["continue"] is True
        else:
            assert response == expected

    @pytest.mark.parametrize("failure", ["ImportError", "RuntimeError"])
    def test_antigravity_fallback_failure_uses_root_deny_schema(
        self, tmp_path, failure
    ):
        """Exercise run_fallback's real exception branches in a fresh process."""
        plugin = tmp_path / "broken-plugin"
        package = plugin / "src" / "autorun"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "__main__.py").write_text(
            f"raise {failure}('injected fallback failure')\n",
            encoding="utf-8",
        )
        runner = (
            "import importlib.util, io, json, sys; "
            f"spec=importlib.util.spec_from_file_location('hook', {str(HOOK_ENTRY)!r}); "
            "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
            "sys.argv=['hook_entry.py','--cli','antigravity']; "
            "sys.stdin=io.StringIO(json.dumps({'hook_event_name':'PreToolUse','tool_name':'run_command'})); "
            "module.run_fallback()"
        )
        env = os.environ.copy()
        env.update(
            {
                "AUTORUN_PLUGIN_ROOT": str(plugin),
                "AUTORUN_NO_BOOTSTRAP": "1",
                "AUTORUN_HOME": str(tmp_path / "autorun-home"),
            }
        )

        result = subprocess.run(
            [_isolated_interpreter(tmp_path), "-c", runner],
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
            cwd=tmp_path,
        )

        assert result.returncode == 0, result.stderr
        response = json.loads(result.stdout)
        assert set(response) == {"decision", "reason"}
        assert response["decision"] == "deny"
        assert "injected fallback failure" in response["reason"]
        assert not {
            "continue", "stopReason", "suppressOutput", "systemMessage"
        } & response.keys()

    def test_antigravity_lifecycle_fallback_failure_is_native_noop(self, tmp_path):
        """A non-permission bootstrap failure must not emit Claude fields."""
        plugin = tmp_path / "broken-plugin"
        package = plugin / "src" / "autorun"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "__main__.py").write_text(
            "raise RuntimeError('injected lifecycle failure')\n", encoding="utf-8"
        )
        runner = (
            "import importlib.util, io, json, sys; "
            f"spec=importlib.util.spec_from_file_location('hook', {str(HOOK_ENTRY)!r}); "
            "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
            "sys.argv=['hook_entry.py','--cli','antigravity']; "
            "sys.stdin=io.StringIO(json.dumps({'hook_event_name':'SessionStart'})); "
            "module.run_fallback()"
        )
        env = os.environ.copy()
        env.update(
            {
                "AUTORUN_PLUGIN_ROOT": str(plugin),
                "AUTORUN_NO_BOOTSTRAP": "1",
                "AUTORUN_HOME": str(tmp_path / "autorun-home"),
            }
        )

        result = subprocess.run(
            [_isolated_interpreter(tmp_path), "-c", runner],
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
            cwd=tmp_path,
        )

        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {}
        assert result.stderr == ""

    def test_stdin_read_once_in_main(self):
        """stdin must be read once in main(), not inside try_cli."""
        content = HOOK_ENTRY.read_text(encoding="utf-8")
        main_idx = content.find("def main()")
        main_body = content[main_idx:]
        # stdin should be read in main, not try_cli
        assert "sys.stdin.read()" in main_body or "stdin.read()" in main_body, \
            "main() must read stdin once to preserve it for fallback path"

    def test_stdin_restored_for_fallback(self):
        """After try_cli fails, stdin must be restored for run_fallback."""
        content = HOOK_ENTRY.read_text(encoding="utf-8")
        assert "io.StringIO" in content, \
            "main() must use io.StringIO to restore stdin for fallback path. " \
            "Without this, run_client() gets empty stdin after try_cli consumes it."

    def test_try_cli_accepts_stdin_data_param(self):
        """try_cli must accept stdin_data parameter (not read stdin itself)."""
        content = HOOK_ENTRY.read_text(encoding="utf-8")
        assert "def try_cli(bin_path" in content
        try_cli_idx = content.find("def try_cli(")
        sig_end = content.find(")", try_cli_idx) + 1
        signature = content[try_cli_idx:sig_end]
        assert "stdin_data" in signature, \
            "try_cli must accept stdin_data as parameter, not read stdin itself"

    def test_fallback_forces_direct_mode(self, monkeypatch):
        """Fallback must not recursively call the daemon after the CLI path fails."""
        module = load_hook_entry_module()
        fake_main_module = types.ModuleType("autorun.__main__")

        def fake_main():
            assert os.environ["AUTORUN_USE_DAEMON"] == "0"
            return 0

        fake_main_module.main = fake_main
        monkeypatch.setitem(sys.modules, "autorun.__main__", fake_main_module)
        monkeypatch.setattr(module, "get_plugin_root", lambda: str(PLUGIN_ROOT))
        monkeypatch.delenv("AUTORUN_USE_DAEMON", raising=False)

        with pytest.raises(SystemExit) as exc:
            module.run_fallback()

        assert exc.value.code == 0

    def test_main_does_not_run_fallback_after_cli_timeout_for_prompt(self, tmp_path, monkeypatch, capsys):
        """Prompt hooks must fail open promptly after CLI timeout without fallback."""
        module = load_hook_entry_module()
        fake_autorun = write_fake_cli(tmp_path, sleep_seconds=99)

        def timeout_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout", 0))

        def forbidden_fallback():
            pytest.fail("run_fallback() must not run after CLI timeout")

        monkeypatch.setattr(module, "get_autorun_bin", lambda: fake_autorun)
        monkeypatch.setattr(module.subprocess, "run", timeout_run)
        monkeypatch.setattr(module, "run_fallback", forbidden_fallback)
        monkeypatch.setattr(module.sys, "argv", ["hook_entry.py", "--cli", "claude"])
        monkeypatch.setattr(
            module.sys,
            "stdin",
            io.StringIO(json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "hello"})),
        )

        with pytest.raises(SystemExit) as exc:
            module.main()

        captured = capsys.readouterr()
        assert exc.value.code == 0
        output = json.loads(captured.out)
        assert output["continue"] is True
        assert "timed out" in output.get("systemMessage", "")
        assert captured.err == ""

    def test_main_fails_closed_after_cli_timeout_for_tool_gate(self, tmp_path, monkeypatch, capsys):
        """Permission gates must fail closed promptly after CLI timeout without fallback."""
        module = load_hook_entry_module()
        fake_autorun = write_fake_cli(tmp_path, sleep_seconds=99)

        def timeout_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout", 0))

        def forbidden_fallback():
            pytest.fail("run_fallback() must not run after CLI timeout")

        monkeypatch.setattr(module, "get_autorun_bin", lambda: fake_autorun)
        monkeypatch.setattr(module.subprocess, "run", timeout_run)
        monkeypatch.setattr(module, "run_fallback", forbidden_fallback)
        monkeypatch.setattr(module.sys, "argv", ["hook_entry.py", "--cli", "claude"])
        monkeypatch.setattr(
            module.sys,
            "stdin",
            io.StringIO(json.dumps({
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "echo ok"},
            })),
        )

        with pytest.raises(SystemExit) as exc:
            module.main()

        captured = capsys.readouterr()
        assert exc.value.code == 2
        output = json.loads(captured.out)
        assert output.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
        assert "timed out" in captured.err

    def test_hook_rm_blocked_no_stderr(self):
        """Full e2e: hook_entry.py blocks rm with exit code 2.

        Claude Code Bug #4669 Workaround:
        Blocked commands now exit with code 2 and write reason to stderr.
        This is the only way to ACTUALLY block tools in Claude Code.
        Exit code 2 + stderr message is shown to Claude, allowing it to
        understand why the command was blocked and try alternatives.
        """
        env = os.environ.copy()
        env['CLAUDE_PLUGIN_ROOT'] = str(PLUGIN_ROOT)
        payload = json.dumps({
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "rm /tmp/test"}
        })

        result = subprocess.run(
            [sys.executable, str(HOOK_ENTRY)],
            input=payload,
            capture_output=True,
            text=True,
            env=env,
            timeout=10
        )

        # Blocked commands MUST exit with code 2 (Claude Code bug #4669 workaround)
        # Exit code 2 = blocking error, Claude Code shows stderr to user
        assert result.returncode == 2, \
            f"Blocked rm should exit with code 2, got: {result.returncode}"

        # Must have valid JSON on stdout
        try:
            output = json.loads(result.stdout)
        except json.JSONDecodeError:
            pytest.fail(
                f"No valid JSON on stdout (this causes Claude Code fail-open).\n"
                f"stdout: {result.stdout[:200]}\n"
                f"stderr: {result.stderr[:200]}"
            )

        # Must deny rm
        # continue=True: AI keeps running. Tool blocked by permissionDecision + exit code 2 (Bug #4669)
        assert output.get("continue") is True, \
            f"PreToolUse must have continue=True (AI keeps running). Got: {output}"
        perm_decision = output.get("hookSpecificOutput", {}).get("permissionDecision", output.get("decision"))
        assert perm_decision == "deny", \
            f"rm should get permissionDecision=deny but got: {perm_decision}"

        # stderr SHOULD contain the denial reason (exit code 2 shows stderr to Claude)
        # This is the workaround for bug #4669 - stderr is shown, stdout JSON ignored
        assert result.stderr, \
            "Blocked command should have stderr message for exit code 2. stderr was empty."
        assert "trash" in result.stderr.lower() or "blocked" in result.stderr.lower(), \
            f"stderr should contain helpful message. Got: {result.stderr[:200]}"


# =============================================================================
# Test: hooks.json (Claude Code default path) Configuration
# =============================================================================


class TestHooksJson:
    """Verify plugins/autorun/hooks/hooks.json is correctly configured for Claude Code."""

    def test_uses_hook_entry(self):
        """claude-hooks.json calls hook_entry.py."""
        hooks_json = PLUGIN_ROOT / "hooks" / "hooks.json"
        content = hooks_json.read_text(encoding="utf-8")
        assert "hook_entry.py" in content

    def test_no_direct_cli_reference(self):
        """claude-hooks.json uses hook_entry.py, not direct CLI."""
        hooks_json = PLUGIN_ROOT / "hooks" / "hooks.json"
        data = json.loads(hooks_json.read_text(encoding="utf-8"))

        for event, matchers in data.get("hooks", {}).items():
            for matcher in matchers:
                for hook in matcher.get("hooks", []):
                    command = hook.get("command", "")
                    # Should use hook_entry.py wrapper
                    if "autorun" in command.lower():
                        assert "hook_entry.py" in command

    def test_no_shell_script_reference(self):
        """claude-hooks.json does not reference old shell script."""
        hooks_json = PLUGIN_ROOT / "hooks" / "hooks.json"
        content = hooks_json.read_text(encoding="utf-8")
        assert "autorun-hook.sh" not in content


# =============================================================================
# Test: daemon startup owns no package-manager side effects
# =============================================================================


def test_daemon_startup_never_installs_packages_in_the_background():
    content = DAEMON_PY.read_text(encoding="utf-8")

    assert "subprocess" not in content
    assert "pip install" not in content
    assert "threading.Thread" not in content
    assert "Reinstall autorun before daemon startup" in content


# =============================================================================
# Test: UV Compatibility (catches pyproject.toml / UV version mismatches)
# =============================================================================


class TestUVCompatibility:
    """Verify pyproject.toml works with installed UV without warnings.

    Root cause this catches: UV versions may deprecate/remove [tool.uv] fields
    (e.g., `default-extras` was removed in UV 0.9+). When UV encounters unknown
    fields, it prints warnings to stderr. Claude Code interprets any hook stderr
    as "hook error", causing ALL hooks to fail silently — the hook returns valid
    JSON but Claude reports "PreToolUse:Bash hook error" and ignores it.

    This class of bug is invisible: hooks appear to "work" (no crash) but provide
    no protection (all blocked commands pass through).
    """

    def test_uv_available(self):
        """UV must be installed for hook execution."""
        import shutil
        uv_path = shutil.which("uv")
        assert uv_path is not None, (
            "UV not found in PATH. Hooks require UV for execution. "
            "Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
        )

    def test_uv_run_no_stderr_warnings(self):
        """uv run --project <plugin_root> must produce no stderr.

        Claude Code hooks run via 'uv run --project ${CLAUDE_PLUGIN_ROOT} python ...'.
        Any stderr output causes Claude Code to report 'hook error' and ignore
        the hook's JSON response, effectively disabling all hook protections.

        Common causes of stderr:
        - Deprecated [tool.uv] fields (e.g., default-extras removed in UV 0.9+)
        - Invalid pyproject.toml syntax
        - Missing dependencies during resolution
        """
        result = subprocess.run(
            ["uv", "run", "--project", str(PLUGIN_ROOT), "python", "-c", "print('ok')"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Filter out expected UV output (build/install messages go to stderr)
        stderr_lines = [
            line for line in result.stderr.strip().splitlines()
            if line.strip()
            and not line.strip().startswith("Building ")
            and not line.strip().startswith("Built ")
            and not line.strip().startswith("Installed ")
            and not line.strip().startswith("Uninstalled ")
            and not line.strip().startswith("Resolved ")
            and not line.strip().startswith("Prepared ")
            and not line.strip().startswith("Audited ")
        ]

        assert len(stderr_lines) == 0, (
            f"UV produced unexpected stderr (causes Claude Code 'hook error'):\n"
            f"{''.join(stderr_lines)}\n\n"
            f"Fix: Check [tool.uv] section in {PLUGIN_ROOT}/pyproject.toml for "
            f"deprecated fields. Run 'uv run --project {PLUGIN_ROOT} python -c pass' "
            f"to reproduce."
        )
        assert result.returncode == 0, (
            f"uv run failed with exit code {result.returncode}.\n"
            f"stderr: {result.stderr}\n"
            f"Fix: Run 'uv run --project {PLUGIN_ROOT} python -c pass' to diagnose."
        )

    def test_pyproject_toml_no_deprecated_uv_fields(self):
        """pyproject.toml [tool.uv] must not contain deprecated fields.

        UV has removed fields across versions:
        - default-extras: removed in UV 0.9+ (use main dependencies instead)

        When deprecated fields are present, UV prints warnings to stderr,
        which Claude Code interprets as hook errors.
        """
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[no-redef]

        pyproject_path = PLUGIN_ROOT / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)

        uv_config = data.get("tool", {}).get("uv", {})

        # Known deprecated fields
        deprecated_fields = {
            "default-extras": "Removed in UV 0.9+. Move extras to main [project] dependencies instead.",
        }

        found_deprecated = []
        for field, fix in deprecated_fields.items():
            if field in uv_config:
                found_deprecated.append(f"  - {field}: {fix}")

        assert len(found_deprecated) == 0, (
            "pyproject.toml [tool.uv] contains deprecated fields that cause UV stderr warnings.\n"
            "Claude Code treats hook stderr as errors, disabling all hook protections.\n\n"
            "Deprecated fields found:\n" + "\n".join(found_deprecated) + "\n\n"
            f"File: {pyproject_path}"
        )

    def test_hook_entry_via_uv_no_stderr(self):
        """hook_entry.py invoked via UV must produce no stderr.

        This is the actual command Claude Code runs. Tests the full chain:
        UV project resolution → Python launch → hook_entry.py → JSON output.

        Any stderr output = Claude Code reports 'hook error' = hooks disabled.
        """
        env = os.environ.copy()
        env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)

        result = subprocess.run(
            [
                "uv", "run", "--project", str(PLUGIN_ROOT),
                "python", str(HOOK_ENTRY),
            ],
            capture_output=True,
            text=True,
            input='{"tool_name":"Bash","tool_input":{"command":"echo test"}}',
            env=env,
            timeout=15,
        )

        assert result.returncode == 0, (
            f"Hook exited with code {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Phase 1B: empty stdout = pass-through = implicit allow (no rules fired).
        # Input {"tool_name":"Bash",...} with no hook_event_name → event=unknown →
        # dispatch returns None → exit 0 with no stdout (RTK compatibility).
        output = json.loads(result.stdout) if result.stdout.strip() else {}

        # Check for unexpected stderr (filter build/install noise)
        stderr_lines = [
            line for line in result.stderr.strip().splitlines()
            if line.strip()
            and not line.strip().startswith("Building ")
            and not line.strip().startswith("Built ")
            and not line.strip().startswith("Installed ")
            and not line.strip().startswith("Uninstalled ")
            and not line.strip().startswith("Resolved ")
            and not line.strip().startswith("Prepared ")
            and not line.strip().startswith("Audited ")
        ]

        assert len(stderr_lines) == 0, (
            f"Hook produced unexpected stderr (Claude Code treats as 'hook error'):\n"
            f"{''.join(stderr_lines)}\n\n"
            f"JSON output was valid: {output}\n"
            f"Fix: Check pyproject.toml [tool.uv] for deprecated fields, or "
            f"check hook_entry.py for stderr output."
        )

    def test_hook_entry_rm_blocked_or_warned(self):
        """Hook should handle 'rm' commands (block, warn, or pass through cleanly).

        Regardless of whether rm is blocked or allowed, the hook must:
        1. Return valid JSON
        2. Exit 0
        3. Produce no stderr warnings
        """
        env = os.environ.copy()
        env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)

        result = subprocess.run(
            [
                "uv", "run", "--project", str(PLUGIN_ROOT),
                "python", str(HOOK_ENTRY),
            ],
            capture_output=True,
            text=True,
            input='{"tool_name":"Bash","tool_input":{"command":"rm /tmp/nonexistent_file_xyz"}}',
            env=env,
            timeout=15,
        )

        assert result.returncode == 0, (
            f"Hook crashed on 'rm' command (exit {result.returncode}).\n"
            f"stderr: {result.stderr}"
        )

        # Phase 1B: empty stdout = pass-through = implicit allow.
        # Input has no hook_event_name → event=unknown → dispatch returns None →
        # exit 0 with no stdout. Empty = pass-through (no rules fire without event).
        output = json.loads(result.stdout) if result.stdout.strip() else {}

        # Empty dict = pass-through = implicit allow (not a block)
        assert output.get("continue", True) is not False, (
            f"Hook should not block 'rm' without configured session rules: {output}"
        )


# =============================================================================
# Test: Cache Sync (source vs installed plugin)
# =============================================================================


class TestCacheSync:
    """Verify source and cache pyproject.toml stay in sync.

    When the plugin cache has a stale pyproject.toml with deprecated fields,
    hooks fail even though the source is fixed. This test catches the case
    where a developer fixes the source but forgets to update the cache.
    """

    CACHE_ROOT = Path.home() / ".claude" / "plugins" / "cache" / "autorun" / "ar"

    def _get_cache_versions(self):
        """Find installed cache versions."""
        if not self.CACHE_ROOT.exists():
            return []
        return [d for d in self.CACHE_ROOT.iterdir() if d.is_dir() and (d / "pyproject.toml").exists()]

    @pytest.mark.e2e
    def test_cache_pyproject_no_deprecated_uv_fields(self):
        """Cached pyproject.toml must not have deprecated [tool.uv] fields.

        The plugin cache at ~/.claude/plugins/cache/ is what Claude Code
        actually loads at runtime. Even if the source is fixed, a stale
        cache causes hook errors.
        """
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[no-redef]

        versions = self._get_cache_versions()
        if not versions:
            pytest.skip("No plugin cache found (plugin not installed)")

        deprecated_fields = {"default-extras"}
        errors = []

        for version_dir in versions:
            pyproject_path = version_dir / "pyproject.toml"
            with open(pyproject_path, "rb") as f:
                data = tomllib.load(f)
            uv_config = data.get("tool", {}).get("uv", {})
            found = deprecated_fields & set(uv_config.keys())
            if found:
                errors.append(
                    f"  {pyproject_path}: deprecated fields {found}\n"
                    f"  Fix: Copy source pyproject.toml to cache, or reinstall plugin"
                )

        assert len(errors) == 0, (
            "Plugin cache has deprecated [tool.uv] fields that cause hook errors:\n"
            + "\n".join(errors) + "\n\n"
            f"Source: {PLUGIN_ROOT / 'pyproject.toml'}\n"
            f"Run: cp {PLUGIN_ROOT / 'pyproject.toml'} <cache_path>/pyproject.toml"
        )


# =============================================================================
# Test: Old Files Removed
# =============================================================================


def _check_block_hook(session_id, cmd):
    """Daemon-path helper replacing deleted should_block_command.
    Returns the full hook response dict if blocked (deny), else None.
    """
    if not cmd or not cmd.strip():
        return None
    src_dir = PLUGIN_ROOT / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    from autorun.core import EventContext, ThreadSafeDB
    from autorun import plugins as _plugins
    ctx = EventContext(
        session_id=session_id, event="PreToolUse", tool_name="Bash",
        tool_input={"command": cmd}, store=ThreadSafeDB(),
    )
    result = _plugins.check_blocked_commands(ctx)
    if result is None:
        return None
    perm = result.get("hookSpecificOutput", {}).get("permissionDecision", "allow")
    return result if perm == "deny" else None


class TestCommandBlockingE2E:
    """End-to-end tests for command blocking through the hook system.

    Tests exercise check_blocked_commands() (daemon path) to verify that
    dangerous commands are blocked with permissionDecision="deny" + continue=True,
    and safe commands are allowed.

    Bug history:
    - UV stderr from deprecated pyproject.toml fields caused Claude Code to
      treat hook output as "hook error" → fail-open → ALL commands passed
    - Substring matching caused "/ar:plannew" to match "rm" pattern (substring
      of "plannew") → false positive blocking of slash commands
    """

    # ─── Dangerous commands MUST be blocked ───────────────────────────

    def test_rm_is_blocked(self):
        """rm /tmp/file MUST be blocked by default integrations."""
        result = _check_block_hook("test-session", "rm /tmp/file")
        assert result is not None, \
            "rm should be blocked but was allowed. Check DEFAULT_INTEGRATIONS in config.py"
        # For Claude Code deny, 'reason' and 'systemMessage' are intentionally empty
        # (message goes to stderr via exit 2, anti-triple-print).
        # The canonical location is hookSpecificOutput.permissionDecisionReason.
        reason = result.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
        assert "trash" in reason.lower(), f"reason should mention trash: {reason}"

    def test_rm_rf_is_blocked(self):
        """rm -rf /tmp/dir MUST be blocked."""
        result = _check_block_hook("test-session", "rm -rf /tmp/dir")
        assert result is not None, \
            "rm -rf should be blocked. Check DEFAULT_INTEGRATIONS in config.py"

    def test_sudo_rm_is_blocked(self):
        """sudo rm /tmp/file MUST be blocked (prefix detection)."""
        result = _check_block_hook("test-session", "sudo rm /tmp/file")
        assert result is not None, \
            "sudo rm should be blocked via command prefix detection"

    def test_git_reset_hard_matches_pattern(self):
        """'git reset --hard' pattern MUST match the command."""
        from autorun.command_detection import command_matches_pattern
        assert command_matches_pattern(
            "git reset --hard HEAD~1", "git reset --hard"
        ), "git reset --hard should match pattern"

    # ─── Safe commands MUST be allowed ────────────────────────────────

    def test_echo_is_allowed(self):
        """echo is a safe command, must not be blocked."""
        result = _check_block_hook("test-session", "echo hello")
        assert result is None, \
            f"echo should be allowed but was blocked: {result}"

    def test_ls_is_allowed(self):
        """ls is a safe command, must not be blocked."""
        result = _check_block_hook("test-session", "ls -la")
        assert result is None, \
            f"ls should be allowed but was blocked: {result}"

    def test_pwd_is_allowed(self):
        """pwd is a safe command, must not be blocked."""
        result = _check_block_hook("test-session", "pwd")
        assert result is None, \
            f"pwd should be allowed but was blocked: {result}"

    def test_git_status_is_allowed(self):
        """git status is safe, must not be blocked."""
        result = _check_block_hook("test-session", "git status")
        assert result is None, \
            f"git status should be allowed but was blocked: {result}"

    def test_git_log_is_allowed(self):
        """git log is safe, must not be blocked."""
        result = _check_block_hook("test-session", "git log --oneline -5")
        assert result is None, \
            f"git log should be allowed but was blocked: {result}"

    def test_uv_run_is_allowed(self):
        """uv run is safe, must not be blocked."""
        result = _check_block_hook("test-session", "uv run pytest -v")
        assert result is None, \
            f"uv run should be allowed but was blocked: {result}"

    # ─── Response format validation ───────────────────────────────────

    def test_deny_response_has_continue_true(self):
        """deny response MUST have continue=True (AI keeps running, tool blocked by permissionDecision)."""
        from autorun.core import EventContext
        ctx = EventContext(session_id="test-format", event="PreToolUse")
        response = ctx.deny("test")
        assert response["continue"] is True, \
            "deny response must set continue=True (AI keeps running). Tool blocked by permissionDecision + exit 2."
        assert response["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_allow_response_has_continue_true(self):
        """allow response MUST have continue=true to let tool proceed."""
        from autorun.core import EventContext
        ctx = EventContext(session_id="test-format", event="PreToolUse")
        response = ctx.allow("ok")
        assert response["continue"] is True, \
            "allow response must set continue=true"
        assert response["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_deny_response_has_permission_decision(self):
        """deny response MUST have permissionDecision='deny' to block tool."""
        from autorun.core import EventContext
        ctx = EventContext(session_id="test-format", event="PreToolUse")
        response = ctx.deny("blocked rm")
        assert response["hookSpecificOutput"]["permissionDecision"] == "deny", \
            "deny response must have permissionDecision='deny'"
        assert "blocked rm" in response["hookSpecificOutput"]["permissionDecisionReason"]


class TestSlashCommandFalsePositives:
    """Tests that slash commands are NOT falsely blocked by hook patterns.

    Bug history: Substring matching caused "/ar:plannew" to match "rm"
    pattern because "rm" is a substring of "plannew". The AST-based
    command detection (command_detection.py) fixes this by parsing the
    command as a shell AST and only matching commands in command position,
    not as substrings of arguments or other tokens.

    These tests prevent regression of the fix by verifying that all
    autorun slash commands are not falsely blocked.
    """

    # ─── Pattern matching must not match substrings ───────────────────

    def test_rm_pattern_does_not_match_plannew(self):
        """'rm' pattern must NOT match '/ar:plannew' (substring bug)."""
        from autorun.command_detection import command_matches_pattern
        assert not command_matches_pattern("/ar:plannew", "rm"), \
            "'rm' falsely matched '/ar:plannew' — substring matching bug. " \
            "command_matches_pattern must use AST-based detection."

    def test_rm_pattern_does_not_match_autorun(self):
        """'rm' pattern must NOT match '/ar:autorun' (substring 'run')."""
        from autorun.command_detection import command_matches_pattern
        assert not command_matches_pattern("/ar:autorun task", "rm"), \
            "'rm' falsely matched '/ar:autorun' — substring matching bug"

    def test_rm_pattern_does_not_match_planrefine(self):
        """'rm' pattern must NOT match '/ar:planrefine'."""
        from autorun.command_detection import command_matches_pattern
        assert not command_matches_pattern("/ar:planrefine", "rm")

    def test_rm_pattern_does_not_match_commit(self):
        """'rm' pattern must NOT match '/ar:commit'."""
        from autorun.command_detection import command_matches_pattern
        assert not command_matches_pattern("/ar:commit", "rm")

    def test_rm_pattern_does_not_match_echo_rm(self):
        """'rm' pattern must NOT match 'echo rm' (argument position)."""
        from autorun.command_detection import command_matches_pattern
        assert not command_matches_pattern("echo rm", "rm"), \
            "'rm' falsely matched 'echo rm' — rm is an argument, not a command"

    # ─── Slash commands must not be blocked ───────────────────────────

    def test_slash_plannew_not_blocked(self):
        """'/ar:plannew' must not be blocked by any pattern."""
        result = _check_block_hook("test-session", "/ar:plannew")
        assert result is None, \
            f"/ar:plannew was falsely blocked: {result}"

    def test_slash_go_not_blocked(self):
        """'/ar:go task' must not be blocked."""
        result = _check_block_hook("test-session", "/ar:go implement feature")
        assert result is None, \
            f"/ar:go was falsely blocked: {result}"

    def test_slash_status_not_blocked(self):
        """'/ar:st' must not be blocked."""
        result = _check_block_hook("test-session", "/ar:st")
        assert result is None, \
            f"/ar:st was falsely blocked: {result}"

    def test_slash_commit_not_blocked(self):
        """'/ar:commit' must not be blocked."""
        result = _check_block_hook("test-session", "/ar:commit")
        assert result is None, \
            f"/ar:commit was falsely blocked: {result}"

    def test_slash_philosophy_not_blocked(self):
        """'/ar:philosophy' must not be blocked."""
        result = _check_block_hook("test-session", "/ar:philosophy")
        assert result is None, \
            f"/ar:philosophy was falsely blocked: {result}"

    def test_slash_estop_not_blocked(self):
        """'/ar:sos' (emergency stop) must not be blocked."""
        result = _check_block_hook("test-session", "/ar:sos")
        assert result is None, \
            f"/ar:sos was falsely blocked: {result}"

    # ─── Edge cases: rm in various positions ──────────────────────────

    def test_rm_actual_command_IS_blocked(self):
        """Actual 'rm file' command must still be blocked."""
        result = _check_block_hook("test-session", "rm /tmp/test.txt")
        assert result is not None, \
            "Actual 'rm' command should be blocked"

    def test_trash_is_allowed(self):
        """'trash' (the safe alternative) must be allowed."""
        result = _check_block_hook("test-session", "trash /tmp/test.txt")
        assert result is None, \
            f"'trash' should be allowed (it's the safe alternative to rm): {result}"

    def test_echo_rm_is_allowed(self):
        """'echo rm' — rm as argument to echo must be allowed (not blocked as rm)."""
        result = _check_block_hook("test-session", "echo rm")
        assert result is None, \
            f"'echo rm' should be allowed (rm is an echo argument, not a command): {result}"


class TestAllLocationsSync:
    """Verify all code locations stay synchronized across platforms.

    This prevents recurring desync bugs where:
    - Source is fixed but cache/UV tool/Gemini extension have old code
    - UV tool install --force doesn't invalidate cache (uv#9492)
    - Gemini extensions install creates separate copies
    - Build/ artifacts lag behind source

    9 code locations that can desync:
    1. Source: plugins/autorun/src/autorun/
    2. Dev venv: plugins/autorun/.venv/.../autorun/
    3. Build: plugins/autorun/build/lib/autorun/
    4. Claude cache: ~/.claude/plugins/cache/autorun/ar/0.11.0/
    5. UV tool: ~/.local/share/uv/tools/autorun/.../autorun/
    6. Gemini extension: ~/.gemini/extensions/ar/

    Target state (after symlink migration):
    - Source: 1 location (authoritative)
    - Symlinks: UV tool (editable), Gemini extension (link)
    - Caches: Claude Code cache (synced via --install --force)
    - Deleted: Build artifacts (not needed)
    """

    def test_source_hooks_json_is_claude_format(self):
        """Source hooks.json must have Claude Code format (post-split-layout:
        plugins/autorun/hooks/hooks.json holds only Claude events)."""
        hooks_json = PLUGIN_ROOT / "hooks" / "hooks.json"
        content = hooks_json.read_text(encoding="utf-8")

        assert "PreToolUse" in content, \
            "Must have Claude Code event names (not Gemini's BeforeTool)"
        assert "${CLAUDE_PLUGIN_ROOT}" in content, \
            "Must use Claude Code variables (not Gemini's ${extensionPath})"
        assert "run_shell_command" not in content, \
            "Must NOT use Gemini tool names (run_shell_command) in Claude hooks"
        hooks_data = json.loads(content)
        assert set(hooks_data) == {"hooks"}, (
            "Codex plugin loading parses hooks/hooks.json with a strict schema; "
            "metadata such as description belongs in plugin.json"
        )

    @pytest.mark.e2e
    def test_cache_matches_source_hook_entry(self):
        """Location 4: the current Claude cache must match current source."""
        cache_root = Path.home() / ".claude" / "plugins" / "cache" / "autorun" / "ar"
        if not cache_root.is_dir():
            pytest.skip("Claude Code cache not installed")

        manifest = json.loads(
            (PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        cache_file = cache_root / manifest["version"] / "hooks" / "hook_entry.py"
        assert cache_file.is_file(), (
            f"Current Claude cache {cache_file} is missing. "
            "Run: uv run --project plugins/autorun python -m autorun --install ar --force --claude"
        )
        source_content = (PLUGIN_ROOT / "hooks" / "hook_entry.py").read_text(encoding="utf-8")
        assert cache_file.read_text(encoding="utf-8") == source_content, (
            f"Cache {cache_file} doesn't match source. "
            "Run: uv run --project plugins/autorun python -m autorun --install ar --force --claude"
        )

    def test_uv_tool_is_editable_not_copy(self):
        """The generated UV command requests an editable installation."""
        from autorun.installer.runtime import uv_tool_install_argv

        argv = uv_tool_install_argv(PLUGIN_ROOT)
        assert argv[:3] == ("uv", "tool", "install")
        assert "--editable" in argv

    @pytest.mark.e2e
    def test_gemini_extension_hooks_match_source(self):
        """Gemini extension hooks.json must match the Gemini TEMPLATE source
        (not the Claude plugin's hooks.json). Post-split-layout, Claude's
        hooks live at plugins/autorun/hooks/hooks.json and Gemini's live at
        plugins/autorun/src/autorun/gemini_template/hooks/hooks.json.
        """
        gemini_ext = Path.home() / ".gemini/extensions/ar"

        if not gemini_ext.exists():
            pytest.skip("Gemini extension not installed")

        source_hooks = PLUGIN_ROOT / "src" / "autorun" / "gemini_template" / "hooks" / "hooks.json"
        ext_hooks = gemini_ext / "hooks" / "hooks.json"

        assert ext_hooks.exists(), (
            f"Gemini extension missing hooks.json at {ext_hooks}. "
            f"Run: uv run --project plugins/autorun python -m autorun --install --force"
        )

        source_content = source_hooks.read_text(encoding="utf-8")
        ext_content = ext_hooks.read_text(encoding="utf-8")
        assert source_content == ext_content, (
            f"Gemini extension hooks.json doesn't match source. "
            f"Source: {source_hooks}\n"
            f"Extension: {ext_hooks}\n"
            f"Run: uv run --project plugins/autorun python -m autorun --install --force"
        )

    def test_build_artifacts_do_not_exist(self):
        """Locations 3, 9: Build artifacts should be deleted."""
        build_dirs = [
            PLUGIN_ROOT / "build",
            Path.home() / ".gemini/extensions/ar/build"
        ]

        for build_dir in build_dirs:
            if build_dir.exists() and not build_dir.is_symlink():
                pytest.fail(
                    f"Build artifacts at {build_dir} should not exist (gitignored). "
                    f"These are setuptools artifacts that lag behind source. "
                    f"Run: trash {build_dir}"
                )

    def test_only_one_daemon_process(self, daemon_manager):
        """Verify test-spawned daemons don't accumulate.

        Uses DaemonManager.assert_daemon_count() which:
        - Distinguishes test-spawned from production daemons
        - Kills extras (keeps oldest test daemon)
        - Fails at >2 test daemons, warns at 1-2
        - Never touches production daemons
        """
        daemon_manager.assert_daemon_count(max_test_daemons=2)


class TestCleanup:
    """Verify old/unused files are removed."""

    def test_shell_script_deleted(self):
        """autorun-hook.sh should not exist."""
        old_script = PLUGIN_ROOT / "hooks" / "autorun-hook.sh"
        assert not old_script.exists()
