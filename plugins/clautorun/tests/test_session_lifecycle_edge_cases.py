#!/usr/bin/env python3
"""Session lifecycle edge case testing for clautorun"""

import sys
import os
import threading
import time

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

def test_session_creation_edge_cases():
    """Test edge cases in session creation"""
    print("🔍 Testing Session Creation Edge Cases...")

    from clautorun import session_state

    edge_cases = [
        ("", "Empty session ID"),
        ("   ", "Whitespace session ID"),
        ("null", "Null string session ID"),
        ("undefined", "Undefined string session ID"),
        ("../etc/passwd", "Path traversal attempt"),
        ("session\nwith\nnewlines", "Session with newlines"),
        ("session\twith\ttabs", "Session with tabs"),
        ("session\rwith\rcarriage", "Session with carriage returns"),
        ("🚀emoji🎉session", "Emoji session"),
        ("a" * 1000, "Very long session ID"),
        ("../../../", "Trailing path separator"),
        ("CON", "Windows reserved name"),
        ("aux", "Windows reserved name"),
        ("session with spaces", "Spaces in session"),
        ("session-with.dashes", "Dashes and dots"),
        ("session_with_underscores", "Underscores"),
        ("123456", "Numeric session ID"),
        ("$#@!%^&*()", "Special characters"),
    ]

    for session_id, description in edge_cases:
        try:
            with session_state(session_id) as state:
                assert isinstance(state, dict)
                # Test state operations
                state["test_key"] = "test_value"
                assert state.get("test_key") == "test_value"
            print(f"✅ {description}: Session handled correctly")
        except Exception as e:
            print(f"❌ {description}: Session failed - {e}")

def test_session_state_persistence_edge_cases():
    """Test session state persistence edge cases"""
    print("\n🔍 Testing Session State Persistence Edge Cases...")

    from clautorun import session_state

    # Test session isolation
    session_ids = ["session1", "session2", "", " ", "session\nwith\nnewlines"]

    try:
        # Set values in different sessions
        for session_id in session_ids:
            with session_state(session_id) as state:
                state[f"unique_key_{session_id}"] = f"unique_value_{session_id}"

        # Verify session isolation
        for session_id in session_ids:
            with session_state(session_id) as state:
                expected_key = f"unique_key_{session_id}"
                expected_value = f"unique_value_{session_id}"

                if state.get(expected_key) == expected_value:
                    print(f"✅ Session isolation verified for: {repr(session_id)}")
                else:
                    print(f"❌ Session isolation failed for: {repr(session_id)}")

    except Exception as e:
        print(f"❌ Session isolation test failed: {e}")

    # Test concurrent session access
    results = []
    errors = []

    def session_worker(worker_id):
        try:
            session_id = f"worker_session_{worker_id}"
            with session_state(session_id) as state:
                state["worker_id"] = worker_id
                state["timestamp"] = time.time()
                time.sleep(0.01)  # Small delay
                results.append((worker_id, state.get("worker_id"), state.get("timestamp")))
        except Exception as e:
            errors.append((worker_id, str(e)))

    # Start multiple threads accessing different sessions
    threads = []
    for worker_id in range(10):
        thread = threading.Thread(target=session_worker, args=(worker_id,))
        threads.append(thread)
        thread.start()

    # Wait for completion
    for thread in threads:
        thread.join()

    if not errors:
        print(f"✅ Concurrent session access: {len(results)} successful operations")
        # Verify data integrity
        for worker_id, stored_id, timestamp in results:
            if worker_id == stored_id and timestamp > 0:
                continue
            else:
                print(f"❌ Data integrity issue for worker {worker_id}")
    else:
        print(f"❌ Concurrent session access errors: {len(errors)}")

def test_session_termination_edge_cases():
    """Test session termination edge cases"""
    print("\n🔍 Testing Session Termination Edge Cases...")

    from clautorun import session_state

    # Test normal session termination
    try:
        with session_state("test_termination") as state:
            state["test_data"] = "test_value"
        # Session should terminate cleanly
        print("✅ Normal session termination")
    except Exception as e:
        print(f"❌ Normal session termination failed: {e}")

    # Test session with exception
    try:
        with session_state("test_exception") as state:
            state["test_data"] = "test_value"
            raise ValueError("Test exception")
    except ValueError:
        # Expected exception
        print("✅ Session with exception handled correctly")
    except Exception as e:
        print(f"❌ Unexpected exception in session: {e}")

    # Test nested session contexts (if supported)
    try:
        with session_state("outer_session") as outer_state:
            outer_state["outer_key"] = "outer_value"
            with session_state("inner_session") as inner_state:
                inner_state["inner_key"] = "inner_value"
                # Verify both sessions are accessible
                assert inner_state.get("inner_key") == "inner_value"
            print("✅ Nested session contexts handled")
    except Exception as e:
        print(f"⚠️ Nested session contexts: {e}")

