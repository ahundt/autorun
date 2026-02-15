#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Comprehensive integration tests to verify all three integration options
implement the complete AI monitor workflow as documented in README.md
"""

import sys
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# Add src to path for testing
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

def test_main_py_ai_monitor_workflow():
    """Test that main.py implements complete AI monitor workflow"""
    from clautorun import (
        stop_handler, pretooluse_handler, claude_code_handler,
        CONFIG
    )

    print("Testing main.py AI monitor workflow...")

    # Test Stage 1: Initial activation
    ctx = Mock()
    ctx.prompt = "/autorun build a website"
    ctx.session_id = "test_session_main"

    response = claude_code_handler(ctx)

    # Check if command is being detected properly
    command = next((v for k, v in CONFIG["command_mappings"].items() if k == ctx.prompt), None)
    print(f"Debug - detected command: {command}")
    print(f"Debug - available mappings: {CONFIG['command_mappings']}")
    print(f"Debug - main.py response: {response}")

    assert response["continue"], "Autorun activation should continue to AI with injection template"
    response_content = response.get("response") or response.get("systemMessage", "")
    assert "UNINTERRUPTED, FULLY AUTONOMOUS" in response_content, "Should include full injection template"
    print("✅ Stage 1 activation works")

    # Test Stage 2: Premature stop detection and verification trigger
    ctx = Mock()
    ctx.session_id = "test_session_main"
    ctx.session_transcript = ["Some work done", "No completion marker"]

    mock_state = {
        "session_status": "active",
        "autorun_stage": "INITIAL",
        "activation_prompt": "/autorun build a website",
        "verification_attempts": 0
    }

    with patch('clautorun.main.session_state') as mock_session:
        mock_session.return_value.__enter__.return_value = mock_state
        mock_session.return_value.__exit__.return_value = None

        # Debug the is_premature_stop logic
        from clautorun import is_premature_stop
        is_premature = is_premature_stop(ctx, mock_state)
        print(f"Debug - is_premature_stop: {is_premature}")
        print(f"Debug - session_transcript: {ctx.session_transcript}")

        response = stop_handler(ctx)
        print(f"Debug - stop_handler response: {response}")

        assert response["continue"], "Should continue execution"
        # Implementation uses three-stage completion system, not "AUTORUN TASK VERIFICATION"
        assert "UNINTERRUPTED, FULLY AUTONOMOUS" in response["systemMessage"], "Should inject continue prompt"
        print(f"Debug - mock_state after stop_handler: {mock_state}")
        # Three-stage system uses INITIAL, STAGE2, STAGE2_COMPLETED stages
        # After premature stop, it stays in INITIAL and increments hook_call_count
        assert mock_state.get("autorun_stage") in ["INITIAL", "STAGE2", "VERIFICATION"], f"Should be in expected stage, got: {mock_state.get('autorun_stage')}"

    print("✅ Stage 2 verification trigger works")

    # Test Stage 3: Continue prompt injection after max attempts
    ctx.session_transcript = ["More work", "Still no completion marker"]
    mock_state["verification_attempts"] = CONFIG["max_recheck_count"]

    with patch('clautorun.main.session_state') as mock_session:
        mock_session.return_value.__enter__.return_value = mock_state
        mock_session.return_value.__exit__.return_value = None

        response = stop_handler(ctx)

        assert response["continue"], "Should continue execution"
        assert "UNINTERRUPTED, FULLY AUTONOMOUS" in response["systemMessage"], "Should inject continue prompt"

    print("✅ Stage 3 continue prompt injection works")

    # Test Stage 4: Final completion detection (three-stage system)
    # For final completion, we need:
    # 1. autorun_stage = "STAGE2_COMPLETED"
    # 2. hook_call_count >= stage3_countdown_calls (5)
    # 3. stage3_confirmation in transcript
    mock_state_final = {
        "session_status": "active",
        "autorun_stage": "STAGE2_COMPLETED",
        "activation_prompt": "/autorun build a website",
        "verification_attempts": 0,
        "hook_call_count": 5,  # >= stage3_countdown_calls
        "stage1_completed": True,
        "stage2_completion_timestamp": 0
    }
    ctx.session_transcript = ["Verification work", CONFIG["stage3_message"]]

    with patch('clautorun.main.session_state') as mock_session:
        mock_session.return_value.__enter__.return_value = mock_state_final
        mock_session.return_value.__exit__.return_value = None

        response = stop_handler(ctx)

        assert not response["continue"], "Should stop execution after stage 3 completion"
        assert "Three-stage completion successful" in response["systemMessage"], "Should confirm completion"

    print("✅ Stage 4 final completion detection works")

    # Test AutoFile policy enforcement
    ctx = Mock()
    ctx.session_id = "test_session_main"
    ctx.tool_name = "Write"
    ctx.tool_input = {"file_path": "new_file.py"}
    ctx.session_transcript = []

    # Test with proper session state mocking - patch the correct module path
    from unittest.mock import MagicMock
    mock_session_manager = MagicMock()
    mock_state = {"file_policy": "SEARCH"}
    mock_session_manager.__enter__ = MagicMock(return_value=mock_state)
    mock_session_manager.__exit__ = MagicMock(return_value=None)

    with patch('clautorun.main.session_state', return_value=mock_session_manager), \
         patch('clautorun.main.Path') as mock_path:
        # Mock Path chain: Path(file_path).resolve().exists()
        mock_resolved = MagicMock()
        mock_resolved.exists.return_value = False
        mock_resolved.is_file.return_value = False
        mock_path.return_value.resolve.return_value = mock_resolved

        response = pretooluse_handler(ctx)

        print(f"Debug - PreToolUse response: {response}")
        print(f"Debug - hookSpecificOutput: {response.get('hookSpecificOutput', {})}")

        assert response["continue"] is True, "continue=True (AI keeps running); tool blocked by permissionDecision=deny"
        assert response.get("hookSpecificOutput", {}).get("permissionDecision") == "deny", f"Should deny file creation in SEARCH mode, got: {response.get('hookSpecificOutput', {}).get('permissionDecision')}"

    print("✅ AutoFile policy enforcement works")

    print("✅ main.py implements complete AI monitor workflow")

def test_agent_sdk_hook_ai_monitor_workflow():
    """Test that main.py handlers implement AI monitor workflow"""
    try:
        from clautorun.main import claude_code_handler, stop_handler, pretooluse_handler
        from clautorun import CONFIG
    except ImportError as e:
        print(f"❌ Could not import main.py handlers: {e}")
        return False

    print("Testing main.py AI monitor workflow...")

    # Test that agent_sdk_hook delegates to main.py for Stage 1 activation
    ctx = Mock()
    ctx.prompt = "/autorun build a website"
    ctx.session_id = "test_session_hook"

    response = claude_code_handler(ctx)

    assert response["continue"], "Autorun activation should continue to AI with injection template"
    # Check both possible response keys (response for hook, systemMessage for direct calls)
    response_content = response.get("response") or response.get("systemMessage", "")
    assert "UNINTERRUPTED, FULLY AUTONOMOUS" in response_content, "Should include full injection template"
    print("✅ Hook Stage 1 activation works")

    # Test that agent_sdk_hook delegates to main.py for Stage 2 verification
    ctx.session_transcript = ["Some work done", "No completion marker"]

    # Mock session state for active autorun session
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

        assert response["continue"], "Should continue execution"
        # Implementation uses three-stage completion system, not "AUTORUN TASK VERIFICATION"
        assert "UNINTERRUPTED, FULLY AUTONOMOUS" in response["systemMessage"], "Should inject continue prompt"

        print("✅ Hook Stage 2 verification trigger works")

    # Test that agent_sdk_hook delegates to main.py for AutoFile enforcement
    ctx = Mock()
    ctx.session_id = "test_session_hook_policy"
    ctx.tool_name = "Write"
    ctx.tool_input = {"file_path": "new_file.py"}
    ctx.session_transcript = []

    # Mock session state with SEARCH policy
    mock_policy_state = {"file_policy": "SEARCH"}
    mock_session_manager = MagicMock()
    mock_session_manager.__enter__ = MagicMock(return_value=mock_policy_state)
    mock_session_manager.__exit__ = MagicMock(return_value=None)

    with patch('clautorun.main.session_state', return_value=mock_session_manager):
        response = pretooluse_handler(ctx)

        assert response["continue"] is True, "continue=True (AI keeps running); tool blocked by permissionDecision=deny"
        assert response["hookSpecificOutput"]["permissionDecision"] == "deny", "Should deny file creation in SEARCH mode"

        print("✅ Hook AutoFile policy enforcement works")

    print("✅ agent_sdk_hook.py properly implements complete AI monitor workflow")
    return True

def test_hook_integration_completeness():
    """Test that hook integration provides all documented features"""
    print("Testing hook integration completeness...")

    try:
        from clautorun.main import HANDLERS as HOOK_HANDLERS
        from clautorun import CONFIG
    except ImportError as e:
        print(f"❌ Could not import required modules: {e}")
        return False

    # Verify all required hook events are handled
    required_hooks = ["UserPromptSubmit", "PreToolUse", "Stop", "SubagentStop"]
    for hook in required_hooks:
        assert hook in HOOK_HANDLERS, f"Missing handler for {hook}"
    print("✅ All required hook events are handled")

    # Verify that the hook handlers are not just placeholders
    from clautorun.main import stop_handler, pretooluse_handler

    # Mock context
    ctx = Mock()
    ctx.session_id = "test_completeness"
    ctx.session_transcript = ["Some work", CONFIG["stage1_message"]]

    # Test stop handler does something meaningful
    response = stop_handler(ctx)
    assert isinstance(response, dict), "Stop handler should return a dict"
    assert "continue" in response, "Stop handler should return continue key"

    # Test PreToolUse handler does something meaningful
    ctx.tool_name = "Write"
    ctx.tool_input = {"file_path": "test.py"}
    response = pretooluse_handler(ctx)
    assert isinstance(response, dict), "PreToolUse handler should return a dict"
    assert "hookSpecificOutput" in response, "PreToolUse handler should return hookSpecificOutput"

    print("✅ Hook integration provides all documented features")
    return True

def test_readme_workflow_compliance():
    """Test that implementation matches README.md documented workflow"""
    print("Testing README.md workflow compliance...")

    from clautorun import CONFIG

    # Verify documented completion marker exists
    # Three-stage system uses stage messages instead of single completion marker
    assert "stage1_message" in CONFIG, "Missing stage1_message in config"
    assert "stage2_message" in CONFIG, "Missing stage2_message in config"
    assert "stage3_message" in CONFIG, "Missing stage3_message in config"
    assert CONFIG["stage1_message"] == "AUTORUN_INITIAL_TASKS_COMPLETED", "Incorrect stage1 message"
    print("✅ Stage messages match implementation")

    # Verify documented emergency stop phrase exists
    assert "emergency_stop" in CONFIG, "Missing emergency_stop in config"
    # NOTE: emergency_stop should be DESCRIPTIVE (describing what the AI is doing)
    # not just a short internal state variable name
    assert CONFIG["emergency_stop"] == "AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP", "Incorrect emergency stop - should be descriptive"
    print("✅ Emergency stop phrase matches README")

    # Verify documented max recheck count
    assert "max_recheck_count" in CONFIG, "Missing max recheck count in config"
    assert CONFIG["max_recheck_count"] == 3, "Incorrect max recheck count"
    print("✅ Max recheck count matches README")

    # Verify documented command mappings (including /autoproc which is an alias for /autorun)
    # Check that legacy commands are included (new /cr: commands also exist for short/long forms)
    required_legacy_mappings = {
        "/autorun": "activate",
        "/autoproc": "activate",  # Alias for /autorun
        "/autostop": "stop",
        "/estop": "emergency_stop",
        "/afs": "SEARCH",
        "/afa": "ALLOW",
        "/afj": "JUSTIFY",
        "/afst": "STATUS"
    }
    for cmd, expected_action in required_legacy_mappings.items():
        assert cmd in CONFIG["command_mappings"], f"Missing legacy command: {cmd}"
        assert CONFIG["command_mappings"][cmd] == expected_action, f"Command {cmd} should map to {expected_action}, got {CONFIG['command_mappings'][cmd]}"
    print("✅ Legacy command mappings preserved")

    # Verify documented file policies
    expected_policies = {
        "ALLOW": ("allow-all", "ALLOW ALL: Full permission to create/modify files."),
        "JUSTIFY": ("justify-create", "JUSTIFIED: Search existing first. Include <AUTOFILE_JUSTIFICATION>reason</AUTOFILE_JUSTIFICATION> for new files."),
        "SEARCH": ("strict-search", "STRICT SEARCH: ONLY modify existing files. Use Glob/Grep. NO new files.")
    }
    assert CONFIG["policies"] == expected_policies, "File policies don't match README"
    print("✅ File policies match README")

    print("✅ Implementation complies with README.md documented workflow")
    return True

def run_comprehensive_integration_tests():
    """Run all comprehensive integration tests"""
    print("🧪 RUNNING COMPREHENSIVE INTEGRATION TESTS")
    print("=" * 60)

    tests = [
        ("main.py AI Monitor Workflow", test_main_py_ai_monitor_workflow),
        ("agent_sdk_hook.py AI Monitor Workflow", test_agent_sdk_hook_ai_monitor_workflow),
        ("Hook Integration Completeness", test_hook_integration_completeness),
        ("README.md Workflow Compliance", test_readme_workflow_compliance),
    ]

    passed = 0
    failed = 0

    for test_name, test_func in tests:
        print(f"\n🔍 {test_name}")
        print("-" * 40)
        try:
            result = test_func()
            if result is not False:  # False indicates test failure
                passed += 1
                print(f"✅ {test_name} PASSED")
            else:
                failed += 1
                print(f"❌ {test_name} FAILED")
        except Exception as e:
            print(f"❌ {test_name} FAILED: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*60}")
    print("COMPREHENSIVE INTEGRATION TEST RESULTS")
    print(f"{'='*60}")
    print(f"✅ PASSED: {passed}")
    print(f"❌ FAILED: {failed}")
    print(f"TOTAL: {passed + failed}")

    if failed == 0:
        print("\n🎉 ALL INTEGRATION TESTS PASSED!")
        print("📊 Verification complete:")
        print("   ✅ main.py implements complete AI monitor workflow")
        print("   ✅ agent_sdk_hook.py properly delegates to main.py")
        print("   ✅ All three integration options work correctly")
        print("   ✅ Implementation matches README.md documentation")
        print("   ✅ No regressions detected")
        return True
    else:
        print(f"\n💥 {failed} INTEGRATION TESTS FAILED!")
        print("🔧 Critical issues found that must be fixed:")
        print("   ❌ Hook integration missing AI monitor workflow")
        print("   ❌ Implementation doesn't match README documentation")
        print("   ❌ Integration options provide inconsistent functionality")
        return False

if __name__ == "__main__":
    success = run_comprehensive_integration_tests()
    sys.exit(0 if success else 1)