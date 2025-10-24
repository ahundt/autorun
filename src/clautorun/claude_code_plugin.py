#!/usr/bin/env python3
"""Claude Code plugin for Agent SDK command interception"""
import json
import sys
import os
from pathlib import Path

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
    }
}

# Simple state management using dict to avoid shelve complexity
_simple_state = {}

def session_state(session_id):
    """Simple session state context manager"""
    class SessionContext:
        def __init__(self, session_id):
            self.session_id = session_id
            self.state = _simple_state

        def __enter__(self):
            return self.state

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

    return SessionContext(session_id)

def handle_search(state):
    """Handle SEARCH command"""
    policy_name, policy_desc = CONFIG["policies"]["SEARCH"]
    return f"AutoFile policy: {policy_name} - {policy_desc}"

def handle_allow(state):
    """Handle ALLOW command"""
    policy_name, policy_desc = CONFIG["policies"]["ALLOW"]
    return f"AutoFile policy: {policy_name} - {policy_desc}"

def handle_justify(state):
    """Handle JUSTIFY command"""
    policy_name, policy_desc = CONFIG["policies"]["JUSTIFY"]
    return f"AutoFile policy: {policy_name} - {policy_desc}"

def handle_status(state):
    """Handle STATUS command"""
    current_policy = state.get("file_policy", "ALLOW")
    policy_name, policy_desc = CONFIG["policies"][current_policy]
    return f"Current policy: {policy_name}"

def handle_stop(state):
    """Handle STOP command"""
    return "Autorun stopped"

def handle_emergency_stop(state):
    """Handle EMERGENCY_STOP command"""
    return "Emergency stop activated"

def handle_activate(state, prompt):
    """Handle AUTORUN activation"""
    # Store original prompt
    state["activation_prompt"] = prompt
    state["file_policy"] = state.get("file_policy", "ALLOW")

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

# Command handlers
COMMAND_HANDLERS = {
    "SEARCH": handle_search,
    "ALLOW": handle_allow,
    "JUSTIFY": handle_justify,
    "STATUS": handle_status,
    "stop": handle_stop,
    "emergency_stop": handle_emergency_stop,
    "activate": handle_activate
}

def main():
    """Plugin entry point for Claude Code"""
    try:
        # Read input from stdin
        input_data = sys.stdin.read()

        # Handle empty input
        if not input_data.strip():
            result = {
                "continue": True,
                "response": "",
                "error": "No input provided"
            }
            print(json.dumps(result, sort_keys=True))
            sys.stdout.flush()
            return

        # Parse JSON input
        try:
            payload = json.loads(input_data)
        except json.JSONDecodeError as e:
            result = {
                "continue": True,
                "response": "",
                "error": f"Invalid JSON: {e}"
            }
            print(json.dumps(result, sort_keys=True))
            sys.stdout.flush()
            return

        # Extract input data
        prompt = payload.get('prompt', '')
        session_id = payload.get('session_id', 'default')

        # Efficient command detection - exact match first, then prefix match ONLY for autorun commands
        command = next((v for k, v in CONFIG["command_mappings"].items() if k == prompt), None)
        if not command:
            # Check for commands that support arguments (autorun and prefixed versions)
            for k, v in CONFIG["command_mappings"].items():
                if k.startswith('/autorun') and (prompt.startswith(k) or prompt.startswith('/clautorun ')):
                    command = v
                    break

        if command and command in COMMAND_HANDLERS:
            # Handle command locally, don't send to AI
            try:
                with session_state(session_id) as state:
                    if command == "activate":
                        # For autorun activation, return the injection template
                        response = COMMAND_HANDLERS[command](state, prompt)
                    else:
                        response = COMMAND_HANDLERS[command](state)

                    # Update state for policy commands
                    if command in ["SEARCH", "ALLOW", "JUSTIFY"]:
                        state["file_policy"] = command

                # Return response to Claude Code
                result = {
                    "continue": False,
                    "response": response
                }
            except Exception as e:
                result = {
                    "continue": True,
                    "response": "",
                    "error": f"Command execution failed: {e}"
                }
        else:
            # Let AI handle non-commands
            result = {
                "continue": True,
                "response": ""
            }

        # Return JSON response
        print(json.dumps(result, sort_keys=True))
        sys.stdout.flush()

    except Exception as e:
        # Handle any unexpected errors
        result = {
            "continue": True,
            "response": "",
            "error": f"Unexpected error: {e}"
        }
        print(json.dumps(result, sort_keys=True))
        sys.stdout.flush()

if __name__ == "__main__":
    main()