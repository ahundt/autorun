#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Clautorun Hook Entry Point - fast path with background bootstrap.

Entry point for Claude Code hooks that works with or without UV installation.

Execution Priority:
1. Fast path: Plugin-local venv (${CLAUDE_PLUGIN_ROOT}/.venv/bin/clautorun)
2. Fast path: Global CLI if available (UV/pip installed)
3. Fallback: Run from plugin cache via CLAUDE_PLUGIN_ROOT

Design Principles:
- Fail-open: Never crash Claude - always output valid JSON
- Fast: Try CLI first (no Python import overhead if available)
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
import subprocess
import sys
import time
from pathlib import Path
from typing import NoReturn

# =============================================================================
# Constants
# =============================================================================

# Gemini CLI enforces 5s timeout; Claude Code enforces 10s.
# We use 4s to stay safe on both platforms.
HOOK_TIMEOUT = 4
BOOTSTRAP_LOCKFILE = "/tmp/clautorun_bootstrap.lock"
BOOTSTRAP_MSG = (
    "clautorun deps not installed. Run: uv pip install clautorun && clautorun --install"
)

# =============================================================================
# CLI Detection (dual Claude Code / Gemini CLI support)
# =============================================================================


def detect_cli_type() -> str:
    """Detect which CLI is calling the hook.

    Detection priority:
    1. --cli <type> argument in sys.argv (explicit, set by hooks.json/claude-hooks.json)
    2. GEMINI_SESSION_ID environment variable
    3. GEMINI_PROJECT_DIR present without CLAUDE_PROJECT_DIR
    4. Default to "claude"

    The hooks files pass --cli explicitly so both CLIs can call the shared daemon
    simultaneously without ambiguity:
    - hooks.json (Gemini): hook_entry.py --cli gemini
    - claude-hooks.json (Claude): hook_entry.py --cli claude

    Returns:
        str: "claude" or "gemini"
    """
    try:
        # Priority 1: Explicit --cli argument (most reliable - set by hooks.json)
        for i, arg in enumerate(sys.argv[1:], 1):
            if arg == "--cli" and i < len(sys.argv):
                value = sys.argv[i + 1] if i + 1 < len(sys.argv) else ""
                if value in ("gemini", "claude"):
                    return value
            elif arg.startswith("--cli="):
                value = arg.split("=", 1)[1]
                if value in ("gemini", "claude"):
                    return value

        # Priority 2: Gemini-specific environment variables
        if os.environ.get("GEMINI_SESSION_ID"):
            return "gemini"
        elif os.environ.get("GEMINI_PROJECT_DIR") and not os.environ.get("CLAUDE_PROJECT_DIR"):
            return "gemini"

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
        # Try Gemini first (more specific)
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

    Safety: Works with both CLAUDE_PLUGIN_ROOT and Gemini's CLAUTORUN_PLUGIN_ROOT.
            Falls back to using __file__ location if env vars not set.
    """
    try:
        # Try CLAUTORUN_PLUGIN_ROOT first (set by Claude Code hooks when specified)
        plugin_root = os.environ.get("CLAUTORUN_PLUGIN_ROOT")
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
        "systemMessage": f"[clautorun] {message}" if message else "",
    }
    print(json.dumps(response))
    sys.exit(0)


# =============================================================================
# CLI Resolution (priority: venv > global > fallback)
# =============================================================================


def get_clautorun_bin() -> Path | None:
    """Find clautorun executable with priority: venv > global.

    Returns:
        Path to clautorun binary, or None if not found.

    Priority order:
        1. Plugin-local venv (isolated, preferred)
        2. Global installation (uv pip install / pip install)

    Safety: Works with both Claude Code and Gemini CLI via get_plugin_root().
    """
    plugin_root = get_plugin_root()

    if plugin_root:
        # Priority 1: Plugin-local venv
        venv_bin = Path(plugin_root) / ".venv" / "bin" / "clautorun"
        if venv_bin.exists():
            return venv_bin

    # Priority 2: Global installation
    global_bin = shutil.which("clautorun")
    if global_bin:
        return Path(global_bin)

    return None


def try_cli(bin_path: Path, stdin_data: str = "") -> bool:
    """Try to run clautorun CLI, passing pre-read stdin payload.

    Args:
        bin_path: Path to clautorun executable
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
          (non-zero exit code, argparse errors, empty stdout). This caused
          hook_entry.py to exit without printing any JSON, and Claude Code
          would fail-open (allow rm, git reset --hard, etc.).
        - Previously read stdin inside try_cli, consuming it so the fallback
          path (run_fallback → run_client) couldn't read the payload.
    """
    try:
        result = subprocess.run(
            [str(bin_path)],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=HOOK_TIMEOUT,
        )

        # Debug logging (ALWAYS enabled to diagnose hook issues)
        try:
            from pathlib import Path as DebugPath
            debug_log = DebugPath.home() / ".clautorun" / "hook_entry_debug.log"
            with open(debug_log, 'a') as f:
                f.write(f"CLI exit code: {result.returncode}\n")
                f.write(f"CLI stdout ({len(result.stdout)} bytes):\n{result.stdout}\n")
                f.write(f"CLI stderr ({len(result.stderr)} bytes):\n{result.stderr}\n")
                # Validate JSON
                try:
                    import json
                    json.loads(result.stdout)
                    f.write("✓ JSON valid\n")
                except json.JSONDecodeError as e:
                    f.write(f"✗ JSON INVALID: {e}\n")
        except Exception:
            pass  # Never fail hook due to debug logging

        # ═══════════════════════════════════════════════════════════════
        # TWO PATHWAYS: Primary (exit 0) and Workaround (exit 2)
        # ═══════════════════════════════════════════════════════════════
        # Exit 0: Normal (allow OR Gemini deny)
        # Exit 2: Claude Code Bug #4669 workaround (deny + stderr → AI)
        # Other: Error (stale install, import failure, etc.)
        # ═══════════════════════════════════════════════════════════════

        # Must check return code — only 0 and 2 are valid
        if result.returncode not in (0, 2):
            return False

        # Must have stdout output — hook response is JSON on stdout
        if not result.stdout:
            return False

        # Extract valid JSON from stdout (filters out noise like environment warnings)
        def extract_json(text: str) -> str | None:
            """Find valid JSON block efficiently."""
            if not text:
                return None
            
            cleaned = text.strip()
            # Fast path: whole output is valid JSON
            if cleaned.startswith('{') and cleaned.endswith('}'):
                try:
                    json.loads(cleaned)
                    return cleaned
                except json.JSONDecodeError:
                    pass
            
            # Fallback: search for last valid JSON line (filters leading/trailing noise)
            for line in reversed(cleaned.splitlines()):
                line = line.strip()
                if line.startswith('{') and line.endswith('}'):
                    try:
                        json.loads(line)
                        return line
                    except json.JSONDecodeError:
                        continue
            
            return None

        json_block = extract_json(result.stdout)
        if json_block:
            print(json_block, end="")
        else:
            # Fallback to raw output if no JSON found (likely an error message)
            print(result.stdout, end="")

        # Pass through stderr if present (Bug #4669: stderr → AI for exit 2)
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)

        # Pass through exit code to Claude Code (DRY: client.py decides)
        sys.exit(result.returncode)

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
    - --no-bootstrap flag in command (added to hooks.json via `clautorun --no-bootstrap`)
    - CLAUTORUN_NO_BOOTSTRAP=1 environment variable
    """
    # Check command line flag (added to hooks.json commands)
    if "--no-bootstrap" in sys.argv:
        return True

    # Check environment variable
    if os.environ.get("CLAUTORUN_NO_BOOTSTRAP", "0") == "1":
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

    # Check plugin root is set (either CLAUTORUN_PLUGIN_ROOT or CLAUDE_PLUGIN_ROOT)
    if not os.environ.get("CLAUTORUN_PLUGIN_ROOT") and not os.environ.get("CLAUDE_PLUGIN_ROOT"):
        return False, "Plugin root not set (need CLAUTORUN_PLUGIN_ROOT or CLAUDE_PLUGIN_ROOT)"

    return True, "uv" if has_uv else "pip"


