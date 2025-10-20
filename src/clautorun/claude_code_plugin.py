#!/usr/bin/env python3
"""Claude Code plugin for Agent SDK command interception"""
import json
import sys
from pathlib import Path

# Add the clautorun to path - handle both package and standalone execution
try:
    # Try package import first
    from .main import CONFIG, COMMAND_HANDLERS, session_state
except ImportError:
    # Fallback for standalone execution
    sys.path.insert(0, str(Path(__file__).parent))
    from main import CONFIG, COMMAND_HANDLERS, session_state

def main():
    """Plugin entry point for Claude Code"""
    try:
        # Read input from stdin
        input_data = sys.stdin.read()

        # Handle empty input
        if not input_data.strip():
            result = {
                "continue": True,
                "response": "",
                "error": "No input provided"
            }
            print(json.dumps(result, sort_keys=True))
            sys.stdout.flush()
            return

        # Parse JSON input
        try:
            payload = json.loads(input_data)
        except json.JSONDecodeError as e:
            result = {
                "continue": True,
                "response": "",
                "error": f"Invalid JSON: {e}"
            }
            print(json.dumps(result, sort_keys=True))
            sys.stdout.flush()
            return

        # Extract input data
        prompt = payload.get('prompt', '')
        session_id = payload.get('session_id', 'default')

        # Efficient command detection - sort by length (longest first) for specific matches
        command = next((v for k, v in sorted(CONFIG["command_mappings"].items(), key=lambda x: len(x[0]), reverse=True) if prompt.startswith(k)), None)

        if command and command in COMMAND_HANDLERS:
            # Handle command locally, don't send to AI
            try:
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
            except Exception as e:
                result = {
                    "continue": True,
                    "response": "",
                    "error": f"Command execution failed: {e}"
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

    except Exception as e:
        # Handle any unexpected errors
        result = {
            "continue": True,
            "response": "",
            "error": f"Unexpected error: {e}"
        }
        print(json.dumps(result, sort_keys=True))
        sys.stdout.flush()

if __name__ == "__main__":
    main()