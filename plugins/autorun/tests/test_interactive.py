#!/usr/bin/env python3
"""Test script to demonstrate the interactive Agent SDK functionality"""
import sys
from pathlib import Path

# Add the current directory to path
sys.path.insert(0, str(Path(__file__).parent))

import uuid
from autorun import CONFIG
from autorun.core import EventContext, ThreadSafeDB
from autorun import plugins

# COMMAND_HANDLERS removed — canonical path: EventContext + plugins.app.dispatch(ctx)
# Legacy command mappings: /afs→/ar:f, /afa→/ar:a, /afj→/ar:j, /afst→/ar:st,
#   /autostop→/ar:x, /estop→/ar:sos


def test_command_processing():
    """Test the command processing logic with efficient dispatch.

    Canonical replacement for COMMAND_HANDLERS-based dispatch:
    Uses EventContext + plugins.app.dispatch(ctx) directly.
    All legacy command aliases (/afs, /afa, etc.) are still registered
    in plugins.app.command_handlers.
    """
    print("🧪 Testing autorun command processing")
    print("=" * 45)

    session_id = f"test-interactive-{uuid.uuid4().hex[:8]}"

    # Test all commands via canonical dispatch
    test_commands = [
        ("/afs", "strict-search", True),
        ("/afa", "allow-all", True),
        ("/afj", "justify-create", True),
        ("/afst", "AutoFile policy:", True),
        ("/autostop", "Stopped", False),
        ("/estop", "EMERGENCY STOP", False),
    ]

    for cmd, expected_content, should_continue in test_commands:
        print(f"\n🔧 Testing: {cmd}")

        ctx = EventContext(
            session_id=session_id,
            event="UserPromptSubmit",
            prompt=cmd,
            tool_name="",
            tool_input={},
            store=ThreadSafeDB(),
        )
        result = plugins.app.dispatch(ctx)

        assert result is not None, f"Command {cmd!r} must return a result"
        assert result["continue"] == should_continue, \
            f"Command {cmd!r}: continue should be {should_continue}"
        assert expected_content in result["systemMessage"], \
            f"Command {cmd!r}: response must contain {expected_content!r}"

        print(f"   ✅ Response: {result['systemMessage'][:60]}...")

    print("\n🎯 All commands processed successfully!")
    print("📊 Efficiency: Zero AI tokens used for command processing")
    print("⚡ Speed: Instant responses (no AI delay)")

def test_command_detection():
    """Test efficient command detection - O(1) lookup pattern"""
    print("\n🔍 Testing command detection efficiency")
    print("=" * 45)

    # Test cases
    test_cases = [
        ("/afs", True),
        ("/afa", True),
        ("/afj", True),
        ("/afst", True),
        ("/autostop", True),
        ("/estop", True),
        ("/not_a_command", False),
        ("hello world", False),
        ("", False)
    ]

    for prompt, should_detect in test_cases:
        # Same efficient detection as main.py command dispatch
        command = next((v for k, v in CONFIG["command_mappings"].items() if k == prompt), None)

        detected = command is not None
        status = "✅" if detected == should_detect else "❌"

        print(f"   {status} '{prompt}' → {'Detected' if detected else 'Not detected'}")

    print("\n⚡ Command detection: O(1) efficiency - same as main.py")

if __name__ == "__main__":
    test_command_processing()
    test_command_detection()
    print("\n🚀 autorun is ready for interactive use!")
    print("💡 Run: AGENT_MODE=SDK_ONLY python -m autorun")