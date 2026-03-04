#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test ai_monitor integration functionality in autorun.

DEPRECATED: All tests in this file are skipped because the ai_monitor integration
was removed in the main.py cleanup (Phase 2 of Task #13 - main.py consolidation).

The functions under test (is_premature_stop, stop_handler, inject_continue_prompt,
inject_verification_prompt, _manage_monitor, update_injection_outcome) were deleted
from main.py as dead/deprecated code.

Canonical replacements (daemon path):
- is_premature_stop(ctx, state) → plugins.is_premature_stop(ctx: EventContext) at plugins.py
- stop_handler → plugins.autorun_injection(ctx: EventContext) registered via @app.on("Stop")
- inject_continue_prompt → plugins.build_injection_prompt(ctx) at plugins.py
- inject_verification_prompt → plugins.build_injection_prompt(ctx) at plugins.py
- _manage_monitor → removed (ai_monitor integration not in daemon path)
- update_injection_outcome → removed (ai_monitor integration not in daemon path)
"""

import pytest
pytestmark = pytest.mark.skip(reason=(
    "ai_monitor integration removed from daemon path (Phase 2, Task #13). "
    "Canonical replacements: plugins.autorun_injection(ctx) for stop_handler, "
    "plugins.build_injection_prompt(ctx) for inject_*. "
    "See plugins/autorun/src/autorun/plugins.py."
))

import sys
from pathlib import Path
from unittest.mock import Mock, patch

# Add src to path for testing
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

def test_premature_stop_detection():
    """Test that premature stops are correctly detected"""
    from autorun import is_premature_stop, CONFIG

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
    assert is_premature_stop(ctx, state), "Should detect premature stop when no completion marker"

    # Test with stage 1 confirmation
    ctx.session_transcript = ["Some work", CONFIG["stage1_message"]]
    assert not is_premature_stop(ctx, state), "Should not detect premature stop when stage 1 confirmation present"

    # Test with emergency stop
    ctx.session_transcript = ["Some work", CONFIG["emergency_stop"]]
    assert not is_premature_stop(ctx, state), "Should not detect premature stop when emergency stop used"

    # Test with inactive session
    state["session_status"] = "stopped"
    ctx.session_transcript = ["Some work only"]  # No completion marker
    assert not is_premature_stop(ctx, state), "Should not detect premature stop when session inactive"

    print("✅ test_premature_stop_detection passed")


def test_continue_prompt_injection():
    """Test continue prompt injection functionality with three-stage system"""
    from autorun.main import inject_continue_prompt, CONFIG

    state = {"session_status": "active", "file_policy": "ALLOW"}

    response = inject_continue_prompt(state)

    # Verify response structure
    assert response["continue"], "Continue should be True"
    # Should use the full injection template with critical stop signal instructions
    assert "UNINTERRUPTED, FULLY AUTONOMOUS, NONINTERACTIVE, PATIENT, AND SAFE EXECUTION" in response["systemMessage"], "Should contain full injection template"
    assert "SYSTEM STOP SIGNAL RULE" in response["systemMessage"], "Should contain critical stop signal instructions"
    assert CONFIG["stage1_message"] in response["systemMessage"], "Should contain stage 1 confirmation"
    assert CONFIG["emergency_stop"] in response["systemMessage"], "Should contain emergency stop"
    assert "THREE-STAGE COMPLETION SYSTEM" in response["systemMessage"], "Should contain three-stage system instructions"
    assert CONFIG["stage2_message"] in response["systemMessage"], "Should contain stage 2 confirmation"
    assert CONFIG["stage3_message"] in response["systemMessage"], "Should contain stage 3 confirmation"
    assert "FILE CREATION POLICY" in response["systemMessage"], "Should contain file creation policy instructions"

    print("✅ test_continue_prompt_injection passed")

def test_verification_prompt_injection():
    """Test verification prompt injection functionality"""
    from autorun import inject_verification_prompt

    state = {
        "activation_prompt": "/autorun build a website",
        "verification_attempts": 2
    }

    response = inject_verification_prompt(state)

    # Verify response structure
    assert response["continue"], "Continue should be True"
    assert "AUTORUN TASK VERIFICATION" in response["systemMessage"], "Should contain verification message"
    assert "build a website" in response["systemMessage"], "Should contain original task"
    assert "verification attempt #2 of" in response["systemMessage"], "Should contain attempt count"

    print("✅ test_verification_prompt_injection passed")

def test_stop_handler_with_premature_stop():
    """Test stop handler behavior with premature stops in three-stage system"""
    from autorun import stop_handler
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

    with patch('autorun.main.session_state') as mock_session:
        mock_session.return_value.__enter__.return_value = mock_state
        mock_session.return_value.__exit__.return_value = None

        response = stop_handler(ctx)

        # Should inject continue prompt for three-stage system
        assert response["continue"], "Should continue execution"
        assert "UNINTERRUPTED, FULLY AUTONOMOUS" in response["systemMessage"], "Should inject continue message"
        assert mock_state["autorun_stage"] == "INITIAL", "Should remain in INITIAL stage"

    print("✅ test_stop_handler_with_premature_stop passed")

def test_stop_handler_with_continue_prompt():
    """Test stop handler injects continue prompt when premature stop detected in INITIAL stage"""
    from autorun import stop_handler, CONFIG

    # Mock context with premature stop (no completion markers)
    ctx = Mock()
    ctx.session_id = "test_session"
    ctx.session_transcript = ["Some work done", "No completion marker"]

    # Mock session state in INITIAL stage (active autorun, no completion)
    mock_state = {
        "session_status": "active",
        "autorun_stage": "INITIAL",
        "activation_prompt": "/autorun build a website",
        "verification_attempts": 0
    }

    with patch('autorun.main.session_state') as mock_session:
        mock_session.return_value.__enter__.return_value = mock_state
        mock_session.return_value.__exit__.return_value = None

        response = stop_handler(ctx)

        # Should inject continue prompt (premature stop in INITIAL stage)
        assert response["continue"], "Should continue execution"
        assert "UNINTERRUPTED, FULLY AUTONOMOUS, NONINTERACTIVE, PATIENT, AND SAFE EXECUTION" in response["systemMessage"], "Should inject full injection template"
        assert "SYSTEM STOP SIGNAL RULE" in response["systemMessage"], "Should contain critical stop signal instructions"

    print("✅ test_stop_handler_with_continue_prompt passed")

def test_stop_handler_with_successful_completion():
    """Test stop handler allows proper three-stage completion"""
    from autorun.main import stop_handler, CONFIG

    # Mock context with successful stage 3 completion
    ctx = Mock()
    ctx.session_id = "test_session"
    ctx.session_transcript = ["Some work", CONFIG["stage3_message"]]

    # Mock session state in STAGE2_COMPLETED with countdown completed
    mock_state = {
        "session_status": "active",
        "autorun_stage": "STAGE2_COMPLETED",
        "activation_prompt": "/autorun build a website",
        "hook_call_count": CONFIG["stage3_countdown_calls"]  # Countdown completed
    }

    with patch('autorun.main.session_state') as mock_session:
        mock_session.return_value.__enter__.return_value = mock_state
        mock_session.return_value.__exit__.return_value = None

        response = stop_handler(ctx)

        # Should allow completion
        assert not response["continue"], "Should stop execution"
        assert "Three-stage completion successful" in response["systemMessage"], "Should show success message"

    print("✅ test_stop_handler_with_successful_completion passed")

def test_stop_handler_with_emergency_stop():
    """Test stop handler respects emergency stop"""
    from autorun.main import stop_handler, CONFIG

    # Mock context with emergency stop
    ctx = Mock()
    ctx.session_id = "test_session"
    ctx.session_transcript = ["Some work", CONFIG["emergency_stop"]]

    # Mock session state
    mock_state = {
        "session_status": "active",
        "autorun_stage": "INITIAL",
        "activation_prompt": "/autorun build a website",
        "verification_attempts": 0
    }

    with patch('autorun.main.session_state') as mock_session:
        mock_session.return_value.__enter__.return_value = mock_state
        mock_session.return_value.__exit__.return_value = None

        response = stop_handler(ctx)

        # Emergency stop is detected in is_premature_stop() which returns False,
        # so the stop handler does NOT intervene — it allows Claude to stop normally.
        # This is correct: emergency stop means the user wants to halt, so don't
        # inject continue prompts that would keep the AI working.
        assert response["continue"] is True, "Default response has continue=True"
        # No intervention means empty systemMessage (default build_hook_response)
        assert response["systemMessage"] == "", "Emergency stop should not inject continue prompt"

    print("✅ test_stop_handler_with_emergency_stop passed")

def test_stop_handler_with_non_autorun_session():
    """Test stop handler with non-autorun session"""
    from autorun.main import stop_handler

    # Mock context for non-autorun session
    ctx = Mock()
    ctx.session_id = "test_session"
    ctx.session_transcript = ["Regular conversation", "No completion marker"]

    # Mock inactive session state
    mock_state = {}

    with patch('autorun.main.session_state') as mock_session:
        mock_session.return_value.__enter__.return_value = mock_state
        mock_session.return_value.__exit__.return_value = None

        response = stop_handler(ctx)

        # Should allow normal stop without intervention
        # For non-autorun sessions, returns default response
        assert response["continue"], "Should return default response for non-autorun session"
        assert response["systemMessage"] == "", "Should not inject any message"

    print("✅ test_stop_handler_with_non_autorun_session passed")

# ========== ROBUST AI MONITOR TESTS ==========

def test_template_content_completeness():
    """Test that templates contain all required critical components"""
    from autorun import CONFIG

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
    """Test that template parameter substitution works correctly with three-stage system"""
    from autorun.main import CONFIG, inject_continue_prompt, inject_verification_prompt

    # Test continue prompt injection
    state = {
        "session_status": "active",
        "file_policy": "ALLOW"
    }

    response = inject_continue_prompt(state)
    assert response["continue"]
    system_message = response["systemMessage"]

    # Verify all parameters were substituted
    assert CONFIG["emergency_stop"] in system_message
    assert CONFIG["stage1_instruction"] in system_message
    assert CONFIG["stage1_message"] in system_message
    assert CONFIG["stage2_message"] in system_message
    assert CONFIG["stage3_message"] in system_message
    assert "Complete Stage 1 before proceeding to Stage 2" in system_message
    assert CONFIG["policies"]["ALLOW"][1] in system_message

    # Test verification prompt injection
    state = {
        "activation_prompt": "/autorun build a website",
        "verification_attempts": 2
    }

    response = inject_verification_prompt(state)
    assert response["continue"]
    system_message = response["systemMessage"]

    # Verify all parameters were substituted
    assert "build a website" in system_message
    assert "#2 of 3" in system_message

    print("✅ test_template_parameter_substitution passed")

def test_session_state_isolation():
    """Test that different sessions are properly isolated in three-stage system"""
    from autorun import stop_handler, CONFIG
    from unittest.mock import patch

    # Mock context for session 1 - no completion marker
    ctx1 = Mock()
    ctx1.session_id = "session_1"
    ctx1.session_transcript = ["Work done", "No completion marker"]

    # Mock context for session 2 - with stage 1 confirmation
    ctx2 = Mock()
    ctx2.session_id = "session_2"
    ctx2.session_transcript = ["Different work", CONFIG["stage1_message"]]

    # Mock session states
    mock_state_1 = {
        "session_status": "active",
        "autorun_stage": "INITIAL",
        "activation_prompt": "/autorun task 1",
        "verification_attempts": 0
    }

    mock_state_2 = {
        "session_status": "active",
        "autorun_stage": "INITIAL",
        "activation_prompt": "/autorun task 2"
    }

    with patch('autorun.main.session_state') as mock_session:
        mock_session.return_value.__enter__.return_value = mock_state_1
        mock_session.return_value.__exit__.return_value = None

        # Session 1 should inject continue prompt (no confirmation found)
        response1 = stop_handler(ctx1)
        assert response1["continue"]
        assert "THREE-STAGE COMPLETION SYSTEM" in response1["systemMessage"]
        assert mock_state_1["autorun_stage"] == "INITIAL"  # Should remain in INITIAL

        # Reset mock for session 2
        mock_session.return_value.__enter__.return_value = mock_state_2

        # Session 2 with stage1_message should advance to STAGE2
        response2 = stop_handler(ctx2)
        assert response2["continue"]
        assert "STAGE 2:" in response2["systemMessage"]
        assert mock_state_2["autorun_stage"] == "STAGE2"

    print("✅ test_session_state_isolation passed")

def test_max_verification_attempts_boundary():
    """Test behavior when premature stop detected in INITIAL stage (boundary test)"""
    from autorun import stop_handler, CONFIG
    from unittest.mock import patch

    # Test premature stop with no completion markers
    ctx = Mock()
    ctx.session_id = "test_session"
    ctx.session_transcript = ["Work done", "No completion marker"]

    mock_state = {
        "session_status": "active",
        "autorun_stage": "INITIAL",
        "activation_prompt": "/autorun test task",
        "verification_attempts": 0
    }

    with patch('autorun.main.session_state') as mock_session:
        mock_session.return_value.__enter__.return_value = mock_state
        mock_session.return_value.__exit__.return_value = None

        response = stop_handler(ctx)

        # Should inject continue prompt (premature stop in INITIAL stage)
        assert response["continue"]
        assert "UNINTERRUPTED, FULLY AUTONOMOUS" in response["systemMessage"]

    print("✅ test_max_verification_attempts_boundary passed")

def test_edge_case_transcript_scenarios():
    """Test edge cases in transcript analysis"""
    from autorun.main import is_premature_stop, CONFIG

    # Test with empty transcript
    ctx = Mock()
    ctx.session_transcript = []

    state = {"session_status": "active"}
    assert is_premature_stop(ctx, state), "Empty transcript should be considered premature stop"

    # Test with stage confirmation in middle (current behavior: any confirmation is valid)
    ctx.session_transcript = ["Some work", CONFIG["stage1_message"], "More work after marker"]
    assert not is_premature_stop(ctx, state), "Any stage confirmation in transcript should be considered valid completion"

    # Test with emergency stop marker in middle (current behavior: any emergency stop is valid)
    ctx.session_transcript = ["Some work", CONFIG["emergency_stop"], "More work after stop"]
    assert not is_premature_stop(ctx, state), "Any emergency stop in transcript should be considered valid stop"

    # Test with both markers (emergency should take precedence)
    ctx.session_transcript = ["Some work", CONFIG["stage1_message"], CONFIG["emergency_stop"]]
    assert not is_premature_stop(ctx, state), "Emergency stop in transcript should be considered valid stop"

    # Test with mixed case markers (case sensitivity should be enforced)
    ctx.session_transcript = ["Some work", CONFIG["stage1_message"].lower()]
    assert is_premature_stop(ctx, state), "Lowercase confirmation marker should not match"

    # Test with partial marker that doesn't contain full marker
    ctx.session_transcript = ["Some work", "PARTIAL_COMPLETION_MARKER"]
    assert is_premature_stop(ctx, state), "Non-matching partial marker should be considered premature stop"

    # Test with None transcript (error handling)
    ctx.session_transcript = None
    assert is_premature_stop(ctx, state), "None transcript should be considered premature stop"

    print("✅ test_edge_case_transcript_scenarios passed")

def test_file_policy_integration():
    """Test that file policy is correctly integrated into templates"""
    from autorun import inject_continue_prompt, CONFIG

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
        assert CONFIG["emergency_stop"] in system_message
        assert CONFIG["stage1_message"] in system_message

    print("✅ test_file_policy_integration passed")

def test_concurrent_session_handling():
    """Test behavior with multiple concurrent sessions in three-stage system"""
    from autorun.main import stop_handler
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

        with patch('autorun.main.session_state') as mock_session:
            mock_session.return_value.__enter__.return_value = mock_state
            mock_session.return_value.__exit__.return_value = None

            response = stop_handler(ctx)
            results[session_id] = {
                "continue": response["continue"],
                "has_three_stage": "THREE-STAGE COMPLETION SYSTEM" in response["systemMessage"],
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
        assert result["continue"], f"Session {session_id} should continue"
        assert result["has_three_stage"], f"Session {session_id} should have three-stage instructions"
        assert result["stage"] == "INITIAL", f"Session {session_id} should remain in INITIAL stage"

    print("✅ test_concurrent_session_handling passed")

def test_error_recovery_scenarios():
    """Test error recovery and resilience scenarios in three-stage system"""
    from autorun.main import stop_handler
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

    with patch('autorun.main.session_state') as mock_session:
        mock_session.return_value.__enter__.return_value = minimal_state
        mock_session.return_value.__exit__.return_value = None

        response = stop_handler(ctx)

        # Should inject continue prompt with three-stage instructions
        assert response["continue"]
        system_message = response["systemMessage"]
        assert "THREE-STAGE COMPLETION SYSTEM" in system_message, f"Expected THREE-STAGE in: {system_message}"
        assert minimal_state["autorun_stage"] == "INITIAL"  # Should remain in INITIAL

    # Test with malformed transcript
    ctx.session_transcript = None  # None transcript

    with patch('autorun.main.session_state') as mock_session:
        mock_session.return_value.__enter__.return_value = minimal_state
        mock_session.return_value.__exit__.return_value = None

        response = stop_handler(ctx)

        # Should handle gracefully (continue since it's not premature stop)
        assert response["continue"]

    # Test with empty state dict
    empty_state = {}

    with patch('autorun.main.session_state') as mock_session:
        mock_session.return_value.__enter__.return_value = empty_state
        mock_session.return_value.__exit__.return_value = None

        response = stop_handler(ctx)

        # Should handle gracefully without crashing
        assert response["continue"]

    print("✅ test_error_recovery_scenarios passed")

def test_three_stage_completion_flow():
    """Test complete three-stage completion flow"""
    from autorun.main import stop_handler, CONFIG
    from unittest.mock import patch

    # Test Stage 1 completion
    ctx = Mock()
    ctx.session_id = "test_session"

    mock_state = {
        "session_status": "active",
        "autorun_stage": "INITIAL",
        "session_id": "test_session"
    }

    with patch('autorun.main.session_state') as mock_session:
        mock_session.return_value.__enter__.return_value = mock_state
        mock_session.return_value.__exit__.return_value = None

        # Stage 1: Complete initial tasks
        ctx.session_transcript = ["Work done", CONFIG["stage1_message"]]
        response = stop_handler(ctx)

        assert response["continue"], "Should continue to stage 2"
        assert "STAGE 2:" in response["systemMessage"], "Should provide stage 2 instructions"
        assert mock_state["autorun_stage"] == "STAGE2", "Should advance to stage 2"

    print("✅ test_three_stage_completion_flow passed")

def test_stage_2_countdown_mechanism():
    """Test stage 2 countdown mechanism for stage 3 reveal

    Note: stop_handler increments hook_call_count at start (line 940),
    so the actual count used in countdown logic is (set_value + 1).
    Message format alternates based on even/odd count after increment:
    - Even counts: "Stage 3 countdown: X calls remaining"
    - Odd counts: inject_continue_prompt with "After X more hook calls"
    """
    from autorun.main import stop_handler, CONFIG
    from unittest.mock import patch

    ctx = Mock()
    ctx.session_id = "test_session"

    mock_state = {
        "session_status": "active",
        "autorun_stage": "STAGE2_COMPLETED",
        "hook_call_count": 0,
        "session_id": "test_session"
    }

    with patch('autorun.main.session_state') as mock_session:
        mock_session.return_value.__enter__.return_value = mock_state
        mock_session.return_value.__exit__.return_value = None

        # Test countdown progression
        # Note: stop_handler increments hook_call_count at start
        for i in range(CONFIG["stage3_countdown_calls"]):
            mock_state["hook_call_count"] = i
            ctx.session_transcript = ["No completion marker"]
            response = stop_handler(ctx)

            assert response["continue"], f"Should continue during countdown (call {i+1})"

            if i < CONFIG["stage3_countdown_calls"] - 1:
                # After increment, actual count is i+1
                # remaining_calls = stage3_countdown_calls - (i+1)
                remaining = CONFIG["stage3_countdown_calls"] - (i + 1)
                # Check for either format (alternates based on even/odd count after increment)
                has_countdown_format = f"{remaining} calls remaining" in response["systemMessage"]
                has_continue_format = f"{remaining} more hook calls" in response["systemMessage"]
                assert has_countdown_format or has_continue_format, \
                    f"Should show remaining calls (call {i+1}), remaining={remaining}"

        # Test stage 3 reveal after countdown
        mock_state["hook_call_count"] = CONFIG["stage3_countdown_calls"]
        response = stop_handler(ctx)

        assert response["continue"], "Should continue for stage 3"
        assert "STAGE 3:" in response["systemMessage"], "Should reveal stage 3 instructions"

    print("✅ test_stage_2_countdown_mechanism passed")

def test_premature_stage_3_attempt_handling():
    """Test handling of premature stage 3 attempts"""
    from autorun.main import stop_handler, CONFIG
    from unittest.mock import patch

    ctx = Mock()
    ctx.session_id = "test_session"
    ctx.session_transcript = ["Some work", CONFIG["stage3_message"]]

    mock_state = {
        "session_status": "active",
        "autorun_stage": "INITIAL",  # Still in stage 1
        "session_id": "test_session"
    }

    with patch('autorun.main.session_state') as mock_session:
        mock_session.return_value.__enter__.return_value = mock_state
        mock_session.return_value.__exit__.return_value = None

        response = stop_handler(ctx)

        assert response["continue"], "Should continue execution"
        assert "You must complete Stage 1 first" in response["systemMessage"], "Should require stage 1 completion"
        assert CONFIG["stage1_message"] in response["systemMessage"], "Should provide stage 1 completion instructions"

    print("✅ test_premature_stage_3_attempt_handling passed")

def test_three_stage_ai_monitor_coordination():
    """Test AI monitor coordination with session IDs in three-stage system"""
    from autorun.main import _manage_monitor, CONFIG
    from unittest.mock import patch

    mock_state = {
        "session_id": "test_session_three_stage",
        "ai_monitor_pid": None
    }

    with patch('autorun.main.ai_monitor') as mock_ai_monitor:
        mock_ai_monitor.start_monitor.return_value = 12345
        mock_ai_monitor.stop_monitor.return_value = None

        # Test monitor start coordination
        _manage_monitor(mock_state, 'start')

        mock_ai_monitor.start_monitor.assert_called_once_with(
            session_id="test_session_three_stage",
            prompt="continue working",
            stop_marker=CONFIG["stage3_message"],
            max_cycles=20,
            prompt_on_start=True
        )

        assert mock_state["ai_monitor_pid"] == 12345, "Should set monitor PID in state"

        # Test monitor stop coordination
        _manage_monitor(mock_state, 'stop')

        mock_ai_monitor.stop_monitor.assert_called_once_with("test_session_three_stage")
        assert mock_state["ai_monitor_pid"] is None, "Should clear monitor PID from state"

    print("✅ test_three_stage_ai_monitor_coordination passed")

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
        test_error_recovery_scenarios,
        # Three-stage system tests
        test_three_stage_completion_flow,
        test_stage_2_countdown_mechanism,
        test_premature_stage_3_attempt_handling,
        test_three_stage_ai_monitor_coordination
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
    print("AI_MONITOR INTEGRATION TEST RESULTS")
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