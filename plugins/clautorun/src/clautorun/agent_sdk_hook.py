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
"""Clautorun Hook Delegation Pattern - Alternative Architecture

ALTERNATIVE HOOK PATTERN using delegation to main.py handlers.
- Thin wrapper (83 lines) that delegates all hook logic to main.py
- Demonstrates clean delegation pattern vs inline duplication
- NOT currently used by hooks.json (main.py called directly instead)

Delegation Pattern:
- agent_sdk_user_prompt_submit() → claude_code_handler() [main.py]
- agent_sdk_pre_tool_use() → pretooluse_handler() [main.py]
- agent_sdk_stop_event() → stop_handler() [main.py]

This file shows best practice for creating thin hook wrappers.
Could replace claude_code_plugin.py if CLI tool needs were separated.

Current hook configuration: hooks.json calls main.py directly (simpler).
"""
import json
import sys

# Import ALL required functionality from clautorun package for complete AI monitor workflow
from clautorun import (
    build_hook_response, stop_handler, pretooluse_handler, claude_code_handler
)

# Hook-specific handler registry that delegates to main.py logic
def agent_sdk_user_prompt_submit(ctx):
    """Hook handler using main.py UserPromptSubmit logic - complete AI monitor workflow"""
    # Delegate to main.py's proven UserPromptSubmit handler
    # The result is already in unified format that works for all contexts
    result = claude_code_handler(ctx)

    return result

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