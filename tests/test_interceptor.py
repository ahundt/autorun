#!/usr/bin/env python3
"""Test script to demonstrate Agent SDK command interception"""
import asyncio
import sys

# Mock the session_state to avoid db issues
def mock_session_state(session_id):
    """Mock session state for testing"""
    class MockState:
        def __init__(self):
            self.data = {}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def __setitem__(self, key, value):
            self.data[key] = value
        def __getitem__(self, key):
            return self.data[key]
        def get(self, key, default=None):
            return self.data.get(key, default)
        def clear(self):
            self.data.clear()
    return MockState()

# Patch the session_state function temporarily
sys.path.insert(0, '.')
from main import intercept_commands

async def test_commands():
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

    for prompt, expected in test_cases:
        # Create test context
        context = {"session_id": "test_session"}

        # Test the command interception
        result = await intercept_commands(
            {"prompt": prompt, "session_id": "test_session"},
            context
        )

        print(f"Command: {prompt}")
        print(f"Expected: {expected}")
        print(f"Result: {result}")
        print("-" * 30)

if __name__ == "__main__":
    asyncio.run(test_commands())