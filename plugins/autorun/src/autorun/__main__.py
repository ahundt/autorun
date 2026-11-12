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
from autorun.logging_utils import use_utf8_stdio  # noqa: E402
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


def _print_schema_status(database: str) -> None:
    """Schema lines for --state-status, read without opening the store for writing."""
    import sqlite3

    from .session_manager import (
        SCHEMA_REVISION,
        SCHEMA_USER_VERSION,
        _schema_meta_value,
        _tasks_rebuild_space,
    )

    if not os.path.exists(database):
        return
    try:
        conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            user_version = conn.execute("PRAGMA user_version").fetchone()[0]
            revision = _schema_meta_value(conn, "schema_revision") or user_version
            writer = _schema_meta_value(conn, "schema_written_by")
            space = (
                _tasks_rebuild_space(conn, database)
                if user_version == SCHEMA_USER_VERSION and int(revision) < SCHEMA_REVISION
                else None
            )
        finally:
            conn.close()
    except sqlite3.Error as exc:
        print(f"schema             : unreadable ({exc})")
        return
    if user_version > SCHEMA_USER_VERSION:
        print(f"schema             : {user_version} (newer than this autorun reads)")
    else:
        print(f"schema             : {user_version} (revision {revision})")
    if writer:
        print(f"schema written by  : autorun {writer}")
    if space and space[1] < space[0]:
        needed, free, directory = space
        print(f"upgrade pending    : needs {needed} MB free in {directory} ({free} MB free now)")


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
        _print_schema_status(os.path.join(directory, "daemon_state.sqlite3"))
        if status["phase"] == "COMPLETE" and status["source_present"]:
            print("\nAn unexpected legacy JSON file is also present. It is not authoritative and will not be merged or served.")
        return 0

    raise AssertionError("state maintenance dispatch reached no operation")


def _subcommand_parser(parser: argparse.ArgumentParser, name: str) -> argparse.ArgumentParser:
    """The parser of subcommand `name`, for printing its help.

    Read from the parser's existing subparser group: calling add_subparsers()
    again raises "cannot have multiple subparser arguments".
    """
    commands = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return commands.choices[name]


def add_tmux_arguments(tmux_parser: argparse.ArgumentParser) -> None:
    """The `list` and `send` arguments of `autorun tmux`.

    One definition for both callers: the `tmux` subparser here and
    commands/tmux-exec, which runs without autorun's dependencies installed
    and so cannot build the whole parser.
    """
    tmux_parser.description = (
        "List the AI coding sessions running in your tmux windows (list only reads), "
        "and type text into the ones you name, followed by Enter (send)."
    )
    tmux_subparsers = tmux_parser.add_subparsers(dest="tmux_command", help="tmux operations")
    tmux_list_parser = tmux_subparsers.add_parser(
        "list",
        help="Show each AI session with its letter, pane id, target, directory, harness and state; "
        "types nothing",
        description="Show the AI coding sessions running in your tmux windows, lettered A, B, C. "
        "Reads only: nothing is typed into any pane.",
    )
    tmux_list_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the discovery data as JSON instead of a table",
    )
    tmux_send_parser = tmux_subparsers.add_parser(
        "send",
        help="Type TEXT into the panes --to names, then press Enter",
        description="Type TEXT into the panes --to names, then press Enter in a separate call. "
        "Prints each target's session, window, pane, directory and harness before typing. "
        "Reads each pane just before typing and refuses, typing nothing, a pane showing a "
        "permission, plan or question dialog, one mid-turn, or one with text already typed. "
        "Then reads each pane back and reports text left on its input line as "
        "'typed, not submitted'. Exits 1 if any target was refused or failed, or nothing was sent.",
    )
    tmux_send_parser.add_argument(
        "--to",
        metavar="TARGETS",
        help="Pane ids from the Pane column of `autorun tmux list` (%%3, or %%3,%%7), all, or "
        "awaiting-input (panes listed at their plain input prompt; awaiting is accepted). "
        "Letters are refused: they shift when a window opens or closes",
    )
    tmux_send_parser.add_argument(
        "text",
        nargs="*",
        metavar="TEXT",
        help="The text to type; put -- before it",
    )
    tmux_parser.add_argument(
        "--execute",
        action="store_true",
        help=argparse.SUPPRESS,  # reads {"selections", "command", "sessions"} JSON from stdin
    )


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

