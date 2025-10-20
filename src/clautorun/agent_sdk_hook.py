#!/usr/bin/env python3
"""Hook integration that fully implements autorun5.py AI monitor functionality using clautorun package"""
import json
import sys

# Import ALL required functionality from clautorun package for complete AI monitor workflow
from clautorun import (
    CONFIG, COMMAND_HANDLERS, session_state,
    build_hook_response, build_pretooluse_response,
    # Import the complete AI monitor workflow
    stop_handler, pretooluse_handler, intercept_commands_sync
)

# Hook-specific handler registry that delegates to main.py logic
def agent_sdk_user_prompt_submit(ctx):
    """Hook handler using main.py UserPromptSubmit logic - complete AI monitor workflow"""
    # Delegate to main.py's proven UserPromptSubmit handler
    result = intercept_commands_sync(
        {'prompt': ctx.prompt, 'session_id': ctx.session_id},
        ctx
    )

    # Convert internal response format to hook response format
    if result.get("continue") is False:
        # Command handled locally, return proper hook response
        return build_hook_response(
            continue_execution=False,
            system_message=result.get("response", "")
        )
    else:
        # Let AI handle it
        return build_hook_response(continue_execution=True)

def agent_sdk_pre_tool_use(ctx):
    """Hook handler using main.py PreToolUse logic - complete file policy enforcement"""
    # Delegate to main.py's proven PreToolUse handler
    return pretooluse_handler(ctx)

def agent_sdk_stop_event(ctx):
    """Hook handler using main.py Stop logic - complete AI monitor workflow"""
    # Delegate to main.py's proven stop handler with full AI monitor logic
    return stop_handler(ctx)

# Handler registry mapping hook events to main.py functionality
HOOK_HANDLERS = {
    "UserPromptSubmit": agent_sdk_user_prompt_submit,
    "PreToolUse": agent_sdk_pre_tool_use,
    "Stop": agent_sdk_stop_event,
    "SubagentStop": agent_sdk_stop_event
}

def default_handler(ctx):
    """Default handler - same as main.py"""
    return build_hook_response()

def main():
    """Entry point - identical to main.py but uses hook delegation pattern"""
    try:
        payload = json.loads(sys.stdin.read())
        event = payload.get("hook_event_name", "?")
        _session_id = payload.get("session_id", "?")

        # Create context object - identical to main.py
        class Ctx:
            def __init__(self, p):
                self.hook_event_name = p.get("hook_event_name", "")
                self.session_id = p.get("session_id", "")
                self.prompt = p.get("prompt", "")
                self.tool_name = p.get("tool_name", "")
                self.tool_input = p.get("tool_input", {})
                self.session_transcript = p.get("session_transcript", [])

        ctx = Ctx(payload)
        handler = HOOK_HANDLERS.get(event, default_handler)
        response = handler(ctx)

        print(json.dumps(response, sort_keys=True))
        sys.stdout.flush()

    except Exception:
        print(json.dumps(build_hook_response()))

if __name__ == "__main__":
    main()