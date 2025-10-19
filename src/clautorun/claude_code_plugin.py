#!/usr/bin/env python3
"""Claude Code plugin for Agent SDK command interception"""
import json
import sys
from pathlib import Path

# Add the clautorun to path
sys.path.insert(0, str(Path(__file__).parent / "clautorun"))

from enhanced_main import enhanced_intercept_commands

def main():
    """Plugin entry point for Claude Code"""

    # Read JSON input from stdin
    payload = json.loads(sys.stdin.read())

    # Extract input data
    input_data = {
        'prompt': payload.get('prompt', ''),
        'session_id': payload.get('session_id', 'default'),
        'session_transcript': payload.get('session_transcript', [])
    }

    # Call the enhanced interceptor
    result = enhanced_intercept_commands(input_data, payload)

    # Return JSON response
    print(json.dumps(result, sort_keys=True))
    sys.stdout.flush()

if __name__ == "__main__":
    main()