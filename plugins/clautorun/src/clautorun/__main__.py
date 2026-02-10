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
        "--claude",
        action="store_true",
        help="Install for Claude Code only (default: install for both CLIs if available)",
    )
    install_group.add_argument(
        "--gemini",
        action="store_true",
        help="Install for Gemini CLI only (default: install for both CLIs if available)",
    )
    install_group.add_argument(
        "--conductor",
        action="store_true",
        default=True,
        help="Install Conductor extension for Gemini (default: True)",
    )
    install_group.add_argument(
        "--no-conductor",
        action="store_false",
        dest="conductor",
        help="Skip Conductor extension installation for Gemini",
    )
    # AIX auto-detection: Will use AIX for local installation if available
    # CRITICAL: Only does LOCAL installation, never publishes to public registry
    install_group.add_argument(
        "--aix",
        action="store_true",
        default=None,
        help="Force use of AIX for installation (auto-detects if not specified). "
             "Only performs LOCAL installation, never publishes publicly.",
    )
    install_group.add_argument(
        "--no-aix",
        action="store_false",
        dest="aix",
        help="Skip AIX even if installed (use direct installation instead)",
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

    # Update group
    update_group = parser.add_argument_group("Update")
    update_group.add_argument(
        "--update",
        action="store_true",
        help="Check for and install clautorun updates",
    )
    update_group.add_argument(
        "--update-method",
        choices=["auto", "plugin", "uv", "pip", "aix"],
        default="auto",
        help="Force specific update method (default: auto-detect)",
    )

    # Task lifecycle management group
    tasks_group = parser.add_argument_group("Task Lifecycle (Manual History Management)")
    tasks_group.add_argument(
        "--task-status",
        action="store_true",
        help="Show task status for current or specified session",
    )
    tasks_group.add_argument(
        "--task-export",
        metavar="FILE",
        help="Export tasks to JSON/CSV/markdown file",
    )
    tasks_group.add_argument(
        "--task-export-format",
        choices=["json", "csv", "markdown"],
        default="json",
        help="Export format (default: json)",
    )
    tasks_group.add_argument(
        "--task-clear",
        action="store_true",
        help="Clear tasks for current or specified session (requires --confirm)",
    )
    tasks_group.add_argument(
        "--task-clear-all",
        action="store_true",
        help="Clear ALL session tasks (requires --confirm)",
    )
    tasks_group.add_argument(
        "--task-gc",
        action="store_true",
        help="Garbage-collect old session task data (archive-then-purge)",
    )
    tasks_group.add_argument(
        "--task-gc-pattern",
        default="*",
        help="Session ID pattern for GC (default: * = all)",
    )
    tasks_group.add_argument(
        "--task-gc-ttl",
        type=int,
        metavar="DAYS",
        help="Only GC sessions older than DAYS (default: config.task_ttl_days)",
    )
    tasks_group.add_argument(
        "--task-gc-no-archive",
        action="store_true",
        help="Skip archiving before deletion (DANGEROUS - permanent data loss)",
    )
    tasks_group.add_argument(
        "--task-session",
        metavar="SESSION_ID",
        help="Session ID for task operations (default: $CLAUDE_SESSION_ID)",
    )
    tasks_group.add_argument(
        "--task-verbose",
        action="store_true",
        help="Show detailed task information",
    )
    tasks_group.add_argument(
        "--task-dry-run",
        action="store_true",
        help="Preview task operations without making changes",
    )
    tasks_group.add_argument(
        "--task-no-confirm",
        action="store_true",
        help="Skip confirmation prompts (use with caution)",
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

        return install_plugins(
            args.install,
            tool=args.tool,
            force=args.force_install,
            claude_only=args.claude,
            gemini_only=args.gemini,
            conductor=args.conductor,
            use_aix=args.aix if hasattr(args, 'aix') else None,  # NEW: Auto-detect if None
        )

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

    # Update mode
    if args.update:
        from clautorun.install import perform_self_update

        result = perform_self_update(method=args.update_method)
        print(result.output)
        return 0 if result.ok else 1

    # Task lifecycle operations
    if args.task_status or args.task_export or args.task_clear or args.task_clear_all or args.task_gc:
        from clautorun.task_lifecycle import TaskLifecycle

        session_id = args.task_session or os.environ.get("CLAUDE_SESSION_ID")

        # Task status
        if args.task_status:
            return TaskLifecycle.cli_status(
                session_id=session_id,
                verbose=args.task_verbose,
                format='text'
            )

        # Task export
        if args.task_export:
            if not session_id:
                print("Error: --task-session required when CLAUDE_SESSION_ID not set", file=sys.stderr)
                return 1
            return TaskLifecycle.cli_export(
                session_id=session_id,
                output_path=args.task_export,
                format=args.task_export_format,
                include_completed=True
            )

        # Task clear
        if args.task_clear or args.task_clear_all:
            return TaskLifecycle.cli_clear(
                session_id=session_id,
                all_sessions=args.task_clear_all,
                confirm=not args.task_no_confirm
            )

        # Task GC (garbage collect)
        if args.task_gc:
            return TaskLifecycle.cli_gc(
                archive=not args.task_gc_no_archive,
                dry_run=args.task_dry_run,
                pattern=args.task_gc_pattern,
                ttl_days=args.task_gc_ttl,
                confirm=not args.task_no_confirm
            )

    # Default: run as hook handler
    return run_hook_handler()


if __name__ == "__main__":
    sys.exit(main())
