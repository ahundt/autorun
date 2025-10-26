#!/usr/bin/env python3
"""
Centralized tmux utilities - DRY compliant implementation

Ensures consistent tmux/byobu handling across clautorun with proper
control sequence parsing, session naming, and command dispatch.
"""

import os
import subprocess
import time
import re
from typing import Dict, List, Optional, Tuple, Any
from enum import Enum




class TmuxUtilities:
    """
    Centralized tmux utilities with session targeting safety

    Enforces clautorun standards: default session naming "clautorun",
    essential tmux operations, and session targeting safety.

    Session Targeting:
    - Default session: "clautorun" - prevents interference with current Claude Code session
    - Custom targeting: Pass session parameter to target different sessions
    - Safety guarantee: All commands are explicitly targeted to prevent accidental execution in wrong session
    - Format: session:window.pane for precise targeting
    """

    # Default session name as required by CLI_USAGE_AND_TEST_AUTOMATION_WITH_BYOBU_TMUX_SESSIONS.md
    DEFAULT_SESSION_NAME = "clautorun"

    # Essential WIN_OPS dispatch - minimal set for core functionality
    # Reduced from 160+ lines to essential operations only
    WIN_OPS = {
        # Core session management
        'new-session': 'new-session',
        'attach-session': 'attach-session -t',
        'detach-client': 'detach-client',
        'kill-session': 'kill-session -t',
        'list-sessions': 'list-sessions',

        # Core window management
        'new-window': 'new-window',
        'select-window': 'select-window -t',
        'kill-window': 'kill-window -t',
        'list-windows': 'list-windows',

        # Core pane management
        'split-window': 'split-window',
        'select-pane': 'select-pane -t',
        'kill-pane': 'kill-pane -t',
        'list-panes': 'list-panes',

        # Essential operations
        'send-keys': 'send-keys',
        'capture-pane': 'capture-pane -p',
        'display-message': 'display-message',
    }

    def __init__(self, session_name: Optional[str] = None):
        """
        Initialize tmux utilities with session name enforcement

        Args:
            session_name: Override default session name (should rarely be used)
        """
        self.session_name = session_name or self.DEFAULT_SESSION_NAME

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

        # Combine with actual command
        full_cmd = base_cmd + cmd

        # Add target specification for session, window, and pane operations
        # CRITICAL FIX: Always specify target to ensure commands go to correct session
        target = target_session
        if window:
            target += f":{window}"
        if pane:
            target += f".{pane}"
        full_cmd.extend(["-t", target])

        try:
            result = subprocess.run(
                full_cmd,
                capture_output=True,
                text=True,
                timeout=5
            )
            return {
                'returncode': result.returncode,
                'stdout': result.stdout,
                'stderr': result.stderr,
                'command': full_cmd
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