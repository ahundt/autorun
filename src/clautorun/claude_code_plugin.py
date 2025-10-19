#!/usr/bin/env python3
"""Claude Code plugin for Agent SDK command interception"""
import json
import sys
from pathlib import Path

# Add the clautorun to path
sys.path.insert(0, str(Path(__file__).parent))

from main import CONFIG, COMMAND_HANDLERS, session_state

def main():
    """Plugin entry point for Claude Code"""

    # Read JSON input from stdin
    payload = json.loads(sys.stdin.read())

    # Extract input data
    prompt = payload.get('prompt', '')
    session_id = payload.get('session_id', 'default')

    # Efficient command detection - autorun5.py pattern
    command = next((v for k, v in CONFIG["command_mappings"].items() if k == prompt), None)

    if command and command in COMMAND_HANDLERS:
        # Handle command locally, don't send to AI
        with session_state(session_id) as state:
            if command == "activate":
                # For autorun activation, return the injection template
                response = COMMAND_HANDLERS[command](state, prompt)
            else:
                response = COMMAND_HANDLERS[command](state)

        # Return response to Claude Code
        result = {
            "continue": False,
            "response": response
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

if __name__ == "__main__":
    main()