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
"""Claude Code plugin for Agent SDK command interception"""
import json
import sys
import os
import time
from pathlib import Path

# CRITICAL: Add plugin source to Python path for imports when called as hook
# Claude Code sets CLAUDE_PLUGIN_ROOT before executing hook commands
PLUGIN_ROOT = os.environ.get('CLAUDE_PLUGIN_ROOT')
if PLUGIN_ROOT:
    src_dir = os.path.join(PLUGIN_ROOT, 'src')
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

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

# Import centralized configuration - single source of truth (DRY)
try:
    from .config import CONFIG
except ImportError:
    try:
        from config import CONFIG
    except ImportError:
        # Minimal fallback - should not normally be reached
        CONFIG = {
            "command_mappings": {
                "/autorun": "activate", "/autostop": "stop", "/estop": "emergency_stop",
                "/afs": "SEARCH", "/afa": "ALLOW", "/afj": "JUSTIFY", "/afst": "STATUS",
                "/cr:f": "SEARCH", "/cr:a": "ALLOW", "/cr:j": "JUSTIFY", "/cr:st": "STATUS",
                "/cr:go": "activate", "/cr:x": "stop", "/cr:sos": "emergency_stop",
            },
            "policies": {
                "ALLOW": ("allow-all", "ALLOW ALL: Full permission to create/modify files."),
                "JUSTIFY": ("justify-create", "JUSTIFIED: Search existing first. Include <AUTOFILE_JUSTIFICATION>reason</AUTOFILE_JUSTIFICATION> for new files."),
                "SEARCH": ("strict-search", "STRICT SEARCH: ONLY modify existing files. Use Glob/Grep. NO new files.")
            },
            "stage1_confirmation": "AUTORUN_STAGE1_COMPLETE",
            "stage2_confirmation": "AUTORUN_STAGE2_COMPLETE",
            "stage3_confirmation": "AUTORUN_STAGE3_COMPLETE",
            "completion_marker": "AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY",
            "emergency_stop": "AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP",
            "stage1_instruction": "starting tasks, analyzing user requirements, and developing comprehensive plan",
            "stage2_instruction": "Critically evaluate previous work and continue tasks as needed",
            "stage3_instruction": "Verify all tasks completed, critically evaluated, corrected and verified",
            "stage3_countdown_calls": 5,
            "policy_blocked": {
                "SEARCH": "Blocked: STRICT SEARCH policy active. Use Glob/Grep to find existing files.",
                "JUSTIFY": "Blocked: Include <AUTOFILE_JUSTIFICATION>reason</AUTOFILE_JUSTIFICATION> for new files."
            },
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
    """Handle AUTORUN activation using injection template from CONFIG"""
    # Store original prompt and set session status
    state["session_status"] = "active"
    state["autorun_stage"] = "INITIAL"
    state["activation_prompt"] = prompt
    state["file_policy"] = state.get("file_policy", "ALLOW")
    state["hook_call_count"] = 0

    policy = state["file_policy"]
    policy_name, policy_desc = CONFIG["policies"][policy]

    # Use injection template from CONFIG if available
    if "injection_template" in CONFIG:
        return CONFIG["injection_template"].format(
            emergency_stop=CONFIG.get("emergency_stop", "AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP"),
            stage1_confirmation=CONFIG.get("stage1_confirmation", "AUTORUN_STAGE1_COMPLETE"),
            stage2_confirmation=CONFIG.get("stage2_confirmation", "AUTORUN_STAGE2_COMPLETE"),
            stage3_confirmation=CONFIG.get("stage3_confirmation", "AUTORUN_STAGE3_COMPLETE"),
            stage1_instruction=CONFIG.get("stage1_instruction", "starting tasks"),
            stage2_instruction=CONFIG.get("stage2_instruction", "critically evaluate"),
            stage3_instruction=CONFIG.get("stage3_instruction", "verify completion"),
            stage3_instructions="",  # Revealed later after countdown
            policy_instructions=policy_desc,
        ) + f"\n\nOriginal task: {prompt}"

    # Fallback if no injection template - only reveal Stage 1 initially
    stage1_conf = CONFIG.get('stage1_confirmation', 'AUTORUN_STAGE1_COMPLETE')
    stage1_inst = CONFIG.get('stage1_instruction', 'starting tasks, analyzing requirements, and developing plan')
    return f"""Your primary objective is to continue the **UNINTERRUPTED, FULLY AUTONOMOUS, NONINTERACTIVE, PATIENT, AND SAFE EXECUTION** of your current tasks and goals.

FILE CREATION POLICY: {policy_desc}

**STAGE 1 - CURRENT OBJECTIVE:** {stage1_inst}
- When Stage 1 is complete, output **{stage1_conf}** to advance
- (Stage 2 and 3 instructions will be revealed as you progress)

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


def handle_stop_hook(payload: dict, session_id: str) -> dict:
    """Handle Stop/SubagentStop hook with three-stage completion verification.

    Three-stage system:
    - INITIAL: Claude outputs AUTORUN_STAGE1_COMPLETE to advance to STAGE2
    - STAGE2: Claude outputs AUTORUN_STAGE2_COMPLETE to advance to STAGE2_COMPLETED
    - STAGE2_COMPLETED: Countdown, then reveal Stage 3 instructions
    - Stage 3: Claude outputs AUTORUN_STAGE3_COMPLETE to finish

    If Claude outputs stage 3 marker before countdown complete, reset to STAGE2.
    """
    transcript_path = payload.get('transcript_path', '')
    transcript = read_transcript(transcript_path)

    try:
        with session_state(session_id) as state:
            # Only intervene in active autorun sessions
            if state.get("session_status") != "active":
                return {}

            current_stage = state.get("autorun_stage", "INITIAL")
            state['hook_call_count'] = state.get('hook_call_count', 0) + 1
            hook_call_count = state['hook_call_count']

            log_info(f"Stop hook: stage={current_stage}, calls={hook_call_count}")

            # Check for emergency stop
            if CONFIG.get("emergency_stop", "") in transcript:
                state["session_status"] = "emergency_stopped"
                state["autorun_stage"] = None
                return {"systemMessage": "Emergency stop activated."}

            # STAGE 1: Initial work
            if current_stage == "INITIAL":
                stage1_conf = CONFIG.get("stage1_confirmation", "AUTORUN_STAGE1_COMPLETE")
                stage3_conf = CONFIG.get("stage3_confirmation", "AUTORUN_STAGE3_COMPLETE")
                completion_marker = CONFIG.get("completion_marker", "")

                if stage1_conf in transcript:
                    # Advance to Stage 2
                    state["autorun_stage"] = "STAGE2"
                    state["hook_call_count"] = 0
                    msg = f"Stage 1 complete. STAGE 2: {CONFIG.get('stage2_instruction', 'Critically evaluate')}. Output **{CONFIG.get('stage2_confirmation', 'AUTORUN_STAGE2_COMPLETE')}** when done."
                    return {"decision": "block", "reason": msg, "systemMessage": msg}

                elif stage3_conf in transcript or completion_marker in transcript:
                    # Premature stage 3 attempt - reset to stage 1
                    msg = f"You must complete Stage 1 first. Output **{stage1_conf}** when Stage 1 is done."
                    return {"decision": "block", "reason": msg, "systemMessage": msg}

                else:
                    # Premature stop - continue working
                    msg = f"Autorun active. Complete Stage 1 ({CONFIG.get('stage1_instruction', 'starting tasks')}) and output **{stage1_conf}**."
                    return {"decision": "block", "reason": msg, "systemMessage": msg}

            # STAGE 2: Critical evaluation
            elif current_stage == "STAGE2":
                stage2_conf = CONFIG.get("stage2_confirmation", "AUTORUN_STAGE2_COMPLETE")

                if stage2_conf in transcript:
                    # Advance to Stage 2 completed (countdown phase)
                    state["autorun_stage"] = "STAGE2_COMPLETED"
                    state["hook_call_count"] = 0
                    countdown = CONFIG.get("stage3_countdown_calls", 5)
                    msg = f"Stage 2 complete. Continue working for {countdown} more cycles before Stage 3 instructions."
                    return {"decision": "block", "reason": msg, "systemMessage": msg}

                else:
                    # Continue stage 2
                    msg = f"Continue Stage 2: {CONFIG.get('stage2_instruction', 'Critically evaluate')}. Output **{stage2_conf}** when done."
                    return {"decision": "block", "reason": msg, "systemMessage": msg}

            # STAGE 2 COMPLETED: Countdown to stage 3
            elif current_stage == "STAGE2_COMPLETED":
                countdown = CONFIG.get("stage3_countdown_calls", 5)
                remaining = countdown - hook_call_count
                stage3_conf = CONFIG.get("stage3_confirmation", "AUTORUN_STAGE3_COMPLETE")
                completion_marker = CONFIG.get("completion_marker", "")

                if stage3_conf in transcript or completion_marker in transcript:
                    if remaining > 0:
                        # Early attempt - reset to STAGE2
                        state["autorun_stage"] = "STAGE2"
                        state["hook_call_count"] = 0
                        msg = f"Too early for Stage 3 ({remaining} cycles remain). Reset to Stage 2: {CONFIG.get('stage2_instruction', '')}."
                        return {"decision": "block", "reason": msg, "systemMessage": msg}
                    else:
                        # Proper completion
                        state["session_status"] = "completed"
                        state["autorun_stage"] = None
                        return {"systemMessage": "Three-stage completion successful!"}

                elif remaining > 0:
                    # Continue countdown
                    msg = f"Stage 3 countdown: {remaining} cycles remaining. Continue evaluation."
                    return {"decision": "block", "reason": msg, "systemMessage": msg}

                else:
                    # Reveal stage 3 instructions
                    msg = f"STAGE 3: {CONFIG.get('stage3_instruction', 'Verify completion')}. Output **{stage3_conf}** to complete."
                    return {"decision": "block", "reason": msg, "systemMessage": msg}

    except Exception as e:
        log_info(f"Stop hook error: {e}")
        return {"systemMessage": f"Hook error: {e}"}

    return {}


def handle_pretooluse_hook(payload: dict, session_id: str) -> dict:
    """Handle PreToolUse hook for file policy enforcement."""
    tool_name = payload.get('tool_name', '')
    tool_input = payload.get('tool_input', {})

    # Only enforce on Write operations
    if tool_name != 'Write':
        return {}

    file_path = tool_input.get('file_path', '')

    try:
        with session_state(session_id) as state:
            policy = state.get("file_policy", "ALLOW")

            if policy == "ALLOW":
                return {}

            elif policy == "SEARCH":
                # Block new file creation
                if file_path and not os.path.exists(file_path):
                    msg = CONFIG.get("policy_blocked", {}).get("SEARCH", f"Blocked: SEARCH policy - no new files. Use Glob/Grep.")
                    return {
                        "hookSpecificOutput": {"permissionDecision": "deny"},
                        "systemMessage": f"**[AutoFile: SEARCH]** {msg}"
                    }

            elif policy == "JUSTIFY":
                # Require justification for new files
                if file_path and not os.path.exists(file_path):
                    content = tool_input.get('content', '')
                    if '<AUTOFILE_JUSTIFICATION>' not in content:
                        msg = CONFIG.get("policy_blocked", {}).get("JUSTIFY", "Include <AUTOFILE_JUSTIFICATION>reason</AUTOFILE_JUSTIFICATION>")
                        return {"systemMessage": f"**[AutoFile: JUSTIFY]** {msg}"}

    except Exception as e:
        log_info(f"PreToolUse hook error: {e}")

    return {}


def handle_userpromptsubmit_hook(payload: dict, session_id: str) -> dict:
    """Handle UserPromptSubmit hook for command interception."""
    # UserPromptSubmit uses 'user_prompt' field
    prompt = payload.get('user_prompt', '') or payload.get('prompt', '')

    # Command detection - exact match first
    command = next((v for k, v in CONFIG["command_mappings"].items() if k == prompt), None)
    if not command:
        # Check for commands with arguments (autorun variants and /cr:go)
        for k, v in CONFIG["command_mappings"].items():
            if v == "activate" and prompt.startswith(k):
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

            return {
                "continue": False,
                "response": response,
                "systemMessage": f"[clautorun] {command} executed"
            }

        except Exception as e:
            log_info(f"Command error: {e}")
            return {
                "continue": False,
                "systemMessage": f"[clautorun] Error: {e}"
            }

    return {"continue": True, "response": ""}


def main():
    """Plugin entry point for Claude Code hooks."""
    try:
        input_data = sys.stdin.read()

        if not input_data.strip():
            print(json.dumps({}))
            sys.stdout.flush()
            return

        try:
            payload = json.loads(input_data)
        except json.JSONDecodeError as e:
            print(json.dumps({"continue": True, "error": f"Invalid JSON: {e}"}))
            sys.stdout.flush()
            return

        session_id = payload.get('session_id', 'default')
        hook_event = payload.get('hook_event_name', '')

        log_info(f"Hook: {hook_event}, session: {session_id}")

        # Route based on hook event type
        if hook_event in ('Stop', 'SubagentStop'):
            result = handle_stop_hook(payload, session_id)
        elif hook_event == 'PreToolUse':
            result = handle_pretooluse_hook(payload, session_id)
        elif hook_event == 'UserPromptSubmit':
            result = handle_userpromptsubmit_hook(payload, session_id)
        else:
            # Legacy fallback: detect from payload structure
            if 'user_prompt' in payload or 'prompt' in payload:
                result = handle_userpromptsubmit_hook(payload, session_id)
            elif 'tool_name' in payload:
                result = handle_pretooluse_hook(payload, session_id)
            elif 'transcript_path' in payload or 'reason' in payload:
                result = handle_stop_hook(payload, session_id)
            else:
                result = {"continue": True, "response": ""}

        print(json.dumps(result))
        sys.stdout.flush()

    except Exception as e:
        print(json.dumps({"continue": True, "error": f"clautorun error: {e}"}))
        sys.stdout.flush()


if __name__ == "__main__":
    main()