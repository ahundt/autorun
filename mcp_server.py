#!/usr/bin/env python3
"""MCP server for Agent SDK command interception"""
import json
import sys
from pathlib import Path
from typing import Dict, Any

# Add the clautorun to path
sys.path.insert(0, str(Path(__file__).parent))

from enhanced_main import enhanced_intercept_commands

def create_mcp_server():
    """Create MCP server for Agent SDK functionality"""

    async def handle_intercept_command(params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle command interception via MCP"""
        input_data = {
            'prompt': params.get('prompt', ''),
            'session_id': params.get('session_id', 'default'),
            'session_transcript': params.get('session_transcript', [])
        }

        result = await enhanced_intercept_commands(input_data, params)
        return result

    return {
        "name": "agent-sdk-interceptor",
        "version": "1.0.0",
        "description": "Intercept autorun commands before they reach AI",
        "tools": [
            {
                "name": "intercept_command",
                "description": "Intercept and handle autorun commands",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "The prompt/command to intercept"
                        },
                        "session_id": {
                            "type": "string",
                            "description": "Session identifier"
                        },
                        "session_transcript": {
                            "type": "array",
                            "description": "Session transcript context"
                        }
                    },
                    "required": ["prompt"]
                }
            }
        ]
    }

def main():
    """MCP server entry point"""
    if len(sys.argv) > 1 and sys.argv[1] == "--server":
        # Run as MCP server
        print(json.dumps(create_mcp_server()))
        sys.stdout.flush()
    else:
        # Interactive mode
        print("🚀 Agent SDK MCP Server")
        print("Available commands: /afs, /afa, /afj, /afst, /autostop, /estop")
        print("Set AGENT_MODE=SDK_ONLY for maximum efficiency")

if __name__ == "__main__":
    main()