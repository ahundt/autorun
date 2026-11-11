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
"""
Centralized tmux utilities - DRY compliant implementation

Ensures consistent tmux/byobu handling across autorun with proper
control sequence parsing, session naming, and command dispatch.

# ═══════════════════════════════════════════════════════════════════════════════
# PRIMARY API - Main functions for tmux window operations
# ═══════════════════════════════════════════════════════════════════════════════
#
# READ OPERATIONS (Safe - no side effects):
#
#   tmux_list_windows(content_lines=0) -> WindowList
#       Get all tmux windows with optional Claude status detection.
#       Returns WindowList with chainable filters: .claude_sessions(), .in_mode(),
#       .prompting_user_for_input(), .actively_generating(), .thinking_enabled()
#
#   tmux_get_claude_window_status(tmux, session, window) -> Dict
#       Get detailed status of a single Claude Code window (mode, thinking, active).
#
#   tmux_get_claude_window_mode(tmux, session, window) -> str
#       Get just the Claude mode ('default', 'plan', 'bypass', 'accept_edits').
#
# WRITE OPERATIONS (⚠️ DANGEROUS - modifies state):
#
#   tmux_dangerous_batch_execute(tmux, action, targets, ...) -> Dict
#       ⚠️  Execute actions on MULTIPLE windows. Can disrupt work if misused!
#       Actions: 'send', 'continue', 'escape', 'exit', 'kill', 'toggle_thinking',
#                'cycle_mode', 'set_mode'
#       ALWAYS verify targets before executing. See function docstring for safety.
#
# DETECTION FUNCTIONS:
#
#   tmux_detect_claude_mode(content) -> str
#       Detect CLI mode from terminal content.
#
#   tmux_detect_claude_thinking_mode(content) -> bool
#       Detect if thinking mode is enabled.
#
#   tmux_detect_claude_active(content) -> bool
#       Detect if Claude is actively generating.
#
#   tmux_detect_prompt_type(content) -> str|None
#       Detect prompt type ('input', 'plan_approval', 'tool_permission', etc.)
#
# ═══════════════════════════════════════════════════════════════════════════════
"""

import os
import platform
import re
import shutil
import subprocess
import time
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Callable, Union
from enum import Enum


_KNOWN_TMUX_PATHS = (
    "/opt/homebrew/bin/tmux",
    "/usr/local/bin/tmux",
    "/usr/bin/tmux",
)
_KNOWN_HOMEBREW_PATHS = (
    "/opt/homebrew/bin/brew",
    "/usr/local/bin/brew",
)


def _current_tmux_socket() -> Optional[str]:
    """Return the inherited tmux socket path from TMUX, if present."""
    tmux_env = os.getenv("TMUX")
    if not tmux_env:
        return None
    socket_path = tmux_env.split(",", 1)[0].strip()
    return socket_path or None


def _is_apple_silicon() -> bool:
    """True on Apple Silicon, including Rosetta-translated processes."""
    if platform.system() != "Darwin":
        return False
    if platform.machine() == "arm64":
        return True
    try:
        result = subprocess.run(
            ["/usr/sbin/sysctl", "-n", "hw.optional.arm64"],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=2,
            shell=False,
        )
    except Exception:
        return False
    return result.returncode == 0 and result.stdout.strip() == "1"


def _known_homebrew_paths_for_platform() -> tuple[str, ...]:
    """Return Homebrew binary paths ordered for the current hardware."""
    if _is_apple_silicon():
        return ("/opt/homebrew/bin/brew", "/usr/local/bin/brew")
    if platform.system() == "Darwin":
        return ("/usr/local/bin/brew", "/opt/homebrew/bin/brew")
    return _KNOWN_HOMEBREW_PATHS


def _known_tmux_paths_for_platform() -> tuple[str, ...]:
    """Return tmux binary paths ordered for the current hardware."""
    if _is_apple_silicon():
        return ("/opt/homebrew/bin/tmux", "/usr/local/bin/tmux", "/usr/bin/tmux")
    if platform.system() == "Darwin":
        return ("/usr/local/bin/tmux", "/usr/bin/tmux", "/opt/homebrew/bin/tmux")
    return _KNOWN_TMUX_PATHS


def _candidate_homebrew_binaries() -> List[str]:
    """Return ordered Homebrew candidates for the current hardware."""
    candidates: List[str] = []
    if platform.system() == "Darwin":
        candidates.extend(_known_homebrew_paths_for_platform())
    path_brew = shutil.which("brew")
    if path_brew:
        candidates.append(path_brew)
    if platform.system() != "Darwin":
        candidates.extend(_known_homebrew_paths_for_platform())

    seen = set()
    existing = []
    for candidate in candidates:
        # Candidate strings are already canonical for the platform being
        # probed.  Re-parsing a mocked Darwin path through Windows ``Path``
        # changes ``/opt/homebrew`` into ``\\opt\\homebrew`` and corrupts
        # otherwise portable ordering tests (and diagnostics).
        path = candidate
        if path in seen or not Path(path).exists():
            continue
        seen.add(path)
        existing.append(path)
    return existing


def _tmux_paths_from_homebrew() -> List[str]:
    """Return tmux client paths reported by Homebrew, in preferred order."""
    paths: List[str] = []
    for brew in _candidate_homebrew_binaries():
        try:
            result = subprocess.run(
                # No formula: `brew --prefix tmux` resolves the formula and took
                # 3.9-6.3 s under an empty HOME against this 2 s timeout; the
                # bare prefix answers offline in about 0.02 s.
                [brew, "--prefix"],
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=2,
                shell=False,
            )
        except Exception:
            continue
        if result.returncode != 0:
            continue
        prefix = result.stdout.strip()
        if prefix:
            paths.append(str(Path(prefix) / "opt" / "tmux" / "bin" / "tmux"))
    return paths


def _candidate_tmux_binaries() -> List[str]:
    """Return ordered tmux client candidates, deduplicated and existing."""
    candidates: List[str] = []
    if platform.system() == "Darwin":
        candidates.extend(_tmux_paths_from_homebrew())
        if _is_apple_silicon():
            candidates.append("/opt/homebrew/bin/tmux")
        candidates.extend(_known_tmux_paths_for_platform())
    path_tmux = shutil.which("tmux")
    if path_tmux:
        candidates.append(path_tmux)
    if platform.system() != "Darwin":
        candidates.extend(_known_tmux_paths_for_platform())

    seen = set()
    existing = []
    for candidate in candidates:
        path = candidate
        if path in seen or not Path(path).exists():
            continue
        seen.add(path)
        existing.append(path)
    return existing or ["tmux"]


