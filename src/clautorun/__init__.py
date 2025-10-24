#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
clautorun - Claude Agent SDK Command Interceptor

A lightweight, efficient command interceptor for Claude Code that saves tokens by processing
autorun commands locally before they reach the AI.

Features:
- Zero AI token consumption for autorun commands
- Interactive mode with smart Ctrl+C handling
- Multiple integration methods (hooks, MCP, plugin)
- Efficient dispatch patterns matching autorun5.py
- Full compatibility with existing autorun workflows
"""

__version__ = "0.1.0"
__author__ = "Your Name"
__email__ = "your.email@example.com"

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
    "log_info"
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
    from .claude_code_plugin import CONFIG as PLUGIN_CONFIG, COMMAND_HANDLERS, log_info

    # Create complete CONFIG that matches autorun5.py for test compatibility
    CONFIG = {
        # Autorun system constants
        "completion_marker": "AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY",
        "emergency_stop_phrase": "AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP",
        "max_recheck_count": 3,
        "monitor_stop_delay_seconds": 300,

        # Command mappings
        "command_mappings": {
            "/autorun": "activate",
            "/autostop": "stop",
            "/estop": "emergency_stop",
            "/afs": "SEARCH",
            "/afa": "ALLOW",
            "/afj": "JUSTIFY",
            "/afst": "STATUS"
        },

        # File policies
        "policies": {
            "ALLOW": ("allow-all", "ALLOW ALL: Full permission to create/modify files."),
            "JUSTIFY": ("justify-create", "JUSTIFIED: Search existing first. Include <AUTOFILE_JUSTIFICATION>reason</AUTOFILE_JUSTIFICATION> for new files."),
            "SEARCH": ("strict-search", "STRICT SEARCH: ONLY modify existing files. Use Glob/Grep. NO new files.")
        },

        # Policy blocked messages
        "policy_blocked": {
            "SEARCH": 'Blocked: STRICT SEARCH policy active. To proceed: 1) Identify what functionality this file provides, 2) Search for existing files handling similar functionality using Glob patterns like "*related-topic*", 3) Use Grep to find files with relevant classes/functions/imports, 4) Modify the most appropriate existing file. Search examples: "*auth*" for authentication, "*api*" for endpoints, "*config*" for settings, "*model*" for data structures.',
            "JUSTIFY": "Blocked: JUSTIFIED CREATION policy requires justification. To proceed: 1) Search for existing files using Glob/Grep related to your functionality, 2) Evaluate if existing files can be extended, 3) If no existing file works, include <AUTOFILE_JUSTIFICATION>Specific technical reason why existing files cannot accommodate this functionality</AUTOFILE_JUSTIFICATION> in your reasoning during the same prompt where you request the file creation, then retry file creation."
        },

        # Injection template
        "injection_template": """Your primary objective is to continue **UNINTERRUPTED, FULLY AUTONOMOUS, NONINTERACTIVE, PATIENT, AND SAFE EXECUTION** of your current tasks and goals.

1.  **MANDATORY PROCESS TO CONTINUE EXECUTION:** Cautiously and deliberately continue working **carefully, patiently, concretely, and safely**, non-stop, autonomously, and non-interactively per your instructions.
    * **Permissions Granted:** You have full permission to use all tools without requiring further permission prompts.

FILE CREATION POLICY: {policy_instructions}

2.  **SYSTEM STOP SIGNAL RULE:** The exact strings **{emergency_stop_phrase}** and **{completion_marker}** are **SYSTEM STOP SIGNALS**. You MUST **NEVER** output these strings unless corresponding stop condition has been met.

3.  **Safety Protocol (Risk Assessment & Mitigation):** Execute full Preservation Sequence **ONLY IF** current task involves high-risk irreversible actions.
    * **INITIATE SAFETY PROTOCOL:** Risk assessment complete
    * **CRITICAL ESCAPE TO STOP SYSTEM:** Emergency procedures activated
    * **FINAL OUTPUT ON SUCCESS TO STOP SYSTEM:** Completion verification complete

4.  **FINAL OUTPUT ON SUCCESS:** When all tasks are 100% complete, output: **{completion_marker}**

Original task: {activation_prompt}""",

        # Recheck template
        "recheck_template": """AUTORUN TASK VERIFICATION

The task appears complete but requires careful verification.

Original Task: {activation_prompt}

CRITICAL VERIFICATION INSTRUCTIONS:
1. Carefully review ALL aspects of the original task above
2. Verify EVERY requirement has been fully met and tested
3. Check for any incomplete, partial, or missed elements
4. Test any implemented functionality thoroughly
5. Double-check your work against the original requirements
6. Verify all files are in their correct final state
7. Ensure no temporary or incomplete work remains

This is verification attempt #{recheck_count} of {max_recheck_count}.

{completion_marker}"""
    }

except ImportError:
    # Complete fallback CONFIG for tests
    CONFIG = {
        "completion_marker": "AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY",
        "emergency_stop_phrase": "AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP",
        "max_recheck_count": 3,
        "monitor_stop_delay_seconds": 300,
        "command_mappings": {
            "/autorun": "activate",
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

    # Fallback COMMAND_HANDLERS for tests
    def handle_search(state):
        state["file_policy"] = "SEARCH"
        return "AutoFile policy: strict-search - STRICT SEARCH: ONLY modify existing files. Use Glob/Grep. NO new files."

    def handle_allow(state):
        state["file_policy"] = "ALLOW"
        return "AutoFile policy: allow-all - ALLOW ALL: Full permission to create/modify files."

    def handle_justify(state):
        state["file_policy"] = "JUSTIFY"
        return "AutoFile policy: justify-create - JUSTIFIED: Search existing first. Include <AUTOFILE_JUSTIFICATION>reason</AUTOFILE_JUSTIFICATION> for new files."

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
        "SEARCH": handle_search,
        "ALLOW": handle_allow,
        "JUSTIFY": handle_justify,
        "STATUS": handle_status,
        "status": handle_status,  # Add lowercase version for compatibility
        "stop": handle_stop,
        "emergency_stop": handle_emergency_stop,
        "activate": handle_activate
    }

    def log_info(message):
        """Fallback log_info function"""
        pass