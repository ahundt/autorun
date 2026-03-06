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
"""tmux-based prompt injection system for autorun - Using centralized tmux utilities"""

import time
import os
from typing import Optional, Dict, Tuple
import logging

# Import centralized tmux utilities for DRY compliance
from .tmux_utils import get_tmux_utilities

from .config import CONFIG
from .session_manager import session_state
from .logging_utils import get_logger as _get_logger
_log = _get_logger(__name__)


def log_info(message: str) -> None:
    """Log info message to file (AUTORUN_DEBUG=1 to enable)."""
    _log.info(message)

# Configure logging - file-only when AUTORUN_DEBUG=1, disabled otherwise
# CRITICAL: No stderr output to avoid breaking hooks
if os.environ.get('AUTORUN_DEBUG') == '1':
    from . import ipc
    log_file = ipc.AUTORUN_LOG_FILE
    logging.basicConfig(
        handlers=[logging.FileHandler(log_file)],
        level=logging.DEBUG,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    )
else:
    # Disabled - use NullHandler to prevent default stderr handler
    logging.basicConfig(handlers=[logging.NullHandler()], level=logging.CRITICAL + 1)
logger = logging.getLogger(__name__)

# Follow main.py pattern for handlers
INJECTION_HANDLERS = {}
def injection_handler(name):
    """Decorator to register injection handlers - following main.py pattern"""
    def dec(f):
        INJECTION_HANDLERS[name] = f
        return f
    return dec

class TmuxInjector:
    """tmux-based prompt injection system with user activity detection using centralized utilities"""

    def __init__(self, session_id: str = None):
        # Use default "autorun" session if none provided (standards compliance)
        self.session_id = session_id or get_tmux_utilities().DEFAULT_SESSION_NAME
        self.tmux_utils = get_tmux_utilities(self.session_id)
        self.tmux_session = None
        self.tmux_window = None
        self.tmux_pane = None
        self._detected_session = None

    def detect_tmux_environment(self) -> Optional[Dict[str, str]]:
        """Detect tmux session, window, and pane information using centralized utilities"""
        env_info = self.tmux_utils.detect_tmux_environment()
        if env_info:
            return env_info

        # If no current tmux environment, ensure our session exists
        if self.tmux_utils.ensure_session_exists(self.session_id):
            return {
                "session": self.session_id,
                "window": "0",  # Default to first window
                "pane": "0"    # Default to first pane
            }

        return None

    def capture_current_input(self) -> str:
        """Capture current command line input from tmux pane using centralized utilities"""
        if not self.tmux_session:
            return ""
        return self.tmux_utils.capture_current_input(
            self.tmux_session, self.tmux_window, self.tmux_pane
        )

    def is_user_typing(self, wait_time: float = 1.0) -> bool:
        """Detect if user is actively typing using centralized utilities"""
        if not self.tmux_session:
            return False
        return self.tmux_utils.is_user_typing(
            check_interval=wait_time, max_checks=1,
            session=self.tmux_session, window=self.tmux_window, pane=self.tmux_pane
        )

    def clear_command_line(self) -> bool:
        """Clear current command line in tmux using centralized utilities"""
        if not self.tmux_session:
            return False

        try:
            # Send Ctrl+U to clear line (works in most shells)
            self.tmux_utils.send_keys("C-u", self.tmux_session, self.tmux_window, self.tmux_pane)
            # Also try Ctrl+A, Ctrl+K as fallback
            self.tmux_utils.send_keys("C-a C-k", self.tmux_session, self.tmux_window, self.tmux_pane)
            # Finally try Esc+0+d to clear line (zsh)
            self.tmux_utils.send_keys("Escape 0 d", self.tmux_session, self.tmux_window, self.tmux_pane)
            return True
        except Exception as e:
            log_info(f"Failed to clear command line: {e}")
            return False

    def send_command(self, command: str) -> bool:
        """Send command to tmux pane using centralized utilities"""
        if not self.tmux_session:
            return False

        try:
            # Send the command
            self.tmux_utils.send_keys(command, self.tmux_session, self.tmux_window, self.tmux_pane)
            # Send Enter to execute
            self.tmux_utils.send_keys("Enter", self.tmux_session, self.tmux_window, self.tmux_pane)
            return True
        except Exception as e:
            log_info(f"Failed to send command: {e}")
            return False

    def restore_input(self, saved_input: str) -> bool:
        """Restore previously saved input using centralized utilities"""
        if not saved_input or not self.tmux_session:
            return False

        try:
            self.tmux_utils.send_keys(saved_input, self.tmux_session, self.tmux_window, self.tmux_pane)
            return True
        except Exception as e:
            log_info(f"Failed to restore input: {e}")
            return False

    def inject_prompt(self, prompt_text: str, max_retries: int = 3) -> Tuple[bool, str]:
        """
        Inject prompt via tmux with user activity detection

        Returns tuple of (success, message)
        """
        if not self.detect_tmux_environment():
            return False, "tmux environment not detected"

        # Store detected environment
        env = self.detect_tmux_environment()
        self.tmux_session = env["session"]
        self.tmux_window = env["window"]
        self.tmux_pane = env["pane"]

        log_info(f"Injecting prompt via tmux session {self.tmux_session}")

        # Save current input if any
        saved_input = self.capture_current_input()

        # Check if user is actively typing
        if self.is_user_typing():
            log_info("User actively typing, delaying injection")
            return False, "User actively typing"

        # Clear current command line
        if not self.clear_command_line():
            log_info("Failed to clear command line")
            return False, "Failed to clear command line"

        # Send the prompt
        if not self.send_command(prompt_text):
            log_info("Failed to send prompt via tmux")
            return False, "Failed to send prompt"

        # Restore saved input if any
        if saved_input:
            if not self.restore_input(saved_input):
                log_info("Failed to restore saved input")
                # Don't fail the injection, just log the issue

        return True, f"Prompt injected via tmux session {self.tmux_session}"

    def verify_tmux_session_health(self) -> bool:
        """Verify tmux session is still active and responsive using centralized utilities"""
        if not self.tmux_session:
            return False

        # Check session health using centralized utilities
        result = self.tmux_utils.execute_tmux_command(['display-message', '-t', self.tmux_session])
        return result and result['returncode'] == 0

    def get_tmux_session_info(self) -> Dict[str, str]:
        """Get current tmux session information using centralized utilities"""
        if not self.tmux_session:
            env_info = self.detect_tmux_environment()
            if env_info:
                self.tmux_session = env_info["session"]
                self.tmux_window = env_info["window"]
                self.tmux_pane = env_info["pane"]

        return {
            "session": self.tmux_session or "unknown",
            "window": self.tmux_window or "unknown",
            "pane": self.tmux_pane or "unknown"
        }

