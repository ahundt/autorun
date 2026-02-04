#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Clautorun Hook Entry Point

Entry point for Claude Code hooks that works with or without UV installation.

Execution Priority:
1. Fast path: Use installed CLI if available (UV/pip installed)
2. Fallback: Run from plugin cache via CLAUDE_PLUGIN_ROOT

Design Principles:
- Fail-open: Never crash Claude - always output valid JSON
- Fast: Try CLI first (no Python import overhead if available)
- Robust: Handle missing env vars, missing files, import errors
- Minimal: Only stdlib imports (json, os, shutil, subprocess, sys)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import NoReturn

# =============================================================================
# Constants
# =============================================================================

HOOK_TIMEOUT = 9  # Leave 1s buffer for Claude's 10s hook timeout

# =============================================================================
# Fail-Open Response
# =============================================================================


def fail_open(error_msg: str = "") -> NoReturn:
    """
    Output valid JSON that allows Claude to continue, then exit.

    Claude hooks MUST return valid JSON. On any error, we return a
    permissive response so Claude can continue operating.
    """
    response = {
        "continue": True,
        "stopReason": "",
        "suppressOutput": False,
        "systemMessage": f"[clautorun] {error_msg}" if error_msg else ""
    }
    print(json.dumps(response))
    sys.exit(0)


# =============================================================================
# CLI Execution (Fast Path)
# =============================================================================


def try_cli() -> bool:
    """
    Try to run clautorun CLI if available in PATH.

    Returns True if CLI executed successfully, False otherwise.
    This is the fast path - avoids Python import overhead.
    """
    clautorun_path = shutil.which('clautorun')
    if not clautorun_path:
        return False

    try:
        # Read stdin (hook payload) and pass to CLI
        stdin_data = ""
        if not sys.stdin.isatty():
            stdin_data = sys.stdin.read()

        result = subprocess.run(
            [clautorun_path],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=HOOK_TIMEOUT
        )

        # Output CLI response
        if result.stdout:
            print(result.stdout, end='')

        return True

    except subprocess.TimeoutExpired:
        return False  # Fall back to plugin root
    except (subprocess.SubprocessError, OSError):
        return False  # Fall back to plugin root


# =============================================================================
# Plugin Root Execution (Fallback)
# =============================================================================


def get_relative_src_dir() -> str | None:
    """
    Get src directory relative to this script's location.

    Returns path to ../src from hook_entry.py location, or None if not found.
    This allows running without CLAUDE_PLUGIN_ROOT if the script is in
    the expected location (hooks/hook_entry.py -> ../src).
    """
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        # hooks/hook_entry.py -> hooks/ -> plugin_root/ -> plugin_root/src
        plugin_root = os.path.dirname(script_dir)
        src_dir = os.path.join(plugin_root, 'src')
        if os.path.isdir(src_dir):
            return src_dir
    except Exception:
        pass
    return None


def run_from_plugin_root() -> None:
    """
    Run clautorun from plugin cache directory.

    Uses CLAUDE_PLUGIN_ROOT environment variable set by Claude Code
    to find the plugin source and add it to Python path.
    Falls back to relative path from script location.
    """
    # Get plugin root from environment
    plugin_root = os.environ.get('CLAUDE_PLUGIN_ROOT')

    if plugin_root:
        src_dir = os.path.join(plugin_root, 'src')
    else:
        # Fallback: try relative path from script location
        src_dir = get_relative_src_dir()

    if not src_dir or not os.path.isdir(src_dir):
        fail_open("Cannot locate plugin source (no CLAUDE_PLUGIN_ROOT, relative path failed)")

    # Add to Python path for imports
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

    # Import and run main entry point
    try:
        from clautorun.__main__ import main as clautorun_main
        clautorun_main()
    except ImportError as e:
        fail_open(f"Import error: {e}")
    except Exception as e:
        fail_open(f"Runtime error: {e}")


# =============================================================================
# Entry Point
# =============================================================================


def main() -> None:
    """
    Hook entry point.

    1. Try CLI first (fastest if UV installed)
    2. Fall back to plugin root (works without installation)
    """
    if try_cli():
        return

    run_from_plugin_root()


if __name__ == "__main__":
    main()
