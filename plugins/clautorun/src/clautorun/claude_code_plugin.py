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
"""Claude Code plugin for Agent SDK command interception.

This module handles Claude Code hook events:
- UserPromptSubmit: Command interception for /autorun, /afs, etc.
- PreToolUse: File policy enforcement on Write operations
- Stop: Three-stage verification for autorun sessions
- SubagentStop: Subagent completion verification
"""
import json
import sys
import os
import time
from pathlib import Path

# Import robust session manager with centralized error handling
try:
    from .session_manager import session_state, shared_session_state, SessionStateError, SessionTimeoutError
    SESSION_MANAGER_AVAILABLE = True
except ImportError as e:
    # Use centralized error handling - follows DRY principles
    try:
        from .error_handling import handle_import_error

        if handle_import_error(e, exit_on_error=False):
            # The error was handled by our centralized function, exit
            sys.exit(1)
        else:
            # Standard fallback for other import errors
            pass
    except ImportError:
        # If error handling itself can't be imported, show basic message
        if "clautorun.python_check" in str(e) or "is not a package" in str(e):
            print("=" * 70)
            print("IMPORT ERROR: clautorun module structure issue detected")
            print("=" * 70)
            print("UV environment not properly configured. Install UV and activate environment.")
            print("Run: curl -LsSf https://astral.sh/uv/install.sh | sh")
            print("Then: uv venv --python 3.10 && source .venv/bin/activate && uv sync --extra claude-code")
            print("=" * 70)
            sys.exit(1)

    # Standard fallback for other import errors
        SESSION_MANAGER_AVAILABLE = False
        _simple_state = {}

        def session_state(session_id: str, timeout: float = 30.0, shared_access: bool = False):
            """Fallback session state using simple dict"""
            class FallbackSessionState:
                def __init__(self, session_id):
                    self.session_id = session_id

                def __enter__(self):
                    if self.session_id not in _simple_state:
                        _simple_state[self.session_id] = {}
                    return _simple_state[self.session_id]

                def __exit__(self, exc_type, exc_val, exc_tb):
                    return False

            return FallbackSessionState(session_id)

        def shared_session_state(session_id: str, timeout: float = 5.0):
            """Fallback shared session state"""
            return session_state(session_id, timeout, shared_access=True)

        class SessionStateError(Exception):
            pass

        class SessionTimeoutError(Exception):
            pass

# Self-contained configuration to avoid circular imports
CONFIG = {
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
    # Autorun stages for three-stage verification
    "autorun_stages": ["INITIAL", "VERIFICATION", "FINAL"]
}

# Completion signals that indicate work is done
COMPLETION_SIGNALS = [
    "AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY",
    "AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP"
]


def log_info(message):
    """Log info message for debugging"""
    debug_value = os.getenv("DEBUG", "false").lower().strip()
    true_values = {"true", "1", "yes", "on", "enabled"}
    if debug_value not in true_values:
        return

    try:
        state_dir = Path.home() / ".claude" / "sessions"
        state_dir.mkdir(parents=True, exist_ok=True)
        with open(state_dir / "plugin.log", "a") as f:
            log_time = time.strftime('%Y-%m-%d %H:%M:%S')
            pid = os.getpid()
            f.write(f"[{log_time}] {pid}: {message}\n")
            f.flush()
    except Exception:
        pass  # Silently ignore logging failures


def handle_search(state):
    """Handle SEARCH command"""
    state["file_policy"] = "SEARCH"
    policy_name, policy_desc = CONFIG["policies"]["SEARCH"]
    return f"AutoFile policy: {policy_name} - {policy_desc}"


def handle_allow(state):
    """Handle ALLOW command"""
    state["file_policy"] = "ALLOW"
    policy_name, policy_desc = CONFIG["policies"]["ALLOW"]
    return f"AutoFile policy: {policy_name} - {policy_desc}"


def handle_justify(state):
    """Handle JUSTIFY command"""
    state["file_policy"] = "JUSTIFY"
    policy_name, policy_desc = CONFIG["policies"]["JUSTIFY"]
    return f"AutoFile policy: {policy_name} - {policy_desc}"


def handle_status(state):
    """Handle STATUS command"""
    current_policy = state.get("file_policy", "ALLOW")
    policy_name, _ = CONFIG["policies"][current_policy]
    autorun_status = state.get("session_status", "inactive")
    autorun_stage = state.get("autorun_stage", "N/A")
    return f"Current policy: {policy_name} | Autorun: {autorun_status} (stage: {autorun_stage})"


def handle_stop(state):
    """Handle STOP command"""
    state["session_status"] = "stopped"
    state["autorun_stage"] = None
    return "Autorun stopped"


def handle_emergency_stop(state):
    """Handle EMERGENCY_STOP command"""
    state["session_status"] = "emergency_stopped"
    state["autorun_stage"] = None
    return "Emergency stop activated"


