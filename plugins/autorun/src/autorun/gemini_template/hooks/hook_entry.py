#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Autorun Hook Entry Point - fast path with background bootstrap.

Entry point for Claude Code hooks that works with or without UV installation.

Execution Priority:
1. Fast path: Direct IPC to the daemon for a complete plugin-local venv
2. Recovery: Plugin-local or global autorun CLI
3. Bootstrap fallback: Run from plugin source

Design Principles:
- Fail-open for lifecycle/context hooks, fail-closed for tool permission gates
- Fast: Use stdlib-only daemon IPC without a second Python process
- Robust: Handle missing env vars, missing files, import errors
- Minimal: Only stdlib imports (json, os, shutil, subprocess, sys, time)
- Background bootstrap: Spawn bootstrap via nohup, return immediately

Bootstrap Strategy:
- Hook timeout: 10s (Claude enforced)
- Bootstrap time: 5-10s with modern uv (fast package manager)
- Background spawn via nohup - hook returns immediately
- Next hook invocation finds deps installed
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import NoReturn

# =============================================================================
# Constants
# =============================================================================

# Stdlib hot-path mirror of CONFIG["hook_wrapper_timeouts_seconds"]. The spec
# test compares every value to CONFIG so this cannot drift silently. Importing
# autorun.config here executes the package initializer on every hook invocation.
HOOK_TIMEOUT_BY_CLI = {
    "gemini": 4.0,
    "antigravity": 4.0,
    "qwen": 4.0,
    "claude": 5.0,
    "codex": 5.0,
    "forgecode": 5.0,
}
HOOK_TIMEOUT = HOOK_TIMEOUT_BY_CLI["gemini"]
BOOTSTRAP_LOCKFILE = "/tmp/autorun_bootstrap.lock"
BOOTSTRAP_MSG = (
    "autorun deps not installed. Run: uv pip install autorun && autorun --install"
)
DEBUG_LOG_MAX_BYTES = 1_000_000
DEBUG_VALUE_MAX_CHARS = 4_000


def _debug_log_path() -> Path:
    """Return the bounded hook debug log path."""
    return Path.home() / ".autorun" / "hook_entry_debug.log"