def spawn_background_bootstrap() -> bool:
    """Spawn bootstrap process in background using nohup.

    This runs uv pip install + clautorun --install detached from the hook process.
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
        install_cmd = "uv pip install clautorun"
    else:
        install_cmd = "pip3 install --user clautorun"

    bootstrap_cmd = f"""
        {install_cmd} 2>/dev/null
        clautorun --install 2>/dev/null
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

    This is the fallback when no CLI is available. It attempts to import
    clautorun directly from the plugin source. If dependencies are missing,
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
        fail_open("Cannot locate plugin source")

    # Add to Python path for imports
    src_str = str(src_dir)
    if src_str not in sys.path:
        sys.path.insert(0, src_str)

    try:
        from clautorun.__main__ import main as clautorun_main

        exit_code = clautorun_main()
        sys.exit(exit_code if exit_code is not None else 0)
    except ImportError as e:
        # Deps missing - try background bootstrap
        if is_bootstrap_disabled():
            fail_open(
                f"Import error: {e}. Bootstrap disabled. "
                "Run manually: uv pip install clautorun && clautorun --install"
            )
        elif is_bootstrap_running():
            fail_open("clautorun bootstrapping in background, will be ready shortly")
        else:
            can_run, reason = can_bootstrap()
            if can_run:
                spawn_background_bootstrap()
                fail_open("clautorun bootstrapping in background, will be ready shortly")
            else:
                fail_open(
                    f"Import error: {e}. Cannot bootstrap: {reason}. "
                    "Run manually: uv pip install clautorun && clautorun --install"
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
        2. Try installed CLI (venv or global) - fast path
        3. If CLI fails, restore stdin and try fallback (direct import)
        4. If import fails, spawn background bootstrap via nohup
        5. Return fail_open so Claude can continue; next hook will work

    Bug history:
        stdin was previously read inside try_cli(), so on CLI failure the
        fallback path (run_fallback → run_client → json.load(sys.stdin))
        would get empty input and fail silently. Now stdin is read once
        here and passed explicitly to try_cli, then restored via StringIO
        for the fallback path.
    """
    import io
    from pathlib import Path

    # Read stdin once — it can only be consumed once
    stdin_data = ""
    try:
        if not sys.stdin.isatty():
            stdin_data = sys.stdin.read()
    except EOFError:
        pass

    # Debug logging (ALWAYS enabled to diagnose hook issues)
    def log_debug(msg: str):
        try:
            debug_log = Path.home() / ".clautorun" / "hook_entry_debug.log"
            debug_log.parent.mkdir(exist_ok=True)
            with open(debug_log, 'a') as f:
                import datetime
                f.write(f"[{datetime.datetime.now()}] {msg}\n")
        except Exception:
            pass

    log_debug("=" * 80)
    event_name = "unknown"
    if stdin_data:
        try:
            event_name = json.loads(stdin_data).get('hook_event_name', 'unknown')
        except json.JSONDecodeError:
            pass
    
    log_debug(f"Hook entry started (Event: {event_name})")
    log_debug(f"Hook entry stdin ({len(stdin_data)} bytes)")

    clautorun_bin = get_clautorun_bin()
    log_debug(f"Selected binary: {clautorun_bin}")
    cli_type = detect_cli_type()
    log_debug(f"Detected CLI: {cli_type} (from --cli arg: {'--cli' in sys.argv})")
    log_debug(f"Env GEMINI_SESSION_ID: {os.environ.get('GEMINI_SESSION_ID')}")
    log_debug(f"Env GEMINI_PROJECT_DIR: {os.environ.get('GEMINI_PROJECT_DIR')}")
    log_debug(f"Env CLAUDE_PROJECT_DIR: {os.environ.get('CLAUDE_PROJECT_DIR')}")

    # Inject cli_type into stdin payload so daemon gets explicit CLI identity.
    # This allows both CLIs to call the shared daemon simultaneously without
    # ambiguity — each hook file passes --cli gemini or --cli claude.
    if stdin_data and cli_type:
        try:
            payload = json.loads(stdin_data)
            payload["cli_type"] = cli_type
            stdin_data = json.dumps(payload)
            log_debug(f"Injected cli_type={cli_type} into payload")
        except (json.JSONDecodeError, Exception) as e:
            log_debug(f"Could not inject cli_type: {e}")

    if clautorun_bin:
        try:
            # OPTIMIZATION: Bypassing 'uv run' overhead by using venv python directly.
            # If clautorun_bin is in a .venv, we use its python to run the module.
            cmd = [str(clautorun_bin)]
            if ".venv/bin/clautorun" in str(clautorun_bin):
                venv_python = clautorun_bin.parent / "python"
                if venv_python.exists():
                    # Check if it's a script we can run as a module
                    cmd = [str(venv_python), "-m", "clautorun"]
                    log_debug(f"Using optimized venv path: {' '.join(cmd)}")

            # Inline try_cli logic to allow for better logging
            result = subprocess.run(
                cmd,
                input=stdin_data,
                capture_output=True,
                text=True,
                timeout=HOOK_TIMEOUT,
            )
            log_debug(f"CLI exit code: {result.returncode}")
            log_debug(f"CLI stdout ({len(result.stdout)} bytes):\n{result.stdout}")
            if result.stderr:
                log_debug(f"CLI stderr ({len(result.stderr)} bytes):\n{result.stderr}")

            if result.stdout:
                # Extract valid JSON from stdout (filters out noise like environment warnings)
                def extract_json(text: str) -> str | None:
                    """Find valid JSON block efficiently."""
                    if not text:
                        return None
                    
                    cleaned = text.strip()
                    # Fast path: whole output is valid JSON
                    if cleaned.startswith('{') and cleaned.endswith('}'):
                        try:
                            json.loads(cleaned)
                            return cleaned
                        except json.JSONDecodeError:
                            pass
                    
                    # Fallback: search for last valid JSON line (filters leading/trailing noise)
                    for line in reversed(cleaned.splitlines()):
                        line = line.strip()
                        if line.startswith('{') and line.endswith('}'):
                            try:
                                json.loads(line)
                                return line
                            except json.JSONDecodeError:
                                continue
                    
                    return None

                json_block = extract_json(result.stdout)
                if json_block:
                    log_debug("✓ JSON valid")
                    # CRITICAL: Print ONLY the JSON block. Do not print result.stdout
                    # which might contain leading/trailing noise or multiple JSONs.
                    print(json_block, end="")
                else:
                    log_debug("✗ JSON NOT found or invalid")
                    # If no JSON found, something is wrong. Printing raw stdout 
                    # is risky but may contain the error message.
                    print(result.stdout, end="")

                # Pass through stderr if present (Bug #4669: stderr → AI for exit 2)
                if result.stderr:
                    print(result.stderr, end="", file=sys.stderr)

                log_debug(f"Hook entry finished with exit code {result.returncode}")
                sys.exit(result.returncode)
            else:
                log_debug("✗ CLI returned empty stdout")
        except subprocess.TimeoutExpired:
            log_debug("✗ CLI timed out")
        except Exception as e:
            log_debug(f"✗ CLI exception: {e}")

    # Restore stdin for fallback path (run_client reads from sys.stdin)
    sys.stdin = io.StringIO(stdin_data)

    # No CLI available or CLI failed - try fallback
    log_debug("Starting fallback (direct import)...")
    run_fallback()
    log_debug("Hook entry finished (fallback path)")


if __name__ == "__main__":
    main()
