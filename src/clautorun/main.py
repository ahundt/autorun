#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ultra-compact Agent SDK - Enhanced autorun command interceptor with efficient dispatch"""
import os
import json
import shelve
import sys
import time
import threading
import asyncio
from typing import Dict, Any, Optional
from contextlib import contextmanager
from pathlib import Path

# Import Agent SDK
try:
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, query
except ImportError:
    print("❌ Claude Agent SDK not installed. Run: pip install claude-agent-sdk")
    sys.exit(1)

# Import the ai_monitor library, with a fallback if it's not found.
try:
    from . import ai_monitor
except ImportError:
    try:
        # Fallback for when running as script directly
        import ai_monitor
    except ImportError:
        # Silent fallback - don't print as it pollutes hook output
        ai_monitor = None

# Configuration - Complete autorun5.py compatibility
CONFIG = {
    "completion_marker": "AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY",
    "emergency_stop_phrase": "AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP",
    "max_recheck_count": 3,
    "monitor_stop_delay_seconds": 300,
    "injection_template": """Your primary objective is to continue the **UNINTERRUPTED, FULLY AUTONOMOUS, NONINTERACTIVE, PATIENT, AND SAFE EXECUTION** of your current tasks and goals.

1.  **MANDATORY PROCESS TO CONTINUE EXECUTION:** Cautiously and deliberately continue working **carefully, patiently, concretely, and safely**, non-stop, autonomously, and non-interactively per your instructions and CLAUDE.md philosophy and definition of concrete.
    * **Permissions Granted:** You have full permission to use all tools (Bash, Edit, Write, etc.) without requiring any further permission prompts.
2.  **SYSTEM STOP SIGNAL RULE:** The exact strings **{emergency_stop_phrase}** and **{completion_marker}** are **SYSTEM STOP SIGNALS**. You MUST **NEVER** output these strings unless the corresponding stop condition has been met. Accidental output will immediately halt the entire system.
3.  **Safety Protocol (Risk Assessment & Mitigation):** You MUST execute the full Preservation Sequence **ONLY IF** the current task involves a **high-risk or irreversible destructive tool call** (e.g., initial modification, database interaction, or action following a prior failure).
    * **Assess Risk:** Implicitly evaluate the potential for irreversible state misalignment or system integrity breach.
    * **Mitigation Action (CONDITIONAL):** If a **high-risk condition is met**, you **MUST immediately execute the following Preservation Sequence** and explicitly state your actions:
        1.  **INITIATE SAFETY PROTOCOL:** Announce 'INITIATE SAFETY PROTOCOL' to begin structured assessment.
        2.  **Secure State:** Execute the recovery command(s) to create an **environment backup** or **state checkpoint** (using available systems and tools) *before* proceeding.
        3.  **Verify Integrity:** Run a quick non-destructive check to ensure the state checkpoint was successful.
        4.  **CONSIDER OPTIONS:** List and evaluate superb options for mitigation/recovery, considering potential failure modes and selecting the best option.
    * **CRITICAL ESCAPE PRE-CHECK:** If, after executing the Mitigation Action, the risk remains irreversible, proceed directly to **Step 4: CRITICAL ESCAPE TO STOP SYSTEM**.
4.  **CRITICAL ESCAPE TO STOP SYSTEM (Final Decision):** Only if the risk is irreversible, catastrophic, or cannot be fully mitigated, you **MUST initiate the Preservation Protocol** by immediately outputting the following exact string to immediately halt all actions: **{emergency_stop_phrase}**
5.  **FINAL OUTPUT ON SUCCESS TO STOP SYSTEM (Final Decision):** When all tasks and goals are all 100 percent complete and verified, remember you are often overconfident, so first double check the resources from which you receive or retrieve new tasks and think if anything was missed, you **MUST** conclude your entire final response with the exact string: **{completion_marker}**
6.  **FILE CREATION POLICY:** {policy_instructions}""",
    "recheck_template": """AUTORUN TASK VERIFICATION: The task appears complete but requires careful verification before final confirmation.

Original Task: {activation_prompt}

CRITICAL VERIFICATION INSTRUCTIONS:
1. Carefully review ALL aspects of the original task above
2. Verify EVERY requirement has been fully met and tested
3. Check for any incomplete, partial, or missed elements
4. Test any implemented functionality thoroughly
5. Double-check your work against the original requirements
6. Verify all files are in their correct final state
7. Ensure no temporary or incomplete work remains

Only if you are ABSOLUTELY CERTAIN everything is complete, tested, and meets all requirements, output: {completion_marker}

If ANY aspect is incomplete, uncertain, or needs additional work, continue until truly finished.

This is verification attempt #{recheck_count} of {max_recheck_count}.""",
    "policies": {
        "ALLOW": ("allow-all", "ALLOW ALL: Full permission to create/modify files."),
        "JUSTIFY": ("justify-create", "JUSTIFIED: Search existing first. Include <AUTOFILE_JUSTIFICATION>reason</AUTOFILE_JUSTIFICATION> for new files."),
        "SEARCH": ("strict-search", "STRICT SEARCH: ONLY modify existing files. Use Glob/Grep. NO new files.")
    },
    "policy_blocked": {
        "SEARCH": 'Blocked: STRICT SEARCH policy active. To proceed: 1) Identify what functionality this file provides, 2) Search for existing files handling similar functionality using Glob patterns like "*related-topic*", 3) Use Grep to find files with relevant classes/functions/imports, 4) Modify the most appropriate existing file. Search examples: "*auth*" for authentication, "*api*" for endpoints, "*config*" for settings, "*model*" for data structures.',
        "JUSTIFY": "Blocked: JUSTIFIED CREATION policy requires justification. To proceed: 1) Search for existing files using Glob/Grep related to your functionality, 2) Evaluate if existing files can be extended, 3) If no existing file works, include <AUTOFILE_JUSTIFICATION>Specific technical reason why existing files cannot accommodate this functionality</AUTOFILE_JUSTIFICATION> in your reasoning during the same prompt where you request the file creation, then retry file creation."
    },
    "command_mappings": {
        "/autorun": "activate",
        "/autostop": "stop",
        "/estop": "emergency_stop",
        "/afs": "SEARCH",
        "/afa": "ALLOW",
        "/afj": "JUSTIFY",
        "/afst": "status"
    }
}

