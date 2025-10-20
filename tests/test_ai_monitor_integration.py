#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test ai_monitor integration functionality in clautorun"""

import sys
import tempfile
import shutil
import json
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# Add src to path for testing
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

def test_premature_stop_detection():
    """Test that premature stops are correctly detected"""
    from clautorun import is_premature_stop, CONFIG

    # Mock context with no completion marker
    ctx = Mock()
    ctx.session_transcript = ["Some work done", "More work", "No completion marker"]

    # Mock state with active autorun session
    state = {
        "session_status": "active",
        "autorun_stage": "INITIAL",
        "activation_prompt": "/autorun build a website",
        "verification_attempts": 0
    }

    # Should detect premature stop
    assert is_premature_stop(ctx, state) == True, "Should detect premature stop when no completion marker"

    # Test with completion marker
    ctx.session_transcript = ["Some work", CONFIG["completion_marker"]]
    assert is_premature_stop(ctx, state) == False, "Should not detect premature stop when completion marker present"

    # Test with emergency stop
    ctx.session_transcript = ["Some work", CONFIG["emergency_stop_phrase"]]
    assert is_premature_stop(ctx, state) == False, "Should not detect premature stop when emergency stop used"

    # Test with inactive session
    state["session_status"] = "stopped"
    ctx.session_transcript = ["Some work only"]  # No completion marker
    assert is_premature_stop(ctx, state) == False, "Should not detect premature stop when session inactive"

    print("✅ test_premature_stop_detection passed")

def test_verification_trigger_logic():
    """Test verification stage triggering logic"""
    from clautorun import should_trigger_verification, CONFIG

    # Test initial stage with attempts below max
    state = {
        "autorun_stage": "INITIAL",
        "verification_attempts": 1
    }
    assert should_trigger_verification(state) == True, "Should trigger verification in initial stage"

    # Test max attempts reached
    state["verification_attempts"] = CONFIG["max_recheck_count"]
    assert should_trigger_verification(state) == False, "Should not trigger verification when max attempts reached"

    # Test already in verification stage
    state = {
        "autorun_stage": "VERIFICATION",
        "verification_attempts": 1
    }
    assert should_trigger_verification(state) == False, "Should not trigger verification when already in verification stage"

    print("✅ test_verification_trigger_logic passed")

def test_continue_prompt_injection():
    """Test continue prompt injection functionality"""
    from clautorun import inject_continue_prompt, CONFIG

    state = {"session_status": "active", "file_policy": "ALLOW"}

    response = inject_continue_prompt(state)

    # Verify response structure
    assert response["continue"] == True, "Continue should be True"
    # Should use the full injection template with critical stop signal instructions
    assert "UNINTERRUPTED, FULLY AUTONOMOUS, NONINTERACTIVE, PATIENT, AND SAFE EXECUTION" in response["systemMessage"], "Should contain full injection template"
    assert "SYSTEM STOP SIGNAL RULE" in response["systemMessage"], "Should contain critical stop signal instructions"
    assert CONFIG["completion_marker"] in response["systemMessage"], "Should contain completion marker"
    assert CONFIG["emergency_stop_phrase"] in response["systemMessage"], "Should contain emergency stop phrase"
    assert "FILE CREATION POLICY" in response["systemMessage"], "Should contain file creation policy instructions"

    print("✅ test_continue_prompt_injection passed")

def test_verification_prompt_injection():
    """Test verification prompt injection functionality"""
    from clautorun import inject_verification_prompt, CONFIG

    state = {
        "activation_prompt": "/autorun build a website",
        "verification_attempts": 2
    }

    response = inject_verification_prompt(state)

    # Verify response structure
    assert response["continue"] == True, "Continue should be True"
    assert "AUTORUN TASK VERIFICATION" in response["systemMessage"], "Should contain verification message"
    assert "build a website" in response["systemMessage"], "Should contain original task"
    assert "verification attempt #2 of" in response["systemMessage"], "Should contain attempt count"

    print("✅ test_verification_prompt_injection passed")

