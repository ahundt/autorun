#!/usr/bin/env python3
"""Test script to demonstrate Agent SDK command interception"""
import sys
from pathlib import Path
from unittest.mock import patch

# Add src to path for testing
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from clautorun import intercept_commands_sync

def test_commands():
    """Test the command interception functionality"""

    # Test data that simulates Claude Code input
    test_cases = [
        ("/afs", "Should respond with SEARCH policy"),
        ("/afa", "Should respond with ALLOW policy"),
        ("/afj", "Should respond with JUSTIFY policy"),
        ("/afst", "Should respond with STATUS"),
        ("/autostop", "Should respond with STOP"),
        ("/estop", "Should respond with EMERGENCY STOP"),
        ("Hello world", "Should let AI handle this (continue=True)"),
    ]

    print("🧪 Testing Agent SDK Command Interception")
    print("=" * 50)

    # Mock session state to avoid database issues
    mock_state = {}
    with patch('clautorun.main.session_state') as mock_session:
        mock_session.return_value.__enter__.return_value = mock_state
        mock_session.return_value.__exit__.return_value = None

        for prompt, expected in test_cases:
            # Create test context
            context = {"session_id": "test_session"}

            # Test the command interception (sync version)
            result = intercept_commands_sync(
                {"prompt": prompt, "session_id": "test_session"},
                context
            )

            print(f"Command: {prompt}")
            print(f"Expected: {expected}")
            print(f"Result: {result}")
            print("-" * 30)

if __name__ == "__main__":
    test_commands()