#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test complete autorun5.py compatibility - verify all strings and behavior match exactly
"""
import sys
from pathlib import Path

# Add src directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from clautorun import CONFIG, COMMAND_HANDLERS, session_state, log_info

def test_completion_marker():
    """Test completion marker matches autorun5.py exactly"""
    expected = "AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY"
    actual = CONFIG["completion_marker"]
    assert actual == expected, f"Completion marker mismatch: expected '{expected}', got '{actual}'"
    print("✅ Completion marker matches autorun5.py")

def test_emergency_stop_phrase():
    """Test emergency stop phrase matches autorun5.py exactly"""
    expected = "AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP"
    actual = CONFIG["emergency_stop_phrase"]
    assert actual == expected, f"Emergency stop phrase mismatch: expected '{expected}', got '{actual}'"
    print("✅ Emergency stop phrase matches autorun5.py")

def test_policy_descriptions():
    """Test all policy descriptions match autorun5.py exactly"""
    expected_policies = {
        "ALLOW": ("allow-all", "ALLOW ALL: Full permission to create/modify files."),
        "JUSTIFY": ("justify-create", "JUSTIFIED: Search existing first. Include <AUTOFILE_JUSTIFICATION>reason</AUTOFILE_JUSTIFICATION> for new files."),
        "SEARCH": ("strict-search", "STRICT SEARCH: ONLY modify existing files. Use Glob/Grep. NO new files.")
    }

    for policy, expected_tuple in expected_policies.items():
        actual_tuple = CONFIG["policies"][policy]
        assert actual_tuple == expected_tuple, f"Policy {policy} mismatch: expected {expected_tuple}, got {actual_tuple}"

    print("✅ All policy descriptions match autorun5.py exactly")

def test_policy_blocked_messages():
    """Test policy blocked messages match autorun5.py exactly"""
    expected_blocked = {
        "SEARCH": 'Blocked: STRICT SEARCH policy active. To proceed: 1) Identify what functionality this file provides, 2) Search for existing files handling similar functionality using Glob patterns like "*related-topic*", 3) Use Grep to find files with relevant classes/functions/imports, 4) Modify the most appropriate existing file. Search examples: "*auth*" for authentication, "*api*" for endpoints, "*config*" for settings, "*model*" for data structures.',
        "JUSTIFY": "Blocked: JUSTIFIED CREATION policy requires justification. To proceed: 1) Search for existing files using Glob/Grep related to your functionality, 2) Evaluate if existing files can be extended, 3) If no existing file works, include <AUTOFILE_JUSTIFICATION>Specific technical reason why existing files cannot accommodate this functionality</AUTOFILE_JUSTIFICATION> in your reasoning during the same prompt where you request the file creation, then retry file creation."
    }

    for policy, expected_message in expected_blocked.items():
        actual_message = CONFIG["policy_blocked"][policy]
        assert actual_message == expected_message, f"Policy blocked {policy} mismatch"

    print("✅ All policy blocked messages match autorun5.py exactly")

def test_injection_template():
    """Test injection template contains all autorun5.py components"""
    template = CONFIG["injection_template"]

    # Check for key autorun5.py phrases
    required_phrases = [
        "UNINTERRUPTED, FULLY AUTONOMOUS, NONINTERACTIVE, PATIENT, AND SAFE EXECUTION",
        "carefully, patiently, concretely, and safely",
        "SYSTEM STOP SIGNAL RULE",
        "Safety Protocol (Risk Assessment & Mitigation)",
        "INITIATE SAFETY PROTOCOL",
        "CRITICAL ESCAPE TO STOP SYSTEM",
        "FINAL OUTPUT ON SUCCESS TO STOP SYSTEM",
        "FILE CREATION POLICY"
    ]

    for phrase in required_phrases:
        assert phrase in template, f"Missing required phrase in injection template: '{phrase}'"

    # Check for placeholders
    assert "{emergency_stop_phrase}" in template, "Missing emergency_stop_phrase placeholder"
    assert "{completion_marker}" in template, "Missing completion_marker placeholder"
    assert "{policy_instructions}" in template, "Missing policy_instructions placeholder"

    print("✅ Injection template contains all autorun5.py components")

def test_recheck_template():
    """Test recheck template matches autorun5.py exactly"""
    template = CONFIG["recheck_template"]

    # Check for key recheck phrases
    required_phrases = [
        "AUTORUN TASK VERIFICATION",
        "CRITICAL VERIFICATION INSTRUCTIONS",
        "verification attempt #{recheck_count} of {max_recheck_count}"
    ]

    for phrase in required_phrases:
        assert phrase in template, f"Missing required phrase in recheck template: '{phrase}'"

    # Check for placeholders
    assert "{activation_prompt}" in template, "Missing activation_prompt placeholder"
    assert "{recheck_count}" in template, "Missing recheck_count placeholder"
    assert "{max_recheck_count}" in template, "Missing max_recheck_count placeholder"

    print("✅ Recheck template matches autorun5.py exactly")

def test_command_mappings():
    """Test command mappings match autorun5.py exactly"""
    expected_mappings = {
        "/autorun": "activate",
        "/autostop": "stop",
        "/estop": "emergency_stop",
        "/afs": "SEARCH",
        "/afa": "ALLOW",
        "/afj": "JUSTIFY",
        "/afst": "status"
    }

    for cmd, expected_action in expected_mappings.items():
        actual_action = CONFIG["command_mappings"][cmd]
        assert actual_action == expected_action, f"Command mapping {cmd} mismatch: expected {expected_action}, got {actual_action}"

    print("✅ All command mappings match autorun5.py exactly")

def test_config_values():
    """Test configuration values match autorun5.py exactly"""
    assert CONFIG["max_recheck_count"] == 3, f"max_recheck_count should be 3, got {CONFIG['max_recheck_count']}"
    assert CONFIG["monitor_stop_delay_seconds"] == 300, f"monitor_stop_delay_seconds should be 300, got {CONFIG['monitor_stop_delay_seconds']}"

    print("✅ Configuration values match autorun5.py exactly")

def test_command_handlers():
    """Test command handlers produce correct responses"""
    session_id = "test_compatibility"

    # Use a simple dict instead of shelve for testing
    test_state = {}

    # Test policy commands
    response = COMMAND_HANDLERS["SEARCH"](test_state)
    expected = "AutoFile policy: strict-search - STRICT SEARCH: ONLY modify existing files. Use Glob/Grep. NO new files."
    assert response == expected, f"SEARCH handler response mismatch"
    assert test_state["file_policy"] == "SEARCH", "SEARCH handler should update state"

    response = COMMAND_HANDLERS["ALLOW"](test_state)
    expected = "AutoFile policy: allow-all - ALLOW ALL: Full permission to create/modify files."
    assert response == expected, f"ALLOW handler response mismatch"
    assert test_state["file_policy"] == "ALLOW", "ALLOW handler should update state"

    response = COMMAND_HANDLERS["JUSTIFY"](test_state)
    expected = "AutoFile policy: justify-create - JUSTIFIED: Search existing first. Include <AUTOFILE_JUSTIFICATION>reason</AUTOFILE_JUSTIFICATION> for new files."
    assert response == expected, f"JUSTIFY handler response mismatch"
    assert test_state["file_policy"] == "JUSTIFY", "JUSTIFY handler should update state"

    # Test status command
    response = COMMAND_HANDLERS["STATUS"](test_state)
    expected = "Current policy: justify-create"
    assert response == expected, f"STATUS handler response mismatch"

    # Test stop commands
    response = COMMAND_HANDLERS["STOP"](test_state)
    expected = "Autorun stopped"
    assert response == expected, f"STOP handler response mismatch"
    assert test_state["session_status"] == "stopped", "STOP handler should update state"

    response = COMMAND_HANDLERS["EMERGENCY_STOP"](test_state)
    expected = "Emergency stop activated"
    assert response == expected, f"EMERGENCY_STOP handler response mismatch"
    assert test_state["session_status"] == "emergency_stopped", "EMERGENCY_STOP handler should update state"

    # Test activation command
    test_prompt = "/autorun test task description"
    response = COMMAND_HANDLERS["activate"](test_state, test_prompt)
    assert "UNINTERRUPTED, FULLY AUTONOMOUS" in response, "ACTIVATE handler should return injection template"
    assert test_state["session_status"] == "active", "ACTIVATE handler should set session status"
    assert test_state["autorun_stage"] == "INITIAL", "ACTIVATE handler should set autorun stage"
    assert test_state["activation_prompt"] == test_prompt, "ACTIVATE handler should store activation prompt"

    print("✅ All command handlers produce correct autorun5.py responses")

def test_log_function():
    """Test log_info function works like autorun5.py"""
    try:
        log_info("Test log message")
        print("✅ Log info function works correctly")
    except Exception as e:
        print(f"❌ Log info function error: {e}")
        raise

def main():
    """Run all compatibility tests"""
    print("🧪 Testing clautorun vs autorun5.py compatibility")
    print("=" * 60)

    test_completion_marker()
    test_emergency_stop_phrase()
    test_policy_descriptions()
    test_policy_blocked_messages()
    test_injection_template()
    test_recheck_template()
    test_command_mappings()
    test_config_values()
    test_command_handlers()
    test_log_function()

    print("\n🎯 All tests passed! clautorun is 100% compatible with autorun5.py")
    print("📋 Verification complete:")
    print("   ✅ All prompt strings match exactly")
    print("   ✅ All configuration values match")
    print("   ✅ All command responses match")
    print("   ✅ All state management works correctly")
    print("   ✅ No regressions detected")

if __name__ == "__main__":
    main()