def test_stop_handler_with_premature_stop():
    """Test stop handler behavior with premature stops"""
    from clautorun import stop_handler, CONFIG
    from unittest.mock import patch

    # Mock context with premature stop
    ctx = Mock()
    ctx.session_id = "test_session"
    ctx.session_transcript = ["Some work done", "No completion marker"]

    # Mock session state
    mock_state = {
        "session_status": "active",
        "autorun_stage": "INITIAL",
        "activation_prompt": "/autorun build a website",
        "verification_attempts": 0
    }

    with patch('clautorun.main.session_state') as mock_session:
        mock_session.return_value.__enter__.return_value = mock_state
        mock_session.return_value.__exit__.return_value = None

        response = stop_handler(ctx)

        # Should trigger verification stage
        assert response["continue"] == True, "Should continue execution"
        assert "VERIFICATION" in response["systemMessage"], "Should inject verification message"
        assert mock_state["autorun_stage"] == "VERIFICATION", "Should update stage to VERIFICATION"
        assert mock_state["verification_attempts"] == 1, "Should increment verification attempts"

    print("✅ test_stop_handler_with_premature_stop passed")

def test_stop_handler_with_continue_prompt():
    """Test stop handler injects continue prompt after max verification attempts"""
    from clautorun import stop_handler, CONFIG

    # Mock context with premature stop
    ctx = Mock()
    ctx.session_id = "test_session"
    ctx.session_transcript = ["Some work done", "No completion marker"]

    # Mock session state at max verification attempts
    mock_state = {
        "session_status": "active",
        "autorun_stage": "VERIFICATION",
        "activation_prompt": "/autorun build a website",
        "verification_attempts": CONFIG["max_recheck_count"]
    }

    with patch('clautorun.main.session_state') as mock_session:
        mock_session.return_value.__enter__.return_value = mock_state
        mock_session.return_value.__exit__.return_value = None

        response = stop_handler(ctx)

        # Should inject continue prompt instead of verification
        assert response["continue"] == True, "Should continue execution"
        assert "UNINTERRUPTED, FULLY AUTONOMOUS, NONINTERACTIVE, PATIENT, AND SAFE EXECUTION" in response["systemMessage"], "Should inject full injection template"
        assert "SYSTEM STOP SIGNAL RULE" in response["systemMessage"], "Should contain critical stop signal instructions"

    print("✅ test_stop_handler_with_continue_prompt passed")

def test_stop_handler_with_successful_completion():
    """Test stop handler allows proper completion"""
    from clautorun import stop_handler, CONFIG

    # Mock context with successful completion
    ctx = Mock()
    ctx.session_id = "test_session"
    ctx.session_transcript = ["Some work", CONFIG["completion_marker"]]

    # Mock session state in verification stage
    mock_state = {
        "session_status": "active",
        "autorun_stage": "VERIFICATION",
        "activation_prompt": "/autorun build a website",
        "verification_attempts": 1
    }

    with patch('clautorun.main.session_state') as mock_session:
        mock_session.return_value.__enter__.return_value = mock_state
        mock_session.return_value.__exit__.return_value = None

        response = stop_handler(ctx)

        # Should allow completion
        assert response["continue"] == False, "Should stop execution"
        assert "completed and verified successfully" in response["systemMessage"], "Should show success message"

    print("✅ test_stop_handler_with_successful_completion passed")

def test_stop_handler_with_emergency_stop():
    """Test stop handler respects emergency stop"""
    from clautorun import stop_handler, CONFIG

    # Mock context with emergency stop
    ctx = Mock()
    ctx.session_id = "test_session"
    ctx.session_transcript = ["Some work", CONFIG["emergency_stop_phrase"]]

    # Mock session state
    mock_state = {
        "session_status": "active",
        "autorun_stage": "INITIAL",
        "activation_prompt": "/autorun build a website",
        "verification_attempts": 0
    }

    with patch('clautorun.main.session_state') as mock_session:
        mock_session.return_value.__enter__.return_value = mock_state
        mock_session.return_value.__exit__.return_value = None

        response = stop_handler(ctx)

        # Should allow emergency stop without intervention
        # The default build_hook_response() has continue=True, but for emergency stop we expect no intervention
        # So the response should be the default (continue=True, empty message) since is_premature_stop returns False
        assert response["continue"] == True, "Should return default response for emergency stop"
        assert response["systemMessage"] == "", "Should not inject any message"

    print("✅ test_stop_handler_with_emergency_stop passed")

