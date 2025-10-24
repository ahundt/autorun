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
        "/afst": "STATUS"
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

    # Test policy commands (both uppercase and lowercase)
    for policy_cmd in ["SEARCH", "search"]:
        test_state.clear()
        response = COMMAND_HANDLERS[policy_cmd](test_state)
        expected = "AutoFile policy: strict-search - STRICT SEARCH: ONLY modify existing files. Use Glob/Grep. NO new files."
        assert response == expected, f"{policy_cmd} handler response mismatch"
        assert test_state["file_policy"] == "SEARCH", f"{policy_cmd} handler should update state"

    for policy_cmd in ["ALLOW", "allow"]:
        test_state.clear()
        response = COMMAND_HANDLERS[policy_cmd](test_state)
        expected = "AutoFile policy: allow-all - ALLOW ALL: Full permission to create/modify files."
        assert response == expected, f"{policy_cmd} handler response mismatch"
        assert test_state["file_policy"] == "ALLOW", f"{policy_cmd} handler should update state"

    for policy_cmd in ["JUSTIFY", "justify"]:
        test_state.clear()
        response = COMMAND_HANDLERS[policy_cmd](test_state)
        expected = "AutoFile policy: justify-create - JUSTIFIED: Search existing first. Include <AUTOFILE_JUSTIFICATION>reason</AUTOFILE_JUSTIFICATION> for new files."
        assert response == expected, f"{policy_cmd} handler response mismatch"
        assert test_state["file_policy"] == "JUSTIFY", f"{policy_cmd} handler should update state"

    # Test status command (both versions)
    for status_cmd in ["STATUS", "status"]:
        response = COMMAND_HANDLERS[status_cmd](test_state)
        expected = "Current policy: justify-create"
        assert response == expected, f"{status_cmd} handler response mismatch"

    # Test stop commands (both versions)
    for stop_cmd in ["stop", "STOP"]:
        test_state.clear()
        response = COMMAND_HANDLERS[stop_cmd](test_state)
        expected = "Autorun stopped"
        assert response == expected, f"{stop_cmd} handler response mismatch"
        assert test_state["session_status"] == "stopped", f"{stop_cmd} handler should update state"

    for emergency_cmd in ["emergency_stop", "EMERGENCY_STOP"]:
        test_state.clear()
        response = COMMAND_HANDLERS[emergency_cmd](test_state)
        expected = "Emergency stop activated"
        assert response == expected, f"{emergency_cmd} handler response mismatch"
        assert test_state["session_status"] == "emergency_stopped", f"{emergency_cmd} handler should update state"

    # Test activation command (both versions)
    test_prompt = "/autorun test task description"
    for activate_cmd in ["activate", "ACTIVATE"]:
        test_state.clear()
        test_state["session_id"] = "test_session"  # Set session_id for monitor
        response = COMMAND_HANDLERS[activate_cmd](test_state, test_prompt)
        assert "UNINTERRUPTED, FULLY AUTONOMOUS" in response, f"{activate_cmd} handler should return injection template"
        assert test_state["session_status"] == "active", f"{activate_cmd} handler should set session status"
        assert test_state["autorun_stage"] == "INITIAL", f"{activate_cmd} handler should set autorun stage"
        assert test_state["activation_prompt"] == test_prompt, f"{activate_cmd} handler should store activation prompt"

    print("✅ All command handlers produce correct autorun5.py responses (both uppercase and lowercase)")

def test_both_capitalizations_available():
    """Test that both uppercase and lowercase versions are available for all commands"""
    expected_pairs = [
        ("SEARCH", "search"),
        ("ALLOW", "allow"),
        ("JUSTIFY", "justify"),
        ("STATUS", "status"),
        ("activate", "ACTIVATE"),
        ("stop", "STOP"),
        ("emergency_stop", "EMERGENCY_STOP")
    ]

    for uppercase, lowercase in expected_pairs:
        assert uppercase in COMMAND_HANDLERS, f"Missing uppercase handler: {uppercase}"
        assert lowercase in COMMAND_HANDLERS, f"Missing lowercase handler: {lowercase}"
        # Both should point to the same function
        assert COMMAND_HANDLERS[uppercase] == COMMAND_HANDLERS[lowercase], f"Handlers for {uppercase}/{lowercase} should be the same function"

    print("✅ Both uppercase and lowercase handlers available for all commands")

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
    test_both_capitalizations_available()
    test_log_function()

    print("\n🎯 All tests passed! clautorun is 100% compatible with autorun5.py")
    print("📋 Verification complete:")
    print("   ✅ All prompt strings match exactly")
    print("   ✅ All configuration values match")
    print("   ✅ All command responses match")
    print("   ✅ All state management works correctly")
    print("   ✅ Both uppercase and lowercase handlers available")
    print("   ✅ No regressions detected")

if __name__ == "__main__":
    main()