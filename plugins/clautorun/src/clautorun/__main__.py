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
        description="""Clautorun - Claude Code plugin for autonomous task execution and lifecycle management.

INSTALLATION (Two steps - see below for details):
  1. Install Python package:  pip install clautorun  (or: uv pip install clautorun)
  2. Register with CLI:       clautorun --install

QUICK START (after installation):
  1. Use /cr:go <task> in Claude Code to start autonomous execution
  2. Control file creation: clautorun file status (or /cr:st in Claude)
  3. Manage task history: clautorun task status

Features: Autonomous execution, file policies, safety guards, task lifecycle tracking.
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INSTALLATION GUIDE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Why two steps?
  Step 1: Install Python package    → Makes 'clautorun' CLI command available
  Step 2: Register with Claude CLI  → Adds /cr:* slash commands to Claude Code/Gemini

Note: For local development from a git clone, use the full module path in Step 2:
  uv run python -m plugins.clautorun.src.clautorun.install --install --force
This ensures the installer finds the source .claude-plugin/ directory.

Method 1: Via Claude Code plugin system (EASIEST - one command does both):
  claude plugin install https://github.com/ahundt/clautorun.git

Method 2: Via pip/uv (two steps):
  # Step 1: Install Python package
  pip install git+https://github.com/ahundt/clautorun.git
  # OR with UV (faster, recommended):
  uv pip install git+https://github.com/ahundt/clautorun.git

  # Step 2: Register with Claude Code/Gemini
  clautorun --install                    # Register all plugins
  clautorun --status                     # Verify installation

  # Optional: Install as UV tool for global availability
  uv tool install git+https://github.com/ahundt/clautorun.git

Method 3: From local clone (development):
  git clone https://github.com/ahundt/clautorun.git && cd clautorun

  # Step 1: Install in editable mode
  uv pip install -e .                    # UV (recommended)
  # OR:
  pip install -e .                       # Standard pip

  # Step 2: Register with Claude Code/Gemini (use full module path for local dev)
  uv run python -m plugins.clautorun.src.clautorun.install --install --force

  # Optional: Install as UV tool (adds --tool flag to registration)
  clautorun --install --force --tool     # Only after uv tool install

Install UV (if needed):
  # macOS/Linux:
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # Homebrew:
  brew install uv
  # Windows:
  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EXAMPLES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Installation:
  clautorun --install                    # Register all plugins with Claude/Gemini
  clautorun --install clautorun          # Register only clautorun plugin
  clautorun --status                     # Check installation status

AutoFile - control file creation (slash: /cr:a, /cr:j, /cr:f, /cr:st):
  clautorun file status                    # Show current file policy
  clautorun file allow                     # Allow all file creation (slash: /cr:a)
  clautorun file justify                   # Require justification (slash: /cr:j)
  clautorun file search                    # Only modify existing (slash: /cr:f)
  clautorun file allow --global            # Set global default for all sessions

Task lifecycle management:
  clautorun task status                  # Show current task status
  clautorun task status --verbose        # Show detailed task information
  clautorun task export tasks.json       # Export task history to JSON
  clautorun task gc --dry-run            # Preview old data cleanup (safe)
  clautorun task gc --no-confirm         # Clean up old task data

Common workflows:
  # First time setup - production (see INSTALLATION GUIDE above for full details)
  pip install clautorun                  # Step 1: Install Python package
  clautorun --install                    # Step 2: Register with Claude/Gemini

  # First time setup - local development from clone
  cd /path/to/clautorun && uv pip install -e .
  uv run python -m plugins.clautorun.src.clautorun.install --install --force

  # Check what's installed
  clautorun --status                     # See plugin status

  # Control file creation
  clautorun file status                    # See current policy
  clautorun file justify                   # Enable strict mode (equivalent to /cr:j)

  # View task progress
  clautorun task status --verbose        # See all incomplete tasks

  # Clean up old data
  clautorun task gc --dry-run            # Preview what will be deleted
  clautorun task gc                      # Confirm and clean up

For more information: https://github.com/ahundt/clautorun
        """,
    )

    # Install options
    install_group = parser.add_argument_group("Installation (Start Here!)")
    install_group.add_argument(
        "--install",
        "-i",
        nargs="?",
        const="all",
        metavar="PLUGINS",
        help="Install clautorun plugins to Claude Code and/or Gemini CLI. "
             "This registers the plugins, installs hooks, and makes slash commands available. "
             "Default: all plugins (clautorun + pdf-extractor). "
             "Specify plugins: --install clautorun or --install clautorun,pdf-extractor",
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
    # Status/info options
    info_group = parser.add_argument_group("Information")
    info_group.add_argument(
        "--status",
        "-s",
        action="store_true",
        help="Show installation status: which plugins are installed, where they're located, "
             "and which CLIs (Claude Code, Gemini) have them enabled",
    )
    info_group.add_argument(
        "--version",
        "-V",
        action="store_true",
        help="Show version and exit",
    )
    info_group.add_argument(
        "--restart-daemon",
        action="store_true",
        help="Restart the clautorun daemon (stops, cleans up, and starts fresh)",
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

    # Subcommands (modern CLI structure)
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # AutoFile (af) subcommand - file creation control
    file_parser = subparsers.add_parser(
        "file",
        help="AutoFile - control file creation policy",
        description="Control file creation and modification policies (AutoFile system). "
                    "Equivalent to /cr:a (allow), /cr:j (justify), /cr:f (find), /cr:st (status) slash commands.",
    )
    file_subparsers = file_parser.add_subparsers(dest="file_command", help="AutoFile operations")

    # file allow
    allow_parser = file_subparsers.add_parser(
        "allow",
        aliases=["a"],
        help="Allow creating new files freely (CLI: file a, Slash: /cr:a)",
        description="""Set AutoFile policy to 'allow-all' mode.

Claude can create new files and modify existing files without any restrictions.
This is the most permissive mode - good for new projects or exploratory work.

Examples:
  clautorun file allow              # Set for current session
  clautorun file a --global         # Set as default for all sessions

Equivalent slash commands: /cr:a, /cr:allow, /afa""",
    )
    allow_parser.add_argument(
        "--global",
        "-g",
        action="store_true",
        dest="file_global",
        help="Set globally (all sessions). Default: current session only",
    )

    # file justify
    justify_parser = file_subparsers.add_parser(
        "justify",
        aliases=["j"],
        help="Require written justification to create new files (CLI: file j, Slash: /cr:j)",
        description="""Set AutoFile policy to 'justify-create' mode.

Claude must search for existing files first. If creating a new file, Claude must
include <AUTOFILE_JUSTIFICATION>reason</AUTOFILE_JUSTIFICATION> explaining why.
This encourages modifying existing code rather than duplicating functionality.

Good for established projects where you want to minimize unnecessary new files.

Examples:
  clautorun file justify            # Set for current session
  clautorun file j --global         # Set as default for all sessions

Equivalent slash commands: /cr:j, /cr:justify, /afj""",
    )
    justify_parser.add_argument(
        "--global",
        "-g",
        action="store_true",
        dest="file_global",
        help="Set globally (all sessions). Default: current session only",
    )

    # file search (find) - strict mode
    search_parser = file_subparsers.add_parser(
        "search",
        aliases=["find", "f"],
        help="Block all new file creation - only modify existing (CLI: file f, Slash: /cr:f)",
        description="""Set AutoFile policy to 'strict-search' mode (strictest).

Claude CANNOT create any new files. Can only modify existing files.
Claude must use Glob/Grep to find existing files before making changes.

This is the most restrictive mode - good when you want to prevent any
accidental new file creation in a mature codebase.

Examples:
  clautorun file search             # Set for current session
  clautorun file f --global         # Set as default for all sessions (short version)

Equivalent slash commands: /cr:f, /cr:find, /afs
Aliases: file search, file find, file f (all equivalent)""",
    )
    search_parser.add_argument(
        "--global",
        "-g",
        action="store_true",
        dest="file_global",
        help="Set globally (all sessions). Default: current session only",
    )

    # file status
    af_status_parser = file_subparsers.add_parser(
        "status",
        aliases=["st", "s"],
        help="Show current file creation policy (CLI: file st, Slash: /cr:st)",
        description="""Display current AutoFile policy setting.

Shows whether Claude can create new files freely (allow), must justify (justify),
or is blocked from creating new files (search/strict).

By default shows policy for current session. Use --global to see the default
policy that applies to all new sessions.

Examples:
  clautorun file status             # Show current session policy
  clautorun file st --global        # Show global default policy (short version)

Equivalent slash commands: /cr:st, /cr:status, /afst
Aliases: file status, file st, file s (all equivalent)""",
    )
    af_status_parser.add_argument(
        "--global",
        "-g",
        action="store_true",
        dest="file_global",
        help="Show global policy. Default: current session policy",
    )

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

    # Restart daemon mode
    if args.restart_daemon:
        from clautorun.restart_daemon import restart_daemon

        return restart_daemon()

    # Uninstall mode
    if args.uninstall:
        from clautorun.install import uninstall_plugins

        return uninstall_plugins()

    # Update mode
    if args.update:
        from clautorun.install import perform_self_update

        result = perform_self_update(method=args.update_method)
        print(result.output)
        return 0 if result.ok else 1

    # AutoFile (af) subcommand - file creation control
    if args.command == "file":
        from clautorun.session_manager import get_session_manager
        from clautorun.config import CONFIG

        if not hasattr(args, 'file_command') or args.file_command is None:
            # No subcommand specified - show help
            file_parser = create_parser().add_subparsers().choices['file']
            file_parser.print_help()
            return 1

        session_id = os.environ.get("CLAUDE_SESSION_ID")
        is_global = getattr(args, 'file_global', False)

        # Get session manager
        mgr = get_session_manager()

        # Normalize aliases to canonical names
        file_cmd = args.file_command
        alias_map = {
            "a": "allow",
            "j": "justify",
            "f": "search",
            "find": "search",
            "st": "status",
            "s": "status"
        }
        file_cmd = alias_map.get(file_cmd, file_cmd)

        # file status
        if file_cmd == "status":
            if is_global:
                # Show global default policy
                with mgr.session_state("__autofile_policy__global") as state:
                    global_policy = state.get("policy", "allow-all")
                policy_desc = {
                    "allow-all": "ALLOW ALL: Full permission to create/modify files",
                    "justify-create": "JUSTIFIED: Search existing first. Require justification for new files",
                    "strict-search": "STRICT SEARCH: ONLY modify existing files. NO new files"
                }.get(global_policy, f"Unknown policy: {global_policy}")
                print(f"Global AutoFile policy: {global_policy}")
                print(f"{policy_desc}")
                print()
                print("This is the default for new sessions.")
                print("Override per-session with: clautorun file <allow|justify|search>")
            else:
                # Show session-specific policy
                if not session_id:
                    print("Error: No CLAUDE_SESSION_ID set. Cannot show session policy.")
                    print("Use --global to show global default policy.")
                    return 1

                with mgr.session_state(f"__autofile_policy__{session_id}") as state:
                    session_policy = state.get("policy", None)

                if session_policy:
                    policy_desc = {
                        "allow-all": "ALLOW ALL: Full permission to create/modify files",
                        "justify-create": "JUSTIFIED: Search existing first. Require justification for new files",
                        "strict-search": "STRICT SEARCH: ONLY modify existing files. NO new files"
                    }.get(session_policy, f"Unknown policy: {session_policy}")
                    print(f"Session AutoFile policy: {session_policy}")
                    print(f"{policy_desc}")
                    print()
                    print(f"Session: {session_id[:12]}...")
                    print("Slash command equivalent: /cr:st")
                else:
                    # No session override, show global default
                    with mgr.session_state("__autofile_policy__global") as gstate:
                        global_policy = gstate.get("policy", "allow-all")
                    print(f"AutoFile policy: {global_policy} (using global default)")
                    print()
                    print(f"Session: {session_id[:12]}...")
                    print("No session-specific override. Using global default.")

            return 0

        # Set policy (allow, justify, search) - file_cmd already normalized above
        policy_value = {
            "allow": "allow-all",
            "justify": "justify-create",
            "search": "strict-search"
        }.get(file_cmd)

        if not policy_value:
            print(f"Error: Unknown file command: {file_cmd}", file=sys.stderr)
            return 1

        if is_global:
            # Set global default
            with mgr.session_state("__autofile_policy__global") as state:
                state["policy"] = policy_value
            print(f"Global AutoFile policy set to: {policy_value}")
            print("This will be the default for all new sessions.")
            print(f"Slash command equivalent: /cr:{file_cmd[0]} (or /cr:{file_cmd})")
        else:
            # Set for current session
            if not session_id:
                print("Error: No CLAUDE_SESSION_ID set. Cannot set session policy.")
                print("Use --global to set global default policy instead.")
                return 1

            with mgr.session_state(f"__autofile_policy__{session_id}") as state:
                state["policy"] = policy_value

            print(f"Session AutoFile policy set to: {policy_value}")
            print(f"Session: {session_id[:12]}...")
            print(f"Slash command equivalent: /cr:{file_cmd[0]} (or /cr:{file_cmd})")

        return 0

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