# State management - copied from autorun5.py
STATE_DIR = Path.home() / ".claude" / "sessions"
STATE_DIR.mkdir(parents=True, exist_ok=True)

def log_info(message):
    """Log info message to file - copied from autorun5.py"""
    try:
        with open(STATE_DIR / "autorun.log", "a") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {os.getpid()}: {message}\n")
    except: pass

# Global lock to ensure only one backend selection happens at a time
_backend_selection_lock = threading.Lock()
# Registry to track which backend works for each session_id
_session_backends = {}

@contextmanager
def session_state(session_id: str):
    """Session state with shelve - copied from autorun5.py with thread-safe backend selection"""
    # Thread-safe backend selection (happens once per session_id)
    with _backend_selection_lock:
        if session_id not in _session_backends:
            # Test different backends and pick one that works for this platform
            try:
                # Try default shelve backend first - but be more robust about testing
                test_db = STATE_DIR / f"test_backend_{session_id}.db"
                test_state = shelve.open(str(test_db), writeback=True)
                test_state["test"] = "test"  # Actually write something to test
                test_state.sync()
                test_state.close()
                os.remove(test_db)  # Clean up test file
                _session_backends[session_id] = "default"
                log_info(f"Session {session_id}: Using default shelve backend")
            except Exception as e:
                log_info(f"Session {session_id}: Default backend failed: {e}")
                try:
                    # Try dumbdbm fallback
                    import dbm.dumb
                    test_db = STATE_DIR / f"test_dumbdbm_{session_id}.db"
                    test_state = shelve.open(str(test_db), writeback=True)
                    test_state["test"] = "test"  # Actually write something to test
                    test_state.sync()
                    test_state.close()
                    os.remove(test_db)  # Clean up test file
                    _session_backends[session_id] = "dumbdbm"
                    log_info(f"Session {session_id}: Using dumbdbm backend")
                except Exception as e2:
                    log_info(f"Session {session_id}: Dumbdbm failed: {e2}")
                    # Try to use default shelve anyway without testing (some systems have issues with test/create/delete)
                    try:
                        _session_backends[session_id] = "default"
                        log_info(f"Session {session_id}: Trying default shelve without test")
                    except Exception as e3:
                        # Last resort: use in-memory with thread-safe dict
                        _session_backends[session_id] = "memory"
                        log_info(f"Session {session_id}: Using in-memory fallback")

    # Use the selected backend consistently for this session_id
    backend = _session_backends[session_id]

    state = None
    try:
        if backend == "default":
            state = shelve.open(str(STATE_DIR / f"{session_id}.db"), writeback=True)
        elif backend == "dumbdbm":
            import dbm.dumb
            state = shelve.open(str(STATE_DIR / f"{session_id}_dumb.db"), writeback=True)
        else:  # memory
            state = {}

        yield state

    finally:
        if state and hasattr(state, 'sync'):
            state.sync()
            state.close()

