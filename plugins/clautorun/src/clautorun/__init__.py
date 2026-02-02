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
clautorun - Claude Agent SDK Command Interceptor

A lightweight, efficient command interceptor for Claude Code that saves tokens by processing
autorun commands locally before they reach the AI.

Features:
- Zero AI token consumption for autorun commands
- Interactive mode with smart Ctrl+C handling
- Multiple integration methods (hooks, MCP, plugin)
- Efficient command dispatch via decorator pattern
- Full compatibility with existing autorun workflows
"""

__version__ = "0.6.1"
__author__ = "Andrew Hundt"
__email__ = "ATHundt@gmail.com"

import json

# Minimal exports to avoid circular imports
__all__ = [
    "__version__",
    "__author__",
    "__email__",
    "session_state",
    "shared_session_state",
    "SessionStateError",
    "SessionTimeoutError",
    "show_comprehensive_uv_error",
    "handle_import_error",
    "check_uv_environment",
    "show_uv_environment_status",
    "CONFIG",
    "COMMAND_HANDLERS",
    "log_info",
    "build_hook_response",
    "build_pretooluse_response",
    "claude_code_handler",
    "pretooluse_handler",
    "stop_handler",
    "inject_continue_prompt",
    "inject_verification_prompt",
    "is_premature_stop",
    "should_trigger_verification",
    "analyze_verification_results"
]

# Export session manager functionality
try:
    from .session_manager import (
        session_state,
        shared_session_state,
        SessionStateError,
        SessionTimeoutError
    )
except ImportError:
    # If session manager can't be imported, provide dummy exports
    # This helps with import errors in plugin environments
    class SessionStateError(Exception):
        pass

    class SessionTimeoutError(Exception):
        pass

    def session_state(session_id: str, timeout: float = 30.0, shared_access: bool = False):
        raise ImportError("Session manager not available - check UV environment")

    def shared_session_state(session_id: str, timeout: float = 5.0):
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
        if "clautorun" in str(import_error):
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

# Export complete CONFIG, COMMAND_HANDLERS, and log_info for tests
try:
    # Import CONFIG from centralized config.py (DRY principle - single source of truth)
    from .config import CONFIG
    # Import COMMAND_HANDLERS and log_info from main.py
    from .main import COMMAND_HANDLERS, log_info

except ImportError:
    # Complete fallback CONFIG for tests - matches main.py three-stage system
    CONFIG = {
        # ─── Stage 1: Initial Work ────────────────────────────────────────────────
        "stage1_instruction": "starting tasks, analyzing user requirements, and developing comprehensive plan",
        "stage1_confirmation": "AUTORUN_STAGE1_COMPLETE",

        # ─── Stage 2: Critical Evaluation ─────────────────────────────────────────
        "stage2_instruction": "Critically evaluate previous work and continue tasks as needed",
        "stage2_confirmation": "AUTORUN_STAGE2_COMPLETE",

        # ─── Stage 3: Final Verification ──────────────────────────────────────────
        "stage3_instruction": "Verify all tasks completed, critically evaluated, corrected and verified",
        "stage3_confirmation": "AUTORUN_STAGE3_COMPLETE",

        # ─── Descriptive Completion Markers ──────────────────────────────────────
        # NOTE: These are DESCRIPTIVE strings the AI outputs to communicate what it accomplished.
        # Markdown command files use these descriptive strings for clarity.
        "completion_marker": "AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY",

        # ─── Emergency Stop ───────────────────────────────────────────────────────
        # NOTE: This is a DESCRIPTIVE string that the AI outputs to communicate its action.
        "emergency_stop": "AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP",

        # ─── Timing ───────────────────────────────────────────────────────────────
        "max_recheck_count": 3,
        "monitor_stop_delay_seconds": 300,
        "stage3_countdown_calls": 5,

        "command_mappings": {
            "/autorun": "activate",
            "/autoproc": "activate",
            "/autostop": "stop",
            "/estop": "emergency_stop",
            "/afs": "SEARCH",
            "/afa": "ALLOW",
            "/afj": "JUSTIFY",
            "/afst": "STATUS"
        },
        "policies": {
            "ALLOW": ("allow-all", "ALLOW ALL: Full permission to create/modify files."),
            "JUSTIFY": ("justify-create", "JUSTIFIED: Search existing first. Include <AUTOFILE_JUSTIFICATION>reason</AUTOFILE_JUSTIFICATION> for new files."),
            "SEARCH": ("strict-search", "STRICT SEARCH: ONLY modify existing files. Use Glob/Grep. NO new files.")
        },
        "policy_blocked": {
            "SEARCH": 'Blocked: STRICT SEARCH policy active. To proceed: 1) Identify what functionality this file provides, 2) Search for existing files handling similar functionality using Glob patterns like "*related-topic*", 3) Use Grep to find files with relevant classes/functions/imports, 4) Modify the most appropriate existing file. Search examples: "*auth*" for authentication, "*api*" for endpoints, "*config*" for settings, "*model*" for data structures.',
            "JUSTIFY": "Blocked: JUSTIFIED CREATION policy requires justification. To proceed: 1) Search for existing files using Glob/Grep related to your functionality, 2) Evaluate if existing files can be extended, 3) If no existing file works, include <AUTOFILE_JUSTIFICATION>Specific technical reason why existing files cannot accommodate this functionality</AUTOFILE_JUSTIFICATION> in your reasoning during the same prompt where you request the file creation, then retry file creation."
        },
        "injection_template": "Template placeholder",
        "recheck_template": "Recheck template placeholder"
    }

    # Fallback COMMAND_HANDLERS for tests - use CONFIG for DRY compliance
    def handle_search(state):
        state["file_policy"] = "SEARCH"
        policy_name, policy_desc = CONFIG["policies"]["SEARCH"]
        return f"AutoFile policy: {policy_name} - {policy_desc}"

    def handle_allow(state):
        state["file_policy"] = "ALLOW"
        policy_name, policy_desc = CONFIG["policies"]["ALLOW"]
        return f"AutoFile policy: {policy_name} - {policy_desc}"

    def handle_justify(state):
        state["file_policy"] = "JUSTIFY"
        policy_name, policy_desc = CONFIG["policies"]["JUSTIFY"]
        return f"AutoFile policy: {policy_name} - {policy_desc}"

    def handle_status(state):
        current_policy = state.get("file_policy", "ALLOW")
        policy_name, _ = CONFIG["policies"][current_policy]
        return f"Current policy: {policy_name}"

    def handle_stop(state):
        state["session_status"] = "stopped"
        return "Autorun stopped"

    def handle_emergency_stop(state):
        state["session_status"] = "emergency_stopped"
        return "Emergency stop activated"

    def handle_activate(state, prompt):
        state["session_status"] = "active"
        state["autorun_stage"] = "INITIAL"
        state["activation_prompt"] = prompt
        return "Your primary objective is to continue **UNINTERRUPTED, FULLY AUTONOMOUS, NONINTERACTIVE, PATIENT, AND SAFE EXECUTION** of your current tasks and goals."

    COMMAND_HANDLERS = {
        # Policy commands (both versions available)
        "SEARCH": handle_search,
        "search": handle_search,
        "ALLOW": handle_allow,
        "allow": handle_allow,
        "JUSTIFY": handle_justify,
        "justify": handle_justify,
        "STATUS": handle_status,
        "status": handle_status,

        # Control commands (both versions available)
        "activate": handle_activate,
        "ACTIVATE": handle_activate,
        "stop": handle_stop,
        "STOP": handle_stop,
        "emergency_stop": handle_emergency_stop,
        "EMERGENCY_STOP": handle_emergency_stop
    }

    def log_info(message):
        """Fallback log_info function"""
        pass

# Export additional imports needed for tests
try:
    from .main import build_hook_response
except ImportError:
    def build_hook_response(continue_execution=True, stop_reason="", system_message="",
                            decision=None, reason=None):
        """Fallback build_hook_response function - matches main.py signature.

        DRY: This is a fallback for tests if main.py import fails.
        Source of truth: main.py:877-895
        """
        response = {"continue": continue_execution, "stopReason": stop_reason,
                    "suppressOutput": False, "systemMessage": system_message}
        # Stop-hook-specific fields for blocking stops
        if decision is not None:
            response["decision"] = decision
        if reason is not None:
            response["reason"] = reason
        return response

try:
    from .main import claude_code_handler
except ImportError:
    def claude_code_handler(ctx):
        """Fallback claude_code_handler function - matches main.py signature"""
        return {"continue": True, "stopReason": "", "suppressOutput": False, "systemMessage": ""}

try:
    from .main import pretooluse_handler
except ImportError:
    def pretooluse_handler(ctx):
        """Fallback pretooluse_handler function - matches main.py signature"""
        return {"decision": "allow", "reason": "fallback"}

# Export main functions needed for tests
try:
    from .main import (
        stop_handler,
        inject_continue_prompt,
        inject_verification_prompt,
        is_premature_stop,
        should_trigger_verification,
        analyze_verification_results,
        build_pretooluse_response
    )
except ImportError:
    # Fallback implementations for tests
    def stop_handler():
        """Fallback stop_handler function"""
        pass

    def inject_continue_prompt():
        """Fallback inject_continue_prompt function"""
        return "Continue prompt"

    def inject_verification_prompt():
        """Fallback inject_verification_prompt function"""
        return "Verification prompt"

    def is_premature_stop():
        """Fallback is_premature_stop function"""
        return False

    def should_trigger_verification():
        """Fallback should_trigger_verification function"""
        return False

    def analyze_verification_results():
        """Fallback analyze_verification_results function"""
        return {}

    def build_pretooluse_response(decision="allow", reason=""):
        """Fallback build_pretooluse_response function - matches main.py signature.

        DRY: This is a fallback for tests if main.py import fails.
        Source of truth: main.py:897-907
        """
        return {"continue": True, "stopReason": "", "suppressOutput": False,
                "systemMessage": json.dumps(reason)[1:-1] if reason else "",
                "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                      "permissionDecisionReason": json.dumps(reason)[1:-1] if reason else ""}}