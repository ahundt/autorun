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
        "/autorun ": "activate",
        "/autostop ": "stop",
        "/estop ": "emergency_stop",
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
                # Try default shelve backend first
                test_db = STATE_DIR / f"test_backend_{session_id}.db"
                test_state = shelve.open(str(test_db), writeback=True)
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
                    test_state.close()
                    os.remove(test_db)  # Clean up test file
                    _session_backends[session_id] = "dumbdbm"
                    log_info(f"Session {session_id}: Using dumbdbm backend")
                except Exception as e2:
                    log_info(f"Session {session_id}: Dumbdbm failed: {e2}")
                    # Last resort: use in-memory with thread-safe dict
                    _session_backends[session_id] = "memory"
                    log_info(f"Session {session_id}: Using in-memory fallback")

    # Use the selected backend consistently for this session_id
    backend = _session_backends[session_id]

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
        if hasattr(state, 'sync'):
            state.sync()
            state.close()

# Response builders - copied from autorun5.py
def build_hook_response(continue_execution=True, stop_reason="", system_message=""):
    """Build standardized JSON hook response"""
    return {"continue": continue_execution, "stopReason": json.dumps(stop_reason)[1:-1],
            "suppressOutput": False, "systemMessage": json.dumps(system_message)[1:-1]}

def build_pretooluse_response(decision="allow", reason=""):
    """Build PreToolUse hook response - copied from autorun5.py"""
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
    state["session_status"] = "stopped"
    return "Autorun stopped"

def handle_emergency_stop(state):
    """Handle EMERGENCY_STOP command - update state and return response"""
    log_info(f"Emergency stop: autorun session")
    state["session_status"] = "emergency_stopped"
    return "Emergency stop activated"

def handle_activate(state, prompt=""):
    """Handle AUTORUN activation - complete autorun setup with injection template"""
    log_info(f"Activating autorun: autorun session")

    # Clear and setup state like autorun5.py
    state.clear()
    state.update({
        "session_status": "active",
        "autorun_stage": "INITIAL",
        "activation_prompt": prompt,
        "verification_attempts": 0,
        "file_policy": state.get("file_policy", "ALLOW")
    })

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

@handler("UserPromptSubmit")
async def intercept_commands(input_data: Dict[str, Any], context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Intercept autorun commands using efficient dispatch - like autorun5.py line 144"""
    prompt = input_data.get('prompt', '').strip()
    session_id = getattr(context, 'session_id', 'default') if context else 'default'

    # Efficient command detection - same pattern as autorun5.py
    command = next((v for k, v in CONFIG["command_mappings"].items() if k == prompt), None)

    if command and command in COMMAND_HANDLERS:
        # Handle command locally, don't send to AI
        with session_state(session_id) as state:
            response = COMMAND_HANDLERS[command](state)
            return {"continue": False, "response": response}

    # Let AI handle non-commands
    return {"continue": True}

# Synchronous version for hook integration
@handler("UserPromptSubmit")
def intercept_commands_sync(input_data: Dict[str, Any], context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Synchronous version for hook integration"""
    import asyncio

    # Run the async function in the event loop
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(intercept_commands(input_data, context))

# Claude Code hook handlers - ultra-compact
@handler("UserPromptSubmit")
def claude_code_handler(ctx):
    """Claude Code UserPromptSubmit hook - simplified"""
    input_data = {'prompt': ctx.prompt, 'session_id': ctx.session_id}
    return intercept_commands_sync(input_data, ctx)

@handler("PreToolUse")
def pretooluse_handler(ctx):
    """PreToolUse hook - simplified policy enforcement"""
    if ctx.tool_name != "Write" or ctx.tool_input.get("file_path", ""):
        return build_pretooluse_response("allow")

    with session_state(ctx.session_id) as state:
        file_policy = state.get("file_policy", "ALLOW")
        if file_policy == "SEARCH":
            return build_pretooluse_response("deny", CONFIG["policies"]["SEARCH"][1])
        elif file_policy == "JUSTIFY" and "AUTOFILE_JUSTIFICATION" not in str(ctx.session_transcript):
            return build_pretooluse_response("deny", CONFIG["policies"]["JUSTIFY"][1])

    return build_pretooluse_response("allow")

@handler("Stop")
@handler("SubagentStop")
def stop_handler(ctx):
    """Stop handlers - simplified"""
    with session_state(ctx.session_id) as state:
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