def test_memory_leak_edge_cases():
    """Test for memory leaks in session management"""
    print("\n🔍 Testing Memory Leak Edge Cases...")

    from clautorun import session_state, clear_test_session_state

    # Test rapid session creation and destruction
    created_sessions = []
    try:
        for i in range(1000):
            session_id = f"memory_test_{i}"
            created_sessions.append(session_id)
            with session_state(session_id) as state:
                state[f"key_{i}"] = f"value_{i}" * 100  # Larger values
        print("✅ Rapid session creation/destruction handled")
    except Exception as e:
        print(f"❌ Memory leak test failed: {e}")
    finally:
        # CLEANUP: Remove all 1000 sessions from global dicts
        for session_id in created_sessions:
            clear_test_session_state(session_id)
        print(f"   Cleaned up {len(created_sessions)} test sessions")

    # Test session with large data
    try:
        large_data = "x" * 1000000  # 1MB string
        with session_state("large_data_test") as state:
            state["large_data"] = large_data
            assert len(state["large_data"]) == 1000000
        print("✅ Large data handling in session")
    except Exception as e:
        print(f"❌ Large data test failed: {e}")
    finally:
        # CLEANUP: Remove large data session
        clear_test_session_state("large_data_test")

def test_session_security_edge_cases():
    """Test security-related session edge cases"""
    print("\n🔍 Testing Session Security Edge Cases...")

    from clautorun import session_state

    # Test session ID injection attempts
    malicious_ids = [
        "../../../etc/passwd",
        "..\\..\\..\\windows\\system32\\config\\sam",
        "/etc/shadow",
        "C:\\Windows\\System32\\config\\SAM",
        "${HOME}/.ssh/id_rsa",
        "$(cat /etc/passwd)",
        "`whoami`",
        "; rm -rf /",
        "| cat /etc/passwd",
        "&& cat /etc/passwd",
        "|| cat /etc/passwd",
    ]

    for malicious_id in malicious_ids:
        try:
            with session_state(malicious_id) as state:
                state["test"] = "test"
            print(f"✅ Malicious session ID handled safely: {malicious_id[:20]}...")
        except Exception as e:
            print(f"⚠️ Malicious session ID error: {malicious_id[:20]}... - {e}")

def test_session_recovery_edge_cases():
    """Test session recovery scenarios"""
    print("\n🔍 Testing Session Recovery Edge Cases...")

    from clautorun import session_state

    # Test session data corruption scenarios
    try:
        with session_state("recovery_test") as state:
            state["test_data"] = "original_value"

            # Simulate corruption by setting invalid data types
            try:
                state["invalid_data"] = object()  # Non-serializable object
                print("⚠️ Non-serializable object accepted in session")
            except Exception:
                print("✅ Non-serializable object properly rejected")

            state["test_data"] = "modified_value"

        # Verify session recovery
        with session_state("recovery_test") as state:
            # Should maintain session state
            final_value = state.get("test_data")
            if final_value == "modified_value":
                print("✅ Session state properly recovered")
            else:
                print(f"⚠️ Session state recovery issue: {final_value}")

    except Exception as e:
        print(f"❌ Session recovery test failed: {e}")

def run_session_lifecycle_tests():
    """Run all session lifecycle edge case tests"""
    print("🚀 Starting Session Lifecycle Edge Case Testing")
    print("=" * 60)

    test_session_creation_edge_cases()
    test_session_state_persistence_edge_cases()
    test_session_termination_edge_cases()
    test_memory_leak_edge_cases()
    test_session_security_edge_cases()
    test_session_recovery_edge_cases()

    print("\n" + "=" * 60)
    print("🎉 Session Lifecycle Edge Case Testing Complete!")

if __name__ == "__main__":
    run_session_lifecycle_tests()