AutoFile - control file creation (slash: /ar:file allow|justify|existing-only, /ar:status):
  autorun file status
  autorun file allow
  autorun file justify
  autorun file existing-only
  autorun file existing-only --session SESSION_ID

Task lifecycle management:
  autorun task status --verbose
  autorun task export tasks.json
  autorun task gc --dry-run

For more information: https://github.com/ahundt/autorun
        """,
    )

    # Install options
    from .installer.entrypoint import _marketplace_root, parse_selection

    plugin_names = ", ".join(parse_selection(_marketplace_root()))
    install_group = parser.add_argument_group("Installation (Start Here!)")
    install_group.add_argument(
        "--install",
        "-i",
        nargs="?",
        const="all",
        metavar="PLUGINS",
        help="Install plugins for detected supported harnesses. This publishes each "
        "harness's native hooks, commands, skills, guidance, plugins, or extensions. "
        f"Default: every plugin this distribution carries ({plugin_names}). "
        "Select with --install ar or a comma-separated list.",
    )
    install_group.add_argument(
        "--force",
        "-f",
        action="store_true",
        help=(
            "Force reinstall even if same version (for development). Also "
            "republishes — or, with --uninstall or when the route is no longer "
            "shipped, retires — an owned tree installed before file hashes were "
            "recorded, after backing the current copy up under "
            "~/.autorun/installer/backups/; a recorded user edit is still kept."
        ),
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
        "--pi",
        action="store_true",
        help="Install for Pi only (native TypeScript extension surface)",
    )
    install_group.add_argument(
        "--prime",
        action="store_true",
        help="Install for Prime Agent only (Pi variant; same extension surface)",
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
    # Accepted, not advertised: Antigravity hook commands append it
    # (platforms.Platform._native_handler) and hook_entry.py reads it; the
    # payload's hook_event_name wins. Nobody types it.
    hook_group.add_argument("--event", default=None, help=argparse.SUPPRESS)

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
        # Accepted and ignored, so a statusline script that
        # still pipes into it keeps working. It fed the removed context-window
        # and rate-limit cache axes; nothing reads a snapshot any more.
        help=argparse.SUPPRESS,
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
        # None so AUTORUN_UPDATE_METHOD and the config file can answer; see
        # --codex-hook-source above. perform_self_update resolves it.
        default=None,
        help="Force specific update method (default: auto-detect). "
        "AUTORUN_UPDATE_METHOD also sets this; the flag wins.",
    )

    # Subcommands (modern CLI structure)
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # AutoFile (af) subcommand - file creation control
    file_parser = subparsers.add_parser(
        "file",
        help="AutoFile - control file creation policy",
        description="Control file creation and modification policies (AutoFile system). "
        "Equivalent to the /ar:file allow|justify|existing-only and /ar:status slash commands.",
    )
    file_subparsers = file_parser.add_subparsers(dest="file_command", help="AutoFile operations")

    # file allow
    allow_parser = file_subparsers.add_parser(
        "allow",
        aliases=["a"],
        help="Allow creating new files freely (CLI: file a, Slash: /ar:file allow)",
        description="""Set AutoFile policy to 'allow-all' mode.

Claude can create new files and modify existing files without any restrictions.
This is the most permissive mode - good for new projects or exploratory work.

Examples:
  autorun file allow              # Set for current session
  autorun file a --session ID     # Set for session ID

Hooks served by the autorun daemon (the default) cache session state, so
there this exits 1 and names the slash command to run in the session;
it writes the policy when hooks run daemonless (AUTORUN_USE_DAEMON=0).

Equivalent slash command: /ar:file allow""",
    )
    allow_parser.add_argument(
        "--global",
        "-g",
        action="store_true",
        dest="file_global",
        help="Rejected: there is no global AutoFile policy; every session starts at allow-all",
    )

    # file justify
    justify_parser = file_subparsers.add_parser(
        "justify",
        aliases=["j"],
        help="Require written justification to create new files (CLI: file j, Slash: /ar:file justify)",
        description="""Set AutoFile policy to 'justify-create' mode.

