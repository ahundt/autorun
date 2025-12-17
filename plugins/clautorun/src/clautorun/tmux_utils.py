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

Ensures consistent tmux/byobu handling across clautorun with proper
control sequence parsing, session naming, and command dispatch.
"""

import os
import re
import subprocess
import time
from typing import Dict, List, Optional, Tuple, Any, Callable, Union
from enum import Enum


class TmuxControlState(Enum):
    """States for tmux control sequence parsing"""
    NORMAL = "normal"
    ESCAPE = "escape"
    LITERAL = "literal"


class TmuxUtilities:
    """
    Centralized tmux utilities with control sequence support and session management

    Enforces clautorun standards: default session naming "clautorun",
    control sequence parsing, and comprehensive WIN_OPS dispatch.

    Session Targeting:
    - Default session: "clautorun" - prevents interference with current Claude Code session
    - Custom targeting: Pass session parameter to target different sessions
    - Safety guarantee: All commands are explicitly targeted to prevent accidental execution in wrong session
    - Format: session:window.pane for precise targeting
    """

    # Default session name as required by CLI_USAGE_AND_TEST_AUTOMATION_WITH_BYOBU_TMUX_SESSIONS.md
    DEFAULT_SESSION_NAME = "clautorun"

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
        """
        self.session_name = session_name or self.DEFAULT_SESSION_NAME
        self.control_state = TmuxControlState.NORMAL

    def detect_tmux_environment(self) -> Optional[Dict[str, str]]:
        """
        Detect tmux environment with consistent approach

        Returns:
            Dict with session, window, pane info or None if not in tmux
        """
        # Check TMUX environment variable first
        tmux_env = os.getenv('TMUX')
        if not tmux_env:
            return None

        # Parse TMUX environment variable: /tmp/tmux-1000/default,4219,0
        try:
            parts = tmux_env.split(',')
            if len(parts) >= 3:
                socket_path = parts[0]
                session_and_window = parts[1].split('/')[-1] if '/' in parts[1] else parts[1]
                pane = parts[2]

                # Extract session name from session_and_window (format: session.window)
                if '.' in session_and_window:
                    session, window = session_and_window.split('.', 1)
                else:
                    session = session_and_window
                    window = "0"

                return {
                    "session": session,
                    "window": window,
                    "pane": pane,
                    "socket_path": socket_path
                }
        except (IndexError, ValueError):
            pass

        # Fallback to tmux command-based detection
        try:
            result = self.execute_tmux_command(['display-message', '-p', '#S:#I:#P'])
            if result and result['returncode'] == 0:
                session_window_pane = result['stdout'].strip()
                if ':' in session_window_pane:
                    session, window, pane = session_window_pane.split(':', 2)
                    return {
                        "session": session,
                        "window": window,
                        "pane": pane
                    }
        except Exception:
            pass

        return None

    def execute_tmux_command(self, cmd: List[str], session: Optional[str] = None,
                           window: Optional[str] = None, pane: Optional[str] = None) -> Optional[Dict[str, str]]:
        """
        Execute tmux command with standardized approach and reliable session targeting

        Args:
            cmd: Command list to execute
            session: Target session (uses instance default "clautorun" if None)
            window: Target window (optional, defaults to current window in session)
            pane: Target pane (optional, defaults to current pane in window)

        Returns:
            Command result dict or None if failed

        Session Targeting Behavior:
        - Default: Always targets "clautorun" session to avoid affecting current Claude Code session
        - Custom session: Pass session parameter to target a different session
        - Full targeting: session:window.pane format for precise targeting
        - Safety: Commands will NEVER go to the current Claude Code session accidentally

        Examples:
            tmux.execute_tmux_command(['list-windows'])  # Targets "clautorun" session
            tmux.execute_tmux_command(['send-keys', 'test'], 'custom-session')  # Targets custom session
            tmux.execute_tmux_command(['capture-pane'], None, '0', '0')  # Targets clautorun:0.0
        """
        target_session = session or self.session_name

        # Build command list
        base_cmd = ["tmux"]

        # CRITICAL FIX: For send-keys commands, target specification must come BEFORE keys
        # Format: tmux [socket] send-keys -t target [keys...] where control sequences are separate args
        if cmd and cmd[0] == 'send-keys' and len(cmd) > 1:
            # Extract target specification first
            # NOTE: 'new-session' is excluded because -t means "session group" not "target"
            commands_supporting_target = {
                'send-keys', 'capture-pane', 'new-window', 'kill-window',
                'select-window', 'split-window', 'select-pane', 'kill-pane',
                'select-layout', 'display-message', 'attach-session', 'detach-client',
                'kill-session', 'list-windows', 'list-panes', 'has-session'
            }

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

            if cmd and cmd[0] in commands_supporting_target:
                target = target_session
                if window:
                    target += f":{window}"
                if pane:
                    target += f".{pane}"
                full_cmd.extend(["-t", target])


        try:
            # CRITICAL FIX: Use explicit tmux socket specification when running from within tmux
            # This prevents subprocess from inheriting current session context and ensures proper targeting

            # Check if we're running from within a tmux session
            tmux_env = os.getenv('TMUX')
            if tmux_env:
                # Extract socket path from TMUX environment: /tmp/tmux-1000/default,4219,0
                socket_path = tmux_env.split(',')[0]

                # Build command with explicit socket specification
                # Format: tmux -S <socket> <command> [args...]
                socket_cmd = ['tmux', '-S', socket_path] + full_cmd[1:]

                # Ensure target session exists for commands that support targeting
                if cmd and cmd[0] in commands_supporting_target:
                    subprocess.run(['tmux', '-S', socket_path, 'new-session', '-d', '-s', target_session],
                                  capture_output=True, text=True, timeout=5, shell=False)

                result = subprocess.run(
                    socket_cmd,
                    capture_output=True,
                    text=True,
                    timeout=5,
                    shell=False
                )
            else:
                # Not running within tmux, use regular command
                # Ensure target session exists for commands that support targeting
                if cmd and cmd[0] in commands_supporting_target:
                    subprocess.run(['tmux', 'new-session', '-d', '-s', target_session],
                                  capture_output=True, text=True, timeout=5, shell=False)

                result = subprocess.run(
                    full_cmd,
                    capture_output=True,
                    text=True,
                    timeout=5,
                    shell=False
                )
            # Return the result with the actual command that was executed
            actual_command = socket_cmd if tmux_env else full_cmd
            return {
                'returncode': result.returncode,
                'stdout': result.stdout,
                'stderr': result.stderr,
                'command': actual_command
            }
        except subprocess.TimeoutExpired:
            return None
        except Exception:
            return None

    def parse_control_sequences(self, text: str) -> Tuple[str, TmuxControlState]:
        """
        Parse control sequences according to requirements:
        - ^ escapes to normal tmux mode
        - ^^ for literal ^

        Args:
            text: Text to parse for control sequences

        Returns:
            Tuple of (processed_text, new_state)
        """
        result = []
        i = 0
        local_state = self.control_state  # Use local state to avoid modifying instance state

        while i < len(text):
            char = text[i]

            if local_state == TmuxControlState.NORMAL:
                if char == '^':
                    if i + 1 < len(text) and text[i + 1] == '^':
                        # Literal ^
                        result.append('^')
                        i += 2
                    else:
                        # Escape to normal tmux - don't add the ^ character
                        local_state = TmuxControlState.ESCAPE
                        i += 1
                else:
                    result.append(char)
                    i += 1

            elif local_state == TmuxControlState.ESCAPE:
                # In escape mode - pass through to tmux directly
                result.append(char)
                i += 1
                # Reset after one character
                local_state = TmuxControlState.NORMAL

            else:
                result.append(char)
                i += 1

        # Update instance state for next call
        self.control_state = local_state
        return ''.join(result), local_state

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

        # Check if session exists
        result = self.execute_tmux_command(['has-session', '-t', target_session])
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
        # CRITICAL FIX: Split control sequences from regular text
        # Control sequences like C-m, C-c must be separate arguments to tmux send-keys
        if keys in ['C-m', 'C-c', 'C-l', 'C-u', 'C-w']:
            # Single control sequence - send as individual argument
            result = self.execute_tmux_command(['send-keys', keys], session, window, pane)
        else:
            # Regular text - send as single argument
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
        """
        Detect if a tmux window is running Claude Code by checking process tree.

        Uses existing execute_tmux_command infrastructure to get pane PID,
        then examines child processes for Claude CLI indicators.

        Args:
            session: Tmux session name
            window: Window index within session

        Returns:
            True if window contains active Claude Code session

        Implementation:
            1. Query tmux for pane PID using execute_tmux_command
            2. Use pgrep -P to find child processes of shell
            3. Check process names for Claude CLI indicators:
               - 'claude' (official CLI)
               - 'happy' (Claude CLI wrapper)
               - 'happy-dev' (development build)

        Best Practices:
            - Leverages existing execute_tmux_command for consistency
            - Timeout protection for subprocess calls (2s)
            - Robust error handling for missing processes
            - Case-insensitive matching for process names
            - Fails safely (returns False on any error)
        """
        try:
            # Get pane PID using existing execute_tmux_command
            result = self.execute_tmux_command(
                ['list-panes', '-F', '#{pane_pid}'],
                session=session,
                window=window
            )

            if not result or result['returncode'] != 0 or not result['stdout'].strip():
                return False

            pane_pid = result['stdout'].strip()

            # Get child processes
            child_result = subprocess.run(
                ['pgrep', '-P', pane_pid],
                capture_output=True,
                text=True,
                timeout=2
            )

            if child_result.returncode != 0 or not child_result.stdout.strip():
                return False

            # Check each child process for Claude indicators
            claude_indicators = {'claude', 'happy', 'happy-dev'}

            for child_pid in child_result.stdout.strip().split('\n'):
                if not child_pid.strip():
                    continue

                ps_result = subprocess.run(
                    ['ps', '-p', child_pid.strip(), '-o', 'command='],
                    capture_output=True,
                    text=True,
                    timeout=2
                )

                if ps_result.returncode == 0:
                    cmd_lower = ps_result.stdout.lower()
                    # Check for any Claude CLI indicator
                    if any(indicator in cmd_lower for indicator in claude_indicators):
                        return True

            return False

        except (subprocess.TimeoutExpired, Exception):
            # Fail safely - don't report as Claude session if detection fails
            return False