def _tmux_version(binary: str) -> str:
    """Return `tmux -V` output for a candidate client, or empty string."""
    try:
        result = subprocess.run(
            [binary, "-V"],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=2,
            shell=False,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _tmux_socket_is_usable(binary: str, socket_path: str) -> bool:
    """True if binary can talk to the inherited tmux server socket."""
    try:
        result = subprocess.run(
            [binary, "-S", socket_path, "display-message", "-p", "#S"],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=2,
            shell=False,
        )
    except Exception:
        return False
    return result.returncode == 0


@lru_cache(maxsize=1)
def resolve_tmux_binary() -> str:
    """Resolve a tmux client that is safe for the current execution context.

    When autorun runs inside byobu/tmux, subprocesses inherit TMUX and should
    talk to the same server socket. If PATH finds an older tmux client than the
    active server, clients can fail with "server exited unexpectedly" even
    though the live session is healthy. Prefer a candidate that can perform a
    read-only query against the inherited socket, then fall back to PATH.
    """
    override = os.getenv("AUTORUN_TMUX_BIN")
    if override:
        return override

    candidates = _candidate_tmux_binaries()
    socket_path = _current_tmux_socket()
    if not socket_path:
        return candidates[0]

    working = [binary for binary in candidates if _tmux_socket_is_usable(binary, socket_path)]
    if not working:
        return candidates[0]

    version_hint = os.getenv("TERM_PROGRAM_VERSION", "").strip()
    if version_hint:
        for binary in working:
            if _tmux_version(binary).endswith(version_hint):
                return binary

    return working[0]


class TmuxControlState(Enum):
    """States for tmux control sequence parsing"""
    NORMAL = "normal"
    ESCAPE = "escape"


class TmuxUtilities:
    """
    Centralized tmux utilities with control sequence support and session management

    Enforces autorun standards: default session naming "autorun",
    control sequence parsing, and comprehensive WIN_OPS dispatch.

    Session Targeting:
    - Default session: "autorun" - prevents interference with current Claude Code session
    - Custom targeting: Pass session parameter to target different sessions
    - Safety guarantee: All commands are explicitly targeted to prevent accidental execution in wrong session
    - Format: session:window.pane for precise targeting
    """

    # Default session name as required by skills/tmux-automation/SKILL.md
    DEFAULT_SESSION_NAME = "autorun"

    # Complete WIN_OPS dispatch dictionary as required by documentation
    WIN_OPS = {
        # Navigation and window management
        'new-window': 'new-window',
        'new': 'new-window',
        'nw': 'new-window',

        'select-window': 'select-window -t',
        'sw': 'select-window -t',
        'window': 'select-window -t',
        'w': 'select-window -t',

        'next-window': 'next-window',
        'n': 'next-window',
        'prev-window': 'previous-window',
        'p': 'previous-window',
        'pw': 'previous-window',

        # Pane management
        'split-window': 'split-window',
        'split': 'split-window',
        'sp': 'split-window',
        'vsplit': 'split-window -h',
        'vsp': 'split-window -h',

        'select-pane': 'select-pane -t',
        'selp': 'select-pane -t',  # 'sp' is used for split-window above
        'pane': 'select-pane -t',

        'next-pane': 'select-pane -t :.+',
        'np': 'select-pane -t :.+',
        'prev-pane': 'select-pane -t :.-',
        'pp': 'select-pane -t :.-',

        # Session management
        'new-session': 'new-session',
        'ns': 'new-session',
        'new-sess': 'new-session',

        'attach-session': 'attach-session -t',
        'attach': 'attach-session -t',
        'as': 'attach-session -t',

        'detach-client': 'detach-client',
        'detach': 'detach-client',
        'dc': 'detach-client',

        # Layout and display
        'select-layout': 'select-layout',
        'layout': 'select-layout',
        'sl': 'select-layout',

        'clock-mode': 'clock-mode',
        'clock': 'clock-mode',

        # Copy mode
        'copy-mode': 'copy-mode',
        'copy': 'copy-mode',

        # Search and navigation in copy mode
        'search-forward': 'search-forward',
        'search-backward': 'search-backward',

        # Misc operations
        'list-windows': 'list-windows',
        'lw': 'list-windows',
        'list-sessions': 'list-sessions',
        'ls': 'list-sessions',
        'list-panes': 'list-panes',
        'lp': 'list-panes',

        'rename-window': 'rename-window',
        'rename': 'rename-window',

        'kill-window': 'kill-window',
        'kw': 'kill-window',
        'kill-pane': 'kill-pane',
        'kp': 'kill-pane',
        'kill-session': 'kill-session',
        'ks': 'kill-session',

        # Display and info
        'display-message': 'display-message',
        'display': 'display-message',
        'dm': 'display-message',

        'show-options': 'show-options',
        'show': 'show-options',
        'so': 'show-options',

        # Control and scripting
        'send-keys': 'send-keys',
        'send': 'send-keys',
        'sk': 'send-keys',

        'capture-pane': 'capture-pane',
        'capture': 'capture-pane',
        'cp': 'capture-pane',

        'pipe-pane': 'pipe-pane',
        'pipe': 'pipe-pane',

        # Buffer operations
        'list-buffers': 'list-buffers',
        'lb': 'list-buffers',
        'save-buffer': 'save-buffer',
        'sb': 'save-buffer',
        'delete-buffer': 'delete-buffer',
        'db': 'delete-buffer',

        # Advanced operations
        'resize-pane': 'resize-pane',
        'resize': 'resize-pane',
        'rp': 'resize-pane',

        'swap-pane': 'swap-pane',
        'swap': 'swap-pane',

        'join-pane': 'join-pane',
        'join': 'join-pane',
        'break-pane': 'break-pane',
        'break': 'break-pane',
    }

    def __init__(self, session_name: Optional[str] = None):
        """
        Initialize tmux utilities with session name enforcement

        Args:
            session_name: Override default session name (should rarely be used)

        Which tmux *server* this talks to is decided by
        :func:`resolve_tmux_binary`, not here. Tests redirect it by pointing
        ``AUTORUN_TMUX_BIN`` at a wrapper that adds ``-S <private socket>``
        (see ``tests/conftest.py::pytest_configure``), which keeps one owner
        for that question instead of two competing ones.
        """
        self.session_name = session_name or self.DEFAULT_SESSION_NAME
        self.control_state = TmuxControlState.NORMAL
        self.tmux_binary = resolve_tmux_binary()

    def detect_tmux_environment(self) -> Optional[Dict[str, str]]:
        """
        Detect tmux environment with consistent approach

        Returns:
            Dict with session, window, pane info or None if not in tmux
        """
        # TMUX is /tmp/tmux-1000/default,4219,0: socket, server PID, session
        # id. It holds no names, so only the socket comes from it.
        tmux_env = os.getenv('TMUX')
        if not tmux_env:
            return None

        location = _tmux_current_location()
        if location is None:
            return None
        session, window, pane = location
        return {
            "session": session,
            "window": window,
            "pane": pane,
            "socket_path": tmux_env.split(',')[0],
        }

    def execute_tmux_command(self, cmd: List[str], session: Optional[str] = None,
                           window: Optional[str] = None, pane: Optional[str] = None) -> Optional[Dict[str, str]]:
        """
        Execute tmux command with standardized approach and reliable session targeting

        Args:
            cmd: Command list to execute
            session: Target session (uses instance default "autorun" if None)
            window: Target window (optional, defaults to current window in session)
            pane: Target pane (optional, defaults to current pane in window)

        Returns:
            Command result dict or None if failed

        Session Targeting Behavior:
        - Default: Always targets "autorun" session to avoid affecting current Claude Code session
        - Custom session: Pass session parameter to target a different session
        - Full targeting: session:window.pane format for precise targeting
        - Safety: Commands will NEVER go to the current Claude Code session accidentally
        - Never creates a session: a command aimed at a missing one fails
          (returncode 1, "can't find session"). Create it explicitly with
          ensure_session_exists(); a query must be able to answer "no".

        Examples:
            tmux.execute_tmux_command(['list-windows'])  # Targets "autorun" session
            tmux.execute_tmux_command(['send-keys', 'test'], 'custom-session')  # Targets custom session
            tmux.execute_tmux_command(['capture-pane'], None, '0', '0')  # Targets autorun:0.0
        """
        target_session = session or self.session_name

        # Build command list
        base_cmd = [self.tmux_binary]

        # tmux obeys the last -t, so a target the caller wrote must be the only
        # one; appending ours after it sent a caller's `list-windows -t <s>`
        # to the "autorun" session. For send-keys only a leading -t is a flag;
        # later arguments are keys to type.
        caller_target = len(cmd) > 2 and (
            cmd[1] == '-t' if cmd[0] == 'send-keys' else '-t' in cmd[1:]
        )

        # CRITICAL FIX: For send-keys commands, target specification must come BEFORE keys
        # Format: tmux [socket] send-keys -t target [keys...] where control sequences are separate args
        if cmd and cmd[0] == 'send-keys' and len(cmd) > 1 and not caller_target:
            # Start with base command
            full_cmd = base_cmd + [cmd[0]]  # ['tmux', 'send-keys']

            # Add target specification immediately after send-keys
            target = target_session
            if window:
                target += f":{window}"
            if pane:
                target += f".{pane}"
            full_cmd.extend(["-t", target])  # ['tmux', 'send-keys', '-t', 'session']

            # Add keys and control sequences as separate arguments
            # cmd[1:] contains all the keys and control sequences
            full_cmd.extend(cmd[1:])
        else:
            # Non-send-keys commands: use original logic
            full_cmd = base_cmd + cmd

            # Add target specification for commands that support session targeting
            # NOTE: 'new-session' is excluded because -t means "session group" not "target"
            # For new-session, use -s to specify the session name instead
            commands_supporting_target = {
                'send-keys', 'capture-pane', 'new-window', 'kill-window',
                'select-window', 'split-window', 'select-pane', 'kill-pane',
                'select-layout', 'display-message', 'attach-session', 'detach-client',
                'kill-session', 'list-windows', 'list-panes', 'has-session'
            }

            if cmd and cmd[0] in commands_supporting_target and not caller_target:
                target = target_session
                if window:
                    target += f":{window}"
                if pane:
                    target += f".{pane}"
                full_cmd.extend(["-t", target])


        try:
            # Name the server explicitly rather than letting the subprocess
            # inherit one. `-S <socket>` pins which tmux server every command
            # reaches, so a command cannot land in whatever session happens to
            # be current. With no TMUX in the environment, tmux picks its own
            # default socket, which is the behaviour outside a session.
            # TMUX looks like /tmp/tmux-1000/default,4219,0
            tmux_env = os.getenv('TMUX')
            socket_path = tmux_env.split(',')[0] if tmux_env else None

            # -u: without a UTF-8 locale a tmux client prints every
            # non-printable or non-ASCII byte as "_", which turned
            # list-windows' \x1f field separator and Claude's ❯ prompt into "_".
            prefix = (
                [self.tmux_binary, '-u', '-S', socket_path] if socket_path
                else [self.tmux_binary, '-u']
            )

            executed = prefix + full_cmd[1:]
            result = subprocess.run(
                executed,
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=5,
                shell=False
            )
            return {
                'returncode': result.returncode,
                'stdout': result.stdout,
                'stderr': result.stderr,
                'command': executed,
            }
        except subprocess.TimeoutExpired:
            return None
        except Exception:
            return None

    def execute_win_op(self, operation: str, args: Optional[List[str]] = None,
                      session: Optional[str] = None, window: Optional[str] = None,
                      pane: Optional[str] = None) -> bool:
        """
        Execute window operation using WIN_OPS dispatch

        Args:
            operation: Operation name (e.g., 'new-window', 'split-window')
            args: Additional arguments for the operation
            session: Target session
            window: Target window
            pane: Target pane

        Returns:
            True if successful, False otherwise
        """
        # Look up operation in WIN_OPS
        if operation not in self.WIN_OPS:
            return False

        tmux_cmd = self.WIN_OPS[operation].split()

        # Add arguments if provided
        if args:
            tmux_cmd.extend(args)

        # Execute command
        result = self.execute_tmux_command(tmux_cmd, session, window, pane)
        return result and result['returncode'] == 0

    def ensure_session_exists(self, session_name: Optional[str] = None) -> bool:
        """
        Ensure session exists, create if needed

        Args:
            session_name: Session name to ensure exists

        Returns:
            True if session exists or was created successfully
        """
        target_session = session_name or self.session_name

        # session=, not an inline -t: execute_tmux_command appends its own -t
        # and tmux honours the last one, which was this instance's session.
        result = self.execute_tmux_command(['has-session'], target_session)
        if result and result['returncode'] == 0:
            return True

        # Create session if it doesn't exist
        result = self.execute_tmux_command(['new-session', '-d', '-s', target_session])
        return result and result['returncode'] == 0

    def capture_current_input(self, session: Optional[str] = None, window: Optional[str] = None,
                            pane: Optional[str] = None) -> str:
        """
        Capture current input from tmux pane

        Returns:
            Current input text or empty string if failed
        """
        result = self.execute_tmux_command(['capture-pane', '-p'], session, window, pane)
        if result and result['returncode'] == 0:
            lines = result['stdout'].strip().split('\n')
            return lines[-1] if lines else ""
        return ""

    def is_user_typing(self, check_interval: float = 0.5, max_checks: int = 3,
                      session: Optional[str] = None, window: Optional[str] = None,
                      pane: Optional[str] = None) -> bool:
        """
        Check if user is actively typing

        Args:
            check_interval: Time between checks in seconds
            max_checks: Maximum number of checks to perform
            session: Target session
            window: Target window
            pane: Target pane

        Returns:
            True if user is typing, False otherwise
        """
        result = self.execute_tmux_command(['has-session'], session)
        if not result or result['returncode'] != 0:
            return False

        initial_input = self.capture_current_input(session, window, pane)

        for _ in range(max_checks - 1):
            time.sleep(check_interval)
            current_input = self.capture_current_input(session, window, pane)
            if current_input != initial_input:
                return True

        return False

    def send_keys(self, keys: str, session: Optional[str] = None, window: Optional[str] = None,
                 pane: Optional[str] = None) -> bool:
        """
        Send keys to tmux pane

        Args:
            keys: Keys to send
            session: Target session
            window: Target window
            pane: Target pane

        Returns:
            True if successful, False otherwise
        """
        # One argument serves both kinds of send. `tmux send-keys <arg>` parses
        # the argument as a key name and falls back to sending its characters
        # when it is not one, so `C-m` arrives as Enter and `export FOO=1`
        # arrives as text without this having to tell them apart.
        #
        # A branch here used to claim it separated control sequences from text,
        # with both arms building this same list. Sending text as text needs
        # `send-keys -l`, which never appeared in this file; and applying it to
        # the "not a control sequence" arm would have broken the `Enter`,
        # `Escape`, `Tab`, `BTab`, `C-a C-k` and `Escape 0 d` call sites, none
        # of which that arm's five-name list recognised.
        #
        # Known limit of the fallback: text that is exactly a tmux key name is
        # sent as that key. A prompt of exactly `Up` recalls shell history
        # instead of typing "Up". Fixing that means classifying at each call
        # site, not here, because only the caller knows which it meant.
        result = self.execute_tmux_command(['send-keys', keys], session, window, pane)
        return result and result['returncode'] == 0

    def get_session_info(self, session_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Get comprehensive session information

        Args:
            session_name: Override instance session name for this query

        Returns:
            Dict with session details
        """
        target_session = session_name or self.session_name
        env_info = self.detect_tmux_environment()

        info = {
            "target_session": target_session,
            "session": target_session,  # Maintain backward compatibility
            "window": "unknown",
            "pane": "unknown",
            "socket_path": None,
            "tmux_active": env_info is not None,
            "current_session": env_info["session"] if env_info else None
        }

        # Only update with environment info if it matches our target session
        if env_info and env_info.get("session") == target_session:
            info.update(env_info)
        elif env_info:
            # Add environment info as separate fields to avoid confusion
            info["env_session"] = env_info.get("session")
            info["env_window"] = env_info.get("window")
            info["env_pane"] = env_info.get("pane")

        # Get additional session details
        result = self.execute_tmux_command(['list-sessions'])
        if result and result['returncode'] == 0:
            info["available_sessions"] = [
                line.split(':')[0] for line in result['stdout'].strip().split('\n') if line.strip()
            ]
        else:
            info["available_sessions"] = []

        return info

    def is_claude_session(self, session: str, window: str) -> bool:
        """True when a pane of the window runs any registered AI harness
        (see pane_harness); the name predates the other harnesses."""
        return self.pane_harness(session, window) is not None

    def pane_harness(self, session: str, window: str) -> Optional[str]:
        """
        Return the platform name of the AI harness a tmux window runs, or None.

        Uses existing execute_tmux_command infrastructure to get pane PID,
        then examines child processes for a registered harness executable.

        Args:
            session: Tmux session name
            window: Window index within session

        Returns:
            The ``platforms.PLATFORMS`` key of the first harness found, or None

        Implementation:
            1. Query tmux for the PID of every pane in the window
            2. Use pgrep -P to find child processes of those shells
            3. Match each child's executable name against
               client.harness_process_names() (see _process_harness)

        Best Practices:
            - Leverages existing execute_tmux_command for consistency
            - Timeout protection for subprocess calls (2s)
            - Robust error handling for missing processes
            - Case-insensitive matching for process names
            - Fails safely (returns None on any error)
        """
        try:
            # Get pane PID using existing execute_tmux_command
            result = self.execute_tmux_command(
                ['list-panes', '-F', '#{pane_pid}'],
                session=session,
                window=window
            )

            if not result or result['returncode'] != 0 or not result['stdout'].strip():
                return None

            # One pid per pane; pgrep -P takes a comma-separated list and
            # rejects the newline-separated one list-panes prints.
            pane_pids = ','.join(result['stdout'].split())

            child_result = subprocess.run(
                ['pgrep', '-P', pane_pids],
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=2
            )

            if child_result.returncode != 0:
                return None

            return next(filter(None, map(_process_harness, child_result.stdout.split())), None)

        except (subprocess.TimeoutExpired, Exception):
            # Fail safely - don't report a harness if detection fails
            return None


SCRIPT_INTERPRETERS = frozenset({'node', 'bun', 'deno'})


def _ps_field(pid: str, field: str) -> str:
    result = subprocess.run(['ps', '-p', pid, '-o', f'{field}='],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=2)
    return result.stdout.strip() if result.returncode == 0 else ''


def is_named_process(comm: str, argv, names) -> bool:
    """True when a process IS one of ``names`` (see _named_process)."""
    return _named_process(comm, argv, names) is not None


def _named_process(comm: str, argv, names) -> Optional[str]:
    """The entry of ``names`` a process IS, not one its arguments mention; or None.

    Matches the executable basename (``comm``, or argv[0]) and, for a script
    interpreter such as node, the script basename in argv[1]. A substring
    match on the whole command line made `vim ~/.claude/settings.json` count.
    ``names`` are lowercase; a Windows ``.exe`` suffix is ignored.
    """
    def base(path: str) -> str:
        return os.path.basename(path).lower().removesuffix('.exe')

    comm = base(comm)
    argv = [base(a) for a in list(argv)[:2]]
    for name in (comm, *argv[:1]):
        if name in names:
            return name
    interpreted = comm in SCRIPT_INTERPRETERS or (argv and argv[0] in SCRIPT_INTERPRETERS)
    return argv[1] if interpreted and len(argv) > 1 and argv[1] in names else None


def _process_harness(pid: str) -> Optional[str]:
    """Platform name of the harness the process IS (see is_named_process), or None."""
    from .client import harness_process_names

    names = harness_process_names()
    comm = _ps_field(pid, 'comm')
    # Known limit: args is split on whitespace, so an argv[1] script path
    # containing a space is not matched (comm still covers argv[0]).
    matched = (_named_process(comm, (), names)
               or _named_process(comm, _ps_field(pid, 'args').split(), names))
    return names.get(matched) if matched else None


# Global instance for consistent usage
_tmux_utils = None

def detect_current_tmux_session() -> Optional[str]:
    """Detect current tmux session name from environment.

    Returns the session name of the tmux session this process is running in.
    Useful when you explicitly want to target your own session.

    NOTE: This returns the session of the calling process. If multiple
    windows have Claude sessions, this doesn't tell you which one -
    use tmux_list_windows() to discover all Claude sessions.

    Returns:
        Session name if in tmux, None otherwise.

    Example:
        >>> session = detect_current_tmux_session()
        >>> if session:
        ...     tmux = TmuxUtilities(session_name=session)
        ...     send_text_and_enter(tmux, "hello", window='48')
    """
    location = _tmux_current_location()
    return location[0] if location else None


def _tmux_current_location() -> Optional[Tuple[str, str, str]]:
    """(session, window, pane) of the caller, as tmux itself reports it.

    No -t: display-message then describes the client's own pane, which tmux
    resolves from $TMUX_PANE. The one place detect_tmux_environment,
    detect_current_tmux_session and _tmux_get_current_window ask this.
    """
    try:
        result = subprocess.run(
            [resolve_tmux_binary(), '-u', 'display-message', '-p',
             '#{session_name}:#{window_index}:#{pane_index}'],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=2
        )
    except (subprocess.SubprocessError, OSError):
        return None
    parts = result.stdout.rstrip('\n').rsplit(':', 2)
    if result.returncode != 0 or len(parts) != 3 or not parts[0]:
        return None
    return parts[0], parts[1], parts[2]


def get_tmux_utilities(session_name: Optional[str] = None) -> TmuxUtilities:
    """Get or create tmux utilities instance with session-based caching.

    Args:
        session_name: Target session. Defaults to "autorun" (safe isolation).
                     Pass detect_current_tmux_session() to target your own session.

    Returns:
        TmuxUtilities instance for the specified session.
    """
    global _tmux_utils

    # If no instance exists, create one
    if _tmux_utils is None:
        _tmux_utils = TmuxUtilities(session_name)
        return _tmux_utils

    # If session_name is specified and different from current instance, create new one
    if session_name is not None and session_name != _tmux_utils.session_name:
        _tmux_utils = TmuxUtilities(session_name)

    return _tmux_utils


# ─────────────────────────────────────────────────────────────────────────────
# WindowList API - Simplified tmux window query with pandas-like filtering
# ─────────────────────────────────────────────────────────────────────────────

# Constants (no magic numbers)
HAPPY_TITLE_MARKER = '✳'  # Set by happy-cli MCP tool
DEFAULT_CONTENT_LINES = 0  # Content disabled by default (expensive)
CONTENT_PREVIEW_LENGTH = 500  # For Claude session detection
DEFAULT_CAPTURE_LINES = 100  # For Claude session content analysis

SHELL_COMMANDS = frozenset({
    'zsh', 'bash', 'sh', 'fish', 'login', '-',  # Common shells
    'xonsh', 'ksh', 'csh', 'tcsh',               # Alternative shells
    'dash', 'ash', 'nu', 'pwsh', 'elvish',       # Other shells
})


# ASCII unit separator: titles, paths and session names may contain '|', and a
# '|' separator then gave the wrong field count and dropped the window.
TMUX_FORMAT_SEPARATOR = '\x1f'
TMUX_FORMAT_SEPARATOR_VIS = '\\037'  # how tmux 3.4 prints it
TMUX_WINDOW_FORMAT = TMUX_FORMAT_SEPARATOR.join([
    '#{session_name}',
    '#{window_index}',
    '#{pane_title}',
    '#{pane_current_command}',
    '#{pane_current_path}',
    '#{pane_pid}',
    '#{window_active}',
    '#{window_activity}',
    '#{window_flags}'
])
TMUX_FORMAT_FIELDS = ('session', 'w', 'raw_title', 'cmd', 'path', 'pid', 'active', 'activity', 'flags')


class WindowList(list):
    """Filterable list of window dicts. Extends list - all list ops work.

    Each method returns a NEW WindowList (stateless/immutable pattern).
    Zero external dependencies.

    Example:
        >>> windows = tmux_list_windows()
        >>> windows.filter(cmd='node').contains('title', HAPPY_TITLE_MARKER)
        WindowList([{'session': 'main', 'w': 1, 'title': '✳ Task', ...}])
    """

    def filter(self, **kwargs: Union[Any, Callable[[Any], bool]]) -> 'WindowList':
        """Filter by key=value or key=lambda. Returns NEW WindowList.

        Args:
            **kwargs: key=value for exact match, or key=callable for predicate

        Example:
            .filter(cmd='node')           # Exact match
            .filter(w=1)                  # Window number 1
            .filter(w=lambda x: x > 5)    # Lambda predicate
            .filter(cmd='node', w=1)      # Multiple conditions (AND)
        """
        filtered = list(self)
        for key, val in kwargs.items():
            if callable(val):
                filtered = [w for w in filtered if val(w.get(key))]
            else:
                filtered = [w for w in filtered if w.get(key) == val]
        return WindowList(filtered)

    def contains(self, key: str, substr: str) -> 'WindowList':
        """Filter where key contains substring. Returns NEW WindowList.

        Example:
            .contains('title', HAPPY_TITLE_MARKER)  # Happy-cli sessions
            .contains('path', 'autorun')          # Path contains
        """
        return WindowList([w for w in self if substr in str(w.get(key, ''))])

    def select(self, *keys: str) -> 'WindowList':
        """Select specific keys only. Returns NEW WindowList with subset of keys.

        Example:
            .select('session', 'w', 'title')  # Only these keys in output
        """
        return WindowList([{k: w[k] for k in keys if k in w} for w in self])

    def group_by(self, key: str = 'session') -> Dict[str, 'WindowList']:
        """Group windows by key. Returns dict of WindowLists.

        Useful for LLM-optimized output (reduces token count ~60%).

        Example:
            .group_by('session')  # {'main': WindowList([...]), 'dev': [...]}
        """
        from collections import defaultdict
        groups: Dict[str, WindowList] = defaultdict(WindowList)
        for w in self:
            groups[w.get(key, '')].append(w)
        return dict(groups)

    def first(self) -> Optional[Dict[str, Any]]:
        """Get first item or None. Safe alternative to [0]."""
        return self[0] if self else None

    def to_targets(self) -> List[str]:
        """Get list of tmux targets (session:window format). LLM-friendly.

        Example:
            .to_targets()  # ['main:1', 'main:2', 'dev:1']
        """
        return [f"{w.get('session')}:{w.get('w')}" for w in self]

    def to_compact(self, keys: tuple = ('session', 'w', 'title')) -> 'WindowList':
        """Return minimal representation for LLM consumption.

        Default keys are the most useful for LLM decision-making.

        Example:
            .to_compact()  # Minimal: session, w, title only
            .to_compact(('session', 'w', 'cmd'))  # Custom keys
        """
        return self.select(*keys)

    def to_grouped_compact(self) -> Dict[str, List[Dict[str, Any]]]:
        """Return grouped + compact format. Optimal for LLM output.

        Combines group_by('session') with compact selection.
        Reduces token count ~70% vs full output.

        Example:
            .to_grouped_compact()
            # {'main': [{'w': 1, 'title': '✳ Task'}, {'w': 2, 'title': 'shell'}]}
        """
        return {
            session: [{'w': w['w'], 'title': w.get('title', '')} for w in wins]
            for session, wins in self.group_by('session').items()
        }

    def prompting_user_for_input(self) -> 'WindowList':
        """Return windows prompting user for input (any prompt_type).

        Example:
            windows.prompting_user_for_input()  # All windows with a prompt
            windows.prompting_user_for_input().filter(prompt_type='input')
        """
        return WindowList([w for w in self if w.get('prompt_type') is not None])

    def no_prompt_detected(self) -> 'WindowList':
        """Return windows with no detected Claude Code prompt.

        Note: This doesn't mean Claude is active - window could be idle,
        running another app, or have an unrecognized prompt.

        Example:
            windows.no_prompt_detected()
        """
        return WindowList([w for w in self if w.get('prompt_type') is None])

    def actively_generating(self) -> 'WindowList':
        """Return windows where Claude is actively generating output.

        Filters for windows with is_active=True, meaning Claude is currently
        working (spinner visible, tokens being generated).

        Requires content_lines > 0 when calling tmux_list_windows.

        Example:
            active = windows.actively_generating()
            tmux_dangerous_batch_execute(tmux, 'stop', active)
        """
        return WindowList([w for w in self if w.get('is_active') is True])

    def in_mode(self, mode: str) -> 'WindowList':
        """Return windows in a specific Claude Code mode.

        Args:
            mode: Mode to filter for ('default', 'plan', 'bypass', 'accept_edits')

        Requires content_lines > 0 when calling tmux_list_windows.

        Example:
            plan_windows = windows.in_mode('plan')
            default_windows = windows.in_mode('default')
        """
        return WindowList([w for w in self if w.get('claude_mode') == mode])

    def claude_sessions(self) -> 'WindowList':
        """Return only windows that are Claude Code sessions.

        Filters for windows with is_claude_session=True, meaning the process
        tree contains claude or happy-cli processes.

        Requires content_lines > 0 when calling tmux_list_windows.

        Example:
            claude_only = windows.claude_sessions()
        """
        return WindowList([w for w in self if w.get('is_claude_session') is True])

    def thinking_enabled(self) -> 'WindowList':
        """Return windows with thinking mode enabled.

        Filters for windows with is_thinking=True.

        Requires content_lines > 0 when calling tmux_list_windows.

        Example:
            thinking = windows.thinking_enabled()
        """
        return WindowList([w for w in self if w.get('is_thinking') is True])

    def __repr__(self) -> str:
        """Debug-friendly representation."""
        return f'WindowList({list.__repr__(self)})'


def tmux_list_windows(
    session: Optional[str] = None,
    content_lines: int = DEFAULT_CONTENT_LINES,
    exclude_current: bool = True
) -> WindowList:
    """List all tmux windows as a filterable WindowList.

    Single tmux query per session. Content capture disabled by default (expensive).
    Returns empty WindowList if tmux not running or no windows found.

    Args:
        session: Filter to specific session name (None = all sessions)
        content_lines: Lines to capture per pane (0 = none, >0 = last N lines)
        exclude_current: Skip the window running this script (default: True)

    Returns:
        WindowList of window dicts. Each dict contains:
        - session: str - tmux session name (use with w to form target "session:w")
        - w: int - window index number (short for "window"; tmux #{window_index})
        - title: str - enhanced window title (✳ prefix = happy-cli set title)
        - cmd: str - current foreground command running in pane (e.g., 'node', 'zsh', 'python')
        - path: str - current working directory/cwd of the pane (~ abbreviated)
        - pid: int - process ID of the pane's foreground process
        - active: bool - True if this window is currently selected/visible in its session
        - activity: int - Unix timestamp of last activity in this window
        - flags: str - tmux window flags (* = current, - = last, Z = zoomed, ! = bell)
        - content: str - (only if content_lines > 0) captured terminal output, last N lines
        - prompt_type: str|None - (only if content_lines > 0) detected Claude Code prompt type:
          'input', 'plan_approval', 'tool_permission_yn', 'tool_permission_numbered',
          'question', 'happy_mode_switch', 'happy_remote', 'clarification', 'error_prompt', or None
        - is_active: bool - (only if content_lines > 0) True if Claude is actively generating output

    Example:
        >>> tmux_list_windows()
        WindowList([{'session': 'main', 'w': 1, 'title': '✳ Task', ...}])

        >>> tmux_list_windows().filter(cmd='node')
        >>> tmux_list_windows().group_by('session')
        >>> tmux_list_windows(content_lines=200).filter(prompt_type='input')  # Awaiting input

        >>> for w in tmux_list_windows():
        ...     print(f"{w['session']}:{w['w']} - {w['title']}")

    Raises:
        No exceptions - returns empty WindowList on any error (fail-safe).
    """
    tmux = get_tmux_utilities()
    windows = WindowList()

    # Get current window to exclude (RAII: detect once, use throughout)
    current_session, current_window = _tmux_get_current_window() if exclude_current else ('', '')

    # Get session list (or single session if specified)
    session_names = [session] if session else _tmux_list_sessions(tmux)
    if not session_names:
        return windows  # No sessions - return empty WindowList

    # Single query per session using format string
    for session_name in session_names:
        result = tmux.execute_tmux_command(
            ['list-windows', '-F', TMUX_WINDOW_FORMAT],
            session=session_name
        )
        if not result or result.get('returncode') != 0:
            continue

        # No strip(): Python counts '\x1f' as whitespace and would drop the
        # empty last field (window_flags of a background window).
        for line in result['stdout'].split('\n'):
            if not line:
                continue

            # tmux 3.4 prints list output through utf8_stravisx(VIS_OCTAL), so
            # the separator arrives as the four characters "\037"; tmux 3.5+
            # prints the byte itself.
            parts = line.split(TMUX_FORMAT_SEPARATOR)
            if len(parts) == 1:
                parts = line.split(TMUX_FORMAT_SEPARATOR_VIS)
            if len(parts) != len(TMUX_FORMAT_FIELDS):
                continue  # Malformed line - skip

            data = dict(zip(TMUX_FORMAT_FIELDS, parts))
            win_session = data['session']
            win_index = data['w']

            # Skip current window if requested
            if exclude_current and win_session == current_session and win_index == current_window:
                continue

            # Build window dict
            win = {
                'session': win_session,
                'w': int(win_index),
                'title': _tmux_enhance_title(
                    data['raw_title'], data['cmd'], data['path'],
                    win_session, int(win_index)
                ),
                'cmd': data['cmd'],
                'path': data['path'].replace(os.path.expanduser('~'), '~'),
                'pid': int(data['pid']) if data['pid'].isdigit() else 0,
                'active': data['active'] == '1',  # Is this the current window?
                'activity': int(data['activity']) if data['activity'].isdigit() else 0,  # Unix timestamp
                'flags': data['flags']  # * = current, - = last, etc.
            }

            # Optional: capture pane content and detect state
            if content_lines > 0:
                win['content'] = _tmux_capture_pane(
                    tmux, win_session, win_index, content_lines
                )
                # Detect prompt type for filtering (e.g., windows.filter(prompt_type='input'))
                win['prompt_type'] = tmux_detect_prompt_type(win['content'])
                # Detect if Claude is actively working
                win['is_active'] = tmux_detect_claude_active(win['content'])
                # Detect Claude Code CLI mode (plan/bypass/default/accept_edits)
                win['claude_mode'] = tmux_detect_claude_mode(win['content'])
                # Detect if thinking mode is enabled
                win['is_thinking'] = tmux_detect_claude_thinking_mode(win['content'])
                # Which registered AI harness the pane runs (None: not an AI pane)
                win['harness'] = tmux.pane_harness(win_session, win_index)
                win['is_claude_session'] = win['harness'] is not None

            windows.append(win)

    return windows


def _tmux_get_current_window() -> Tuple[str, str]:
    """Get current tmux session:window. Returns ('', '') if not in tmux."""
    location = _tmux_current_location()
    return (location[0], location[1]) if location else ('', '')


def _tmux_list_sessions(tmux: 'TmuxUtilities') -> List[str]:
    """List all tmux session names. Returns [] if none or error."""
    result = tmux.execute_tmux_command(['list-sessions', '-F', '#{session_name}'])
    if not result or result.get('returncode') != 0:
        return []
    return [s.strip() for s in result['stdout'].strip().split('\n') if s.strip()]


def _tmux_capture_pane(
    tmux: 'TmuxUtilities', session: str, window: str, lines: int
) -> str:
    """Capture last N lines from pane. Returns '' on error."""
    result = tmux.execute_tmux_command(
        ['capture-pane', '-p', '-S', f'-{lines}'],
        session=session, window=window
    )
    return result['stdout'] if result and result.get('returncode') == 0 else ''


def _tmux_enhance_title(
    raw_title: Optional[str], cmd: str, path: str, session: str, window: int
) -> str:
    """Compute best display title with fallback hierarchy.

    Title sources (in priority order):
    1. Happy-cli title (has HAPPY_TITLE_MARKER prefix) - most reliable
    2. Non-empty title different from command - trust as user-set
    3. Command + path (for non-shell processes) - descriptive fallback
    4. session:window - always works

    Note: Cannot read happy-cli session metadata externally.
    The pane_title is our ONLY reliable source for titles.
    """
    # Level 1: Happy-cli explicitly set this title
    if raw_title and HAPPY_TITLE_MARKER in raw_title:
        return raw_title

    # Level 2: Non-empty title that differs from command name (user-set)
    if raw_title and raw_title.strip() and raw_title.strip().lower() != cmd.lower():
        return raw_title

    # Level 3: Command + path for non-shell processes
    if cmd not in SHELL_COMMANDS:
        short_path = path.replace(os.path.expanduser('~'), '~')
        return f'{cmd} ({short_path})'

    # Level 4: Fallback
    return f'{session}:{window}'


# ─────────────────────────────────────────────────────────────────────────────
# PROMPT DETECTION - Claude Code CLI prompt type detection
# ─────────────────────────────────────────────────────────────────────────────

# Prompt type constants
PROMPT_TYPE_PLAN_APPROVAL = 'plan_approval'
PROMPT_TYPE_TOOL_PERMISSION_YN = 'tool_permission_yn'
PROMPT_TYPE_TOOL_PERMISSION_NUMBERED = 'tool_permission_numbered'
PROMPT_TYPE_QUESTION = 'question'
PROMPT_TYPE_INPUT = 'input'
PROMPT_TYPE_HAPPY_MODE_SWITCH = 'happy_mode_switch'
# Happy-cli remote mode - mobile device(s) connected via happy-cli
# CAUTION: Pressing space twice rapidly switches to local mode but DISCONNECTS
# all mobile connections. This is disruptive for mobile users who lose their
# session view. Only switch modes when mobile user has been warned or is not
# actively using the connection. The mobile user will need to reconnect.
PROMPT_TYPE_HAPPY_REMOTE = 'happy_remote'
PROMPT_TYPE_CLARIFICATION = 'clarification'
PROMPT_TYPE_ERROR = 'error_prompt'


def _prompt_lines(content: str) -> List[str]:
    """The last 15 non-blank lines of the last 800 chars, stripped."""
    return [line.strip() for line in content[-800:].split('\n') if line.strip()][-15:]


# Claude Code's choice prompts (tool permission "Do you want to proceed?", plan
# approval, AskUserQuestion) put the ❯ cursor on a numbered option, "❯ 1. Yes",
# sometimes inside a "│" border. The idle input prompt is a ❯ too, so a ❯
# somewhere plus a numbered list in Claude's reply is not a choice prompt.
_CHOICE_CURSOR = re.compile(r'(?:│\s*)?❯\s*\d+\.\s')


def _input_prompt_line(last_lines: List[str]) -> Optional[str]:
    """The main input prompt line among the last 5 of last_lines, or None.

    A bare '>' or a line starting with '❯' (Claude Code's prompt, possibly
    followed by typed or placeholder text) that is not a choice cursor.
    """
    for line in last_lines[-5:]:
        if line == '>' or (line.startswith('❯') and not _CHOICE_CURSOR.match(line)):
            return line
    return None


def tmux_detect_prompt_type(content: str) -> Optional[str]:
    """Detect Claude Code CLI prompt type from terminal content.

    Analyzes the last portion of terminal content to determine if Claude Code
    is waiting for user input and what type of prompt is displayed.

    Args:
        content: Terminal content string (typically from tmux capture-pane)

    Returns:
        Prompt type string constant, or None if no prompt detected:
        - 'plan_approval': Plan/feature approval prompt (Would you like to proceed?)
        - 'tool_permission_yn': Yes/No tool permission prompt ([Y/n], (yes/no))
        - 'tool_permission_numbered': Numbered option permission prompt ([1] [2])
        - 'question': ❯ cursor on a numbered option ("❯ 1. Yes"): AskUserQuestion
          or the "Do you want to proceed?" tool permission dialog
        - 'input': Main input prompt (standalone > at line end)
        - 'happy_mode_switch': Happy-cli mode switch prompt (📱 Press space)
        - 'happy_remote': Happy-cli remote mode - mobile device connected.
          CAUTION: Double-space switches to local mode but DROPS mobile connections.
          Mobile user loses session view and must reconnect. Only switch when
          mobile user is warned or inactive.
        - 'clarification': Natural language question from Claude (ends with ?)
        - 'error_prompt': Error state requiring user action

    Example:
        >>> windows = tmux_list_windows(content_lines=200)
        >>> for w in windows:
        ...     prompt = tmux_detect_prompt_type(w.get('content', ''))
        ...     if prompt:
        ...         print(f"{w['session']}:{w['w']} - {prompt}")

        >>> # Find all windows awaiting input
        >>> awaiting = [w for w in windows if tmux_detect_prompt_type(w.get('content', ''))]
    """
    if not content:
        return None

    last_lines = _prompt_lines(content)
    last_text = '\n'.join(last_lines)
    choice = any(_CHOICE_CURSOR.match(line) for line in last_lines)

    # 1. Plan/Feature approval - "Would you like to proceed?" with selector
    if choice and 'Would you like to proceed?' in last_text:
        return PROMPT_TYPE_PLAN_APPROVAL

    # 2. Tool permission - Yes/No patterns
    yn_pattern = r'\[Y/n\]|\[y/N\]|\(yes/no\)|\(y/n\)'
    if re.search(yn_pattern, last_text, re.IGNORECASE):
        return PROMPT_TYPE_TOOL_PERMISSION_YN

    # 3. Tool permission - Numbered options ([1] Allow once, [2] Allow always)
    if re.search(r'\[1\].*\[2\]', last_text):
        return PROMPT_TYPE_TOOL_PERMISSION_NUMBERED

    # 4. AskUserQuestion or the tool permission dialog - "❯ 1." cursor
    if choice:
        return PROMPT_TYPE_QUESTION

    # 5. Main input prompt - standalone > or ❯ (Claude Code uses Unicode ❯)
    if _input_prompt_line(last_lines) is not None:
        return PROMPT_TYPE_INPUT

    # 6. Happy-cli mode prompts
    if '📱 Press space' in last_text:
        # Remote mode: shows "switch to local mode" - mobile connection active
        # WARNING: Double-space switches to local mode but drops mobile connections
        if 'switch to local mode' in last_text:
            return PROMPT_TYPE_HAPPY_REMOTE
        return PROMPT_TYPE_HAPPY_MODE_SWITCH

    # 7. Error state prompts
    error_patterns = [
        r'Press Enter to continue',
        r'Error:.*\?$',
        r'failed.*retry\?',
    ]
    for pattern in error_patterns:
        if re.search(pattern, last_text, re.IGNORECASE):
            return PROMPT_TYPE_ERROR

    # 8. Clarification question - natural question from Claude
    for line in reversed(last_lines[-5:]):
        if line.endswith('?') and not line.startswith(('#', '//', '⎿', '│')):
            # Filter out very short or very long lines
            if 10 < len(line) < 200:
                return PROMPT_TYPE_CLARIFICATION

    return None


def tmux_detect_claude_active(content: str) -> bool:
    """Detect if Claude is actively generating output.

    Looks for happy-cli status indicators when Claude is working:
    - "esc to interrupt" - always present when active
    - "↓ N tokens" or "↑ N tokens" - token counter in status

    Examples:
        "✳ Schlepping… (esc to interrupt · 7s · ↓ 44 tokens · thinking)"
        "· Schlepping… (esc to interrupt · 1m 20s · ↑ 3.6k tokens · thought for 2s)"

    Args:
        content: Terminal content string

    Returns:
        True if Claude appears to be actively working, False otherwise.
    """
    if not content:
        return False

    # Check last 15 lines for status line
    # Real status line starts with a status symbol: ✳ · ✻ ✢ ∴ etc.
    # Example: "✳ Schlepping… (esc to interrupt · 7s · ↓ 44 tokens · thinking)"
    # Pasted content is typically indented with spaces
    status_symbols = (
        '✳·✻✢✶✽✼✾'       # Claude Code spinner: observed ✢·✻✳✶✽ + likely ✼✾
        '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'  # dots spinner (default)
        '⣾⣽⣻⢿⡿⣟⣯⣷'    # dots2 spinner
        '⠄⠆⠇⠋⠙⠚⠞⠖⠦⠴⠲⠳⠓⠸⠰⠠'  # dots3/4 variants
        '✶✸✹✺✷'          # star spinner
        '✔✖⚠ℹ'           # log-symbols status
        '◐◓◑◒◴◷◶◵'       # circle spinners
        '▖▘▝▗▌▀▐▄'       # box spinners
    )
    last_lines = content.rstrip().split('\n')[-15:]

    for line in last_lines:
        stripped = line.lstrip()
        # Check if line starts with status symbol (allowing minimal leading whitespace)
        if stripped and stripped[0] in status_symbols:
            if 'esc to interrupt' in line and re.search(r'[\d.]+k?\s*tokens', line):
                return True

    return False


# ─────────────────────────────────────────────────────────────────────────────
# CLAUDE CODE MODE DETECTION - Status bar mode indicators
# ─────────────────────────────────────────────────────────────────────────────

# Claude Code CLI modes (shown in status bar, cycled with Tab/Shift+Tab)
# Order matches Shift+Tab cycle order
CLAUDE_MODE_DEFAULT = 'default'  # Blank status bar - normal operation
CLAUDE_MODE_PLAN = 'plan'  # "plan mode on" - planning without execution
CLAUDE_MODE_BYPASS = 'bypass'  # "bypass permissions on" - requires --dangerously-skip-permissions
CLAUDE_MODE_ACCEPT_EDITS = 'accept_edits'  # "accept edits on" - auto-accept file edits

# Note: bypass mode only available if --dangerously-skip-permissions was passed


def tmux_detect_claude_mode(content: str) -> str:
    """Detect current Claude Code CLI mode from terminal content.

    Modes are shown in the status bar and can be cycled with Tab (toggle
    thinking) or Shift+Tab (cycle through modes).

    Args:
        content: Terminal content string

    Returns:
        Mode constant:
        - 'default': Normal operation (blank status)
        - 'plan': Plan mode on - planning without execution
        - 'bypass': Bypass permissions on (only if --dangerously-skip-permissions)
        - 'accept_edits': Accept edits on - auto-accept file edits

    Example:
        >>> mode = tmux_detect_claude_mode(window['content'])
        >>> if mode == CLAUDE_MODE_PLAN:
        ...     print("Plan mode active")
    """
    if not content:
        return CLAUDE_MODE_DEFAULT

    # Check last 20 lines for mode indicators in status bar
    last_lines = content.rstrip().split('\n')[-20:]
    last_text = '\n'.join(last_lines).lower()

    # Check for mode indicators (case-insensitive)
    if 'plan mode on' in last_text:
        return CLAUDE_MODE_PLAN
    if 'bypass permissions on' in last_text:
        return CLAUDE_MODE_BYPASS
    if 'accept edits on' in last_text:
        return CLAUDE_MODE_ACCEPT_EDITS

    return CLAUDE_MODE_DEFAULT


def tmux_detect_claude_thinking_mode(content: str) -> bool:
    """Detect if Claude Code is in thinking mode.

    Thinking mode shows extended reasoning in the output. When enabled,
    "thinking" appears in the status bar during generation.

    Status bar format example:
        ✳ Schlepping… (esc to interrupt · 7s · ↓ 44 tokens · thinking)

    Args:
        content: Terminal content string

    Returns:
        True if thinking mode is active, False otherwise

    Example:
        >>> is_thinking = tmux_detect_claude_thinking_mode(window['content'])
        >>> if is_thinking:
        ...     print("Extended thinking enabled")
    """
    if not content:
        return False

    # Check last 5 lines for "thinking" in status bar
    # Must also have "tokens" or "esc to interrupt" to distinguish from
    # regular text that might contain the word "thinking"
    lines = content.strip().split('\n')
    for line in reversed(lines[-5:]):
        line_lower = line.lower()
        if 'thinking' in line_lower and ('tokens' in line_lower or 'esc to interrupt' in line_lower):
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# CLAUDE CODE CLI CONTROL - Key sequences for controlling Claude Code
# ─────────────────────────────────────────────────────────────────────────────


def send_text_and_enter(
    tmux: 'TmuxUtilities',
    text: str,
    session: Optional[str] = None,
    window: Optional[str] = None,
    pane: Optional[str] = None,
    delay_ms: int = 100
) -> bool:
    """Send text to Claude Code CLI followed by Enter.

    IMPORTANT: Text and Enter (C-m) must be sent as separate tmux commands
    with a small delay between them. This is required because:
    1. tmux send-keys handles control sequences differently from regular text
    2. The target terminal needs time to process the text before Enter

    Args:
        tmux: TmuxUtilities instance
        text: Text to send (command, prompt, etc.)
        session: Target session (uses tmux default if None)
        window: Target window
        pane: Target pane
        delay_ms: Delay between text and Enter in milliseconds (default 100ms)

    Returns:
        True if both sends succeeded, False otherwise

    Example:
        >>> tmux = get_tmux_utilities(detect_current_tmux_session())
        >>> send_text_and_enter(tmux, "/exit", window='48')  # Exit Claude CLI
        >>> send_text_and_enter(tmux, "continue", window='48')  # Continue prompt
    """
    import time
    # Send text first
    text_ok = tmux.send_keys(text, session, window, pane)
    if not text_ok:
        return False
    # Brief delay for terminal to process text
    time.sleep(delay_ms / 1000.0)
    # Send Enter as separate command (CRITICAL: must be separate)
    return tmux.send_keys('C-m', session, window, pane)


def check_safe_to_send(content: str) -> tuple[bool, str]:
    """Check if it's safe to send a message to a Claude Code window.

    Performs safety checks to avoid interfering with:
    - Claude actively generating output
    - User actively typing in the input field
    - Non-input states (permission prompts, plan approval, etc.)

    Args:
        content: Terminal content from the window

    Returns:
        Tuple of (is_safe, reason):
        - (True, "ready") - Safe to send, input prompt is empty
        - (False, "active") - Claude is actively generating
        - (False, "no_prompt") - No input prompt detected
        - (False, "user_typing") - User appears to be typing
        - (False, "special_prompt") - Special prompt requiring specific response

    Example:
        >>> safe, reason = check_safe_to_send(window['content'])
        >>> if safe:
        ...     send_text_and_enter(tmux, "continue", window='48')
        >>> else:
        ...     print(f"Not safe to send: {reason}")
    """
    if not content:
        return False, "no_content"

    # Check if Claude is actively generating
    if tmux_detect_claude_active(content):
        return False, "active"

    # Check prompt type
    prompt_type = tmux_detect_prompt_type(content)

    if prompt_type is None:
        return False, "no_prompt"

    # Special prompts require specific responses, not arbitrary text
    if prompt_type in [
        PROMPT_TYPE_PLAN_APPROVAL,
        PROMPT_TYPE_TOOL_PERMISSION_YN,
        PROMPT_TYPE_TOOL_PERMISSION_NUMBERED,
        PROMPT_TYPE_QUESTION,
        PROMPT_TYPE_HAPPY_MODE_SWITCH,
        PROMPT_TYPE_HAPPY_REMOTE,
    ]:
        return False, f"special_prompt:{prompt_type}"

    # For regular input prompt, text after the prompt character means the user
    # may be typing. Same line tmux_detect_prompt_type matched, so the two agree.
    if prompt_type == PROMPT_TYPE_INPUT:
        if _input_prompt_line(_prompt_lines(content))[1:].strip():
            return False, "user_typing"
        return True, "ready"

    # Error prompts or clarification - might be ok but be cautious
    if prompt_type in [PROMPT_TYPE_ERROR, PROMPT_TYPE_CLARIFICATION]:
        return False, f"special_prompt:{prompt_type}"

    return False, "unknown_state"


def send_message_to_claude(
    tmux: 'TmuxUtilities',
    message: str,
    session: Optional[str] = None,
    window: Optional[str] = None,
    pane: Optional[str] = None,
    force: bool = False,
    delay_ms: int = 100
) -> tuple[bool, str]:
    """Safely send a message to a Claude Code window.

    Performs safety checks before sending to avoid interfering with:
    - Claude actively generating output
    - User actively typing in the input field
    - Non-input states (permission prompts, etc.)

    Args:
        tmux: TmuxUtilities instance
        message: Message to send
        session: Target session
        window: Target window
        pane: Target pane
        force: If True, skip safety checks (use with caution!)
        delay_ms: Delay between text and Enter (default 100ms)

    Returns:
        Tuple of (success, reason):
        - (True, "sent") - Message was sent successfully
        - (False, reason) - Message not sent, reason explains why

    Example:
        >>> tmux = get_tmux_utilities(detect_current_tmux_session())
        >>> success, reason = send_message_to_claude(tmux, "continue", window='48')
        >>> if not success:
        ...     print(f"Could not send: {reason}")
    """
    if not force:
        # Capture current content to check state
        result = tmux.execute_tmux_command(
            ['capture-pane', '-p', '-S', '-50'],
            session, window, pane
        )
        if not result or result['returncode'] != 0:
            return False, "capture_failed"

        content = result.get('stdout', '')
        safe, reason = check_safe_to_send(content)
        if not safe:
            return False, reason

    # Safe to send (or force=True)
    success = send_text_and_enter(tmux, message, session, window, pane, delay_ms)
    return (True, "sent") if success else (False, "send_failed")


def send_escape(
    tmux: 'TmuxUtilities',
    session: Optional[str] = None,
    window: Optional[str] = None,
    pane: Optional[str] = None
) -> bool:
    """Send Escape to stop Claude execution.

    Pressing Escape once while Claude is generating will interrupt and
    stop the current generation.

    Args:
        tmux: TmuxUtilities instance
        session: Target session
        window: Target window
        pane: Target pane

    Returns:
        True if send succeeded
    """
    return tmux.send_keys('Escape', session, window, pane)


def send_ctrl_c_twice(
    tmux: 'TmuxUtilities',
    session: Optional[str] = None,
    window: Optional[str] = None,
    pane: Optional[str] = None,
    delay_ms: int = 100
) -> bool:
    """Send Ctrl+C twice in rapid succession to exit Claude Code CLI.

    Double Ctrl+C is the keyboard shortcut to exit the Claude Code CLI
    entirely (equivalent to /exit command).

    Args:
        tmux: TmuxUtilities instance
        session: Target session
        window: Target window
        pane: Target pane
        delay_ms: Delay between Ctrl+C presses (default 100ms)

    Returns:
        True if both sends succeeded
    """
    import time
    first = tmux.send_keys('C-c', session, window, pane)
    if not first:
        return False
    time.sleep(delay_ms / 1000.0)
    return tmux.send_keys('C-c', session, window, pane)


def send_tab(
    tmux: 'TmuxUtilities',
    session: Optional[str] = None,
    window: Optional[str] = None,
    pane: Optional[str] = None
) -> bool:
    """Send Tab to toggle thinking mode.

    Tab alternates between thinking and non-thinking modes in Claude Code CLI.

    Args:
        tmux: TmuxUtilities instance
        session: Target session
        window: Target window
        pane: Target pane

    Returns:
        True if send succeeded
    """
    return tmux.send_keys('Tab', session, window, pane)


def send_shift_tab(
    tmux: 'TmuxUtilities',
    session: Optional[str] = None,
    window: Optional[str] = None,
    pane: Optional[str] = None
) -> bool:
    """Send Shift+Tab to cycle through Claude Code modes.

    Shift+Tab cycles through: default -> plan mode -> accept edits -> default
    (bypass mode only available if --dangerously-skip-permissions was passed)

    Args:
        tmux: TmuxUtilities instance
        session: Target session
        window: Target window
        pane: Target pane

    Returns:
        True if send succeeded
    """
    # BTab is tmux's name for Shift+Tab (Back Tab)
    return tmux.send_keys('BTab', session, window, pane)


def send_exit_command(
    tmux: 'TmuxUtilities',
    session: Optional[str] = None,
    window: Optional[str] = None,
    pane: Optional[str] = None
) -> bool:
    """Send /exit command to gracefully exit Claude Code CLI.

    This is equivalent to Ctrl+C twice but uses the explicit command.

    Args:
        tmux: TmuxUtilities instance
        session: Target session
        window: Target window
        pane: Target pane

    Returns:
        True if send succeeded
    """
    return send_text_and_enter(tmux, '/exit', session, window, pane)


def cycle_to_mode(
    tmux: 'TmuxUtilities',
    target_mode: str,
    current_content: str,
    session: Optional[str] = None,
    window: Optional[str] = None,
    pane: Optional[str] = None,
    max_cycles: int = 5
) -> bool:
    """Cycle through modes until reaching target mode.

    Uses Shift+Tab to cycle through modes. Will not cycle more than
    max_cycles times to prevent infinite loops.

    NOTE: bypass mode cannot be reached by cycling - it requires
    --dangerously-skip-permissions flag at startup.

    Args:
        tmux: TmuxUtilities instance
        target_mode: Target mode constant (CLAUDE_MODE_*)
        current_content: Current terminal content for mode detection
        session: Target session
        window: Target window
        pane: Target pane
        max_cycles: Maximum Shift+Tab presses (default 5)

    Returns:
        True if target mode reached, False if max cycles exceeded or error

    Example:
        >>> cycle_to_mode(tmux, CLAUDE_MODE_PLAN, window['content'])
    """
    import time

    if target_mode == CLAUDE_MODE_BYPASS:
        # bypass mode cannot be cycled to - requires startup flag
        return False

    current_mode = tmux_detect_claude_mode(current_content)
    if current_mode == target_mode:
        return True

    for _ in range(max_cycles):
        if not send_shift_tab(tmux, session, window, pane):
            return False
        # Brief delay to allow mode switch
        time.sleep(0.2)
        content = _tmux_capture_pane(tmux, session, window, DEFAULT_CAPTURE_LINES)
        if tmux_detect_claude_mode(content) == target_mode:
            return True

    return False


def find_windows_awaiting_input(
    windows: Optional['WindowList'] = None,
    content_lines: int = DEFAULT_CAPTURE_LINES
) -> 'WindowList':
    """Find all windows that are waiting for user input.

    Convenience function that combines tmux_list_windows with detect_prompt_type
    to return only windows with active prompts.

    Args:
        windows: Optional pre-fetched WindowList. If None, fetches fresh data.
        content_lines: Lines to capture if fetching fresh (default: DEFAULT_CAPTURE_LINES)

    Returns:
        WindowList of windows awaiting input, each with additional 'prompt_type' key.

    Example:
        >>> awaiting = find_windows_awaiting_input()
        >>> for w in awaiting:
        ...     print(f"{w['session']}:{w['w']} - {w['prompt_type']}")
    """
    if windows is None:
        windows = tmux_list_windows(content_lines=content_lines)

    result = WindowList()
    for w in windows:
        prompt_type = tmux_detect_prompt_type(w.get('content', ''))
        if prompt_type:
            w_copy = dict(w)
            w_copy['prompt_type'] = prompt_type
            result.append(w_copy)

    return result


def tmux_get_claude_window_status(
    tmux: 'TmuxUtilities',
    session: str,
    window: str,
    pane: Optional[str] = None,
    content_lines: int = DEFAULT_CAPTURE_LINES
) -> Dict[str, Any]:
    """Get current status of a Claude Code window.

    Captures window content and analyzes it to return comprehensive status
    including mode, thinking state, activity, and prompt type.

    Args:
        tmux: TmuxUtilities instance
        session: Target session name
        window: Target window index
        pane: Target pane (optional)
        content_lines: Lines to capture (default: DEFAULT_CAPTURE_LINES)

    Returns:
        Dict with status information:
        {
            'claude_mode': str,      # 'default', 'plan', 'bypass', 'accept_edits'
            'is_thinking': bool,     # thinking mode enabled
            'is_active': bool,       # actively generating
            'prompt_type': str|None, # 'input', 'plan_approval', etc.
            'content': str,          # captured content (if capture succeeded)
            'success': bool          # whether capture succeeded
        }

    Example:
        >>> status = tmux_get_claude_window_status(tmux, 'main', '5')
        >>> if status['success']:
        ...     print(f"Mode: {status['claude_mode']}, Active: {status['is_active']}")
    """
    result = {
        'claude_mode': CLAUDE_MODE_DEFAULT,
        'is_thinking': False,
        'is_active': False,
        'prompt_type': None,
        'content': '',
        'success': False
    }

    # Capture window content
    capture_result = tmux.execute_tmux_command(
        ['capture-pane', '-p', '-S', f'-{content_lines}'],
        session, window, pane
    )

    if not capture_result or capture_result.get('returncode') != 0:
        return result

    content = capture_result.get('stdout', '')
    result['content'] = content
    result['success'] = True

    # Analyze content
    result['claude_mode'] = tmux_detect_claude_mode(content)
    result['is_thinking'] = tmux_detect_claude_thinking_mode(content)
    result['is_active'] = tmux_detect_claude_active(content)
    result['prompt_type'] = tmux_detect_prompt_type(content)

    return result


def tmux_get_claude_window_mode(
    tmux: 'TmuxUtilities',
    session: str,
    window: str,
    pane: Optional[str] = None
) -> str:
    """Get current Claude Code mode for a window.

    Convenience wrapper around tmux_get_claude_window_status that returns just the mode.

    Args:
        tmux: TmuxUtilities instance
        session: Target session name
        window: Target window index
        pane: Target pane (optional)

    Returns:
        Mode string: 'default', 'plan', 'bypass', or 'accept_edits'
        Returns 'default' if capture fails.

    Example:
        >>> mode = tmux_get_claude_window_mode(tmux, 'main', '5')
        >>> if mode == CLAUDE_MODE_PLAN:
        ...     print("Window is in plan mode")
    """
    status = tmux_get_claude_window_status(tmux, session, window, pane, content_lines=50)
    return status['claude_mode']


# ─────────────────────────────────────────────────────────────────────────────
# BATCH WINDOW ACTIONS - Execute actions on multiple windows
# ─────────────────────────────────────────────────────────────────────────────
#
# ⚠️  WARNING: DANGEROUS BATCH OPERATIONS ⚠️
#
# The tmux_dangerous_batch_execute() function can send commands to MULTIPLE
# windows simultaneously. This is powerful but risky:
#
# RISKS:
#   - Sending to wrong windows can disrupt ongoing work
#   - No undo - once sent, commands execute immediately
#   - May interrupt active Claude Code sessions unexpectedly
#   - Could cause data loss if sessions are not in expected state
#
# SAFE USAGE:
#   1. ALWAYS verify targets first: print the windows you're targeting
#   2. Use .prompting_user_for_input() to target only idle sessions
#   3. Start with force=False (default) to skip active sessions
#   4. Test with a single window before batch operations
#   5. Have emergency stop ready: tmux_dangerous_batch_execute(tmux, 'escape', targets)
#
# EXAMPLE SAFE WORKFLOW:
#   windows = tmux_list_windows(content_lines=100)
#   targets = windows.prompting_user_for_input()  # Only idle sessions
#   print(f"Will target: {[f'{w['session']}:{w['w']}' for w in targets]}")
#   # Verify the list looks correct, then:
#   result = tmux_dangerous_batch_execute(tmux, 'continue', targets)
#
# ─────────────────────────────────────────────────────────────────────────────

# Action constants for tmux_dangerous_batch_execute
ACTION_SEND = 'send'
ACTION_MESSAGE = 'message'
ACTION_CONTINUE = 'continue'
ACTION_ESCAPE = 'escape'
ACTION_STOP = 'stop'
ACTION_EXIT = 'exit'
ACTION_KILL = 'kill'
ACTION_TOGGLE_THINKING = 'toggle_thinking'
ACTION_CYCLE_MODE = 'cycle_mode'
ACTION_SET_MODE = 'set_mode'


def _tmux_normalize_targets(
    targets: Union['WindowList', List[Dict], Dict, str]
) -> List[Tuple[str, str]]:
    """Normalize various target formats to list of (session, window) tuples.

    Args:
        targets: Windows to target in various formats:
            - WindowList from tmux_list_windows()
            - List of window dicts with 'session' and 'w' keys
            - Single window dict
            - Target string like "main:5" or "main:5.0"

    Returns:
        List of (session, window) tuples

    Example:
        >>> _tmux_normalize_targets("main:5")
        [('main', '5')]
        >>> _tmux_normalize_targets({'session': 'main', 'w': 5})
        [('main', '5')]
    """
    result = []

    # String target like "main:5" or "main:5.0"
    if isinstance(targets, str):
        parts = targets.split(':')
        if len(parts) >= 2:
            session = parts[0]
            # Handle window.pane format - extract just window
            window = parts[1].split('.')[0]
            result.append((session, window))
        return result

    # Single dict
    if isinstance(targets, dict):
        targets = [targets]

    # List of dicts or WindowList
    for t in targets:
        if isinstance(t, dict) and 'session' in t and 'w' in t:
            result.append((t['session'], str(t['w'])))

    return result


def tmux_dangerous_batch_execute(
    tmux: 'TmuxUtilities',
    action: str,
    targets: Union['WindowList', List[Dict], Dict, str],
    message: Optional[str] = None,
    force: bool = False,
    delay_ms: int = 100
) -> Dict[str, Any]:
    """Execute an action on one or more Claude Code windows.

    ⚠️  WARNING: DANGEROUS BATCH OPERATION ⚠️

    This function sends commands to MULTIPLE windows simultaneously.
    See section header for detailed safety guidelines.

    BEFORE USING:
    1. Verify your targets are correct
    2. Use .prompting_user_for_input() to avoid disrupting active work
    3. Test with a single window first
    4. Have force=False (default) to skip active sessions

    Args:
        tmux: TmuxUtilities instance
        action: Action to perform:
            - 'send' or 'message': Send custom message (requires message param)
            - 'continue': Send "continue" to resume work
            - 'escape' or 'stop': Stop generation (Escape key)
            - 'exit': Exit CLI cleanly (/exit command)
            - 'kill': Force exit (Ctrl+C twice)
            - 'toggle_thinking': Toggle thinking mode (Tab)
            - 'cycle_mode': Cycle to next mode (Shift+Tab)
            - 'set_mode': Set specific mode (requires message='plan'|'default'|'accept_edits')
        targets: Windows to target:
            - WindowList from tmux_list_windows()
            - List of window dicts with 'session' and 'w' keys
            - Single window dict
            - Target string like "main:5"
        message: Message text for 'send' action, or mode name for 'set_mode'
        force: Skip safety checks for send/continue actions (⚠️  use with caution)
        delay_ms: Delay between text and Enter for message actions (default 100ms)

    Returns:
        Dict with results:
        {
            'success_count': int,
            'failure_count': int,
            'results': [
                {'target': 'main:5', 'success': True, 'reason': 'sent'},
                {'target': 'main:7', 'success': False, 'reason': 'active'}
            ]
        }

    Example - Safe workflow:
        >>> # Get windows and filter to only those awaiting input
        >>> windows = tmux_list_windows(content_lines=100)
        >>> targets = windows.prompting_user_for_input()
        >>> print(f"Targeting: {[f'{w['session']}:{w['w']}' for w in targets]}")
        >>> # Verify the list, then execute:
        >>> result = tmux_dangerous_batch_execute(tmux, 'continue', targets)
        >>> print(f"Sent to {result['success_count']} windows")

    Example - Emergency stop all active:
        >>> active = tmux_list_windows(content_lines=100).actively_generating()
        >>> tmux_dangerous_batch_execute(tmux, 'escape', active)
    """
    normalized = _tmux_normalize_targets(targets)
    results = []
    success_count = 0
    failure_count = 0

    action_lower = action.lower()

    for session, window in normalized:
        target_str = f"{session}:{window}"
        success = False
        reason = "unknown"

        try:
            if action_lower in (ACTION_SEND, ACTION_MESSAGE):
                if not message:
                    success = False
                    reason = "no_message"
                else:
                    success, reason = send_message_to_claude(
                        tmux, message, session, window, None, force, delay_ms
                    )

            elif action_lower == ACTION_CONTINUE:
                success, reason = send_message_to_claude(
                    tmux, "continue", session, window, None, force, delay_ms
                )

            elif action_lower in (ACTION_ESCAPE, ACTION_STOP):
                success = send_escape(tmux, session, window, None)
                reason = "sent" if success else "failed"

            elif action_lower == ACTION_EXIT:
                success = send_exit_command(tmux, session, window, None)
                reason = "sent" if success else "failed"

            elif action_lower == ACTION_KILL:
                success = send_ctrl_c_twice(tmux, session, window, None)
                reason = "sent" if success else "failed"

            elif action_lower == ACTION_TOGGLE_THINKING:
                success = send_tab(tmux, session, window, None)
                reason = "sent" if success else "failed"

            elif action_lower == ACTION_CYCLE_MODE:
                success = send_shift_tab(tmux, session, window, None)
                reason = "sent" if success else "failed"

            elif action_lower == ACTION_SET_MODE:
                if not message:
                    success = False
                    reason = "no_mode_specified"
                else:
                    current = _tmux_capture_pane(
                        tmux, session, window, DEFAULT_CAPTURE_LINES
                    )
                    success = cycle_to_mode(tmux, message, current, session, window, None)
                    reason = "cycled" if success else "failed"

            else:
                reason = f"unknown_action:{action}"

        except Exception as e:
            success = False
            reason = f"error:{str(e)}"

        if success:
            success_count += 1
        else:
            failure_count += 1

        results.append({
            'target': target_str,
            'success': success,
            'reason': reason
        })

    return {
        'success_count': success_count,
        'failure_count': failure_count,
        'results': results
    }


# ---------------------------------------------------------------------------
# Claude/AI session tab discovery and batch command execution
# ---------------------------------------------------------------------------

def discover_claude_sessions(tmux: 'TmuxUtilities') -> 'list[dict]':
    """Discover all Claude sessions across tmux windows.

    Uses tmux_list_windows() for a single-query window scan, then filters to
    windows running a Claude/AI process via is_claude_session(). Populates
    the 'tmux_target' key (format: 'session:window') used by
    execute_session_selections() and send_to_session().

    Returns list of session dicts with tmux_target, session_name, window_id,
    content, content_preview, title, cmd, path, pid, active, activity, flags,
    branch, and the prompt_type and is_active tmux_list_windows() detected.
    """
    all_windows = tmux_list_windows(content_lines=DEFAULT_CAPTURE_LINES)
    sessions = []
    for win in all_windows:
        if tmux.is_claude_session(win['session'], str(win['w'])):
            content = win.get('content', '')
            sessions.append({
                'session_name': win['session'],
                'window_id': str(win['w']),
                'tmux_target': f"{win['session']}:{win['w']}",
                'content': content,
                'content_preview': content[:CONTENT_PREVIEW_LENGTH],
                'title': win.get('title', ''),
                'cmd': win.get('cmd', ''),
                'path': win.get('path', ''),
                'pid': win.get('pid', 0),
                'active': win.get('active', False),
                'activity': win.get('activity', 0),
                'flags': win.get('flags', ''),
                'branch': win.get('branch'),
                'prompt_type': win.get('prompt_type'),
                'is_active': win.get('is_active', False),
                'harness': win.get('harness'),
            })
    return sessions


def _send_result(session: dict, command: str, send: Callable[[str, str], bool]) -> dict:
    """Run send(session_name, window_id) for session['tmux_target'] and report it."""
    target = session['tmux_target']
    session_name, window_id = target.split(':', 1)
    try:
        success = bool(send(session_name, window_id))
    except Exception as e:
        return {'target': target, 'command': command, 'success': False, 'error': str(e)}
    return {
        'target': target,
        'command': command,
        'success': success,
        'error': None if success else 'send_keys failed',
    }


def send_to_session(tmux: 'TmuxUtilities', session: dict, cmd: str) -> dict:
    """Send a command to a tmux session with send_text_and_enter.

    Args:
        tmux:    TmuxUtilities instance
        session: Session dict with 'tmux_target' key ('session:window' format)
        cmd:     Command string to send

    Returns:
        {'target': str, 'command': str, 'success': bool, 'error': str|None};
        success is False when either the text or the Enter was not delivered.
    """
    return _send_result(session, cmd, lambda s, w: send_text_and_enter(tmux, cmd, s, w))


# Default-action labels (tmux_tab_ai_session_status.get_default_actions) that
# name something to send. The others ('let it work', 'new task', 'status', ...)
# are advice for a person; typing one would submit it to Claude as a prompt.
_DEFAULT_ACTION_SENDERS: Dict[str, Callable[..., bool]] = {
    'continue': lambda tmux, s, w: send_text_and_enter(tmux, 'continue', s, w),
    'interrupt': lambda tmux, s, w: send_escape(tmux, s, w),
}


def _run_default_action(tmux: 'TmuxUtilities', session: dict, letter: str) -> dict:
    label = (session.get('actions') or ['continue'])[0]
    sender = _DEFAULT_ACTION_SENDERS.get(label.strip().lower())
    if sender is None:
        return {'target': session['tmux_target'], 'command': label, 'success': False,
                'error': f"'{label}' is advice, not a command; use {letter}:<command>"}
    return _send_result(session, label, lambda s, w: sender(tmux, s, w))


def execute_session_selections(selection_str: str, sessions: list,
                               tmux: 'TmuxUtilities | None' = None,
                               command: Optional[str] = None) -> 'list[dict]':
    """Execute commands on selected Claude sessions using the /ar:tabs selection syntax.

    Selection syntax (same as /ar:tabs command interface):
      'A,C' or 'AC'          Execute each session's default action (sessions[i]['actions'][0]);
                             only 'continue' and 'interrupt' (Escape) are sent, other
                             labels are refused per session
      'A:git status, B:pwd'  Execute custom commands per session
      'all:continue'         Execute on every session in the list
      'awaiting:continue'    Execute only on sessions where awaiting=True

    A group of letters must name distinct existing sessions, and a command
    takes exactly one letter. If any group fails that ('awaiting', 'all',
    'AA', 'AB:pwd') or has a blank command ('all:', 'A: ', a whitespace
    command=), nothing is sent and every bad group is reported.

    Args:
        selection_str: Selection string using the syntax above
        sessions:      List of session dicts from discover_claude_sessions()
        tmux:          TmuxUtilities instance; creates one if None
        command:       Sent instead of the default action for letter groups
                       ('B,C'); a bare 'all' or 'awaiting' becomes 'all:<command>'

    Returns:
        List of result dicts: [{'target', 'command', 'success', 'error'}, ...]
        Caller is responsible for formatting (see tmux_tab_ai_session_status.format_execution_results).
    """
    if tmux is None:
        tmux = get_tmux_utilities()

    if command and selection_str.strip().lower() in ('all', 'awaiting'):
        selection_str = f'{selection_str.strip()}:{command}'

    # An empty command would type nothing and press Enter in every target.
    empty = (" has an empty command; nothing was sent. Use A:<command>, all:<command>, "
             "awaiting:<command>, or letters alone (A,C) for each session's default action")

    for group in ('all', 'awaiting'):
        if selection_str.lower().startswith(f'{group}:'):
            cmd = selection_str[len(group) + 1:].strip()
            if not cmd:
                return [{'target': selection_str, 'command': '', 'success': False,
                         'error': f"'{selection_str}'{empty}"}]
            return [send_to_session(tmux, s, cmd) for s in sessions
                    if group == 'all' or s.get('awaiting')]

    plan = []  # (letter, session, command or None for the default action)
    errors = []
    for part in selection_str.split(','):
        part = part.strip()
        if not part:
            continue
        letters, has_cmd, cmd = part.partition(':')
        letters = letters.replace(' ', '').upper()
        cmd = cmd.strip() if has_cmd else command
        valid = (
            letters
            and all(c.isalpha() and 0 <= ord(c) - 65 < len(sessions) for c in letters)
            and len(set(letters)) == len(letters)
            and not (has_cmd and len(letters) != 1)
        )
        if not valid:
            errors.append({
                'target': part, 'command': cmd or '', 'success': False,
                'error': (f"'{part}' is not distinct session letters A-{chr(64 + len(sessions))} "
                          "or one letter with a command (A:<command>, all:<command>)"),
            })
            continue
        if cmd is not None and not cmd.strip():
            errors.append({'target': part, 'command': '', 'success': False,
                           'error': f"'{part}'{empty}"})
            continue
        plan += [(c, sessions[ord(c) - 65], cmd) for c in letters]

    if errors:
        return errors
    return [
        send_to_session(tmux, s, cmd) if cmd is not None else _run_default_action(tmux, s, c)
        for c, s, cmd in plan
    ]