def handle_activate(state, prompt):
    """Handle AUTORUN activation"""
    # Store original prompt and set session status
    state["session_status"] = "active"
    state["autorun_stage"] = "INITIAL"
    state["activation_prompt"] = prompt
    state["file_policy"] = state.get("file_policy", "ALLOW")
    state["stop_attempts"] = 0

    policy = state["file_policy"]
    policy_name, policy_desc = CONFIG["policies"][policy]

    return f"""Your primary objective is to continue **UNINTERRUPTED, FULLY AUTONOMOUS, NONINTERACTIVE, PATIENT, AND SAFE EXECUTION** of your current tasks and goals.

1.  **MANDATORY PROCESS TO CONTINUE EXECUTION:** Cautiously and deliberately continue working **carefully, patiently, concretely, and safely**, non-stop, autonomously, and non-interactively per your instructions.
    * **Permissions Granted:** You have full permission to use all tools without requiring further permission prompts.

FILE CREATION POLICY: {policy_desc}

2.  **SYSTEM STOP SIGNAL RULE:** The exact strings **AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP** and **AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY** are **SYSTEM STOP SIGNALS**. You MUST **NEVER** output these strings unless corresponding stop condition has been met.

3.  **Safety Protocol:** Execute full Preservation Sequence **ONLY IF** current task involves high-risk irreversible actions.

4.  **FINAL OUTPUT ON SUCCESS:** When all tasks are 100% complete, output: **AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY**

Original task: {prompt}
"""


# Command handlers - both uppercase and lowercase versions available for all commands
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


def read_transcript(transcript_path: str) -> str:
    """Read transcript file content safely."""
    if not transcript_path:
        return ""
    try:
        with open(transcript_path, 'r', encoding='utf-8') as f:
            return f.read()
    except (FileNotFoundError, PermissionError, IOError, UnicodeDecodeError) as e:
        log_info(f"Failed to read transcript: {e}")
        return ""


def check_completion_in_transcript(transcript: str) -> bool:
    """Check if transcript contains completion signals."""
    for signal in COMPLETION_SIGNALS:
        if signal in transcript:
            return True
    return False


def handle_stop_hook(payload: dict, session_id: str) -> dict:
    """Handle Stop hook event with three-stage verification.

    Stop hooks can block Claude from stopping to enforce completion verification.

    Returns:
        dict with decision/reason for blocking, or empty dict to allow stop.
    """
    reason = payload.get('reason', 'unknown')
    transcript_path = payload.get('transcript_path', '')

    log_info(f"Stop hook triggered: reason={reason}, session={session_id}")

    try:
        with session_state(session_id) as state:
            session_status = state.get("session_status", "inactive")
            current_stage = state.get("autorun_stage")
            stop_attempts = state.get("stop_attempts", 0)

            # If autorun is not active, allow stop
            if session_status != "active":
                log_info(f"Autorun not active (status={session_status}), allowing stop")
                return {}

            # Read transcript to check for completion signals
            transcript = read_transcript(transcript_path)
            has_completion_signal = check_completion_in_transcript(transcript)

            # If explicit completion signal found, allow stop and reset state
            if has_completion_signal:
                log_info("Completion signal found in transcript, allowing stop")
                state["session_status"] = "completed"
                state["autorun_stage"] = None
                return {"systemMessage": "Autorun completed successfully."}

            # Three-stage verification logic
            stages = CONFIG["autorun_stages"]
            current_index = stages.index(current_stage) if current_stage in stages else 0

            # Increment stop attempts
            stop_attempts += 1
            state["stop_attempts"] = stop_attempts

            # Stage progression based on stop attempts
            if stop_attempts <= 1:
                # First stop attempt - INITIAL stage
                state["autorun_stage"] = "INITIAL"
                message = """**[Autorun Stage 1: INITIAL]**

You attempted to stop but autorun is active. Please verify:
1. Are ALL tasks from the original request completed?
2. Have you checked the todo list for remaining items?
3. Did you output AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY?

If work remains, continue. If complete, output the completion signal."""

                return {
                    "decision": "block",
                    "reason": message,
                    "systemMessage": message
                }

            elif stop_attempts == 2:
                # Second stop attempt - VERIFICATION stage
                state["autorun_stage"] = "VERIFICATION"
                message = """**[Autorun Stage 2: VERIFICATION]**

Second stop attempt detected. Verification required:
1. Review the original task request
2. Confirm each requirement has been addressed
3. Check for any errors or incomplete items in the todo list

If genuinely complete, output: AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY"""

                return {
                    "decision": "block",
                    "reason": message,
                    "systemMessage": message
                }

            elif stop_attempts >= 3:
                # Third+ stop attempt - FINAL stage, allow stop
                state["autorun_stage"] = "FINAL"
                state["session_status"] = "stopped"
                log_info(f"Third stop attempt, allowing stop after {stop_attempts} attempts")
                return {
                    "systemMessage": f"Autorun stopped after {stop_attempts} verification attempts."
                }

    except Exception as e:
        log_info(f"Error in Stop hook: {e}")
        # On error, allow stop to prevent blocking
        return {"systemMessage": f"Autorun hook error: {e}"}

    return {}


