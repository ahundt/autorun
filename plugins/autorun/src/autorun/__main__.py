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
"""Autorun CLI - unified entry point for hooks and installation.

This module provides:
1. Hook handler mode (default): Process Claude Code hooks efficiently
2. Install mode (--install): Install and enable Claude Code plugins
3. Status mode (--status): Show installation status
4. Task lifecycle management (task subcommand): Manual task history management

Usage:
    # Installation
    autorun --install                    # Install every embedded plugin
    autorun --status                     # Show installation status

    # Task lifecycle management (modern subcommand structure)
    autorun task status                  # Show task status
    autorun task status --verbose        # Detailed task info
    autorun task export tasks.json       # Export to JSON
    autorun task clear --session abc123  # Clear specific session
    autorun task gc --dry-run            # Preview garbage collection
    autorun task gc --no-confirm         # Run GC without confirmation

    # Hook handler (default)
    autorun                              # Run as hook handler

Daemon mode is the default. Set AUTORUN_USE_DAEMON=0 to use direct hook
execution when diagnosing daemon lifecycle behavior.
"""

# Python 2 / version guard — AI assistants frequently invoke `python` (Python 2 on many
# systems) instead of `python3`, wasting tokens trying to debug confusing import errors.
# This guard outputs a clear, actionable error message so the AI (and user) knows exactly
# how to fix the problem without further investigation.
# Note: Python 3 requires `from __future__ import annotations` to be the first executable
# statement, so the guard code must appear after it. Python 2 users invoking this file
# directly see a SyntaxError on the `from __future__` line; the hook system's
# error_handling.py handles that case.
from __future__ import annotations

import sys as _sys

if _sys.version_info < (3, 10):
    _sys.stderr.write(
        "ERROR: autorun requires Python 3.10+. You are running Python " + ".".join(str(v) for v in _sys.version_info[:2]) + ".\n"
        "Fix: Use `uv run python -m autorun` or `python3 -m autorun`.\n"
        "     Install uv: https://docs.astral.sh/uv/getting-started/installation/\n"
    )
    _sys.exit(1)
del _sys

import argparse  # noqa: E402
import contextlib  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
from autorun.logging_utils import use_utf8_output  # noqa: E402
from typing import Sequence  # noqa: E402


# Direct execution remains available for diagnostics and isolated tests.
USE_DAEMON = os.environ.get("AUTORUN_USE_DAEMON", "1") != "0"


def _hook_cli_choices() -> tuple[str, ...]:
    """Return hook-capable CLI names for --cli without duplicating platform data."""
    from .platforms import hook_platforms

    return tuple(platform.name for platform in hook_platforms())


def _agents_skills_choices() -> tuple[str, ...]:
    """Return shared-skills bridge choices without duplicating install data."""
    from .installer.settings import SHARED_SKILLS_BRIDGE

    return SHARED_SKILLS_BRIDGE.choices


def _skill_placement_choices() -> tuple[str, ...]:
    """Return skill-placement choices without duplicating install data."""
    from .installer.settings import SKILL_PLACEMENT

    return SKILL_PLACEMENT.choices


def _skill_placement_help() -> str:
    """Return shared parser help for --skill-placement."""
    from .installer.settings import SKILL_PLACEMENT

    return SKILL_PLACEMENT.rendered_help()


def _skill_placement_token():
    """Return the shared per-token validator for --skill-placement."""
    from .installer.settings import SKILL_PLACEMENT

    return SKILL_PLACEMENT.checked


def _update_method_choices() -> tuple[str, ...]:
    """Return self-update methods from the installer setting declaration."""
    from .installer.settings import UPDATE_METHOD

    return UPDATE_METHOD.choices


def _codex_github_plugin_identity() -> str:
    """The plugin identity published by the selected GitHub marketplace."""
    from .installer.codex import GITHUB_MARKETPLACE_NAME, PLUGIN_NAME

    return f"{PLUGIN_NAME}@{GITHUB_MARKETPLACE_NAME}"