# Response builders - follow autorun5.py pattern exactly (lines 118-128)
def build_hook_response(continue_execution=True, stop_reason="", system_message=""):
    """Build standardized JSON hook response - autorun5.py line 118-121"""
    return {"continue": continue_execution, "stopReason": json.dumps(stop_reason)[1:-1],
            "suppressOutput": False, "systemMessage": json.dumps(system_message)[1:-1]}

def build_pretooluse_response(decision="allow", reason=""):
    """Build PreToolUse hook response - autorun5.py line 123-128"""
    return {"continue": True, "stopReason": "", "suppressOutput": False,
            "systemMessage": json.dumps(reason)[1:-1] if reason else "",
            "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                  "permissionDecisionReason": json.dumps(reason)[1:-1] if reason else ""}}

# Ultra-efficient dispatch system - using autorun5.py patterns
HANDLERS = {}
def handler(name):
    """Decorator to register handlers - copied from autorun5.py"""
    def dec(f):
        HANDLERS[name] = f
        return f
    return dec

def _manage_monitor(state: dict, action: str):
    """Centralized helper for all ai-monitor process management."""
    if not ai_monitor or "session_id" not in state:
        return
    session_id = state["session_id"]
    if action == 'start':
        if state.get("ai_monitor_pid"):
            ai_monitor.stop_monitor(session_id)
        pid = ai_monitor.start_monitor(
            session_id=session_id, prompt="continue working",
            stop_marker=CONFIG["completion_marker"], max_cycles=20, prompt_on_start=True
        )
        log_info(f"Started ai-monitor for session {session_id} with PID: {pid}")
        state["ai_monitor_pid"] = pid
    elif action == 'stop':
        if state.get("ai_monitor_pid"):
            ai_monitor.stop_monitor(session_id)
            log_info(f"Stopped monitor for session {session_id}")
            state["ai_monitor_pid"] = None


# Command handlers - copied from autorun5.py
def handle_search(state):
    """Handle SEARCH command - update state and return response"""
    state["file_policy"] = "SEARCH"
    return f"AutoFile policy: strict-search - {CONFIG['policies']['SEARCH'][1]}"

def handle_allow(state):
    """Handle ALLOW command - update state and return response"""
    state["file_policy"] = "ALLOW"
    return f"AutoFile policy: allow-all - {CONFIG['policies']['ALLOW'][1]}"

def handle_justify(state):
    """Handle JUSTIFY command - update state and return response"""
    state["file_policy"] = "JUSTIFY"
    return f"AutoFile policy: justify-create - {CONFIG['policies']['JUSTIFY'][1]}"

def handle_status(state):
    """Handle STATUS command - return current policy"""
    policy = state.get("file_policy", "ALLOW")
    policy_name = CONFIG["policies"][policy][0]
    return f"Current policy: {policy_name}"

def handle_stop(state):
    """Handle STOP command - update state and return response"""
    # Note: session_id must be in state (added by intercept_commands caller)
    _manage_monitor(state, 'stop')
    state["session_status"] = "stopped"
    return "Autorun stopped"

def handle_emergency_stop(state):
    """Handle EMERGENCY_STOP command - update state and return response"""
    log_info(f"Emergency stop: autorun session")
    # Note: session_id must be in state (added by intercept_commands caller)
    _manage_monitor(state, 'stop')
    state["session_status"] = "emergency_stopped"
    return "Emergency stop activated"

def handle_activate(state, prompt=""):
    """Handle AUTORUN activation - complete autorun setup with injection template"""
    log_info(f"Activating autorun: autorun session")

    # Preserve file_policy and session_id before clearing
    old_file_policy = state.get("file_policy", "ALLOW")
    old_session_id = state.get("session_id")

    # Clear and setup state like autorun5.py
    state.clear()
    state.update({
        "session_status": "active",
        "autorun_stage": "INITIAL",
        "activation_prompt": prompt,
        "verification_attempts": 0,
        "file_policy": old_file_policy,
        "session_id": old_session_id  # Restore session_id for monitor
    })

    # Start monitor AFTER state is set up with session_id
    _manage_monitor(state, 'start')

    # Generate injection template with current policy
    policy = state["file_policy"]
    policy_instructions = CONFIG["policies"][policy][1]

    injection = CONFIG["injection_template"].format(
        emergency_stop_phrase=CONFIG["emergency_stop_phrase"],
        completion_marker=CONFIG["completion_marker"],
        policy_instructions=policy_instructions
    )

    return injection

# Command handlers - clean dispatch like autorun5.py
COMMAND_HANDLERS = {
    "SEARCH": handle_search,
    "ALLOW": handle_allow,
    "JUSTIFY": handle_justify,
    "STATUS": handle_status,
    "status": handle_status,  # Add lowercase version for /afst command
    "stop": handle_stop,         # Add lowercase version for /autostop command
    "STOP": handle_stop,
    "emergency_stop": handle_emergency_stop,  # Add lowercase version for /estop command
    "EMERGENCY_STOP": handle_emergency_stop,
    "activate": handle_activate
}


# Claude Code hook handlers - ultra-compact
@handler("UserPromptSubmit")
def claude_code_handler(ctx):
    """Claude Code UserPromptSubmit hook - sync version like autorun5.py"""
    prompt = ctx.prompt.strip()
    session_id = ctx.session_id

    # Efficient command detection - autorun5.py line 144 pattern
    command = next((v for k, v in CONFIG["command_mappings"].items() if k == prompt), None)
    if not command:
        # Check for commands that support arguments (autorun)
        command = next((v for k, v in CONFIG["command_mappings"].items() if prompt.startswith(k)), None)

    if command and command in COMMAND_HANDLERS:
        # Handle command locally, don't send to AI
        with session_state(session_id) as state:
            state['session_id'] = session_id
            if command == "activate":
                response = COMMAND_HANDLERS[command](state, prompt)
                # Autorun command should NOT continue to AI - injection template is complete
                return build_hook_response(False, "", response)
            elif command in ["stop", "emergency_stop"]:
                response = COMMAND_HANDLERS[command](state)
                # Stop commands should NOT continue to AI
                return build_hook_response(False, "", response)
            else:
                response = COMMAND_HANDLERS[command](state)
                # Policy and status commands should continue to AI
                return build_hook_response(True, "", response)

    # Let AI handle non-commands
    return build_hook_response()

@handler("PreToolUse")
def pretooluse_handler(ctx):
    """PreToolUse hook - enhanced policy enforcement based on test expectations"""
    # Extract file path - autorun5.py line 117
    file_path = ctx.tool_input.get("file_path", "")

    # Apply file creation policies - enhanced based on test expectations
    session_id = ctx.session_id
    with session_state(session_id) as state:
        file_policy = state.get("file_policy", "ALLOW")

        # For non-Write tools, only apply policy if there's no file path
        if ctx.tool_name != "Write":
            if not file_path:
                # No file path - apply policy restrictions
                if file_policy == "SEARCH":
                    return build_pretooluse_response("deny", f"SEARCH policy: {CONFIG['policies']['SEARCH'][1]}")
                elif file_policy == "JUSTIFY":
                    justification_found = state.get("autofile_justification_detected", False) or \
                                        "AUTOFILE_JUSTIFICATION" in str(ctx.session_transcript)
                    if not justification_found:
                        return build_pretooluse_response("deny", f"JUSTIFY policy: {CONFIG['policies']['JUSTIFY'][1]}")
                # ALLOW policy or default - allow
                return build_pretooluse_response("allow", "Non-Write tool without file path allowed")
            else:
                # Non-Write tool with file path - always allow
                return build_pretooluse_response("allow", "Non-Write tool with file path allowed")

        # Write tools - always apply policy
        if file_policy == "SEARCH":
            # SEARCH policy blocks new file creation but allows editing existing files
            if file_path and Path(file_path).exists():
                # File exists - allow editing
                return build_pretooluse_response("allow", "Existing file modification allowed under SEARCH policy")
            else:
                # No file path or file doesn't exist - block new file creation
                # Use policy description which contains "NO new files" as expected by tests
                return build_pretooluse_response("deny", f"SEARCH policy: {CONFIG['policies']['SEARCH'][1]}")

        elif file_policy == "JUSTIFY":
            justification_found = state.get("autofile_justification_detected", False) or \
                                "AUTOFILE_JUSTIFICATION" in str(ctx.session_transcript)
            if not justification_found:
                return build_pretooluse_response("deny", f"JUSTIFY policy: {CONFIG['policies']['JUSTIFY'][1]}")

        # ALLOW policy - allow all operations
        return build_pretooluse_response("allow", "File operations allowed under ALLOW policy")

# ai_monitor integration: Continuation enforcement functions
def inject_continue_prompt(state):
    """Inject continue working prompt - ai_monitor functionality"""
    log_info("Injecting continue working prompt - preventing premature stop")

    # CRITICAL: Use full injection template with stop signal instructions
    # This is NOT a simple continue message - it includes critical stop conditions
    policy = state.get("file_policy", "ALLOW")
    policy_instructions = CONFIG["policies"][policy][1]

    continue_message = CONFIG["injection_template"].format(
        emergency_stop_phrase=CONFIG["emergency_stop_phrase"],
        completion_marker=CONFIG["completion_marker"],
        policy_instructions=policy_instructions
    )

    return build_hook_response(
        continue_execution=True,
        system_message=continue_message
    )

def inject_verification_prompt(state):
    """Inject verification prompt - two-stage verification"""
    log_info(f"Injecting verification prompt - attempt {state.get('verification_attempts', 1)}")

    verification_prompt = CONFIG["recheck_template"].format(
        activation_prompt=state.get("activation_prompt", "original task"),
        completion_marker=CONFIG["completion_marker"],
        recheck_count=state.get("verification_attempts", 1),
        max_recheck_count=CONFIG["max_recheck_count"]
    )

    return build_hook_response(
        continue_execution=True,
        system_message=verification_prompt
    )

def is_premature_stop(ctx, state):
    """Check if this is a premature stop - ai_monitor logic"""
    # Only active autorun sessions are protected
    if state.get("session_status") != "active":
        return False

    # Get transcript for analysis
    transcript = str(getattr(ctx, 'session_transcript', []))

    # Check if completion marker is present
    if CONFIG["completion_marker"] in transcript:
        return False  # Proper completion

    # Check if emergency stop was used
    if CONFIG["emergency_stop_phrase"] in transcript:
        return False  # Intentional emergency stop

    return True  # Premature stop - needs intervention

def should_trigger_verification(state):
    """Check if we should trigger verification stage"""
    return (state.get("autorun_stage") == "INITIAL" and
            state.get("verification_attempts", 0) < CONFIG["max_recheck_count"])

@handler("Stop")
@handler("SubagentStop")
def stop_handler(ctx):
    """Enhanced stop handler with ai_monitor continuation enforcement"""
    session_id = getattr(ctx, 'session_id', 'default')

    with session_state(session_id) as state:
        # Ensure session_id is in state for _manage_monitor
        state['session_id'] = session_id

        # Only intervene in active autorun sessions
        if state.get("session_status") != "active":
            # Normal cleanup for non-autorun sessions
            state.clear()
            return build_hook_response()

        # Check if this is a premature stop that needs intervention
        if is_premature_stop(ctx, state):
            log_info(f"Detected premature stop for session {session_id}")

            # Check if we should trigger verification stage
            if should_trigger_verification(state):
                # Move to verification stage
                state["autorun_stage"] = "VERIFICATION"
                state["verification_attempts"] = state.get("verification_attempts", 0) + 1
                log_info(f"Moving to verification stage, attempt {state['verification_attempts']}")

                return inject_verification_prompt(state)
            else:
                # Already in verification or max attempts reached - inject continue prompt
                return inject_continue_prompt(state)

        # Check if we're in verification stage and completion marker is present
        if (state.get("autorun_stage") == "VERIFICATION" and
            CONFIG["completion_marker"] in str(getattr(ctx, 'session_transcript', []))):
            log_info(f"Verification completed for session {session_id}")
            _manage_monitor(state, 'stop')
            state.clear()  # Clean up successful completion
            return build_hook_response(continue_execution=False,
                                     system_message="✅ Task completed and verified successfully!")

        # Normal cleanup for non-autorun sessions or completed sessions
        state.clear()
        return build_hook_response()

# Default handler
def default_handler(ctx): return build_hook_response()

def main():
    """Entry point - unified with efficient dispatch"""
    operation_mode = os.getenv("AGENT_MODE", "SDK_ONLY").upper()

    if operation_mode == "HOOK_INTEGRATION":
        # Run as Claude Code hook - same as autorun5.py main()
        try:
            payload = json.loads(sys.stdin.read())
            event = payload.get("hook_event_name", "?")
            _session_id = payload.get("session_id", "?")

            # Context object - same as autorun5.py
            class Ctx:
                def __init__(self, p):
                    self.hook_event_name = p.get("hook_event_name", "")
                    self.session_id = p.get("session_id", "")
                    self.prompt = p.get("prompt", "")
                    self.tool_name = p.get("tool_name", "")
                    self.tool_input = p.get("tool_input", {})
                    self.session_transcript = p.get("session_transcript", [])

            ctx = Ctx(payload)
            handler = HANDLERS.get(event, default_handler)
            response = handler(ctx)

            print(json.dumps(response, sort_keys=True))
            sys.stdout.flush()

        except Exception:
            print(json.dumps(build_hook_response()))
            sys.exit(1)

    else:
        # Run as standalone Agent SDK - Interactive mode
        run_interactive_sdk(operation_mode)

def run_interactive_sdk(operation_mode: str):
    """Run interactive Agent SDK with clean async/sync separation - autorun5.py efficiency"""
    print("🚀 Agent SDK Command Interceptor - Interactive Mode")
    print("=" * 55)
    print("Commands handled locally (no AI tokens):")

    for cmd, action in CONFIG["command_mappings"].items():
        policy_info = ""
        if action in CONFIG["policies"]:
            policy_name, policy_desc = CONFIG["policies"][action]
            policy_info = f" - {policy_desc}"
        print(f"  {cmd} → {action}{policy_info}")

    print(f"\nEnvironment: AGENT_MODE={operation_mode}")
    print("Type commands (e.g., '/afs', '/afa', '/afj', '/afst') or 'quit' to exit")
    print("Non-commands will be processed by Claude Code via Agent SDK\n")

    # Initialize session state - autorun5.py pattern
    session_id = "interactive_session"
    with session_state(session_id) as state:
        state["file_policy"] = "ALLOW"

    print("✅ Ready for commands...")
    print("💡 One Ctrl+C = interrupt, two Ctrl+C = goodbye\n")

    # Track Ctrl+C count for double-press detection
    ctrl_c_count = 0
    last_ctrl_c_time = 0

    # Main interactive loop - sync input, async processing
    while True:
        try:
            # Get user input synchronously
            user_input = input("❓ ").strip()

            # Reset Ctrl+C count on successful input
            ctrl_c_count = 0

            # Exit conditions
            if user_input.lower() in ('quit', 'exit', 'q'):
                print("👋 Goodbye!")
                break

            if not user_input:
                continue

            # Efficient command detection - autorun5.py line 144 pattern
            command = next((v for k, v in CONFIG["command_mappings"].items() if k == user_input), None)
            if not command:
                # Check for commands that support arguments (autorun)
                command = next((v for k, v in CONFIG["command_mappings"].items() if user_input.startswith(k)), None)

            if command and command in COMMAND_HANDLERS:
                # Handle locally using dispatch pattern - autorun5.py efficiency
                with session_state(session_id) as state:
                    if command == "activate":
                        # Pass the full prompt for activation
                        response = COMMAND_HANDLERS[command](state, user_input)
                    else:
                        response = COMMAND_HANDLERS[command](state)
                    print(f"✅ {response}")

            else:
                # Send to Claude Code via Agent SDK - async operation
                print("🤖 Processing with Claude Code...")
                try:
                    # Run async operation in sync context
                    asyncio.run(process_with_claude_sdk(user_input, session_id))
                except Exception as e:
                    print(f"❌ Claude Code error: {e}")
                    print("💡 Make sure Claude Code is running and accessible")

        except KeyboardInterrupt:
            import time
            current_time = time.time()

            # Check if this is a rapid second Ctrl+C (within 1 second)
            if current_time - last_ctrl_c_time < 1.0:
                ctrl_c_count += 1
            else:
                ctrl_c_count = 1

            last_ctrl_c_time = current_time

            if ctrl_c_count >= 2:
                print("\n👋 Goodbye!")
                break
            else:
                print("\n⚠️ Interrupted. One more Ctrl+C to exit, or continue with a command.")

        except EOFError:
            print("\n👋 Goodbye!")
            break
        except Exception as e:
            print(f"❌ Error: {e}")

async def process_with_claude_sdk(prompt: str, session_id: str):
    """Process non-command prompts with Claude Code via Agent SDK"""
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

    try:
        # Use ClaudeSDKClient for better control
        options = ClaudeAgentOptions()
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt, session_id=session_id)

            async for message in client.receive_response():
                if hasattr(message, 'content'):
                    for block in message.content:
                        if hasattr(block, 'text'):
                            print(block.text, end='', flush=True)
                elif hasattr(message, 'total_cost_usd'):
                    print(f"\n💰 Cost: ${message.total_cost_usd:.4f}")
                    break  # End of response
            print()  # New line at end

    except Exception as e:
        print(f"❌ Agent SDK error: {e}")
        # Fallback - suggest using Claude Code directly
        print("💡 You can ask this question directly in Claude Code")

if __name__ == "__main__":
    main()