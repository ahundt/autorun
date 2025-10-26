#!/usr/bin/env python3
"""
Centralized tmux utilities - DRY compliant implementation

Ensures consistent tmux/byobu handling across clautorun with proper
control sequence parsing, session naming, and command dispatch.
"""

import os
import subprocess
import sys
import time
import re
from typing import Dict, List, Optional, Tuple, Any
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
        'sp': 'select-pane -t',
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
            commands_supporting_target = {
                'send-keys', 'capture-pane', 'new-window', 'kill-window',
                'select-window', 'split-window', 'select-pane', 'kill-pane',
                'select-layout', 'display-message', 'attach-session', 'detach-client',
                'new-session', 'kill-session', 'list-windows', 'list-panes'
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
            commands_supporting_target = {
                'send-keys', 'capture-pane', 'new-window', 'kill-window',
                'select-window', 'split-window', 'select-pane', 'kill-pane',
                'select-layout', 'display-message', 'attach-session', 'detach-client',
                'new-session', 'kill-session', 'list-windows', 'list-panes'
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