def _codex_hook_source_choices() -> tuple[str, ...]:
    """Return Codex hook-source choices without duplicating install data."""
    from .installer.settings import CODEX_HOOK_SOURCE

    return CODEX_HOOK_SOURCE.choices


def _codex_plugin_marketplace_choices() -> tuple[str, ...]:
    """Return Codex marketplace choices without duplicating install data."""
    from .installer.settings import CODEX_PLUGIN_MARKETPLACE

    return CODEX_PLUGIN_MARKETPLACE.choices


def _custom_harness_spec_help() -> str:
    """Return shared parser help for custom harness specs."""
    from .platforms import custom_harness_spec_help

    return custom_harness_spec_help()


@contextlib.contextmanager
def _scoped_daemon_lease():
    """Own the current runtime's daemon lifecycle for state authority changes."""
    from filelock import FileLock

    from . import ipc

    with FileLock(str(ipc.AUTORUN_LOCK_PATH.with_suffix(".flock")), timeout=0):
        yield


def _run_state_command(args) -> int:
    """Migrate, report on, or undo the row-based state-store conversion.

    These are the maintenance operations an operator needs when
    ``state_backend`` is involved: inspect, convert, measure, and roll back.
    Refusal messages in session_manager point here by name, so they must stay
    in step.
    """
    import os

    from .session_manager import (
        RetentionPolicy,
        SessionStateError,
        SQLiteStore,
        StateMigrator,
        StateRetention,
        _state_dir_key,
    )
    from .config import CONFIG

    directory = _state_dir_key()
    migrator = StateMigrator(
        os.path.join(directory, "daemon_state.json"),
        os.path.join(directory, "daemon_state.sqlite3"),
        os.path.join(directory, "daemon_state.migration.json"),
    )

    if args.state_maintenance:
        database = os.path.join(directory, "daemon_state.sqlite3")
        if not os.path.exists(database):
            print(f"State maintenance did not run: no SQLite state database exists at {database}.")
            return 1
        try:
            store = SQLiteStore(database)
            store.initialize()
            report = StateRetention(store, RetentionPolicy()).maintenance()
        except SessionStateError as exc:
            print(f"State maintenance did not run: {exc}")
            return 1
        print(f"database bytes    : {report['database_bytes']}")
        print(f"wal bytes         : {report['wal_bytes']}")
        print(f"reclaimable bytes : {report['reclaimable_bytes']}")
        return 0

    if args.state_migrate or args.state_rollback:
        from filelock import Timeout as FileLockTimeout

        operation = "migration" if args.state_migrate else "rollback"
        option = "migrate" if args.state_migrate else "rollback"
        try:
            with _scoped_daemon_lease():
                result = migrator.migrate() if args.state_migrate else migrator.rollback()
        except FileLockTimeout:
            print(
                f"State {operation} did not run: the scoped autorun daemon is "
                "active. Stop that daemon/session first, then rerun "
                f"`autorun --state-{option}`. No state was changed."
            )
            return 1
        except SessionStateError as exc:
            print(f"State {operation} did not run: {exc}")
            return 1
        if args.state_migrate:
            print(f"Migrated {result['fields']} fields in {result['sessions']} sessions to {migrator.status()['database']}.")
        else:
            print(f"Wrote {result['fields']} fields back to {result['source']}. JSON authority is restored.")
        return 0

    if args.state_status:
        status = migrator.status()
        configured = CONFIG.get("state_backend", "json")
        effective = "sqlite" if status["phase"] == "COMPLETE" else configured
        print(f"configured default : {configured}")
        print(f"effective backend  : {effective}")
        print(f"state directory    : {directory}")
        print(f"conversion phase   : {status['phase']}")
        print(f"json present       : {status['source_present']}")
        print(f"database present   : {status['database_present']}")
        if status["fields"]:
            print(f"converted          : {status['fields']} fields in {status['sessions']} sessions")
        if status["backup"]:
            print(f"pre-conversion copy: {status['backup']}")
        if status["phase"] == "COMPLETE" and status["source_present"]:
            print("\nAn unexpected legacy JSON file is also present. It is not authoritative and will not be merged or served.")
        return 0

    raise AssertionError("state maintenance dispatch reached no operation")


