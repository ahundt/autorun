#!/usr/bin/env python3
"""Simple test script to demonstrate Agent SDK command interception"""
import asyncio
import sys
from pathlib import Path

# Add src directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

async def test_command_logic():
    """Test the core command logic without database dependencies"""

    # Import the command handling logic directly
    from clautorun import CONFIG, COMMAND_HANDLERS

    print("🧪 Testing Agent SDK Command Logic")
    print("=" * 50)

    # Test the command detection and response logic
    test_cases = [
        ("/afs", "SEARCH", "AutoFile policy: strict-search"),
        ("/afa", "ALLOW", "AutoFile policy: allow-all"),
        ("/afj", "JUSTIFY", "AutoFile policy: justify-create"),
        ("/autostop", "STOP", "Autorun stopped"),
        ("/estop", "EMERGENCY_STOP", "Emergency stop activated"),
    ]

    for command, action, expected_response in test_cases:
        # Test command mapping
        detected_action = None
        for cmd_map, cmd_action in CONFIG["command_mappings"].items():
            if command == cmd_map:
                detected_action = cmd_action
                break

        print(f"Command: {command}")
        print(f"Expected action: {action}")
        print(f"Detected action: {detected_action}")

        if detected_action:
            # Test response generation (mock state to avoid db issues)
            class MockState:
                def get(self, key, default=None):
                    if key == 'file_policy':
                        return 'allow-all'  # Default policy
                    return default

            mock_state = MockState()
            response = COMMAND_HANDLERS[detected_action](mock_state)
            print(f"Response: {response}")
            print(f"Contains expected: {expected_response in response}")
        else:
            print("❌ Command not detected")
        print("-" * 30)

    # Test non-command handling
    print("\nTesting non-command handling:")
    non_commands = ["Hello world", "Write some code", "What's the weather?"]
    for prompt in non_commands:
        print(f"Prompt: '{prompt}'")
        # These should be handled by AI (continue=True)
        # Simulate the AI handling
        should_continue = not any(cmd in prompt for cmd in CONFIG["command_mappings"].keys())
        print(f"Should continue to AI: {should_continue}")
        print("-" * 30)

if __name__ == "__main__":
    asyncio.run(test_command_logic())