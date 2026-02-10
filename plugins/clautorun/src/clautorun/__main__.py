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
4. Task lifecycle management (task subcommand): Manual task history management

Usage:
    # Installation
    clautorun --install                    # Install all plugins
    clautorun --status                     # Show installation status

    # Task lifecycle management (modern subcommand structure)
    clautorun task status                  # Show task status
    clautorun task status --verbose        # Detailed task info
    clautorun task export tasks.json       # Export to JSON
    clautorun task clear --session abc123  # Clear specific session
    clautorun task gc --dry-run            # Preview garbage collection
    clautorun task gc --no-confirm         # Run GC without confirmation

    # Hook handler (default)
    clautorun                              # Run as hook handler

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
  # Installation
  clautorun --install                    # Install all plugins
  clautorun --install clautorun          # Install specific plugin
  clautorun --status                     # Show installation status

  # Task lifecycle management
  clautorun task status                  # Show task status
  clautorun task status --verbose        # Show detailed task info
  clautorun task export file.json        # Export tasks to JSON
  clautorun task clear --session abc123  # Clear specific session
  clautorun task gc --dry-run            # Preview garbage collection
  clautorun task gc --no-confirm         # Run GC without confirmation

  # Hook handler (default)
  clautorun                              # Run as hook handler

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

    # Task lifecycle subcommands (modern CLI structure)
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Task subcommand
    task_parser = subparsers.add_parser(
        "task",
        help="Task lifecycle management",
        description="Manual task lifecycle history management",
    )
    task_subparsers = task_parser.add_subparsers(dest="task_command", help="Task operations")

    # task status
    status_parser = task_subparsers.add_parser(
        "status",
        help="Show task status for session",
        description="Display task status and progress for current or specified session",
    )
    status_parser.add_argument(
        "--session",
        metavar="SESSION_ID",
        help="Session ID (default: $CLAUDE_SESSION_ID)",
    )
    status_parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show detailed task information",
    )
    status_parser.add_argument(
        "--format",
        "-f",
        choices=["text", "json", "table"],
        default="text",
        help="Output format (default: text)",
    )

    # task export
    export_parser = task_subparsers.add_parser(
        "export",
        help="Export task data to file",
        description="Export task data to JSON, CSV, or Markdown file",
    )
    export_parser.add_argument(
        "output",
        metavar="FILE",
        help="Output file path",
    )
    export_parser.add_argument(
        "--session",
        metavar="SESSION_ID",
        help="Session ID (default: $CLAUDE_SESSION_ID)",
    )
    export_parser.add_argument(
        "--format",
        "-f",
        choices=["json", "csv", "markdown"],
        default="json",
        help="Export format (default: json)",
    )
    export_parser.add_argument(
        "--include-completed",
        "-c",
        action="store_true",
        help="Include completed and deleted tasks",
    )

    # task clear
    clear_parser = task_subparsers.add_parser(
        "clear",
        help="Clear task data",
        description="Clear task data for session(s) - DESTRUCTIVE OPERATION",
    )
    clear_parser.add_argument(
        "--session",
        metavar="SESSION_ID",
        help="Session ID to clear (default: $CLAUDE_SESSION_ID)",
    )
    clear_parser.add_argument(
        "--all",
        "-a",
        action="store_true",
        help="Clear ALL sessions (ignores --session)",
    )
    clear_parser.add_argument(
        "--no-confirm",
        action="store_true",
        help="Skip confirmation prompt (use with caution)",
    )

    # task gc
    gc_parser = task_subparsers.add_parser(
        "gc",
        help="Garbage-collect old task data",
        description="Garbage-collect stale task data (archive-then-purge) - DESTRUCTIVE",
    )
    gc_parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="Preview without making changes (RECOMMENDED first)",
    )
    gc_parser.add_argument(
        "--no-confirm",
        action="store_true",
        help="Skip confirmation prompt (use with caution)",
    )
    gc_parser.add_argument(
        "--pattern",
        "-p",
        default="*",
        metavar="PATTERN",
        help="Session ID glob pattern (default: *)",
    )
    gc_parser.add_argument(
        "--ttl",
        "-t",
        type=int,
        metavar="DAYS",
        help="Only GC sessions older than DAYS (default: config.task_ttl_days)",
    )
    gc_parser.add_argument(
        "--no-archive",
        action="store_true",
        help="Skip archiving (DANGEROUS - permanent data loss)",
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

    # Task subcommand (modern CLI structure)
    if args.command == "task":
        from clautorun.task_lifecycle import TaskLifecycle

        if not hasattr(args, 'task_command') or args.task_command is None:
            # No subcommand specified - show help
            task_parser = create_parser().add_subparsers().choices['task']
            task_parser.print_help()
            return 1

        session_id = getattr(args, 'session', None) or os.environ.get("CLAUDE_SESSION_ID")

        # task status
        if args.task_command == "status":
            return TaskLifecycle.cli_status(
                session_id=session_id,
                verbose=args.verbose,
                format=args.format
            )

        # task export
        elif args.task_command == "export":
            if not session_id:
                print("Error: --session required when CLAUDE_SESSION_ID not set", file=sys.stderr)
                return 1
            return TaskLifecycle.cli_export(
                session_id=session_id,
                output_path=args.output,
                format=args.format,
                include_completed=args.include_completed
            )

        # task clear
        elif args.task_command == "clear":
            return TaskLifecycle.cli_clear(
                session_id=session_id,
                all_sessions=args.all,
                confirm=not args.no_confirm
            )

        # task gc
        elif args.task_command == "gc":
            return TaskLifecycle.cli_gc(
                archive=not args.no_archive,
                dry_run=args.dry_run,
                pattern=args.pattern,
                ttl_days=args.ttl,
                confirm=not args.no_confirm
            )

    # Default: run as hook handler
    return run_hook_handler()


if __name__ == "__main__":
    sys.exit(main())
