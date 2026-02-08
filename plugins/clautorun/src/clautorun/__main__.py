#!/usr/bin/env python3

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
"""Clautorun CLI - unified entry point for hooks and installation.

This module provides:
1. Hook handler mode (default): Process Claude Code hooks efficiently
2. Install mode (--install): Install and enable Claude Code plugins
3. Status mode (--status): Show installation status

Usage:
    clautorun                              # Run as hook handler (default)
    clautorun --install                    # Install all plugins
    clautorun --install clautorun          # Install specific plugin
    clautorun --install --force-install    # Force reinstall (dev workflow)
    clautorun --status                     # Show installation status

v0.7: Daemon mode is now default (85-90% complete architecture)
Set CLAUTORUN_USE_DAEMON=0 to revert to legacy main.py if needed
Benefits: 10-30x faster (1-5ms vs 50-150ms), 78% code reduction via DRY
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Sequence


# v0.7: Daemon mode is now default (85-90% complete architecture)
# Set CLAUTORUN_USE_DAEMON=0 to revert to legacy main.py if needed
# Benefits: 10-30x faster (1-5ms vs 50-150ms), 78% code reduction via DRY
USE_DAEMON = os.environ.get("CLAUTORUN_USE_DAEMON", "1") != "0"


def create_parser() -> argparse.ArgumentParser:
    """Create argument parser with all CLI options."""
    parser = argparse.ArgumentParser(
        prog="clautorun",
        description="Claude Code plugin for autonomous task automation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  clautorun                              # Run as hook handler (default)
  clautorun --install                    # Install all plugins
  clautorun --install clautorun          # Install specific plugin
  clautorun --install clautorun,pdf-extractor  # Install multiple
  clautorun --install --force-install    # Force reinstall (dev workflow)
  clautorun --status                     # Show installation status

Legacy commands (still supported):
  clautorun install                      # Install clautorun hooks
  clautorun uninstall                    # Uninstall clautorun hooks
  clautorun check                        # Check installation status
        """,
    )

    # Install options
    install_group = parser.add_argument_group("Installation")
    install_group.add_argument(
        "--install",
        "-i",
        nargs="?",
        const="all",
        metavar="PLUGINS",
        help="Install plugins (default: all, or comma-separated: clautorun,pdf-extractor)",
    )
    install_group.add_argument(
        "--force-install",
        "-f",
        action="store_true",
        help="Force reinstall even if same version (for development)",
    )
    install_group.add_argument(
        "--tool",
        "-t",
        action="store_true",
        help="Also run 'uv tool install' for global CLI availability",
    )
    install_group.add_argument(
        "--no-bootstrap",
        action="store_true",
        help="Disable automatic bootstrap (adds --no-bootstrap to hooks.json commands)",
    )
    install_group.add_argument(
        "--enable-bootstrap",
        action="store_true",
        help="Re-enable automatic bootstrap (removes --no-bootstrap from hooks.json commands)",
    )
    install_group.add_argument(
        "--uninstall",
        "-u",
        action="store_true",
        help="Uninstall plugins and UV tools",
    )
    install_group.add_argument(
        "--sync",
        action="store_true",
        help="Sync source to cache (dev workflow)",
    )

    # Status/info options
    info_group = parser.add_argument_group("Information")
    info_group.add_argument(
        "--status",
        "-s",
        action="store_true",
        help="Show installation status of all plugins",
    )
    info_group.add_argument(
        "--version",
        "-V",
        action="store_true",
        help="Show version and exit",
    )

    return parser


def set_bootstrap_config(enabled: bool) -> int:
    """Enable or disable automatic bootstrap by modifying hooks.json.

    Args:
        enabled: True to enable bootstrap (remove --no-bootstrap),
                 False to disable (add --no-bootstrap)

    Returns:
        Exit code: 0 = success, 1 = failure
    """
    import re
    from pathlib import Path

    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    if not plugin_root:
        # Try to find hooks.json relative to this file
        plugin_root = str(Path(__file__).parent.parent)

    hooks_path = Path(plugin_root) / "hooks" / "hooks.json"
    if not hooks_path.exists():
        print(f"hooks.json not found at {hooks_path}")
        return 1

    # Read current hooks.json
    with open(hooks_path) as f:
        content = f.read()

    if enabled:
        # Remove --no-bootstrap flag from commands
        new_content = re.sub(r"(hook_entry\.py)\s+--no-bootstrap", r"\1", content)
        if new_content == content:
            print("Bootstrap already enabled")
        else:
            with open(hooks_path, "w") as f:
                f.write(new_content)
            print(f"Bootstrap enabled (removed --no-bootstrap from {hooks_path})")
    else:
        # Add --no-bootstrap flag to commands (if not already present)
        if "--no-bootstrap" in content:
            print("Bootstrap already disabled")
        else:
            new_content = re.sub(
                r'(hook_entry\.py)(["\\s])', r"\1 --no-bootstrap\2", content
            )
            with open(hooks_path, "w") as f:
                f.write(new_content)
            print(f"Bootstrap disabled (added --no-bootstrap to {hooks_path})")

    return 0


def run_hook_handler() -> int:
    """Run clautorun as a hook handler (default mode).

    Returns:
        Exit code: 0 = success
    """
    if USE_DAEMON:
        # New daemon mode - forwards to Unix socket daemon
        from .client import run_client

        run_client()
    else:
        # Legacy mode - direct hook handling
        from .main import main as app_main

        app_main()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Main entry point.

    Args:
        argv: Command line arguments (defaults to sys.argv[1:])

    Returns:
        Exit code: 0 = success, 1 = failure
    """
    parser = create_parser()
    args, remaining = parser.parse_known_args(argv)

    # Version check
    if args.version:
        from clautorun import __version__

        print(f"clautorun {__version__}")
        return 0

    # Bootstrap config
    if args.no_bootstrap:
        return set_bootstrap_config(enabled=False)
    if args.enable_bootstrap:
        return set_bootstrap_config(enabled=True)

    # Install mode (new unified installer)
    if args.install is not None:
        from clautorun.install import install_plugins

        return install_plugins(args.install, tool=args.tool, force=args.force_install)

    # Status mode
    if args.status:
        from clautorun.install import show_status

        return show_status()

    # Uninstall mode
    if args.uninstall:
        from clautorun.install import uninstall_plugins

        return uninstall_plugins()

    # Sync mode
    if args.sync:
        from clautorun.install import sync_to_cache

        return sync_to_cache()

    # Default: run as hook handler
    return run_hook_handler()


if __name__ == "__main__":
    sys.exit(main())
