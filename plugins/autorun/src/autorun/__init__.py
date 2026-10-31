#!/usr/bin/env python3
# -*- coding: utf-8 -*-

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
autorun - task lifecycle and safety hooks for AI coding harnesses

The package supplies hook dispatch, persistent task state, command guards,
installer orchestration, and native assets for supported harnesses.

The optional local daemon shares state safely across hook processes; direct
mode exercises the same handlers without the daemon.
"""

__version__ = "1.0.0"
__author__ = "Andrew Hundt"
__email__ = "ATHundt@gmail.com"

# Load automated metadata (commit, build_time) if available
import json
from pathlib import Path

__commit__ = "unknown"
__build_time__ = "unknown"

try:
    _meta_path = Path(__file__).parent / "metadata.json"
    if _meta_path.exists():
        _meta_text = _meta_path.read_text()
        if _meta_text.strip():
            _meta = json.loads(_meta_text)
            __version__ = _meta.get("version", __version__)
            __commit__ = _meta.get("commit", "unknown")
            __build_time__ = _meta.get("build_time", "unknown")
except (json.JSONDecodeError, OSError, AttributeError):
    # Robust fallback for any failure (missing file, invalid JSON, etc.)
    pass

# Minimal exports to avoid circular imports
__all__ = [
    "__version__",
    "__author__",
    "__email__",
    "session_state",
    "shared_session_state",
    "clear_test_session_state",
    "SessionStateError",
    "SessionTimeoutError",
    "show_comprehensive_uv_error",
    "handle_import_error",
    "check_uv_environment",
    "show_uv_environment_status",
    "CONFIG",
    "log_info",
    "build_hook_response",
    # Removed (see main.py module docstring for canonical replacements):
    # "COMMAND_HANDLERS" — use plugins.app.commands + app.dispatch()
    # "claude_code_handler" — use __main__.run_direct() → app.dispatch()
    # "stop_handler" — use plugins.autorun_injection (@app.on("Stop"))
    # "inject_continue_prompt" — use plugins.build_injection_prompt(ctx)
    # "inject_verification_prompt" — use plugins.build_injection_prompt(ctx)
    # "is_premature_stop" — use plugins.is_premature_stop(ctx: EventContext)
]

# Export session manager functionality
try:
    from .session_manager import (
        session_state,
        shared_session_state,
        clear_test_session_state,
        SessionStateError,
        SessionTimeoutError
    )
except ImportError:
    # If session manager can't be imported, provide fallback exports
    # This helps with import errors in plugin environments
    class SessionStateError(Exception):
        pass

    class SessionTimeoutError(Exception):
        pass

    def session_state(session_id: str, timeout: float = 30.0, shared_access: bool = False):
        raise ImportError("Session manager not available - check UV environment")

    def shared_session_state(session_id: str, timeout: float = 5.0):
        raise ImportError("Session manager not available - check UV environment")

    def clear_test_session_state(session_id: str) -> None:
        raise ImportError("Session manager not available - check UV environment")

# Export error handling functionality
try:
    from .error_handling import (
        show_comprehensive_uv_error,
        handle_import_error,
        check_uv_environment,
        show_uv_environment_status
    )
except ImportError:
    # Fallback error handling if error_handling module can't be imported
    def show_comprehensive_uv_error(error_type="IMPORT ERROR", error_message="Module structure issue detected"):
        """Fallback UV error message"""
        print("=" * 70)
        print(f"❌ {error_type}: {error_message}")
        print("=" * 70)
        print("UV environment not properly configured. Install UV and activate environment.")

    def handle_import_error(import_error, exit_on_error=True):
        """Fallback import error handler"""
        if "autorun" in str(import_error):
            show_comprehensive_uv_error()
            if exit_on_error:
                import sys
                sys.exit(1)
            return True
        return False

    def check_uv_environment():
        """Fallback UV environment checker"""
        return False, False, {}

    def show_uv_environment_status():
        """Fallback UV status shower"""
        print("UV environment check not available")

# Export CONFIG and log_info for tests
# COMMAND_HANDLERS removed — use plugins.app.commands + app.dispatch() (canonical)
# CONFIG has one source, config.py, which imports only the stdlib at module
# level and so needs no fallback. A hand-copied fallback CONFIG used to stand
# in whenever `.main` failed to import, and it had drifted from config.py.
from .config import CONFIG

COMMAND_HANDLERS = {}  # Removed: use plugins.app.commands + app.dispatch()

try:
    # Import log_info from main.py (thin shim → logging_utils)
    from .main import log_info
except ImportError:
    def log_info(message):
        """Fallback log_info function"""
        pass

# Export additional imports needed for tests
try:
    from .main import build_hook_response
except ImportError:
    def build_hook_response(continue_execution=True, stop_reason="", system_message="",
                            decision=None, reason=None, event_name="unknown", ctx=None):
        """Fallback build_hook_response function - matches main.py signature.

        DRY: This is a fallback for tests if main.py import fails.
        Source of truth: main.py build_hook_response()
        """
        response = {"continue": continue_execution, "stopReason": stop_reason,
                    "suppressOutput": False, "systemMessage": system_message}
        # Stop-hook-specific fields for blocking stops
        if decision is not None:
            response["decision"] = decision
        if reason is not None:
            response["reason"] = reason
        return response

# claude_code_handler — REMOVED: __main__.run_direct() → app.dispatch() → plugins.py
# pretooluse_handler — REMOVED: plugins.py enforce_file_policy(ctx) + check_blocked_commands(ctx)
# stop_handler — REMOVED: plugins.autorun_injection at plugins.py:923 (@app.on("Stop"))
# inject_continue_prompt — REMOVED: plugins.build_injection_prompt(ctx) at plugins.py:804
# inject_verification_prompt — REMOVED: plugins.build_injection_prompt(ctx) at plugins.py:804
# is_premature_stop(ctx, state) — REMOVED: plugins.is_premature_stop(ctx) at plugins.py:734