def handle_pretooluse_hook(payload: dict, session_id: str) -> dict:
    """Handle PreToolUse hook for file policy enforcement.

    Returns:
        dict with hookSpecificOutput for permission decisions.
    """
    tool_name = payload.get('tool_name', '')
    tool_input = payload.get('tool_input', {})

    log_info(f"PreToolUse hook: tool={tool_name}, session={session_id}")

    # Only enforce on Write operations
    if tool_name != 'Write':
        return {}

    file_path = tool_input.get('file_path', '')

    try:
        with session_state(session_id) as state:
            policy = state.get("file_policy", "ALLOW")

            if policy == "ALLOW":
                # Allow all file operations
                return {}

            elif policy == "SEARCH":
                # Block new file creation
                if file_path and not os.path.exists(file_path):
                    message = f"""**[AutoFile: STRICT SEARCH]**

Blocked: Creating new file '{file_path}'

Current policy: strict-search (no new files)
Action: Use Glob/Grep to find existing files to modify.
Override: Run /cr:a or /cr:allow to permit file creation."""

                    return {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny"
                        },
                        "systemMessage": message
                    }

            elif policy == "JUSTIFY":
                # Require justification for new files
                if file_path and not os.path.exists(file_path):
                    content = tool_input.get('content', '')
                    if '<AUTOFILE_JUSTIFICATION>' not in content:
                        message = f"""**[AutoFile: JUSTIFY]**

Warning: Creating new file '{file_path}' without justification.

Current policy: justify-create
Required: Include <AUTOFILE_JUSTIFICATION>reason</AUTOFILE_JUSTIFICATION> in file content.
Override: Run /cr:a to allow without justification."""

                        # Show warning but allow (user can deny manually)
                        return {"systemMessage": message}

    except Exception as e:
        log_info(f"Error in PreToolUse hook: {e}")

    return {}


def handle_userpromptsubmit_hook(payload: dict, session_id: str) -> dict:
    """Handle UserPromptSubmit hook for command interception.

    Returns:
        dict with additionalContext or decision to block.
    """
    # Use user_prompt field (correct for UserPromptSubmit hooks)
    prompt = payload.get('user_prompt', '') or payload.get('prompt', '')

    log_info(f"UserPromptSubmit hook: prompt='{prompt[:50]}...', session={session_id}")

    # Efficient command detection - exact match first
    command = next((v for k, v in CONFIG["command_mappings"].items() if k == prompt), None)
    if not command:
        # Check for commands that support arguments
        for k, v in CONFIG["command_mappings"].items():
            if k.startswith('/autorun') and (prompt.startswith(k) or prompt.startswith('/clautorun ')):
                command = v
                break

    if command and command in COMMAND_HANDLERS:
        try:
            with session_state(session_id) as state:
                if command in ["activate", "ACTIVATE"]:
                    response = COMMAND_HANDLERS[command](state, prompt)
                else:
                    response = COMMAND_HANDLERS[command](state)

                # Update state for policy commands
                if command.upper() in ["SEARCH", "ALLOW", "JUSTIFY"]:
                    state["file_policy"] = command.upper()

            # Return response using correct Claude Code hook format
            return {
                "additionalContext": response,
                "systemMessage": f"[clautorun] {command} executed"
            }

        except Exception as e:
            log_info(f"Command execution failed: {e}")
            return {"systemMessage": f"[clautorun] Error: {e}"}

    # Not a recognized command, let it through
    return {}


def main():
    """Plugin entry point for Claude Code hooks."""
    try:
        # Read input from stdin
        input_data = sys.stdin.read()

        # Handle empty input
        if not input_data.strip():
            result = {"systemMessage": "clautorun: No input provided"}
            print(json.dumps(result))
            sys.stdout.flush()
            return

        # Parse JSON input
        try:
            payload = json.loads(input_data)
        except json.JSONDecodeError as e:
            result = {"systemMessage": f"clautorun: Invalid JSON: {e}"}
            print(json.dumps(result))
            sys.stdout.flush()
            return

        # Extract session ID
        session_id = payload.get('session_id', 'default')

        # Detect hook event type
        hook_event = payload.get('hook_event_name', '')

        log_info(f"Hook event: {hook_event}, payload keys: {list(payload.keys())}")

        # Route to appropriate handler based on hook event
        if hook_event == 'Stop' or hook_event == 'SubagentStop':
            result = handle_stop_hook(payload, session_id)
        elif hook_event == 'PreToolUse':
            result = handle_pretooluse_hook(payload, session_id)
        elif hook_event == 'UserPromptSubmit':
            result = handle_userpromptsubmit_hook(payload, session_id)
        else:
            # Legacy fallback: try to detect based on payload structure
            if 'user_prompt' in payload or 'prompt' in payload:
                result = handle_userpromptsubmit_hook(payload, session_id)
            elif 'tool_name' in payload:
                result = handle_pretooluse_hook(payload, session_id)
            elif 'reason' in payload or 'transcript_path' in payload:
                result = handle_stop_hook(payload, session_id)
            else:
                result = {}

        # Return JSON response
        print(json.dumps(result))
        sys.stdout.flush()

    except Exception as e:
        # Handle any unexpected errors - allow operation to continue
        result = {"systemMessage": f"clautorun error: {e}"}
        print(json.dumps(result))
        sys.stdout.flush()


if __name__ == "__main__":
    main()