def create_parser() -> argparse.ArgumentParser:
    """Create argument parser with all CLI options."""
    from autorun.platforms import standalone_session_help

    parser = argparse.ArgumentParser(
        prog="autorun",
        description="""Autorun - task lifecycle, safety guards, and native integration for supported AI coding harnesses.

INSTALLATION (Two steps - see below for details):
  1. Install Python tool from the repository's plugins/autorun subdirectory
  2. Install native assets:   autorun --install

QUICK START (after installation):
  1. Use ar:go <task> (or the harness's native displayed spelling)
  2. Control file creation: autorun file status
  3. Manage task history: autorun task status

Features: Autonomous execution, file policies, safety guards, task lifecycle tracking.
""",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INSTALLATION GUIDE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Why two steps?
  Step 1: Install Python package → makes the autorun CLI available
  Step 2: Install native assets  → publishes hooks, commands, skills, guidance,
                                   plugins, and extensions for detected harnesses

Preview any install without writes:
  autorun --install --install-dry-run

Claude Code plugin installation:
  claude plugin marketplace add https://github.com/ahundt/autorun.git
  claude plugin install ar@autorun

Python package installation:
  uv tool install 'git+https://github.com/ahundt/autorun.git#subdirectory=plugins/autorun'
  autorun --install
  autorun --status

Local development:
  git clone https://github.com/ahundt/autorun.git && cd autorun
  uv sync --project plugins/autorun
  uv run --project plugins/autorun autorun --install --force

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EXAMPLES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Installation:
  autorun --install                         # embedded plugins, detected harnesses
  autorun --install ar --codex              # autorun only, Codex only
  autorun --install pdf-extractor           # source marketplace checkout only
  autorun --uninstall pdf-extractor         # source checkout; preserve autorun
  autorun --status                          # preview drift and run health checks

AutoFile - control file creation (slash: /ar:a, /ar:j, /ar:f, /ar:st):
  autorun file status
  autorun file allow
  autorun file justify
  autorun file search
  autorun file allow --global

Task lifecycle management:
  autorun task status --verbose
  autorun task export tasks.json
  autorun task gc --dry-run

For more information: https://github.com/ahundt/autorun
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
        help="Install plugins for detected supported harnesses. This publishes each "
        "harness's native hooks, commands, skills, guidance, plugins, or extensions. "
        "Default: every plugin embedded in this distribution (the standalone "
        "autorun wheel embeds ar; a source marketplace checkout also exposes "
        "pdf-extractor). Select with --install ar or a comma-separated list.",
    )
    install_group.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Force reinstall even if same version (for development)",
    )
    install_group.add_argument(
        "--install-dry-run",
        action="store_true",
        help=("Preview install targets without writing hooks, plugin state, dependencies, or restarting daemons"),
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
        "--exit2-mode",
        choices=["auto", "always", "never"],
        default=None,
        help="Bug #4669 workaround mode: 'auto' (detect CLI - default), 'always' (force exit-2), 'never' (disable). "
        "Controls whether deny decisions use exit code 2 + stderr (Claude Code) or JSON decision field (Gemini CLI). "
        "Can also be set via AUTORUN_EXIT2_WORKAROUND environment variable.",
    )
    install_group.add_argument(
        "--claude",
        action="store_true",
        help="Install for Claude Code only (default: install maintained available harnesses)",
    )
    install_group.add_argument(
        "--gemini",
        action="store_true",
        help="Explicitly install for the legacy Gemini CLI (not selected by default)",
    )
    install_group.add_argument(
        "--antigravity",
        action="store_true",
        help=("Install for Google Antigravity CLI only (native agy plugin bundle, Gemini importer fallback)"),
    )
    install_group.add_argument(
        "--qwen",
        action="store_true",
        help="Install for Qwen Code only (Gemini-compatible extension surface)",
    )
    install_group.add_argument(
        "--custom-harness",
        action="append",
        default=[],
        metavar="SPEC",
        help=_custom_harness_spec_help(),
    )
    install_group.add_argument(
        "--codex",
        action="store_true",
        help="Install for Codex CLI only (default: install for maintained available CLIs)",
    )
    install_group.add_argument(
        "--claude-agents-skills",
        choices=_agents_skills_choices(),
        # None so the env var can win; see --codex-hook-source below.
        default=None,
        help=(
            "Bridge shared ~/.agents skills into Claude Code's skills directory: "
            "link (symlink), copy, or none. Default: none. Skills a plugin "
            "already provides are skipped. "
            "AUTORUN_CLAUDE_AGENTS_SKILLS also sets this; the flag wins."
        ),
    )
    install_group.add_argument(
        "--skill-placement",
        action="append",
        type=_skill_placement_token(),
        # None, not [], so an absent flag stays distinguishable from an
        # explicit one and AUTORUN_SKILL_PLACEMENT can still win.
        default=None,
        metavar="MODE|HARNESS=MODE",
        help=_skill_placement_help(),
    )
    install_group.add_argument(
        "--codex-hook-source",
        choices=_codex_hook_source_choices(),
        # None, not "user": argparse cannot otherwise distinguish an explicit
        # choice from its own default, and the default would then outrank
        # AUTORUN_CODEX_HOOK_SOURCE. resolve_choice_setting applies "user".
        default=None,
        help=(
            "Codex hook install source: user (~/.codex/hooks.json), plugin "
            "(ar@personal bundled hooks), both, or none. Default: user. "
            "AUTORUN_CODEX_HOOK_SOURCE also sets this; the flag wins."
        ),
    )
    install_group.add_argument(
        "--codex-plugin-marketplace",
        choices=_codex_plugin_marketplace_choices(),
        # None so the env var can win; see --codex-hook-source above.
        default=None,
        help=(
            "Codex plugin marketplace mode: personal installs ar@personal "
            "from a local personal marketplace; github adds ahundt/autorun "
            f"and installs {_codex_github_plugin_identity()}. Default: personal. "
            "AUTORUN_CODEX_PLUGIN_MARKETPLACE also sets this; the flag wins."
        ),
    )
    install_group.add_argument(
        "--conductor",
        action="store_true",
        default=None,
        help="Install Conductor extension for Gemini (default: True)",
    )
    install_group.add_argument(
        "--no-conductor",
        action="store_false",
        dest="conductor",
        help="Skip Conductor extension installation for Gemini",
    )
    install_group.add_argument(
        "--uninstall",
        "-u",
        nargs="?",
        const="all",
        default=None,
        metavar="PLUGINS",
        help="Uninstall all plugins and UV tools, or a comma-separated selection",
    )
    # Status/info options
    # Hook integration group (used by hook_entry.py, valid on every code path)
    hook_group = parser.add_argument_group("Hook Integration")
    hook_group.add_argument(
        "--cli",
        choices=_hook_cli_choices(),
        default=None,
        help="Hook-capable CLI type calling this invocation. Choices come from "
        "autorun.platforms hook registry. "
        "Passed by hook_entry.py so every pathway receives CLI identity. "
        "When present, also sets AUTORUN_CLI_TYPE env var for downstream use.",
    )

    info_group = parser.add_argument_group("Information")
    info_group.add_argument(
        "--status",
        "-s",
        action="store_true",
        help="Preview install drift and run health checks for detected supported harnesses",
    )
    info_group.add_argument(
        "--version",
        "-V",
        action="store_true",
        help="Show version and exit",
    )
    info_group.add_argument(
        "--state-status",
        action="store_true",
        help="Report which state backend is in use and any conversion in progress",
    )
    info_group.add_argument(
        "--state-migrate",
        action="store_true",
        help="Convert legacy JSON state to SQLite while the scoped daemon is stopped",
    )
    info_group.add_argument(
        "--state-rollback",
        action="store_true",
        help="Export SQLite state back to daemon_state.json and restore JSON authority",
    )
    info_group.add_argument(
        "--state-maintenance",
        action="store_true",
        help="Report SQLite database, WAL, and reclaimable storage bytes",
    )
    info_group.add_argument(
        "--restart-daemon",
        action="store_true",
        help="Restart the autorun daemon for the current AUTORUN_HOME/source tree",
    )
    info_group.add_argument(
        "--restart-all-daemons",
        action="store_true",
        help="Risky maintenance mode: restart current daemon and stop all matching autorun daemons, which can interrupt active sessions in other installs",
    )
    info_group.add_argument(
        "--restart-daemon-after-install",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    info_group.add_argument(
        "--cache-snapshot",
        action="store_true",
        help="Read Claude Code statusline JSON from stdin and persist "
        "context_window/rate_limits snapshot for /ar:cache. Opt-in "
        "tap; users invoke by piping their statusline stdin through "
        "`autorun --cache-snapshot`. Always exits 0 (fail-open).",
    )
    info_group.add_argument(
        "--capability-snapshot",
        nargs="?",
        const="-",
        metavar="FILE",
        help="Write a read-only JSON inventory of registered platforms, commands, skills, and hook chains. Use '-' or omit FILE to print to stdout.",
    )

    # Update group
    update_group = parser.add_argument_group("Update")
    update_group.add_argument(
        "--update",
        action="store_true",
        help="Check for and install autorun updates",
    )
    update_group.add_argument(
        "--update-method",
        choices=_update_method_choices(),
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
        "Equivalent to /ar:a (allow), /ar:j (justify), /ar:f (find), /ar:st (status) slash commands.",
    )
    file_subparsers = file_parser.add_subparsers(dest="file_command", help="AutoFile operations")

    # file allow
    allow_parser = file_subparsers.add_parser(
        "allow",
        aliases=["a"],
        help="Allow creating new files freely (CLI: file a, Slash: /ar:a)",
        description="""Set AutoFile policy to 'allow-all' mode.

Claude can create new files and modify existing files without any restrictions.
This is the most permissive mode - good for new projects or exploratory work.

Examples:
  autorun file allow              # Set for current session
  autorun file a --global         # Set as default for all sessions

Equivalent slash commands: /ar:a, /ar:allow, /afa""",
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
        help="Require written justification to create new files (CLI: file j, Slash: /ar:j)",
        description="""Set AutoFile policy to 'justify-create' mode.

The active assistant must search for existing files first. If creating a new
file, it must include <AUTOFILE_JUSTIFICATION>reason</AUTOFILE_JUSTIFICATION>
explaining why.
This encourages modifying existing code rather than duplicating functionality.

Good for established projects where you want to minimize unnecessary new files.

Examples:
  autorun file justify            # Set for current session
  autorun file j --global         # Set as default for all sessions

Equivalent slash commands: /ar:j, /ar:justify, /afj""",
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
        help="Block all new file creation - only modify existing (CLI: file f, Slash: /ar:f)",
        description="""Set AutoFile policy to 'strict-search' mode (strictest).

The active assistant cannot create any new files. It can only modify existing files.
It must use platform-native search to find existing files before making changes.

This is the most restrictive mode - good when you want to prevent any
accidental new file creation in a mature codebase.

Examples:
  autorun file search             # Set for current session
  autorun file f --global         # Set as default for all sessions (short version)

Equivalent slash commands: /ar:f, /ar:find, /afs
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
        help="Show current file creation policy (CLI: file st, Slash: /ar:st)",
        description="""Display current AutoFile policy setting.

Shows whether Claude can create new files freely (allow), must justify (justify),
or is blocked from creating new files (search/strict).

By default shows policy for current session. Use --global to see the default
policy that applies to all new sessions.

Examples:
  autorun file status             # Show current session policy
  autorun file st --global        # Show global default policy (short version)

Equivalent slash commands: /ar:st, /ar:status, /afst
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
        help=standalone_session_help(),
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
        help=standalone_session_help(),
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
        help=standalone_session_help(),
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
        "--session",
        metavar="SESSION_ID",
        help=standalone_session_help(),
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

    from autorun.resources import get_hooks_dir

    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    hooks_path = (
        Path(plugin_root) / "hooks" / "hooks.json"
        if plugin_root
        else get_hooks_dir() / "hooks.json"
    )
    if not hooks_path.exists():
        print(f"hooks.json not found at {hooks_path}")
        return 1

    # Read current hooks.json (Claude events)
    with open(hooks_path, encoding="utf-8") as f:
        content = f.read()

    if enabled:
        # Remove --no-bootstrap flag from commands
        new_content = re.sub(r"(hook_entry\.py)\s+--no-bootstrap", r"\1", content)
        if new_content == content:
            print("Bootstrap already enabled")
        else:
            with open(hooks_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            print(f"Bootstrap enabled (removed --no-bootstrap from {hooks_path})")
    else:
        # Add --no-bootstrap flag to commands (if not already present)
        if "--no-bootstrap" in content:
            print("Bootstrap already disabled")
        else:
            new_content = re.sub(r'(hook_entry\.py)(["\\s])', r"\1 --no-bootstrap\2", content)
            with open(hooks_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            print(f"Bootstrap disabled (added --no-bootstrap to {hooks_path})")

    return 0


def run_direct(payload: dict | None = None) -> int:
    """Handle hook payload directly in-process without the daemon socket.

    Implements the same pipeline as the daemon (normalize → EventContext → dispatch)
    but runs synchronously in the current process.  Used when AUTORUN_USE_DAEMON=0
    so tests can exercise production plugins.py code while remaining isolated from
    any live daemon session state.

    Returns:
        int: Exit code (0 = success, 2 = deny with exit-2 workaround for Claude Code)
    """
    import json
    import sys as _sys

    from .core import (
        EventContext,
        ThreadSafeDB,
        normalize_hook_payload,
        resolve_session_identity,
    )
    from .client import output_hook_response, prepare_payload_for_daemon
    from .config import HOOK_DEADLINE_PAYLOAD_KEY

    # Import plugins to register all handlers on the shared `app` object
    from . import plugins as _plugins  # noqa: F401 (side-effect: registers handlers)
    from .core import app

    # Read payload from stdin (mirrors client.py:run_client)
    if payload is None:
        payload = {}
        try:
            if not _sys.stdin.isatty():
                payload = json.load(_sys.stdin)
        except Exception:
            pass

    payload, cli_type = prepare_payload_for_daemon(payload)
    normalized = normalize_hook_payload(payload)
    identity = resolve_session_identity(
        pid=payload["_pid"],
        process_started_at_units=payload.get("_pid_started_at_units"),
        fallback_id=normalized["session_id"],
        transcript_path=normalized.get("transcript_path"),
    )

    ctx = EventContext(
        session_id=identity.key,
        session_identity_authority=identity.authority,
        event=normalized["hook_event_name"],
        prompt=normalized["prompt"],
        tool_name=normalized["tool_name"],
        tool_input=normalized["tool_input"],
        tool_result=normalized["tool_result"],
        session_transcript=normalized["session_transcript"],
        # No daemon here: this process handles one hook and exits, so
        # advisory in-memory state would never survive to be read back.
        store=ThreadSafeDB(persist_volatile_state=True),
        cli_type=cli_type,
        # "cwd" is the harness-reported project directory; "_cwd" is the
        # daemon-client injection of the same value. Prefer either over this
        # process's cwd, which can point at a different project entirely and
        # would send plan_export.py's archive to the wrong notes/ directory.
        cwd=normalized.get("cwd") or os.getcwd(),
        deadline_monotonic=payload.get(HOOK_DEADLINE_PAYLOAD_KEY),
        permission_mode=normalized["permission_mode"],
        source=normalized["source"],
        agent_id=normalized["agent_id"],
        agent_type=normalized["agent_type"],
        transcript_path=normalized.get("transcript_path"),
        agent_transcript_path=normalized.get("agent_transcript_path"),
        stop_hook_active=normalized["stop_hook_active"],
        last_assistant_message=normalized["last_assistant_message"],
        background_tasks=normalized["background_tasks"],
        session_crons=normalized["session_crons"],
    )

    response = app.dispatch(ctx)
    return output_hook_response(response, event=normalized["hook_event_name"], cli_type=cli_type, source="direct")


def run_hook_handler() -> int:
    """Run autorun as a hook handler (default mode).

    Returns:
        Exit code: 0 = success
    """
    if USE_DAEMON:
        # Daemon mode: forwards payload to running daemon via Unix socket
        from .client import run_client

        return run_client()
    else:
        # Direct mode (AUTORUN_USE_DAEMON=0): run canonical plugins.py handlers
        # in-process without connecting to the daemon socket.
        # Used by tests to exercise production code while staying isolated from
        # any live daemon session state.
        return run_direct()


def main(argv: Sequence[str] | None = None) -> int:
    """Main entry point.

    Args:
        argv: Command line arguments (defaults to sys.argv[1:])

    Returns:
        Exit code: 0 = success, 1 = failure
    """
    use_utf8_output()

    parser = create_parser()
    args, remaining = parser.parse_known_args(argv)

    # Version check
    if args.version:
        from autorun import __version__

        print(f"autorun {__version__}")
        return 0

    # Propagate --cli to env so run_hook_handler() / run_client() can use it
    if getattr(args, "cli", None):
        os.environ["AUTORUN_CLI_TYPE"] = args.cli

    # Bug #4669 workaround configuration (set env var from CLI arg)
    if hasattr(args, "exit2_mode") and args.exit2_mode is not None:
        os.environ["AUTORUN_EXIT2_WORKAROUND"] = args.exit2_mode

    # Bootstrap config
    if args.no_bootstrap:
        return set_bootstrap_config(enabled=False)
    if args.enable_bootstrap:
        return set_bootstrap_config(enabled=True)

    # Install mode (new unified installer)
    if args.install is not None or args.install_dry_run:
        from autorun.install import install_plugins

        selection = args.install if args.install is not None else "all"

        install_kwargs = {
            "tool": args.tool or None,
            "force": args.force,
            "claude_only": args.claude,
            "gemini_only": args.gemini,
            "codex_only": args.codex,
            "antigravity_only": args.antigravity,
            "qwen_only": args.qwen,
            "conductor": args.conductor,
            "codex_hook_source": args.codex_hook_source,
            "codex_plugin_marketplace": args.codex_plugin_marketplace,
            "claude_agents_skills": args.claude_agents_skills,
            "skill_placement": args.skill_placement,
        }
        if args.install_dry_run:
            install_kwargs["dry_run"] = True
        if args.custom_harness:
            install_kwargs["custom_harnesses"] = args.custom_harness
        return install_plugins(selection, **install_kwargs)

    if args.status:
        from autorun.install import show_status

        return show_status(
            custom_harnesses=args.custom_harness,
            include_legacy_gemini=args.gemini,
        )

    # Restart daemon mode
    if args.state_status or args.state_migrate or args.state_rollback or args.state_maintenance:
        return _run_state_command(args)

    if (
        args.restart_daemon
        or args.restart_all_daemons
        or args.restart_daemon_after_install
    ):
        from autorun.restart_daemon import restart_daemon

        return restart_daemon(
            all_daemons=args.restart_all_daemons,
            replace_runtime_daemon=args.restart_daemon_after_install,
        )

    # Cache snapshot tap (opt-in; user's statusline pipes JSON here)
    if getattr(args, "cache_snapshot", False):
        from autorun.cache_guard import persist_statusline_snapshot

        return persist_statusline_snapshot(sys.stdin)

    # Capability snapshot (read-only diagnostic; does not install hooks or use daemon)
    if getattr(args, "capability_snapshot", None) is not None:
        from autorun.capability_snapshot import write_capability_snapshot

        write_capability_snapshot(args.capability_snapshot)
        return 0

    # Uninstall mode
    if args.uninstall is not None:
        from autorun.install import uninstall_plugins

        return uninstall_plugins(args.uninstall)

    # Update mode
    if args.update:
        from autorun.install import perform_self_update

        result = perform_self_update(method=args.update_method)
        print(result.describe())
        return 0 if result.ok else 1

    # AutoFile (af) subcommand - file creation control
    if args.command == "file":
        from autorun.session_manager import get_session_manager

        if not hasattr(args, "file_command") or args.file_command is None:
            # No subcommand specified - show help
            file_parser = create_parser().add_subparsers().choices["file"]
            file_parser.print_help()
            return 1

        session_id = os.environ.get("CLAUDE_SESSION_ID")
        is_global = getattr(args, "file_global", False)

        # Get session manager
        mgr = get_session_manager()

        # Normalize aliases to canonical names
        file_cmd = args.file_command
        alias_map = {"a": "allow", "j": "justify", "f": "search", "find": "search", "st": "status", "s": "status"}
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
                    "strict-search": "STRICT SEARCH: ONLY modify existing files. NO new files",
                }.get(global_policy, f"Unknown policy: {global_policy}")
                print(f"Global AutoFile policy: {global_policy}")
                print(f"{policy_desc}")
                print()
                print("This is the default for new sessions.")
                print("Override per-session with: autorun file <allow|justify|search>")
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
                        "strict-search": "STRICT SEARCH: ONLY modify existing files. NO new files",
                    }.get(session_policy, f"Unknown policy: {session_policy}")
                    print(f"Session AutoFile policy: {session_policy}")
                    print(f"{policy_desc}")
                    print()
                    print(f"Session: {session_id[:12]}...")
                    print("Slash command equivalent: /ar:st")
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
        policy_value = {"allow": "allow-all", "justify": "justify-create", "search": "strict-search"}.get(file_cmd)

        if not policy_value:
            # CLI error message - use stdout (not stderr which breaks hooks)
            print(f"Error: Unknown file command: {file_cmd}")
            return 1

        if is_global:
            # Set global default
            with mgr.session_state("__autofile_policy__global") as state:
                state["policy"] = policy_value
            print(f"Global AutoFile policy set to: {policy_value}")
            print("This will be the default for all new sessions.")
            print(f"Slash command equivalent: /ar:{file_cmd[0]} (or /ar:{file_cmd})")
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
            print(f"Slash command equivalent: /ar:{file_cmd[0]} (or /ar:{file_cmd})")

        return 0

    # Task subcommand (modern CLI structure)
    if args.command == "task":
        from autorun.platforms import (
            SessionIdentityResolutionError,
            resolve_standalone_session_identity,
        )
        from autorun.task_lifecycle import TaskLifecycle

        if not hasattr(args, "task_command") or args.task_command is None:
            # No subcommand specified - show help
            task_parser = create_parser().add_subparsers().choices["task"]
            task_parser.print_help()
            return 1

        session_id = None
        if not (args.task_command == "clear" and args.all):
            try:
                session_id = resolve_standalone_session_identity(
                    getattr(args, "session", None),
                ).session_id
            except SessionIdentityResolutionError as exc:
                print(f"Error: {exc}")
                return 1

        # task status
        if args.task_command == "status":
            return TaskLifecycle.cli_status(session_id=session_id, verbose=args.verbose, output_format=args.format)

        # task export
        elif args.task_command == "export":
            return TaskLifecycle.cli_export(session_id=session_id, output_path=args.output, output_format=args.format, include_completed=args.include_completed)

        # task clear
        elif args.task_command == "clear":
            return TaskLifecycle.cli_clear(session_id=session_id, all_sessions=args.all, confirm=not args.no_confirm)

        # task gc
        elif args.task_command == "gc":
            return TaskLifecycle.cli_gc(
                archive=not args.no_archive,
                dry_run=args.dry_run,
                pattern=args.pattern,
                ttl_days=args.ttl,
                confirm=not args.no_confirm,
                current_session_id=session_id,
            )

    # Default: run as hook handler
    return run_hook_handler()


if __name__ == "__main__":
    sys.exit(main())