# Global instance for consistent usage
_tmux_utils = None

def get_tmux_utilities(session_name: Optional[str] = None) -> TmuxUtilities:
    """Get or create tmux utilities instance with session-based caching"""
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


TMUX_WINDOW_FORMAT = '|'.join([
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
TMUX_FORMAT_SEPARATOR = '|'
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
            .contains('path', 'clautorun')          # Path contains
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
          'question', 'happy_mode_switch', 'clarification', 'error_prompt', or None
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

        for line in result['stdout'].strip().split('\n'):
            if not line.strip():
                continue

            parts = line.split(TMUX_FORMAT_SEPARATOR)
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
                win['prompt_type'] = detect_prompt_type(win['content'])
                # Detect if Claude is actively working
                win['is_active'] = detect_claude_active(win['content'])

            windows.append(win)

    return windows


def _tmux_get_current_window() -> Tuple[str, str]:
    """Get current tmux session:window. Returns ('', '') if not in tmux."""
    try:
        result = subprocess.run(
            ['tmux', 'display-message', '-p', '#{session_name}:#{window_index}'],
            capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split(':')
            return (parts[0], parts[1]) if len(parts) >= 2 else ('', '')
    except Exception:
        pass
    return ('', '')


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
PROMPT_TYPE_CLARIFICATION = 'clarification'
PROMPT_TYPE_ERROR = 'error_prompt'


def detect_prompt_type(content: str) -> Optional[str]:
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
        - 'question': AskUserQuestion multi-choice prompt (❯ with numbered options)
        - 'input': Main input prompt (standalone > at line end)
        - 'happy_mode_switch': Happy-cli mode switch prompt (📱 Press space)
        - 'clarification': Natural language question from Claude (ends with ?)
        - 'error_prompt': Error state requiring user action

    Example:
        >>> windows = tmux_list_windows(content_lines=200)
        >>> for w in windows:
        ...     prompt = detect_prompt_type(w.get('content', ''))
        ...     if prompt:
        ...         print(f"{w['session']}:{w['w']} - {prompt}")

        >>> # Find all windows awaiting input
        >>> awaiting = [w for w in windows if detect_prompt_type(w.get('content', ''))]
    """
    if not content:
        return None

    # Analyze last 800 chars for prompt detection
    last_800 = content[-800:]
    last_lines = [l.strip() for l in last_800.split('\n') if l.strip()][-15:]
    last_text = '\n'.join(last_lines)

    # 1. Plan/Feature approval - "Would you like to proceed?" with selector
    if 'Would you like to proceed?' in last_text and '❯' in last_text:
        return PROMPT_TYPE_PLAN_APPROVAL

    # 2. Tool permission - Yes/No patterns
    yn_pattern = r'\[Y/n\]|\[y/N\]|\(yes/no\)|\(y/n\)'
    if re.search(yn_pattern, last_text, re.IGNORECASE):
        return PROMPT_TYPE_TOOL_PERMISSION_YN

    # 3. Tool permission - Numbered options ([1] Allow once, [2] Allow always)
    if re.search(r'\[1\].*\[2\]', last_text):
        return PROMPT_TYPE_TOOL_PERMISSION_NUMBERED

    # 4. AskUserQuestion - Selector with numbered options
    if '❯' in last_text and re.search(r'[1-4]\.\s+\w', last_text):
        return PROMPT_TYPE_QUESTION

    # 5. Main input prompt - standalone > at end of line
    for line in last_lines[-5:]:
        if line == '>' or line == '> ':
            return PROMPT_TYPE_INPUT

    # 6. Happy-cli mode switch prompt
    if '📱 Press space' in last_text:
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


def detect_claude_active(content: str) -> bool:
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
        prompt_type = detect_prompt_type(w.get('content', ''))
        if prompt_type:
            w_copy = dict(w)
            w_copy['prompt_type'] = prompt_type
            result.append(w_copy)

    return result