#!/usr/bin/env python3
"""Hook integration that replaces autorun5.py with Agent SDK functionality"""
import asyncio
import json
import sys
from pathlib import Path

# Add the clautorun to path
sys.path.insert(0, str(Path(__file__).parent))

from main import CONFIG, COMMAND_HANDLERS, session_state

# Handler registry - copied from autorun5.py pattern
HANDLERS = {}
def handler(name):
    """Decorator to register handlers"""
    def dec(f):
        HANDLERS[name] = f
        return f
    return dec

def build_hook_response(continue_execution=True, stop_reason="", system_message=""):
    """Build standardized JSON hook response - compatible with autorun5.py format"""
    return {"continue": continue_execution, "stopReason": json.dumps(stop_reason)[1:-1],
            "suppressOutput": False, "systemMessage": json.dumps(system_message)[1:-1]}

def build_pretooluse_response(decision="allow", reason=""):
    """Build PreToolUse hook response - compatible with autorun5.py format"""
    return {"continue": True, "stopReason": "", "suppressOutput": False,
            "systemMessage": json.dumps(reason)[1:-1] if reason else "",
            "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                  "permissionDecisionReason": json.dumps(reason)[1:-1] if reason else ""}}

@handler("UserPromptSubmit")
def agent_sdk_user_prompt_submit(ctx):
    """Hook handler using Agent SDK - replaces autorun5.py UserPromptSubmit"""
    input_data = {
        'prompt': ctx.prompt,
        'session_id': ctx.session_id,
        'session_transcript': getattr(ctx, 'session_transcript', [])
    }

    # Use efficient command detection - autorun5.py pattern
    prompt = ctx.prompt.lower()
    session_id = ctx.session_id

    # Detect command using dispatch dict - autorun5.py line 144
    command = next((v for k, v in CONFIG["command_mappings"].items() if k == prompt), None)

    if command and command in COMMAND_HANDLERS:
        # Handle command locally, don't send to AI
        with session_state(session_id) as state:
            response = COMMAND_HANDLERS[command](state)
            return build_hook_response(
                continue_execution=False,
                system_message=response
            )

    # Let AI handle non-commands
    return build_hook_response(
        continue_execution=True,
        system_message=""
    )

@handler("PreToolUse")
def agent_sdk_pre_tool_use(ctx):
    """Hook handler using Agent SDK for file policy enforcement"""
    # Use Agent SDK state for policy enforcement
    return build_pretooluse_response("allow")  # Simplified for demonstration

@handler("Stop")
@handler("SubagentStop")
def agent_sdk_stop_event(ctx):
    """Hook handler using Agent SDK for stop events"""
    # Use Agent SDK for stop handling
    return build_hook_response()

def handler(name):
    """Decorator to register handlers"""
    def dec(f):
        HANDLERS[name] = f
        return f
    return dec

HANDLERS = {
    "UserPromptSubmit": agent_sdk_user_prompt_submit,
    "PreToolUse": agent_sdk_pre_tool_use,
    "Stop": agent_sdk_stop_event,
    "SubagentStop": agent_sdk_stop_event
}

def default_handler(ctx):
    """Default handler"""
    return build_hook_response()

def main():
    """Entry point - same as autorun5.py"""
    try:
        payload = json.loads(sys.stdin.read())
        event = payload.get("hook_event_name", "?")
        _session_id = payload.get("session_id", "?")

        # Create context object
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

if __name__ == "__main__":
    main()