def test_stop_handler_with_non_autorun_session():
    """Test stop handler with non-autorun session"""
    from clautorun.main import stop_handler

    # Mock context for non-autorun session
    ctx = Mock()
    ctx.session_id = "test_session"
    ctx.session_transcript = ["Regular conversation", "No completion marker"]

    # Mock inactive session state
    mock_state = {}

    with patch('clautorun.main.session_state') as mock_session:
        mock_session.return_value.__enter__.return_value = mock_state
        mock_session.return_value.__exit__.return_value = None

        response = stop_handler(ctx)

        # Should allow normal stop without intervention
        # For non-autorun sessions, returns default response
        assert response["continue"] == True, "Should return default response for non-autorun session"
        assert response["systemMessage"] == "", "Should not inject any message"

    print("✅ test_stop_handler_with_non_autorun_session passed")

# ========== ROBUST AI MONITOR TESTS ==========

def test_template_content_completeness():
    """Test that templates contain all required critical components"""
    from clautorun import CONFIG

    # Test injection template has all critical components
    injection_template = CONFIG["injection_template"]
    critical_components = [
        "UNINTERRUPTED, FULLY AUTONOMOUS, NONINTERACTIVE, PATIENT, AND SAFE EXECUTION",
        "SYSTEM STOP SIGNAL RULE",
        "Safety Protocol (Risk Assessment & Mitigation)",
        "INITIATE SAFETY PROTOCOL",
        "CRITICAL ESCAPE TO STOP SYSTEM",
        "FINAL OUTPUT ON SUCCESS TO STOP SYSTEM",
        "FILE CREATION POLICY"
    ]

    for component in critical_components:
        assert component in injection_template, f"Missing critical component: {component}"

    # Test verification template has all critical components
    verification_template = CONFIG["recheck_template"]
    verification_components = [
        "AUTORUN TASK VERIFICATION",
        "CRITICAL VERIFICATION INSTRUCTIONS",
        "verification attempt #{recheck_count} of {max_recheck_count}"
    ]

    for component in verification_components:
        assert component in verification_template, f"Missing verification component: {component}"

    print("✅ test_template_content_completeness passed")

def test_template_parameter_substitution():
    """Test that template parameter substitution works correctly"""
    from clautorun import CONFIG, inject_continue_prompt, inject_verification_prompt

    # Test continue prompt injection
    state = {
        "session_status": "active",
        "file_policy": "ALLOW"
    }

    response = inject_continue_prompt(state)
    assert response["continue"] == True
    system_message = response["systemMessage"]

    # Verify all parameters were substituted
    assert CONFIG["emergency_stop_phrase"] in system_message
    assert CONFIG["completion_marker"] in system_message
    assert CONFIG["policies"]["ALLOW"][1] in system_message

    # Test verification prompt injection
    state = {
        "activation_prompt": "/autorun build a website",
        "verification_attempts": 2
    }

    response = inject_verification_prompt(state)
    assert response["continue"] == True
    system_message = response["systemMessage"]

    # Verify all parameters were substituted
    assert "build a website" in system_message
    assert "#2 of 3" in system_message

    print("✅ test_template_parameter_substitution passed")

def test_session_state_isolation():
    """Test that different sessions are properly isolated"""
    from clautorun import stop_handler, CONFIG
    from unittest.mock import patch

    # Mock context for session 1
    ctx1 = Mock()
    ctx1.session_id = "session_1"
    ctx1.session_transcript = ["Work done", "No completion marker"]

    # Mock context for session 2
    ctx2 = Mock()
    ctx2.session_id = "session_2"
    ctx2.session_transcript = ["Different work", CONFIG["completion_marker"]]

    # Mock session states
    mock_state_1 = {
        "session_status": "active",
        "autorun_stage": "INITIAL",
        "activation_prompt": "/autorun task 1",
        "verification_attempts": 0
    }

    mock_state_2 = {
        "session_status": "active",
        "autorun_stage": "VERIFICATION",
        "activation_prompt": "/autorun task 2",
        "verification_attempts": 1
    }

    def mock_session_state_1():
        return mock_state_1

    def mock_session_state_2():
        return mock_state_2

    with patch('clautorun.main.session_state') as mock_session:
        mock_session.return_value.__enter__.return_value = mock_state_1
        mock_session.return_value.__exit__.return_value = None

        # Session 1 should trigger verification
        response1 = stop_handler(ctx1)
        assert response1["continue"] == True
        assert "VERIFICATION" in response1["systemMessage"]
        assert mock_state_1["autorun_stage"] == "VERIFICATION"

        # Reset mock for session 2
        mock_session.return_value.__enter__.return_value = mock_state_2

        # Session 2 should allow completion
        response2 = stop_handler(ctx2)
        assert response2["continue"] == False
        assert "completed and verified successfully" in response2["systemMessage"]
        assert len(mock_state_2) == 0  # Should be cleared

    print("✅ test_session_state_isolation passed")

