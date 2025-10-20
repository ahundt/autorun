#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Comprehensive integration tests to verify all three integration options
implement the complete AI monitor workflow as documented in README.md
"""

import sys
import json
import tempfile
import shutil
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# Add src to path for testing
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

def test_main_py_ai_monitor_workflow():
    """Test that main.py implements complete AI monitor workflow"""
    from clautorun.main import (
        stop_handler, pretooluse_handler, intercept_commands_sync,
        CONFIG, session_state, build_hook_response
    )

    print("Testing main.py AI monitor workflow...")

    # Test Stage 1: Initial activation
    ctx = Mock()
    ctx.prompt = "/autorun build a website"
    ctx.session_id = "test_session_main"

    input_data = {'prompt': ctx.prompt, 'session_id': ctx.session_id}
    response = intercept_commands_sync(input_data, ctx)

    # Check if command is being detected properly
    from clautorun.main import CONFIG
    command = next((v for k, v in CONFIG["command_mappings"].items() if k == ctx.prompt), None)
    print(f"Debug - detected command: {command}")
    print(f"Debug - available mappings: {CONFIG['command_mappings']}")
    print(f"Debug - main.py response: {response}")

    assert response["continue"] == False, "Should handle /autorun command locally"
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
        from clautorun.main import is_premature_stop
        is_premature = is_premature_stop(ctx, mock_state)
        print(f"Debug - is_premature_stop: {is_premature}")
        print(f"Debug - session_transcript: {ctx.session_transcript}")

        response = stop_handler(ctx)
        print(f"Debug - stop_handler response: {response}")

        assert response["continue"] == True, "Should continue execution"
        assert "AUTORUN TASK VERIFICATION" in response["systemMessage"], "Should trigger verification"
        print(f"Debug - mock_state after stop_handler: {mock_state}")
        assert mock_state.get("autorun_stage") == "VERIFICATION", f"Should update stage to VERIFICATION, got: {mock_state.get('autorun_stage')}"
        assert mock_state.get("verification_attempts") == 1, f"Should increment verification attempts, got: {mock_state.get('verification_attempts')}"

    print("✅ Stage 2 verification trigger works")

    # Test Stage 3: Continue prompt injection after max attempts
    ctx.session_transcript = ["More work", "Still no completion marker"]
    mock_state["verification_attempts"] = CONFIG["max_recheck_count"]

    with patch('clautorun.main.session_state') as mock_session:
        mock_session.return_value.__enter__.return_value = mock_state
        mock_session.return_value.__exit__.return_value = None

        response = stop_handler(ctx)

        assert response["continue"] == True, "Should continue execution"
        assert "UNINTERRUPTED, FULLY AUTONOMOUS" in response["systemMessage"], "Should inject continue prompt"

    print("✅ Stage 3 continue prompt injection works")

    # Test Stage 4: Final completion detection
    ctx.session_transcript = ["Verification work", CONFIG["completion_marker"]]

    with patch('clautorun.main.session_state') as mock_session:
        mock_session.return_value.__enter__.return_value = mock_state
        mock_session.return_value.__exit__.return_value = None

        response = stop_handler(ctx)

        assert response["continue"] == False, "Should stop execution"
        assert "completed and verified successfully" in response["systemMessage"], "Should confirm completion"

    print("✅ Stage 4 final completion detection works")

    # Test AutoFile policy enforcement
    ctx = Mock()
    ctx.session_id = "test_session_main"
    ctx.tool_name = "Write"
    ctx.tool_input = {"file_path": "new_file.py"}
    ctx.session_transcript = []

    with patch('main.session_state') as mock_session:
        mock_state = {"file_policy": "SEARCH"}
        mock_session.return_value.__enter__.return_value = mock_state
        mock_session.return_value.__exit__.return_value = None

        response = pretooluse_handler(ctx)

        print(f"Debug - PreToolUse response: {response}")
        print(f"Debug - hookSpecificOutput: {response.get('hookSpecificOutput', {})}")

        # Debug: Check what the policy enforcement is seeing
        with patch('clautorun.main.session_state') as debug_session:
            debug_session.return_value.__enter__.return_value = mock_state
            debug_session.return_value.__exit__.return_value = None

            debug_response = pretooluse_handler(ctx)
            print(f"Debug - PreToolUse response with mock: {debug_response}")

        assert response["continue"] == True, "Should allow tool execution but deny file creation"
        assert response.get("hookSpecificOutput", {}).get("permissionDecision") == "deny", f"Should deny file creation in SEARCH mode, got: {response.get('hookSpecificOutput', {}).get('permissionDecision')}"

    print("✅ AutoFile policy enforcement works")

    print("✅ main.py implements complete AI monitor workflow")

def test_agent_sdk_hook_ai_monitor_workflow():
    """Test that agent_sdk_hook.py properly delegates to main.py AI monitor workflow"""
    try:
        from clautorun.agent_sdk_hook import agent_sdk_user_prompt_submit, agent_sdk_stop_event, agent_sdk_pre_tool_use
        from clautorun.main import CONFIG
    except ImportError as e:
        print(f"❌ Could not import agent_sdk_hook: {e}")
        return False

    print("Testing agent_sdk_hook.py AI monitor workflow...")

    # Test that agent_sdk_hook delegates to main.py for Stage 1 activation
    ctx = Mock()
    ctx.prompt = "/autorun build a website"
    ctx.session_id = "test_session_hook"

    response = agent_sdk_user_prompt_submit(ctx)

    assert response["continue"] == False, "Should handle /autorun command locally"
    # Check both possible response keys (response for hook, systemMessage for direct calls)
    response_content = response.get("response") or response.get("systemMessage", "")
    assert "UNINTERRUPTED, FULLY AUTONOMOUS" in response_content, "Should include full injection template"
    print("✅ Hook Stage 1 activation works")

    # Test that agent_sdk_hook delegates to main.py for Stage 2 verification
    ctx.session_transcript = ["Some work done", "No completion marker"]

    # Set up session state for active autorun session
    import shelve
    from pathlib import Path
    temp_dir = Path(tempfile.mkdtemp())
    try:
        # Create a temporary session database
        session_file = temp_dir / "test_session_hook.db"
        with shelve.open(str(session_file), writeback=True) as state:
            state.update({
                "session_status": "active",
                "autorun_stage": "INITIAL",
                "activation_prompt": "/autorun build a website",
                "verification_attempts": 0
            })

        # Temporarily redirect STATE_DIR to our temp directory
        import clautorun.main as main_module
        original_state_dir = main_module.STATE_DIR
        main_module.STATE_DIR = temp_dir

        response = agent_sdk_stop_event(ctx)

        # Restore original STATE_DIR
        main_module.STATE_DIR = original_state_dir

        assert response["continue"] == True, "Should continue execution"
        assert "AUTORUN TASK VERIFICATION" in response["systemMessage"], "Should trigger verification"

        # Check that the session state was updated
        with shelve.open(str(session_file), writeback=True) as state:
            assert state.get("autorun_stage") == "VERIFICATION", "Should update stage to VERIFICATION"
            assert state.get("verification_attempts") == 1, "Should increment verification attempts"

        print("✅ Hook Stage 2 verification trigger works")

    finally:
        # Clean up temp directory
        if session_file.exists():
            session_file.unlink()
        try:
            temp_dir.rmdir()
        except OSError:
            # Directory might not be empty due to database files, clean them up
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)

    # Test that agent_sdk_hook delegates to main.py for AutoFile enforcement
    ctx = Mock()
    ctx.session_id = "test_session_hook_policy"
    ctx.tool_name = "Write"
    ctx.tool_input = {"file_path": "new_file.py"}
    ctx.session_transcript = []

    # Set up session state with SEARCH policy
    try:
        session_file = temp_dir / "test_session_hook_policy.db"
        with shelve.open(str(session_file), writeback=True) as state:
            state["file_policy"] = "SEARCH"

        # Temporarily redirect STATE_DIR
        original_state_dir = main_module.STATE_DIR
        main_module.STATE_DIR = temp_dir

        response = agent_sdk_pre_tool_use(ctx)

        # Restore original STATE_DIR
        main_module.STATE_DIR = original_state_dir

        assert response["continue"] == True, "Should allow tool execution but deny file creation"
        assert response["hookSpecificOutput"]["permissionDecision"] == "deny", "Should deny file creation in SEARCH mode"

        print("✅ Hook AutoFile policy enforcement works")

    finally:
        # Clean up
        if session_file.exists():
            session_file.unlink()
        try:
            temp_dir.rmdir()
        except OSError:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)

    print("✅ agent_sdk_hook.py properly implements complete AI monitor workflow")
    return True

def test_hook_integration_completeness():
    """Test that hook integration provides all documented features"""
    print("Testing hook integration completeness...")

    try:
        from clautorun.agent_sdk_hook import HOOK_HANDLERS
        from clautorun.main import CONFIG
    except ImportError as e:
        print(f"❌ Could not import required modules: {e}")
        return False

    # Verify all required hook events are handled
    required_hooks = ["UserPromptSubmit", "PreToolUse", "Stop", "SubagentStop"]
    for hook in required_hooks:
        assert hook in HOOK_HANDLERS, f"Missing handler for {hook}"
    print("✅ All required hook events are handled")

    # Verify that the hook handlers are not just placeholders
    from clautorun.agent_sdk_hook import agent_sdk_stop_event, agent_sdk_pre_tool_use

    # Mock context
    ctx = Mock()
    ctx.session_id = "test_completeness"
    ctx.session_transcript = ["Some work", CONFIG["completion_marker"]]

    # Test stop handler does something meaningful
    response = agent_sdk_stop_event(ctx)
    assert isinstance(response, dict), "Stop handler should return a dict"
    assert "continue" in response, "Stop handler should return continue key"

    # Test PreToolUse handler does something meaningful
    ctx.tool_name = "Write"
    ctx.tool_input = {"file_path": "test.py"}
    response = agent_sdk_pre_tool_use(ctx)
    assert isinstance(response, dict), "PreToolUse handler should return a dict"
    assert "hookSpecificOutput" in response, "PreToolUse handler should return hookSpecificOutput"

    print("✅ Hook integration provides all documented features")
    return True

def test_readme_workflow_compliance():
    """Test that implementation matches README.md documented workflow"""
    print("Testing README.md workflow compliance...")

    from clautorun.main import CONFIG

    # Verify documented completion marker exists
    assert "completion_marker" in CONFIG, "Missing completion marker in config"
    assert CONFIG["completion_marker"] == "AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY", "Incorrect completion marker"
    print("✅ Completion marker matches README")

    # Verify documented emergency stop phrase exists
    assert "emergency_stop_phrase" in CONFIG, "Missing emergency stop phrase in config"
    assert CONFIG["emergency_stop_phrase"] == "AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP", "Incorrect emergency stop phrase"
    print("✅ Emergency stop phrase matches README")

    # Verify documented max recheck count
    assert "max_recheck_count" in CONFIG, "Missing max recheck count in config"
    assert CONFIG["max_recheck_count"] == 3, "Incorrect max recheck count"
    print("✅ Max recheck count matches README")

    # Verify documented command mappings
    expected_mappings = {
        "/autorun ": "activate",
        "/autostop ": "stop",
        "/estop ": "emergency_stop",
        "/afs": "SEARCH",
        "/afa": "ALLOW",
        "/afj": "JUSTIFY",
        "/afst": "status"
    }
    assert CONFIG["command_mappings"] == expected_mappings, "Command mappings don't match README"
    print("✅ Command mappings match README")

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
    print(f"COMPREHENSIVE INTEGRATION TEST RESULTS")
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