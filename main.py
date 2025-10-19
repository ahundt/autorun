#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ultra-compact Agent SDK - Enhanced autorun command interceptor with efficient dispatch"""
import os
import json
import shelve
import sys
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

# Configuration - DRY principle like autorun5.py
CONFIG = {
    "completion_marker": "AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY",
    "emergency_stop_phrase": "AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP",
    "policies": {
        "ALLOW": ("allow-all", "ALLOW ALL: Full permission to create/modify files."),
        "JUSTIFY": ("justify-create", "JUSTIFIED: Search existing first. Include <AUTOFILE_JUSTIFICATION>reason</AUTOFILE_JUSTIFICATION> for new files."),
        "SEARCH": ("strict-search", "STRICT SEARCH: ONLY modify existing files. Use Glob/Grep. NO new files.")
    },
    "command_mappings": {
        "/afs": "SEARCH",
        "/afa": "ALLOW",
        "/afj": "JUSTIFY",
        "/afst": "STATUS",
        "/autostop": "STOP",
        "/estop": "EMERGENCY_STOP"
    }
}

# State management - copied from autorun5.py
STATE_DIR = Path.home() / ".claude" / "sessions"
STATE_DIR.mkdir(parents=True, exist_ok=True)

@contextmanager
def session_state(session_id: str):
    """Session state with shelve - copied from autorun5.py"""
    state = shelve.open(str(STATE_DIR / f"{session_id}.db"), writeback=True)
    try:
        yield state
    finally:
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

# Command handlers - streamlined dispatch
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
    state["session_status"] = "emergency_stopped"
    return "Emergency stop activated"

# Command handlers - clean dispatch like autorun5.py
COMMAND_HANDLERS = {
    "SEARCH": handle_search,
    "ALLOW": handle_allow,
    "JUSTIFY": handle_justify,
    "STATUS": handle_status,
    "STOP": handle_stop,
    "EMERGENCY_STOP": handle_emergency_stop
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