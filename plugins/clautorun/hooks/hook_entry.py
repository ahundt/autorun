#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Clautorun Hook Entry Point

Thin entry script that works from plugin cache without UV installation.
Sets up Python path and delegates to __main__.py.

This follows the same pattern as hookify and other Claude Code plugins.

Design:
- Fail-open: On any error, output valid JSON that allows Claude to continue
- Minimal: Only stdlib imports before path setup
- Robust: Handles missing CLAUDE_PLUGIN_ROOT gracefully
"""
import json
import os
import sys


def fail_open(error_msg: str = "") -> None:
    """Output valid JSON that allows Claude to continue on errors."""
    response = {
        "continue": True,
        "stopReason": "",
        "suppressOutput": False,
        "systemMessage": f"[clautorun] Hook entry error: {error_msg}" if error_msg else ""
    }
    print(json.dumps(response))
    sys.exit(0)


def main() -> None:
    """Set up path and run clautorun."""
    # Get plugin root from environment
    plugin_root = os.environ.get('CLAUDE_PLUGIN_ROOT', '')
    if not plugin_root:
        fail_open("CLAUDE_PLUGIN_ROOT environment variable not set")
        return

    # Add plugin's src directory to Python path
    src_dir = os.path.join(plugin_root, 'src')
    if not os.path.isdir(src_dir):
        fail_open(f"Source directory not found: {src_dir}")
        return

    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

    # Import and run main entry point
    try:
        from clautorun.__main__ import main as clautorun_main
        clautorun_main()
    except ImportError as e:
        fail_open(f"Failed to import clautorun: {e}")
    except Exception as e:
        fail_open(f"Unexpected error: {e}")


if __name__ == "__main__":
    main()
