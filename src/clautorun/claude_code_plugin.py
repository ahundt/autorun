#!/usr/bin/env python3
"""Claude Code plugin for Agent SDK command interception"""
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
            print("❌ IMPORT ERROR: clautorun module structure issue detected")
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
    }
}

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
    policy_name, policy_desc = CONFIG["policies"][current_policy]
    return f"Current policy: {policy_name}"

def handle_stop(state):
    """Handle STOP command"""
    state["session_status"] = "stopped"
    return "Autorun stopped"

def handle_emergency_stop(state):
    """Handle EMERGENCY_STOP command"""
    state["session_status"] = "emergency_stopped"
    return "Emergency stop activated"

def handle_activate(state, prompt):
    """Handle AUTORUN activation"""
    # Store original prompt and set session status
    state["session_status"] = "active"
    state["autorun_stage"] = "INITIAL"
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
                    if command in ["activate", "ACTIVATE"]:
                        # For autorun activation, return the injection template
                        response = COMMAND_HANDLERS[command](state, prompt)
                    else:
                        response = COMMAND_HANDLERS[command](state)

                    # Update state for policy commands (handle both cases)
                    if command in ["SEARCH", "ALLOW", "JUSTIFY", "search", "allow", "justify"]:
                        # Always store in uppercase for consistency
                        state["file_policy"] = command.upper()

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