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

HOOK_TIMEOUT = 9  # Leave 1s buffer for Claude's 10s hook timeout
BOOTSTRAP_LOCKFILE = "/tmp/clautorun_bootstrap.lock"
BOOTSTRAP_MSG = (
    "clautorun deps not installed. Run: uv pip install clautorun && clautorun --install"
)

# =============================================================================
# Fail-Open Response (never crash Claude)
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
    """
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")

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


def try_cli(bin_path: Path) -> bool:
    """Try to run clautorun CLI, passing stdin payload.

    Args:
        bin_path: Path to clautorun executable

    Returns:
        True if CLI executed successfully, False otherwise.
    """
    try:
        # Read stdin (hook payload)
        stdin_data = "" if sys.stdin.isatty() else sys.stdin.read()

        result = subprocess.run(
            [str(bin_path)],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=HOOK_TIMEOUT,
        )

        # Output CLI response (stdout to Claude)
        if result.stdout:
            print(result.stdout, end="")

        return True

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

    # Check CLAUDE_PLUGIN_ROOT is set
    if not os.environ.get("CLAUDE_PLUGIN_ROOT"):
        return False, "CLAUDE_PLUGIN_ROOT not set"

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
    it spawns a background bootstrap process and returns fail_open so Claude
    can continue. The next hook invocation will find deps installed.
    """
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")

    # Try CLAUDE_PLUGIN_ROOT first, then relative path
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

        clautorun_main()
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
        1. Try installed CLI (venv or global) - fast path
        2. If no CLI, try fallback (direct import)
        3. If import fails, spawn background bootstrap via nohup
        4. Return fail_open so Claude can continue; next hook will work
    """
    clautorun_bin = get_clautorun_bin()

    if clautorun_bin and try_cli(clautorun_bin):
        return

    # No CLI available - try fallback (spawns background bootstrap on import error)
    run_fallback()


if __name__ == "__main__":
    main()
