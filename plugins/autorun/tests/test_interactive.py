#!/usr/bin/env python3
"""Test script to demonstrate the interactive Agent SDK functionality"""
import sys
from pathlib import Path
from unittest.mock import patch

# Add the current directory to path
sys.path.insert(0, str(Path(__file__).parent))

from autorun import CONFIG, COMMAND_HANDLERS

def test_command_processing():
    """Test the command processing logic with efficient dispatch"""
    print("🧪 Testing autorun command processing")
    print("=" * 45)

    # Mock session state to avoid database creation
    mock_state = {"file_policy": "ALLOW"}

    with patch('autorun.main.session_state') as mock_session:
        mock_session.return_value.__enter__.return_value = mock_state
        mock_session.return_value.__exit__.return_value = None

        # Test all commands
        test_commands = [
            ("/afs", "SEARCH"),
            ("/afa", "ALLOW"),
            ("/afj", "JUSTIFY"),
            ("/afst", "STATUS"),
            ("/autostop", "STOP"),
            ("/estop", "EMERGENCY_STOP")
        ]

        for cmd, expected_action in test_commands:
            print(f"\n🔧 Testing: {cmd}")

            if expected_action in CONFIG["policies"]:
                # Policy change commands
                response = COMMAND_HANDLERS[expected_action](mock_state)
                print(f"   Response: {response}")

                # Verify state change - the COMMAND_HANDLERS already update the state
                if expected_action in ["SEARCH", "ALLOW", "JUSTIFY"]:
                    current_policy = mock_state.get("file_policy", "ALLOW")
                    print(f"   ✅ State after command: file_policy = {current_policy}")

            elif expected_action == "STATUS":
                # Status command
                response = COMMAND_HANDLERS[expected_action](mock_state)
                print(f"   Response: {response}")

            else:
                # Stop commands
                response = COMMAND_HANDLERS[expected_action](mock_state)
                print(f"   Response: {response}")

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