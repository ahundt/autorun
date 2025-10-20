#!/usr/bin/env python3
"""Test ai_monitor integration functionality in clautorun"""

import sys
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, patch

# Add src to path for testing
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

def test_premature_stop_detection():
    """Test that premature stops are correctly detected"""
    from clautorun.main import is_premature_stop, CONFIG

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
    from clautorun.main import should_trigger_verification, CONFIG

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
    from clautorun.main import inject_continue_prompt, CONFIG
    import json

    state = {"session_status": "active"}

    response = inject_continue_prompt(state)

    # Verify response structure
    assert response["continue"] == True, "Continue should be True"
    # Account for JSON processing in build_hook_response which escapes newlines
    expected_processed = json.dumps(CONFIG["continue_template"])[1:-1]
    assert response["systemMessage"] == expected_processed, "Should use CONFIG continue template with JSON processing"
    assert "AUTORUN CONTINUATION" in response["systemMessage"], "Should contain continuation message"
    assert "Review what you've accomplished so far" in response["systemMessage"], "Should contain detailed instructions"

    print("✅ test_continue_prompt_injection passed")

def test_verification_prompt_injection():
    """Test verification prompt injection functionality"""
    from clautorun.main import inject_verification_prompt, CONFIG

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
    from clautorun.main import stop_handler, CONFIG
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
    from clautorun.main import stop_handler, CONFIG

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
        assert "AUTORUN CONTINUATION" in response["systemMessage"], "Should inject continue message"

    print("✅ test_stop_handler_with_continue_prompt passed")

def test_stop_handler_with_successful_completion():
    """Test stop handler allows proper completion"""
    from clautorun.main import stop_handler, CONFIG

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
    from clautorun.main import stop_handler, CONFIG

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
        test_stop_handler_with_non_autorun_session
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"❌ {test.__name__} failed: {e}")
            failed += 1

    print(f"\n{'='*60}")
    print(f"AI_MONITOR INTEGRATION TEST RESULTS")
    print(f"{'='*60}")
    print(f"✅ PASSED: {passed}")
    print(f"❌ FAILED: {failed}")
    print(f"TOTAL: {passed + failed}")

    if failed == 0:
        print("🎉 All ai_monitor integration tests passed!")
        return True
    else:
        print("💥 Some ai_monitor integration tests failed!")
        return False

if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)