def test_max_verification_attempts_boundary():
    """Test behavior at max verification attempts boundary"""
    from clautorun import stop_handler, CONFIG
    from unittest.mock import patch

    # Test exactly at max attempts
    ctx = Mock()
    ctx.session_id = "test_session"
    ctx.session_transcript = ["Work done", "No completion marker"]

    mock_state = {
        "session_status": "active",
        "autorun_stage": "VERIFICATION",
        "activation_prompt": "/autorun test task",
        "verification_attempts": CONFIG["max_recheck_count"]
    }

    with patch('clautorun.main.session_state') as mock_session:
        mock_session.return_value.__enter__.return_value = mock_state
        mock_session.return_value.__exit__.return_value = None

        response = stop_handler(ctx)

        # Should inject continue prompt instead of verification
        assert response["continue"] == True
        assert "UNINTERRUPTED, FULLY AUTONOMOUS" in response["systemMessage"]
        # Should not increment verification attempts beyond max
        assert mock_state["verification_attempts"] == CONFIG["max_recheck_count"]

    print("✅ test_max_verification_attempts_boundary passed")

def test_edge_case_transcript_scenarios():
    """Test edge cases in transcript analysis"""
    from clautorun.main import is_premature_stop, CONFIG

    # Test with empty transcript
    ctx = Mock()
    ctx.session_transcript = []

    state = {"session_status": "active"}
    assert is_premature_stop(ctx, state) == True, "Empty transcript should be considered premature stop"

    # Test with completion marker in middle (current behavior: any completion marker is valid)
    ctx.session_transcript = ["Some work", CONFIG["completion_marker"], "More work after marker"]
    assert is_premature_stop(ctx, state) == False, "Any completion marker in transcript should be considered valid completion"

    # Test with emergency stop marker in middle (current behavior: any emergency stop is valid)
    ctx.session_transcript = ["Some work", CONFIG["emergency_stop_phrase"], "More work after stop"]
    assert is_premature_stop(ctx, state) == False, "Any emergency stop in transcript should be considered valid stop"

    # Test with both markers (emergency should take precedence)
    ctx.session_transcript = ["Some work", CONFIG["completion_marker"], CONFIG["emergency_stop_phrase"]]
    assert is_premature_stop(ctx, state) == False, "Emergency stop in transcript should be considered valid stop"

    # Test with mixed case markers (case sensitivity should be enforced)
    ctx.session_transcript = ["Some work", CONFIG["completion_marker"].lower()]
    assert is_premature_stop(ctx, state) == True, "Lowercase completion marker should not match"

    # Test with partial marker that doesn't contain full marker
    ctx.session_transcript = ["Some work", "PARTIAL_COMPLETION_MARKER"]
    assert is_premature_stop(ctx, state) == True, "Non-matching partial marker should be considered premature stop"

    # Test with None transcript (error handling)
    ctx.session_transcript = None
    assert is_premature_stop(ctx, state) == True, "None transcript should be considered premature stop"

    print("✅ test_edge_case_transcript_scenarios passed")

def test_file_policy_integration():
    """Test that file policy is correctly integrated into templates"""
    from clautorun import inject_continue_prompt, CONFIG

    # Test each file policy type
    policies = ["ALLOW", "JUSTIFY", "SEARCH"]

    for policy in policies:
        state = {
            "session_status": "active",
            "file_policy": policy
        }

        response = inject_continue_prompt(state)
        system_message = response["systemMessage"]

        # Verify policy instructions are included
        policy_instructions = CONFIG["policies"][policy][1]
        assert policy_instructions in system_message, f"Policy instructions for {policy} not found in template"

        # Verify critical components are still present
        assert CONFIG["emergency_stop_phrase"] in system_message
        assert CONFIG["completion_marker"] in system_message

    print("✅ test_file_policy_integration passed")