The active assistant must search for existing files first. If creating a new
file, it must include <AUTOFILE_JUSTIFICATION>reason</AUTOFILE_JUSTIFICATION>
explaining why.
This encourages modifying existing code rather than duplicating functionality.

Good for established projects where you want to minimize unnecessary new files.

Examples:
  autorun file justify            # Set for current session
  autorun file j --session ID     # Set for session ID

Hooks served by the autorun daemon (the default) cache session state, so
there this exits 1 and names the slash command to run in the session;
it writes the policy when hooks run daemonless (AUTORUN_USE_DAEMON=0).

Equivalent slash command: /ar:file justify""",
    )
    justify_parser.add_argument(
        "--global",
        "-g",
        action="store_true",
        dest="file_global",
        help="Rejected: there is no global AutoFile policy; every session starts at allow-all",
    )

    # file existing-only - strict mode (`search`, `find`, `f` are older words)
    search_parser = file_subparsers.add_parser(
        "existing-only",
        aliases=["search", "find", "f"],
        help="Block all new file creation - only modify existing (CLI: file f, Slash: /ar:file existing-only)",
        description="""Set AutoFile policy to 'strict-search' mode (strictest).

The active assistant cannot create any new files. It can only modify existing files.
It must use platform-native search to find existing files before making changes.

This is the most restrictive mode - good when you want to prevent any
accidental new file creation in a mature codebase.

Examples:
  autorun file existing-only      # Set for current session
  autorun file f --session ID     # Set for session ID (short version)

Hooks served by the autorun daemon (the default) cache session state, so
there this exits 1 and names the slash command to run in the session;
it writes the policy when hooks run daemonless (AUTORUN_USE_DAEMON=0).

Equivalent slash command: /ar:file existing-only
Aliases: file search, file find, file f (all equivalent)""",
    )
    search_parser.add_argument(
        "--global",
        "-g",
        action="store_true",
        dest="file_global",
        help="Rejected: there is no global AutoFile policy; every session starts at allow-all",
    )

    # file status
    af_status_parser = file_subparsers.add_parser(
        "status",
        aliases=["st", "s"],
        help="Show current file creation policy (CLI: file st, Slash: /ar:status)",
        description="""Display current AutoFile policy setting.

Shows whether Claude can create new files freely (allow), must justify (justify),
or is blocked from creating new files (search/strict).

Reads the policy the Write gate enforces for the current session (--session
selects another). There is no global policy; every session starts at allow-all.

Examples:
  autorun file status             # Show current session policy
  autorun file st --session ID    # Show session ID's policy (short version)

