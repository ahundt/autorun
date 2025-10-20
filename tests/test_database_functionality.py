#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Real database functionality tests for clautorun session state
Tests actual shelve database operations without mocks
"""
import pytest
import tempfile
import shutil
import time
from pathlib import Path

# Add src directory to Python path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from clautorun import session_state, CONFIG, COMMAND_HANDLERS


class TestRealDatabaseOperations:
    """Test real shelve database operations without mocks"""

    def test_basic_session_state_persistence(self):
        """Test that session state persists across separate accesses"""
        session_id = "test_persistence_session"

        # First, set some data
        with session_state(session_id) as state:
            state["test_key"] = "test_value"
            state["file_policy"] = "SEARCH"
            state["timestamp"] = time.time()

        # Then, access it again in a new context
        with session_state(session_id) as state:
            assert state["test_key"] == "test_value", "Data should persist"
            assert state["file_policy"] == "SEARCH", "Policy should persist"
            assert "timestamp" in state, "Timestamp should persist"

        # Clean up
        with session_state(session_id) as state:
            state.clear()

    def test_concurrent_session_isolation(self):
        """Test that different sessions remain isolated in real database"""
        session_1 = "isolation_test_1"
        session_2 = "isolation_test_2"

        # Set different data in each session
        with session_state(session_1) as state:
            state["unique_data"] = "session_1_data"
            state["file_policy"] = "ALLOW"

        with session_state(session_2) as state:
            state["unique_data"] = "session_2_data"
            state["file_policy"] = "SEARCH"

        # Verify sessions are isolated
        with session_state(session_1) as state:
            assert state["unique_data"] == "session_1_data"
            assert state["file_policy"] == "ALLOW"

        with session_state(session_2) as state:
            assert state["unique_data"] == "session_2_data"
            assert state["file_policy"] == "SEARCH"

        # Clean up both sessions
        for session_id in [session_1, session_2]:
            with session_state(session_id) as state:
                state.clear()

    def test_policy_changes_persist(self):
        """Test that policy changes persist using real database"""
        session_id = "policy_persistence_test"

        # Test changing policies and verify they persist
        policies = ["SEARCH", "ALLOW", "JUSTIFY"]

        for policy in policies:
            with session_state(session_id) as state:
                # Change policy
                response = COMMAND_HANDLERS[policy](state)

                # Verify response contains expected policy
                assert "AutoFile policy:" in response

                # Verify state was updated
                assert state["file_policy"] == policy

            # Verify policy persists in new context
            with session_state(session_id) as state:
                assert state["file_policy"] == policy, f"Policy {policy} should persist"

        # Clean up
        with session_state(session_id) as state:
            state.clear()

    def test_status_command_with_real_state(self):
        """Test status command reflects real database state"""
        session_id = "status_test_session"

        # Set initial policy
        with session_state(session_id) as state:
            state["file_policy"] = "JUSTIFY"
            state["policy_changes"] = 3

        # Test status command
        with session_state(session_id) as state:
            response = COMMAND_HANDLERS["STATUS"](state)

            # Should reflect current state
            assert "justify-create" in response.lower(), "Status should show current policy"

        # Clean up
        with session_state(session_id) as state:
            state.clear()

    def test_session_state_recovery_after_crash(self):
        """Test that session state survives simulated crashes"""
        session_id = "crash_recovery_test"

        # Set up initial data
        with session_state(session_id) as state:
            state["important_data"] = "must_survive"
            state["file_policy"] = "SEARCH"
            state["counter"] = 42

        # Simulate "crash" by just opening a new context
        # (In real usage, this would be a new process start)
        with session_state(session_id) as state:
            assert state["important_data"] == "must_survive"
            assert state["file_policy"] == "SEARCH"
            assert state["counter"] == 42

            # Modify data
            state["counter"] = 43
            state["new_data"] = "added_after_crash"

        # Verify modifications persist
        with session_state(session_id) as state:
            assert state["counter"] == 43
            assert state["new_data"] == "added_after_crash"

        # Clean up
        with session_state(session_id) as state:
            state.clear()

    def test_multiple_policies_in_same_session(self):
        """Test multiple policy changes in same session using real database"""
        session_id = "multiple_policy_test"

        with session_state(session_id) as state:
            # Start with SEARCH
            response1 = COMMAND_HANDLERS["SEARCH"](state)
            assert state["file_policy"] == "SEARCH"

            # Change to ALLOW
            response2 = COMMAND_HANDLERS["ALLOW"](state)
            assert state["file_policy"] == "ALLOW"

            # Change to JUSTIFY
            response3 = COMMAND_HANDLERS["JUSTIFY"](state)
            assert state["file_policy"] == "JUSTIFY"

            # Verify all responses are valid
            for response in [response1, response2, response3]:
                assert "AutoFile policy:" in response
                assert len(response) > 0

        # Verify final state persists
        with session_state(session_id) as state:
            assert state["file_policy"] == "JUSTIFY"

        # Clean up
        with session_state(session_id) as state:
            state.clear()

    def test_large_data_storage(self):
        """Test storing and retrieving large amounts of data"""
        session_id = "large_data_test"

        with session_state(session_id) as state:
            # Store various types and sizes of data
            state["large_string"] = "x" * 10000  # 10KB string
            state["number_list"] = list(range(1000))  # Large list
            state["nested_dict"] = {
                "level1": {
                    "level2": {
                        "level3": {"deep_data": "exists"}
                    }
                }
            }

        # Verify all data persists and is accessible
        with session_state(session_id) as state:
            assert len(state["large_string"]) == 10000
            assert len(state["number_list"]) == 1000
            assert state["nested_dict"]["level1"]["level2"]["level3"]["deep_data"] == "exists"

        # Clean up
        with session_state(session_id) as state:
            state.clear()

    def test_session_cleanup(self):
        """Test that session cleanup works properly"""
        session_id = "cleanup_test"

        # Add data to session
        with session_state(session_id) as state:
            state["temp_data"] = "should_be_deleted"
            state["more_data"] = [1, 2, 3, 4, 5]

        # Verify data exists
        with session_state(session_id) as state:
            assert len(state) >= 2

        # Clean up the session
        with session_state(session_id) as state:
            state.clear()

        # Verify session is empty
        with session_state(session_id) as state:
            assert len(state) == 0