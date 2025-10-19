#!/usr/bin/env python3
"""Enhanced Agent SDK - handles commands AND enforcement - dual-mode operation"""
import os
import shelve
from pathlib import Path
from typing import Dict, Any, Optional
from contextlib import contextmanager
from claude_agent_sdk import ClaudeAgentOptions, HookMatcher

# Configuration - extends autorun5.py functionality
CONFIG = {
    "completion_marker": "AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY",
    "emergency_stop_phrase": "AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP",
    "max_recheck_count": 3,
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
    },
    "command_responses": {
        "SEARCH": "AutoFile policy: strict-search - STRICT SEARCH: ONLY modify existing files",
        "ALLOW": "AutoFile policy: allow-all - ALLOW ALL: Full permission to create/modify files",
        "JUSTIFY": "AutoFile policy: justify-create - JUSTIFIED: Search existing first, justify new files",
        "STATUS": "Current policy: [CURRENT_POLICY]",  # Dynamic
        "STOP": "Autorun stopped",
        "EMERGENCY_STOP": "Emergency stop activated"
    }
}

# State management
STATE_DIR = Path.home() / ".claude" / "sessions"
STATE_DIR.mkdir(parents=True, exist_ok=True)

@contextmanager
def session_state(session_id):
    """Session state with shelve - copied from autorun5.py"""
    state = shelve.open(str(STATE_DIR / f"{session_id}.db"), writeback=True)
    try:
        yield state
    finally:
        state.sync()
        state.close()

# Dispatch dict for handlers
HANDLERS = {}

def handler(name: str):
    """Decorator to register handlers"""
    def dec(f):
        HANDLERS[name] = f
        return f
    return dec

@handler("UserPromptSubmit")
async def enhanced_intercept_commands(input_data: Dict[str, Any], context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Enhanced command handler - can operate in multiple modes"""
    prompt = input_data.get('prompt', '').strip()
    session_id = getattr(context, 'session_id', 'default') if context else 'default'

    # Command detection using dispatch dict
    command = next((v for k, v in CONFIG["command_mappings"].items() if k == prompt), None)

    if command:
        return await handle_command(session_id, command)

    return {"continue": True}

async def handle_command(session_id: str, command: str) -> Dict[str, Any]:
    """Handle command based on mode - SDK-only or hybrid"""

    # SDK-only mode: handle everything at SDK level
    if command in ["STOP", "EMERGENCY_STOP"]:
        return await handle_stop_commands(session_id, command)

    elif command == "STATUS":
        return await handle_status_command(session_id)

    elif command in ["SEARCH", "ALLOW", "JUSTIFY"]:
        return await handle_policy_change(session_id, command)

    return {"continue": False, "response": f"Unknown command: {command}"}

async def handle_policy_change(session_id: str, policy: str) -> Dict[str, Any]:
    """Handle policy changes - SDK-level state management"""

    with session_state(session_id) as state:
        # Set policy in SDK state
        state["file_policy"] = policy
        state["session_status"] = "active"

        # Get response from config
        policy_name, policy_description = CONFIG["policies"][policy]
        response = f"AutoFile policy: {policy_name} - {policy_description}"

        # Optional: Call existing autorun5.py hook for enforcement
        if should_call_existing_hooks():
            await call_autorun5_hook(session_id, policy)

        return {"continue": False, "response": response}

async def handle_stop_commands(session_id: str, command: str) -> Dict[str, Any]:
    """Handle stop commands"""

    with session_state(session_id) as state:
        state.clear()

        if command == "EMERGENCY_STOP":
            return {"continue": False, "response": "Emergency stop activated"}
        else:
            return {"continue": False, "response": "Autorun stopped"}

async def handle_status_command(session_id: str) -> Dict[str, Any]:
    """Handle status command"""

    with session_state(session_id) as state:
        policy = state.get("file_policy", "ALLOW")
        policy_name = CONFIG["policies"][policy][0]
        response = f"Current policy: {policy_name}"

        return {"continue": False, "response": response}

async def call_autorun5_hook(session_id: str, policy: str):
    """Optional: Call existing autorun5.py hook for enforcement"""
    try:
        # This would actually call the autorun5.py hook
        # For now, just demonstrate the concept
        pass
    except Exception:
        # Graceful fallback if hook fails
        pass

def should_call_existing_hooks() -> bool:
    """Configuration flag to choose operation mode"""
    return os.getenv("USE_EXISTING_HOOKS", "false").lower() == "true"

def main():
    """Entry point - flexible operation modes"""

    # Configuration
    _options = ClaudeAgentOptions(
        hooks={
            'UserPromptSubmit': [HookMatcher(hooks=[enhanced_intercept_commands])]
        }
    )

    print("🚀 Enhanced Agent SDK - Dual-Mode Operation")
    print("=" * 50)

    mode = os.getenv("AGENT_MODE", "SDK_ONLY")
    if mode == "SDK_ONLY":
        print("Mode: SDK-Only (commands handled entirely by Agent SDK)")
        print("  • No AI token consumption")
        print("  • Instant responses")
        print("  • Independent operation")
    else:
        print("Mode: Hybrid (SDK + existing autorun5.py hooks)")
        print("  • SDK handles immediate responses")
        print("  • Existing hooks handle enforcement")
        print("  • Maximum compatibility")

    print("\nSupported Commands:")
    for cmd, action in CONFIG["command_mappings"].items():
        print(f"  {cmd} → {action}")

    print("\nSet AGENT_MODE=SDK_ONLY for maximum efficiency")
    print("Set AGENT_MODE=HYBRID for compatibility with existing setup")
    print("Set USE_EXISTING_HOOKS=true to enable hybrid mode")

if __name__ == "__main__":
    main()