def _short_debug_value(value: str, limit: int = DEBUG_VALUE_MAX_CHARS) -> str:
    """Return a bounded debug string so hook logging cannot dominate runtime."""
    if len(value) <= limit:
        return value
    half = max(1, limit // 2)
    omitted = len(value) - (half * 2)
    return (
        value[:half]
        + f"\n... <omitted {omitted} chars from hook debug log> ...\n"
        + value[-half:]
    )


def _append_debug_log(message: str) -> None:
    """Append to hook debug log with a small rotation cap.

    Hook entry runs inside strict harness timeouts. Debug logging must never
    append unbounded stdout/stderr payloads or force large-file writes.
    """
    try:
        debug_log = _debug_log_path()
        debug_log.parent.mkdir(exist_ok=True)
        if debug_log.exists() and debug_log.stat().st_size > DEBUG_LOG_MAX_BYTES:
            rotated = debug_log.with_suffix(debug_log.suffix + ".1")
            try:
                rotated.unlink(missing_ok=True)
            except TypeError:
                if rotated.exists():
                    rotated.unlink()
            debug_log.replace(rotated)
        with open(debug_log, "a", encoding="utf-8") as f:
            f.write(message)
            if not message.endswith("\n"):
                f.write("\n")
    except Exception:
        pass

# =============================================================================
# CLI Detection (dual Claude Code / Gemini CLI support)
# =============================================================================


_VALID_CLI_TYPES = ("claude", "gemini", "antigravity", "qwen", "codex", "forgecode")
_TOOL_GATE_EVENTS = {"PreToolUse", "BeforeTool", "PermissionRequest"}


def hook_timeout_for_cli(cli_type: str) -> float:
    """Return the contract-tested wrapper timeout without package imports."""
    return float(HOOK_TIMEOUT_BY_CLI.get(cli_type, HOOK_TIMEOUT_BY_CLI["claude"]))


def detect_cli_type(payload: dict | None = None) -> str:
    """Detect which CLI is calling the hook.

    Detection priority:
    1. --cli <type> argument in sys.argv (explicit, set by hooks.json files)
    2. cli_type/source in the hook payload
    3. Platform-specific payload path/event hints
    4. Platform-specific environment variables
    5. GEMINI_PROJECT_DIR present without CLAUDE_PROJECT_DIR
    6. Default to "claude"

    Platform-specific hook files may pass --cli explicitly, but plugin hooks
    loaded by multiple harnesses must be able to infer the caller from payload:
        hooks.json   (Gemini): hook_entry.py --cli gemini
        hooks.json   (Claude/Codex plugin): hook_entry.py
        ~/.codex/hooks.json  : hook_entry.py --cli codex

    Returns:
        str: one of "claude", "gemini", "qwen", "codex", "forgecode"
    """
    try:
        # Priority 1: Explicit --cli argument (most reliable - set by hooks.json)
        for i, arg in enumerate(sys.argv[1:], 1):
            if arg == "--cli" and i < len(sys.argv):
                value = sys.argv[i + 1] if i + 1 < len(sys.argv) else ""
                if value in _VALID_CLI_TYPES:
                    return value
            elif arg.startswith("--cli="):
                value = arg.split("=", 1)[1]
                if value in _VALID_CLI_TYPES:
                    return value

        if payload:
            explicit = payload.get("cli_type") or payload.get("source")
            if explicit in _VALID_CLI_TYPES:
                return explicit

            event_name = payload.get("hook_event_name")
            if event_name in {"BeforeTool", "AfterTool", "BeforeAgent", "AfterAgent", "SessionEnd"}:
                return "gemini"
            if event_name in {"PermissionRequest"}:
                return "forgecode"

            transcript_path = str(payload.get("transcript_path", ""))
            if ".codex" in transcript_path or "/codex/" in transcript_path:
                return "codex"
            if ".qwen" in transcript_path or "/qwen/" in transcript_path:
                return "qwen"
            if ".gemini" in transcript_path or "/gemini/" in transcript_path:
                return "gemini"
            if ".claude" in transcript_path or "/claude/" in transcript_path:
                return "claude"

        # Platform-specific environment variables
        if os.environ.get("GEMINI_SESSION_ID"):
            return "gemini"
        if os.environ.get("QWEN_SESSION_ID"):
            return "qwen"
        if os.environ.get("CODEX_SESSION_ID"):
            return "codex"
        if os.environ.get("FORGE_CONFIG"):
            return "forgecode"
        if os.environ.get("GEMINI_PROJECT_DIR") and not os.environ.get("CLAUDE_PROJECT_DIR"):
            return "gemini"
        if os.environ.get("QWEN_PROJECT_DIR") and not os.environ.get("CLAUDE_PROJECT_DIR"):
            return "qwen"

        # Default to Claude (safe fallback - preserves existing behavior)
        return "claude"
    except Exception:
        # Ultimate fail-safe: if detection crashes, assume Claude
        return "claude"


def get_project_dir() -> str:
    """Get project directory regardless of CLI.

    Returns:
        str: Absolute path to project directory

    Safety: Multiple fallbacks ensure we always return a valid path.
    """
    try:
        # Qwen can carry Gemini-compatible env in mixed extension setups; prefer
        # the native Qwen project root when it is present.
        project_dir = os.environ.get("QWEN_PROJECT_DIR")
        if project_dir:
            return project_dir

        # Try Gemini before Claude for Gemini-family hooks.
        project_dir = os.environ.get("GEMINI_PROJECT_DIR")
        if project_dir:
            return project_dir

        # Fall back to Claude (also set by Gemini as alias per docs)
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
        if project_dir:
            return project_dir

        # Final fallback to current directory
        return os.getcwd()
    except Exception:
        # Ultimate fail-safe: if all fails, use cwd
        return os.getcwd()


def get_plugin_root() -> str:
    """Get plugin root directory regardless of CLI.

    Returns:
        str: Absolute path to plugin root directory

    Safety: Works with both CLAUDE_PLUGIN_ROOT and Gemini's AUTORUN_PLUGIN_ROOT.
            Falls back to using __file__ location if env vars not set.
    """
    try:
        # Try AUTORUN_PLUGIN_ROOT first (set by Claude Code hooks when specified)
        plugin_root = os.environ.get("AUTORUN_PLUGIN_ROOT")
        if plugin_root:
            return plugin_root

        # Claude Code also sets CLAUDE_PLUGIN_ROOT
        plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
        if plugin_root:
            return plugin_root

        # Gemini CLI doesn't set env vars, so infer from script location
        # This file is at: <plugin_root>/hooks/hook_entry.py
        # So plugin_root is two directories up
        script_path = os.path.abspath(__file__)
        hooks_dir = os.path.dirname(script_path)  # <plugin_root>/hooks/
        plugin_root = os.path.dirname(hooks_dir)  # <plugin_root>/
        return plugin_root
    except Exception:
        # Ultimate fallback: current directory (may not be correct)
        return os.getcwd()


# =============================================================================
# Fail-Open Response (never crash Claude or Gemini)
# =============================================================================


def fail_open(message: str = "") -> NoReturn:
    """Return valid JSON that allows Claude to continue, then exit.

    Claude hooks MUST return valid JSON. On any error, we return a
    permissive response so Claude can continue operating.

    Args:
        message: Optional message to include in systemMessage field
    """
    response = {
        "continue": True,
        "stopReason": "",
        "suppressOutput": False,
        "systemMessage": f"[autorun] {message}" if message else "",
    }
    print(json.dumps(response))
    sys.exit(0)


def is_tool_gate_event(event_name: str) -> bool:
    """Return True for events where fail-open would allow a tool to run."""
    return event_name in _TOOL_GATE_EVENTS


def _peek_stdin_text() -> str:
    """Read stdin without consuming it when possible."""
    try:
        if hasattr(sys.stdin, "getvalue"):
            return sys.stdin.getvalue()
        if hasattr(sys.stdin, "tell") and hasattr(sys.stdin, "seek"):
            pos = sys.stdin.tell()
            text = sys.stdin.read()
            sys.stdin.seek(pos)
            return text
    except Exception:
        return ""
    return ""


def _peek_event_name(default: str = "unknown") -> str:
    """Best-effort event name extraction for fallback safety decisions."""
    text = _peek_stdin_text()
    if not text:
        return default
    try:
        payload = json.loads(text)
        return payload.get("hook_event_name", default)
    except (json.JSONDecodeError, TypeError, AttributeError):
        return default


def fail_closed_tool_gate(message: str, cli_type: str, event_name: str) -> NoReturn:
    """Block tool execution when autorun cannot evaluate a permission gate."""
    reason = (
        f"[autorun] {message}. Blocking tool use to avoid fail-open. "
        "Run `autorun --restart-daemon` or `autorun --install --force`, then retry."
    )
    hook_specific = {
        "hookEventName": event_name,
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }

    if cli_type == "codex":
        response = {
            "decision": "block",
            "reason": reason,
            "systemMessage": reason,
            "hookSpecificOutput": hook_specific,
        }
        print(json.dumps(response))
        sys.exit(0)

    schema_type = "permissive" if cli_type in {"gemini", "antigravity", "qwen"} else "strict"
    decision = "deny" if schema_type == "permissive" else "block"
    response = {
        "decision": decision,
        "permissionDecision": "deny",
        "reason": reason if schema_type == "permissive" else "",
        "continue": True,
        "stopReason": "",
        "suppressOutput": False,
        "systemMessage": reason if schema_type == "permissive" else "",
        "hookSpecificOutput": hook_specific,
    }
    print(json.dumps(response))
    if cli_type == "claude":
        print(reason, file=sys.stderr)
        sys.exit(2)
    sys.exit(0)


def fail_after_cli_timeout(cli_type: str, event_name: str) -> NoReturn:
    """Return promptly when the fast CLI path times out.

    The outer harness timeout is short. Running the direct-import fallback after
    the CLI already consumed its budget can make Claude/Codex discard output.
    """
    timeout = hook_timeout_for_cli(cli_type)
    message = f"autorun CLI timed out after {timeout:g}s"
    if is_tool_gate_event(event_name):
        fail_closed_tool_gate(message, cli_type, event_name)
    fail_open(message)


# =============================================================================
# CLI Resolution (priority: venv > global > fallback)
# =============================================================================


def get_autorun_bin() -> Path | None:
    """Find autorun executable with priority: venv > global.

    Returns:
        Path to autorun binary, or None if not found.

    Priority order:
        1. Plugin-local venv (isolated, preferred)
        2. Global installation (uv pip install / pip install)

    Safety: Works with both Claude Code and Gemini CLI via get_plugin_root().
    """
    plugin_root = get_plugin_root()

    if plugin_root:
        # Priority 1: Plugin-local venv
        venv_bin = Path(plugin_root) / ".venv" / "bin" / "autorun"
        if venv_bin.exists():
            return venv_bin

    # Priority 2: Global installation
    global_bin = shutil.which("autorun")
    if global_bin:
        return Path(global_bin)

    return None


def _extract_json(text: str) -> str | None:
    """Return the last complete JSON object from child stdout."""
    cleaned = text.strip()
    if not cleaned:
        return None

    candidates = [cleaned, *reversed(cleaned.splitlines())]
    for candidate in candidates:
        candidate = candidate.strip()
        if not (candidate.startswith("{") and candidate.endswith("}")):
            continue
        try:
            json.loads(candidate)
        except json.JSONDecodeError:
            continue
        return candidate
    return None


def _emit_cli_result(result: subprocess.CompletedProcess[str]) -> int | None:
    """Emit only harness-valid child output and return its accepted exit code.

    Exit-zero stderr is diagnostic noise and must stay in the file log. Exit
    two stderr is intentional Claude denial feedback. Invalid success stdout
    returns None so the caller can use the direct-import fallback safely.
    """
    if result.returncode not in (0, 2):
        return None

    json_block = _extract_json(result.stdout)
    if result.stdout and json_block is None:
        if result.returncode == 0:
            return None
    elif json_block is not None:
        print(json_block, end="")

    if result.returncode == 2 and result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result.returncode


def _daemon_socket_path() -> Path:
    """Return the shared daemon socket path without importing autorun."""
    return Path(os.environ.get("AUTORUN_HOME", Path.home() / ".autorun")) / "daemon.sock"


def _can_use_direct_daemon(autorun_bin: Path | None) -> bool:
    """Limit fast IPC to a complete plugin-local installation."""
    if autorun_bin is None:
        return False
    normalized = str(autorun_bin).replace("\\", "/")
    return normalized.endswith("/.venv/bin/autorun")


def _emit_daemon_result(response: dict, cli_type: str) -> int:
    """Emit an already platform-normalized daemon response."""
    if not response:
        if cli_type in {"gemini", "antigravity", "qwen"}:
            print(json.dumps({"continue": True}))
        return 0

    print(json.dumps(response))
    decision = response.get("hookSpecificOutput", {}).get(
        "permissionDecision", response.get("decision", "allow")
    )
    if decision == "deny" and cli_type == "claude":
        # Import the configurable workaround only on the uncommon deny path;
        # successful hooks stay stdlib-only and avoid package startup cost.
        try:
            from autorun.config import should_use_exit2_workaround

            use_exit2 = should_use_exit2_workaround({"cli_type": cli_type})
        except Exception:
            use_exit2 = True
        if use_exit2:
            reason = response.get("hookSpecificOutput", {}).get(
                "permissionDecisionReason", response.get("reason", "Tool blocked")
            )
            print(reason, file=sys.stderr)
            return 2
    return 0


def try_daemon(stdin_data: str, cli_type: str) -> tuple[bool, int]:
    """Send one hook directly to the live Unix daemon without a child Python."""
    if not hasattr(socket, "AF_UNIX"):
        return False, 0

    socket_path = _daemon_socket_path()
    if not socket_path.exists():
        return False, 0

    try:
        payload = json.loads(stdin_data or "{}")
    except (json.JSONDecodeError, TypeError):
        return False, 0
    if not isinstance(payload, dict):
        return False, 0

    payload.setdefault("_pid", os.getppid())
    payload.setdefault("_cwd", os.getcwd())
    payload["cli_type"] = cli_type
    event_name = payload.get("hook_event_name", "unknown")
    connected = False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as daemon_socket:
            daemon_socket.settimeout(hook_timeout_for_cli(cli_type))
            daemon_socket.connect(str(socket_path))
            connected = True
            daemon_socket.sendall(json.dumps(payload).encode("utf-8") + b"\n")
            with daemon_socket.makefile("r", encoding="utf-8") as response_stream:
                response_line = response_stream.readline()
        if not response_line:
            raise ConnectionError("daemon closed without a response")
        response = json.loads(response_line)
        if not isinstance(response, dict):
            raise ValueError("daemon response is not a JSON object")
        return True, _emit_daemon_result(response, cli_type)
    except socket.timeout:
        if connected:
            _append_debug_log("Direct daemon fast path timed out after connecting")
            fail_after_cli_timeout(cli_type, event_name)
        return False, 0
    except (ConnectionError, OSError, ValueError, json.JSONDecodeError) as error:
        if connected:
            message = f"Daemon returned an invalid response: {type(error).__name__}"
            _append_debug_log(message)
            if is_tool_gate_event(event_name):
                fail_closed_tool_gate(message, cli_type, event_name)
            fail_open(message)
        return False, 0


def try_cli(bin_path: Path, stdin_data: str = "", cli_type: str | None = None) -> bool:
    """Try to run autorun CLI, passing pre-read stdin payload.

    Args:
        bin_path: Path to autorun executable
        stdin_data: Pre-read stdin payload (read once in main() to avoid
            consuming stdin before the fallback path needs it)

    Returns:
        True if CLI executed successfully with valid output, False otherwise.

    Exit Code Handling:
        Exit code 0 = CLI succeeded (even when denying tool access)
        Exit code 2 = blocking ERROR causing "hook error"

        The JSON permissionDecision: "deny" blocks the tool, not exit code.

        References:
        - GitHub Issues: #4669, #18312, #13744, #20946
        - Exit code semantics: https://claude.com/blog/how-to-configure-hooks
        - Hook docs: https://code.claude.com/docs/en/hooks

    Bug history:
        - Previously returned True unconditionally even when subprocess failed
          (non-zero exit code, argparse errors). This caused
          hook_entry.py to exit without printing any JSON, and Claude Code
          would fail-open (allow rm, git reset --hard, etc.).
        - Empty stdout with exit code 0 is a valid implicit allow response.
          It must not trigger fallback, especially for Gemini extension hooks
          whose installed extension root intentionally does not contain src/.
        - Previously read stdin inside try_cli, consuming it so the fallback
          path (run_fallback → run_client) couldn't read the payload.
    """
    cli_type = cli_type or detect_cli_type()
    try:
        result = subprocess.run(
            [str(bin_path)],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=hook_timeout_for_cli(cli_type),
        )

        # Debug logging (ALWAYS enabled to diagnose hook issues)
        try:
            lines = [
                f"CLI exit code: {result.returncode}",
                (
                    f"CLI stdout ({len(result.stdout)} bytes):\n"
                    f"{_short_debug_value(result.stdout)}"
                ),
                (
                    f"CLI stderr ({len(result.stderr)} bytes):\n"
                    f"{_short_debug_value(result.stderr)}"
                ),
            ]
            try:
                json.loads(result.stdout)
                lines.append("JSON valid")
            except json.JSONDecodeError as e:
                lines.append(f"JSON invalid: {e}")
            _append_debug_log("\n".join(lines))
        except Exception:
            pass  # Never fail hook due to debug logging

        # ═══════════════════════════════════════════════════════════════
        # TWO PATHWAYS: Primary (exit 0) and Workaround (exit 2)
        # ═══════════════════════════════════════════════════════════════
        # Exit 0: Normal (allow OR Gemini deny)
        # Exit 2: Claude Code Bug #4669 workaround (deny + stderr → AI)
        # Other: Error (stale install, import failure, etc.)
        # ═══════════════════════════════════════════════════════════════

        exit_code = _emit_cli_result(result)
        if exit_code is None:
            return False
        sys.exit(exit_code)

    except subprocess.TimeoutExpired:
        return False
    except (subprocess.SubprocessError, OSError):
        return False


# =============================================================================
# Bootstrap Configuration
# =============================================================================


def is_bootstrap_disabled() -> bool:
    """Check if bootstrap is disabled via --no-bootstrap flag or env var.

    Disabled by:
    - --no-bootstrap flag in command (added to hooks.json via `autorun --no-bootstrap`)
    - AUTORUN_NO_BOOTSTRAP=1 environment variable
    """
    # Check command line flag (added to hooks.json commands)
    if "--no-bootstrap" in sys.argv:
        return True

    # Check environment variable
    if os.environ.get("AUTORUN_NO_BOOTSTRAP", "0") == "1":
        return True

    return False


def is_bootstrap_running() -> bool:
    """Check if bootstrap is already running via lockfile."""
    lockfile = Path(BOOTSTRAP_LOCKFILE)
    if not lockfile.exists():
        return False
    # Check if lockfile is stale (older than 60 seconds)
    try:
        age = time.time() - lockfile.stat().st_mtime
        if age > 60:
            lockfile.unlink()
            return False
        return True
    except OSError:
        return False


def can_bootstrap() -> tuple[bool, str]:
    """Check if we have the tools needed to bootstrap.

    Returns:
        (can_bootstrap, reason) - True if tools available, False with reason if not
    """
    # Check Python version
    if sys.version_info < (3, 10):
        return (
            False,
            f"Python 3.10+ required (have {sys.version_info.major}.{sys.version_info.minor})",
        )

    # Check for uv or pip3
    has_uv = shutil.which("uv") is not None
    has_pip = shutil.which("pip3") is not None or shutil.which("pip") is not None

    if not has_uv and not has_pip:
        return False, "Neither uv nor pip found in PATH"

    # Check plugin root is set (either AUTORUN_PLUGIN_ROOT or CLAUDE_PLUGIN_ROOT)
    if not os.environ.get("AUTORUN_PLUGIN_ROOT") and not os.environ.get("CLAUDE_PLUGIN_ROOT"):
        return False, "Plugin root not set (need AUTORUN_PLUGIN_ROOT or CLAUDE_PLUGIN_ROOT)"

    return True, "uv" if has_uv else "pip"


def spawn_background_bootstrap() -> bool:
    """Spawn bootstrap process in background using nohup.

    This runs uv pip install + autorun --install detached from the hook process.
    The hook returns immediately; next invocation will find deps installed.

    Returns:
        True if bootstrap was spawned, False if skipped/disabled
    """
    # Check if disabled
    if is_bootstrap_disabled():
        return False

    # Check if already running
    if is_bootstrap_running():
        return True  # Already bootstrapping

    # Check if we can bootstrap
    can_run, tool_or_reason = can_bootstrap()
    if not can_run:
        return False  # Can't bootstrap, reason in tool_or_reason

    # Create lockfile
    try:
        Path(BOOTSTRAP_LOCKFILE).touch()
    except OSError:
        pass  # Best effort

    # Bootstrap script: install deps, then run plugin install
    # Use uv if available, fall back to pip3
    if tool_or_reason == "uv":
        install_cmd = "uv pip install autorun"
    else:
        install_cmd = "pip3 install --user autorun"

    bootstrap_cmd = f"""
        {install_cmd} 2>/dev/null
        autorun --install 2>/dev/null
        rm -f {BOOTSTRAP_LOCKFILE}
    """

    # Spawn detached with nohup
    try:
        subprocess.Popen(
            ["nohup", "sh", "-c", bootstrap_cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except OSError:
        return False


# =============================================================================
# Fallback: Direct Import (limited functionality, no deps)
# =============================================================================


def get_src_dir() -> Path | None:
    """Get src directory relative to this script for fallback import.

    Returns:
        Path to src directory, or None if not found.
    """
    try:
        script_dir = Path(__file__).resolve().parent
        # hooks/hook_entry.py -> hooks/ -> plugin_root/ -> src/
        src_dir = script_dir.parent / "src"
        return src_dir if src_dir.is_dir() else None
    except Exception:
        return None


def run_fallback() -> None:
    """Run with direct import (limited functionality, no external deps).

    This is the fallback when no CLI is available. It attempts to import autorun directly from the plugin source. If dependencies are missing,
    it spawns a background bootstrap process and returns fail_open so the CLI
    can continue. The next hook invocation will find deps installed.

    Safety: Works with both Claude Code and Gemini CLI via get_plugin_root().
    """
    plugin_root = get_plugin_root()

    # Try plugin root first, then relative path
    if plugin_root:
        src_dir = Path(plugin_root) / "src"
    else:
        src_dir = get_src_dir()

    if not src_dir or not src_dir.is_dir():
        event_name = _peek_event_name()
        if is_tool_gate_event(event_name):
            fail_closed_tool_gate("Cannot locate plugin source", detect_cli_type(), event_name)
        fail_open("Cannot locate plugin source")

    # Add to Python path for imports
    src_str = str(src_dir)
    if src_str not in sys.path:
        sys.path.insert(0, src_str)

    try:
        # The primary CLI path already tried daemon mode. Fallback must be an
        # actual in-process dispatch path, otherwise a daemon timeout can repeat
        # and exceed the outer hook process timeout.
        os.environ["AUTORUN_USE_DAEMON"] = "0"

        from autorun.__main__ import main as autorun_main

        exit_code = autorun_main()
        sys.exit(exit_code if exit_code is not None else 0)
    except ImportError as e:
        # Deps missing - try background bootstrap
        if is_bootstrap_disabled():
            fail_open(
                f"Import error: {e}. Bootstrap disabled. "
                "Run manually: uv pip install autorun && autorun --install"
            )
        elif is_bootstrap_running():
            fail_open("autorun bootstrapping in background, will be ready shortly")
        else:
            can_run, reason = can_bootstrap()
            if can_run:
                spawn_background_bootstrap()
                fail_open("autorun bootstrapping in background, will be ready shortly")
            else:
                fail_open(
                    f"Import error: {e}. Cannot bootstrap: {reason}. "
                    "Run manually: uv pip install autorun && autorun --install"
                )
    except Exception as e:
        fail_open(f"Runtime error: {e}")


# =============================================================================
# Entry Point
# =============================================================================


def main() -> None:
    """Hook entry point with fast path and background bootstrap.

    Flow:
        1. Read stdin once (payload is consumed on first read)
        2. Use direct daemon IPC for a complete plugin-local install
        3. If unavailable, try the installed CLI recovery path
        4. If CLI fails, restore stdin and try direct-import fallback
        5. If import fails, spawn background bootstrap via nohup

    Bug history:
        stdin was previously read inside try_cli(), so on CLI failure the
        fallback path (run_fallback → run_client → json.load(sys.stdin))
        would get empty input and fail silently. Now stdin is read once
        here and passed explicitly to try_cli, then restored via StringIO
        for the fallback path.
    """
    import io

    # Read stdin once — it can only be consumed once
    stdin_data = ""
    try:
        if not sys.stdin.isatty():
            stdin_data = sys.stdin.read()
    except EOFError:
        pass

    # Debug logging (ALWAYS enabled to diagnose hook issues)
    debug_lines = []

    def log_debug(msg: str):
        import datetime
        debug_lines.append(f"[{datetime.datetime.now()}] {msg}")

    def flush_debug():
        if debug_lines:
            _append_debug_log("\n".join(debug_lines))
            debug_lines.clear()

    log_debug("=" * 80)
    event_name = "unknown"
    payload_for_detection = None
    if stdin_data:
        try:
            payload_for_detection = json.loads(stdin_data)
            event_name = payload_for_detection.get('hook_event_name', 'unknown')
        except json.JSONDecodeError:
            pass
    
    log_debug(f"Hook entry started (Event: {event_name})")
    log_debug(f"Hook entry stdin ({len(stdin_data)} bytes)")

    cli_type = detect_cli_type(payload_for_detection)
    log_debug(f"Detected CLI: {cli_type} (from --cli arg: {'--cli' in sys.argv})")
    log_debug(f"Env GEMINI_SESSION_ID: {os.environ.get('GEMINI_SESSION_ID')}")
    log_debug(f"Env GEMINI_PROJECT_DIR: {os.environ.get('GEMINI_PROJECT_DIR')}")
    log_debug(f"Env QWEN_SESSION_ID: {os.environ.get('QWEN_SESSION_ID')}")
    log_debug(f"Env QWEN_PROJECT_DIR: {os.environ.get('QWEN_PROJECT_DIR')}")
    log_debug(f"Env CLAUDE_PROJECT_DIR: {os.environ.get('CLAUDE_PROJECT_DIR')}")

    # Inject cli_type into stdin payload so daemon gets explicit CLI identity.
    # This allows all harnesses to call the shared daemon simultaneously without
    # ambiguity, including plugin hooks that are shared by Claude and Codex.
    if stdin_data and cli_type:
        try:
            payload = json.loads(stdin_data)
            payload["cli_type"] = cli_type
            stdin_data = json.dumps(payload)
            log_debug(f"Injected cli_type={cli_type} into payload")
        except (json.JSONDecodeError, Exception) as e:
            log_debug(f"Could not inject cli_type: {e}")

    autorun_bin = get_autorun_bin()
    log_debug(f"Selected binary: {autorun_bin}")
    if _can_use_direct_daemon(autorun_bin):
        handled, daemon_exit_code = try_daemon(stdin_data, cli_type)
        if handled:
            log_debug(f"Direct daemon fast path finished with exit code {daemon_exit_code}")
            flush_debug()
            sys.exit(daemon_exit_code)
        log_debug("Direct daemon unavailable; using CLI recovery path")

    if autorun_bin:
        try:
            # OPTIMIZATION: Bypassing 'uv run' overhead by using venv python directly.
            # If autorun_bin is in a .venv, we use its python to run the module.
            cmd = [str(autorun_bin)]
            if ".venv/bin/autorun" in str(autorun_bin):
                venv_python = autorun_bin.parent / "python"
                if venv_python.exists():
                    # Check if it's a script we can run as a module
                    cmd = [str(venv_python), "-m", "autorun"]
                    log_debug(f"Using optimized venv path: {' '.join(cmd)}")

            # Inline try_cli logic to allow for better logging
            result = subprocess.run(
                cmd,
                input=stdin_data,
                capture_output=True,
                text=True,
                timeout=hook_timeout_for_cli(cli_type),
            )
            log_debug(f"CLI exit code: {result.returncode}")
            log_debug(f"CLI stdout ({len(result.stdout)} bytes):\n{result.stdout}")
            if result.stderr:
                log_debug(f"CLI stderr ({len(result.stderr)} bytes):\n{result.stderr}")

            exit_code = _emit_cli_result(result)
            if exit_code is None:
                log_debug("✗ CLI output was not a supported hook response")
            else:
                log_debug(f"Hook entry finished with exit code {exit_code}")
                flush_debug()
                sys.exit(exit_code)
        except subprocess.TimeoutExpired:
            log_debug("✗ CLI timed out")
            flush_debug()
            fail_after_cli_timeout(cli_type, event_name)
        except Exception as e:
            log_debug(f"✗ CLI exception: {e}")

    # Restore stdin for fallback path (run_client reads from sys.stdin)
    sys.stdin = io.StringIO(stdin_data)

    # No CLI available or CLI failed - try fallback
    log_debug("Starting fallback (direct import)...")
    flush_debug()
    run_fallback()
    log_debug("Hook entry finished (fallback path)")


if __name__ == "__main__":
    main()