def test_concurrent_session_handling():
    """Test behavior with multiple concurrent sessions"""
    from clautorun.main import stop_handler
    from unittest.mock import patch
    import threading
    import time

    results = {}

    def handle_session(session_id, delay=0):
        """Handle a session with optional delay to simulate concurrency"""
        time.sleep(delay)

        ctx = Mock()
        ctx.session_id = session_id
        ctx.session_transcript = [f"Work for {session_id}", "No completion marker"]

        mock_state = {
            "session_status": "active",
            "autorun_stage": "INITIAL",
            "activation_prompt": f"/autorun task for {session_id}",
            "verification_attempts": 0
        }

        with patch('clautorun.main.session_state') as mock_session:
            mock_session.return_value.__enter__.return_value = mock_state
            mock_session.return_value.__exit__.return_value = None

            response = stop_handler(ctx)
            results[session_id] = {
                "continue": response["continue"],
                "has_verification": "VERIFICATION" in response["systemMessage"],
                "stage": mock_state["autorun_stage"]
            }

    # Run multiple sessions concurrently
    threads = []
    for i in range(3):
        thread = threading.Thread(target=handle_session, args=(f"session_{i}", i * 0.1))
        threads.append(thread)
        thread.start()

    for thread in threads:
        thread.join()

    # Verify all sessions were handled correctly
    assert len(results) == 3, "All sessions should have results"
    for session_id, result in results.items():
        assert result["continue"] == True, f"Session {session_id} should continue"
        assert result["has_verification"] == True, f"Session {session_id} should have verification"
        assert result["stage"] == "VERIFICATION", f"Session {session_id} should be in verification stage"

    print("✅ test_concurrent_session_handling passed")

def test_error_recovery_scenarios():
    """Test error recovery and resilience scenarios"""
    from clautorun.main import stop_handler
    from unittest.mock import patch

    # Test with missing session state fields
    ctx = Mock()
    ctx.session_id = "test_session"
    ctx.session_transcript = ["Work done", "No completion marker"]

    # Test with minimal state that includes autorun_stage
    minimal_state = {
        "session_status": "active",
        "autorun_stage": "INITIAL"
    }

    with patch('clautorun.main.session_state') as mock_session:
        mock_session.return_value.__enter__.return_value = minimal_state
        mock_session.return_value.__exit__.return_value = None

        response = stop_handler(ctx)

        # Should trigger verification since it's INITIAL stage
        assert response["continue"] == True
        system_message = response["systemMessage"]
        assert "VERIFICATION" in system_message, f"Expected VERIFICATION in: {system_message}"
        assert minimal_state["autorun_stage"] == "VERIFICATION"
        assert minimal_state["verification_attempts"] == 1

    # Test with malformed transcript
    ctx.session_transcript = None  # None transcript

    with patch('clautorun.main.session_state') as mock_session:
        mock_session.return_value.__enter__.return_value = minimal_state
        mock_session.return_value.__exit__.return_value = None

        response = stop_handler(ctx)

        # Should handle gracefully (continue since it's not premature stop)
        assert response["continue"] == True

    # Test with empty state dict
    empty_state = {}

    with patch('clautorun.main.session_state') as mock_session:
        mock_session.return_value.__enter__.return_value = empty_state
        mock_session.return_value.__exit__.return_value = None

        response = stop_handler(ctx)

        # Should handle gracefully without crashing
        assert response["continue"] == True

    print("✅ test_error_recovery_scenarios passed")

def run_all_tests():
    """Run all ai_monitor integration tests"""
    tests = [
        test_premature_stop_detection,
        test_verification_trigger_logic,
        test_continue_prompt_injection,
        test_verification_prompt_injection,
        test_stop_handler_with_premature_stop,
        test_stop_handler_with_continue_prompt,
        test_stop_handler_with_successful_completion,
        test_stop_handler_with_emergency_stop,
        test_stop_handler_with_non_autorun_session,
        # Enhanced robust tests
        test_template_content_completeness,
        test_template_parameter_substitution,
        test_session_state_isolation,
        test_max_verification_attempts_boundary,
        test_edge_case_transcript_scenarios,
        test_file_policy_integration,
        test_concurrent_session_handling,
        test_error_recovery_scenarios
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"❌ {test.__name__} failed: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*60}")
    print(f"AI_MONITOR INTEGRATION TEST RESULTS")
    print(f"{'='*60}")
    print(f"✅ PASSED: {passed}")
    print(f"❌ FAILED: {failed}")
    print(f"TOTAL: {passed + failed}")

    if failed == 0:
        print("🎉 All ai_monitor integration tests passed!")
        print("📊 Test coverage includes:")
        print("   ✅ Basic functionality (9 tests)")
        print("   ✅ Robustness and edge cases (7 tests)")
        print("   ✅ Error recovery and resilience")
        print("   ✅ Concurrent session handling")
        print("   ✅ Template completeness and parameter substitution")
        return True
    else:
        print("💥 Some ai_monitor integration tests failed!")
        return False

if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)