class DualChannelInjector:
    """Dual-channel prompt injection system with API and tmux fallback using centralized utilities"""

    def __init__(self, session_id: str = None):
        # Use default "autorun" session if none provided (standards compliance)
        self.session_id = session_id or get_tmux_utilities().DEFAULT_SESSION_NAME
        self.tmux_injector = TmuxInjector(self.session_id)
        self.injection_history = []

    def inject_prompt(self, prompt_text: str, preferred_channel: str = "api",
                      enable_tmux_fallback: bool = True) -> Tuple[bool, str, str]:
        """
        Inject prompt using preferred channel with automatic fallback

        Returns tuple of (success, message, channel_used)
        """
        injection_record = {
            "timestamp": time.time(),
            "session_id": self.session_id,
            "prompt_length": len(prompt_text),
            "preferred_channel": preferred_channel
        }

        # Try API-based injection first (if preferred)
        if preferred_channel == "api":
            success, message = self._try_api_injection(prompt_text)
            if success:
                injection_record.update({
                    "channel_used": "api",
                    "success": True,
                    "message": message
                })
                self.injection_history.append(injection_record)
                return True, "API injection successful", "api"

        # Try tmux fallback if enabled
        if enable_tmux_fallback:
            success, message = self.tmux_injector.inject_prompt(prompt_text)
            injection_record.update({
                "channel_used": "tmux",
                "success": success,
                "message": message
            })
            self.injection_history.append(injection_record)

            return success, message, "tmux"

        injection_record.update({
            "channel_used": "none",
            "success": False,
            "message": "No injection channel available"
        })
        self.injection_history.append(injection_record)

        return False, "No injection channel available", "none"

    def _try_api_injection(self, prompt_text: str) -> Tuple[bool, str]:
        """
        Try API-based injection (placeholder for future implementation)

        This would integrate with Claude Code's API if available.
        """
        # TODO: Implement API-based injection when Claude Code API is available
        return False, "API injection not implemented"

    def get_injection_statistics(self) -> Dict:
        """Get statistics about injection attempts"""
        if not self.injection_history:
            return {"total_attempts": 0}

        total_attempts = len(self.injection_history)
        successful_injections = sum(1 for record in self.injection_history if record["success"])

        channel_stats = {}
        for record in self.injection_history:
            channel = record.get("channel_used", "unknown")
            if channel not in channel_stats:
                channel_stats[channel] = {"attempts": 0, "successes": 0}
            channel_stats[channel]["attempts"] += 1
            if record["success"]:
                channel_stats[channel]["successes"] += 1

        return {
            "total_attempts": total_attempts,
            "successful_injections": successful_injections,
            "success_rate": successful_injections / total_attempts if total_attempts > 0 else 0,
            "channel_statistics": channel_stats,
            "tmux_session_info": self.tmux_injector.get_tmux_session_info()
        }

# Export main functions
__all__ = [
    'TmuxInjector',
    'DualChannelInjector'
]