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
"""MCP server for Agent SDK command interception"""
import json
import sys
from pathlib import Path
from typing import Dict, Any

# Add the clautorun to path
sys.path.insert(0, str(Path(__file__).parent))

from main import claude_code_handler

def create_mcp_server():
    """Create MCP server for Agent SDK functionality"""

    async def handle_intercept_command(params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle command interception via MCP"""
        input_data = {
            'prompt': params.get('prompt', ''),
            'session_id': params.get('session_id', 'default'),
            'session_transcript': params.get('session_transcript', [])
        }

        class Ctx:
            def __init__(self, data):
                self.prompt = data.get('prompt', '')
                self.session_id = data.get('session_id', 'default')
                self.session_transcript = data.get('session_transcript', [])

        ctx = Ctx(input_data)
        result = claude_code_handler(ctx)
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