Equivalent slash command: /ar:status
Aliases: file status, file st, file s (all equivalent)""",
    )
    af_status_parser.add_argument(
        "--global",
        "-g",
        action="store_true",
        dest="file_global",
        help="Report that no global policy exists (every session starts at allow-all)",
    )
    for file_command_parser in (allow_parser, justify_parser, search_parser, af_status_parser):
        file_command_parser.add_argument(
            "--session",
            metavar="SESSION_ID",
            help=standalone_session_help(),
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

    # tmux subcommand: AI sessions running in tmux panes. Its code is imported
    # only when it runs, so building this parser costs a hook nothing.
    add_tmux_arguments(
        subparsers.add_parser(
            "tmux",
            help="List the AI sessions in your tmux windows; type text into the ones you name",
        )
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

    import json

    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    hooks = [
        hook
        for groups in data.get("hooks", {}).values()
        for group in groups
        for hook in group.get("hooks", [])
        if "hook_entry.py" in hook.get("command", "")
    ]
    if not hooks:
        # A wheel's hooks run the installed `autorun` command, whose
        # dependencies are already installed, so there is no bootstrap to toggle.
        print(f"No hook_entry.py command in {hooks_path}; nothing changed (these hooks never bootstrap).")
        return 0

    # Edit the parsed command, never the JSON text: the script path is quoted,
    # so the flag goes after the closing quote. The removal pattern also
    # repairs a flag that an earlier text edit put inside the quotes.
    script = r"""(hook_entry\.py["']?)"""
    changed = False
    for hook in hooks:
        command = hook["command"]
        if enabled:
            hook["command"] = re.sub(script + r"\s+--no-bootstrap", r"\1", command)
        else:
            hook["command"] = re.sub(script + r"""(?!["']?\s+--no-bootstrap)""", r"\1 --no-bootstrap", command)
        changed |= hook["command"] != command

    state = "enabled" if enabled else "disabled"
    if not changed:
        print(f"Bootstrap already {state}")
        return 0
    hooks_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    action = "removed --no-bootstrap from" if enabled else "added --no-bootstrap to"
    print(f"Bootstrap {state} ({action} {hooks_path})")
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
    from .client import output_hook_response, prepare_payload_for_daemon, resolve_launcher_pid
    from .config import HOOK_DEADLINE_PAYLOAD_KEY, monotonic_deadline

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
    resolve_launcher_pid(payload)
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
        deadline_monotonic=monotonic_deadline(payload.get(HOOK_DEADLINE_PAYLOAD_KEY)),
        permission_mode=normalized["permission_mode"],
        source=normalized["source"],
        agent_id=normalized["agent_id"],
        agent_type=normalized["agent_type"],
        prompt_author=normalized["prompt_author"],
        active_tools=normalized["active_tools"],
        transcript_path=normalized.get("transcript_path"),
        agent_transcript_path=normalized.get("agent_transcript_path"),
        stop_hook_active=normalized["stop_hook_active"],
        last_assistant_message=normalized["last_assistant_message"],
        background_tasks=normalized["background_tasks"],
        session_crons=normalized["session_crons"],
        server_url=payload.get("server_url", ""),
        harness_pid=payload.get("_pid"),
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


def _reinstall_command() -> str:
    """The reinstall that restores this install's declared dependencies."""
    from autorun.installer.discovery import marketplace_root
    from autorun.installer.entrypoint import _PLUGIN_DISTRIBUTIONS

    root = marketplace_root()  # follows an editable install back to its checkout
    if root is not None:
        return f"uv tool install --force --editable {root / 'plugins' / 'autorun'}"
    return f"uv tool install --force {_PLUGIN_DISTRIBUTIONS['ar'][0]}"


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command; report a missing dependency instead of a traceback.

    Dependencies are imported lazily, so an install made before one was added
    to pyproject.toml fails only when a command first reaches it -- `--status`
    reading ~/.codex/config.toml needed tomlkit. Reinstalling is the repair.
    """
    try:
        result = _main(argv)
    except ModuleNotFoundError as missing:
        # stdout, never stderr: a hook's stderr disables every hook.
        print(
            f"autorun cannot run this command: Python module "
            f"'{missing.name}' is not installed in autorun's environment "
            f"({sys.executable}).\n"
            f"Reinstall autorun to restore its dependencies:\n"
            f"    {_reinstall_command()}"
        )
        return 1
    # Hook mode runs outside the handler above: stdout is the hook response
    # there, and its failures already have their own fail-open/closed path.
    return run_hook_handler() if result is _RUN_AS_HOOK else result


#: Returned by _main when no command matched and stdin is a pipe.
_RUN_AS_HOOK = object()


def _main(argv: Sequence[str] | None = None) -> int:
    """Main entry point.

    Args:
        argv: Command line arguments (defaults to sys.argv[1:])

    Returns:
        Exit code: 0 = success, 1 = failure
    """
    use_utf8_stdio()

    parser = create_parser()
    # parse_args, not parse_known_args: a misspelled flag such as
    # --codex-hook-sorce=plugin must stop with an error naming it rather than
    # run with the default it was meant to change.
    args = parser.parse_args(argv)

    # Version check
    if args.version:
        from autorun import __version__

        print(f"autorun {__version__}")
        return 0

    # Propagate --cli to env so run_hook_handler() / run_client() can use it
    if getattr(args, "cli", None):
        os.environ["AUTORUN_CLI_TYPE"] = args.cli

    # Workaround configuration for
    # https://github.com/anthropics/claude-code/issues/4669 (set env var from
    # CLI arg). The `--exit2-mode` help text above uses the short "#4669" form
    # to stay readable at the terminal; this is where a reader resolves it.
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
            "pi_only": args.pi,
            "prime_only": args.prime,
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

    # Retired statusline tap: accepted so old statusline scripts keep working.
    if args.cache_snapshot:
        return 0

    # Capability snapshot (read-only diagnostic; does not install hooks or use daemon)
    if getattr(args, "capability_snapshot", None) is not None:
        from autorun.capability_snapshot import write_capability_snapshot

        write_capability_snapshot(args.capability_snapshot)
        return 0

    # Uninstall mode
    if args.uninstall is not None:
        from autorun.install import uninstall_plugins

        return uninstall_plugins(args.uninstall, force=args.force, custom_harnesses=args.custom_harness)

    # Update mode
    if args.update:
        from autorun.install import perform_self_update

        result = perform_self_update(method=args.update_method)
        print(result.describe())
        return 0 if result.ok else 1

    # AutoFile (af) subcommand - file creation control
    if args.command == "file":
        from autorun.config import CONFIG
        from autorun.core import EventContext, ThreadSafeDB, format_command_for_cli, format_suggestion
        from autorun.platforms import (
            SessionIdentityResolutionError,
            resolve_standalone_session_identity,
        )

        if not hasattr(args, "file_command") or args.file_command is None:
            # No subcommand specified - show help
            _subcommand_parser(parser, "file").print_help()
            return 1

        file_cmd = {
            "a": "allow",
            "j": "justify",
            "f": "existing-only",
            "find": "existing-only",
            "search": "existing-only",
            "st": "status",
            "s": "status",
        }.get(args.file_command, args.file_command)
        # The key CONFIG["policies"] and the Write gate (plugins.enforce_file_policy) use.
        words = CONFIG["file_policy_words"]
        policy = next((key for key, word in words.items() if word == file_cmd), "STATUS")
        slash = f"/ar:file {words[policy]}" if policy in words else "/ar:status"
        default_name = CONFIG["policies"]["ALLOW"][0]

        if args.file_global:
            # Nothing stores or reads a global AutoFile policy: every session
            # starts at allow-all (EventContext's file_policy default).
            print(f"There is no global AutoFile policy; every session starts at {default_name}.")
            if policy == "STATUS":
                return 0
            print(f"Set it per session: run {slash} in the session, or autorun file {file_cmd} --session SESSION_ID.")
            return 1

        try:
            identity = resolve_standalone_session_identity(args.session)
        except SessionIdentityResolutionError as exc:
            print(f"Error: {exc}")
            return 1
        session_id = identity.session_id
        slash = format_command_for_cli(slash, identity.platform_name)

        # The same state access the /ar:file handler makes: ctx.file_policy.
        ctx = EventContext(session_id=session_id, event="UserPromptSubmit", store=ThreadSafeDB())
        if policy != "STATUS":
            if USE_DAEMON:
                # The daemon serves session state from memory once a session
                # has been read, so a write from this process would leave a
                # live session on its old policy while reporting success.
                print(f"Error: the autorun daemon caches session state, so this command cannot change a live session's AutoFile policy. Run {slash} in session {session_id} instead.")
                return 1
            ctx.file_policy = policy

        name, desc = CONFIG["policies"].get(ctx.file_policy, (str(ctx.file_policy), ""))
        print(f"AutoFile policy: {name}\n{format_suggestion(desc, ctx.cli_type)}\nSession: {session_id}\nSlash command equivalent: {slash}")
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
            _subcommand_parser(parser, "task").print_help()
            return 1

        session_id = None
        if not (args.task_command == "clear" and args.all):
            try:
                session_id = resolve_standalone_session_identity(
                    getattr(args, "session", None),
                ).session_id
            except SessionIdentityResolutionError as exc:
                # gc from a plain terminal has no session to protect;
                # cli_gc says so. Every other task command needs one.
                if not (args.task_command == "gc" and exc.reason == "missing"):
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

    # tmux subcommand: list reads, send types
    if args.command == "tmux":
        from autorun.tmux_tab_ai_session_status import run_tmux_command

        return run_tmux_command(args, _subcommand_parser(parser, "tmux"))

    # Default: run as hook handler. Hooks arrive as JSON on a pipe; a person
    # at a terminal typed a bare `autorun`, and the useful answer there is
    # usage — the same answer the dispatcher gives a bare `/ar` — not a
    # daemon round-trip on an empty payload that exits 0 in silence.
    try:
        interactive = sys.stdin.isatty()
    except (AttributeError, ValueError):
        interactive = False
    if interactive:
        parser.print_help()
        return 0
    return _RUN_AS_HOOK


if __name__ == "__main__":
    sys.exit(main())
