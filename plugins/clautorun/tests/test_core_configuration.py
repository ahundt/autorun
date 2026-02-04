#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test core configuration constants and values
"""
import sys
from pathlib import Path

# Add src directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from clautorun import CONFIG, COMMAND_HANDLERS, log_info

def test_three_stage_confirmations():
    """Test three-stage confirmation markers are properly configured with DESCRIPTIVE strings"""
    # Stage 1 - dual-key pattern
    assert "stage1_instruction" in CONFIG, "Missing stage1_instruction"
    assert "stage1_completion" in CONFIG, "Missing stage1_completion"
    assert "stage1_message" in CONFIG, "Missing stage1_message"
    assert CONFIG["stage1_message"] == "AUTORUN_INITIAL_TASKS_COMPLETED", \
        f"Stage 1 message mismatch: {CONFIG['stage1_message']}"

    # Stage 2 - dual-key pattern
    assert "stage2_instruction" in CONFIG, "Missing stage2_instruction"
    assert "stage2_completion" in CONFIG, "Missing stage2_completion"
    assert "stage2_message" in CONFIG, "Missing stage2_message"
    assert CONFIG["stage2_message"] == "CRITICALLY_EVALUATING_PREVIOUS_WORK_AND_CONTINUING_TASKS_AS_NEEDED", \
        f"Stage 2 message mismatch: {CONFIG['stage2_message']}"

    # Stage 3 - dual-key pattern
    assert "stage3_instruction" in CONFIG, "Missing stage3_instruction"
    assert "stage3_completion" in CONFIG, "Missing stage3_completion"
    assert "stage3_message" in CONFIG, "Missing stage3_message"
    assert CONFIG["stage3_message"] == "AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY", \
        f"Stage 3 message mismatch: {CONFIG['stage3_message']}"

    print("✅ Three-stage confirmation markers properly configured with DESCRIPTIVE ALL-CAPS strings")

def test_emergency_stop():
    """Test emergency stop key exists with correct DESCRIPTIVE value.

    NOTE: The emergency_stop string should be DESCRIPTIVE (describing what the AI is doing)
    rather than a short internal state variable name. This makes it clear to the AI what
    action it's taking when it outputs this string.

    CORRECT: AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP (descriptive - "preserving state due to emergency")
    WRONG:   AUTORUN_EMERGENCY_STOP (just a variable name, not descriptive)
    """
    expected = "AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP"
    actual = CONFIG["emergency_stop"]
    assert actual == expected, f"Emergency stop mismatch: expected '{expected}', got '{actual}'"

    # Verify it's descriptive (should contain words describing the action)
    assert "STATE_PRESERVATION" in actual, "Emergency stop should be descriptive, containing 'STATE_PRESERVATION'"
    print("✅ Emergency stop key correctly configured with descriptive string")

def test_completion_marker():
    """Test completion marker key exists with correct DESCRIPTIVE value.

    NOTE: The completion_marker string should be DESCRIPTIVE (describing what the AI accomplished)
    rather than a short internal state variable name. This makes it clear to the AI what
    it's confirming when it outputs this string.

    CORRECT: AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY (descriptive)
    WRONG:   AUTORUN_STAGE3_COMPLETE (just a variable name, not descriptive)

    The hook system recognizes BOTH the stage confirmations AND the descriptive completion_marker
    for compatibility with both the three-stage hook system and standalone markdown commands.
    """
    expected = "AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY"
    actual = CONFIG["completion_marker"]
    assert actual == expected, f"Completion marker mismatch: expected '{expected}', got '{actual}'"

    # Verify it's descriptive (should contain words describing the accomplishment)
    assert "COMPLETED" in actual, "Completion marker should be descriptive, containing 'COMPLETED'"
    assert "VERIFIED" in actual, "Completion marker should be descriptive, containing 'VERIFIED'"
    assert "SUCCESSFULLY" in actual, "Completion marker should be descriptive, containing 'SUCCESSFULLY'"
    print("✅ Completion marker correctly configured with descriptive string")

def test_policy_descriptions():
    """Test all policy descriptions match documented specifications"""
    expected_policies = {
        "ALLOW": ("allow-all", "ALLOW ALL: Full permission to create/modify files."),
        "JUSTIFY": ("justify-create", "JUSTIFIED: Search existing first. Include <AUTOFILE_JUSTIFICATION>reason</AUTOFILE_JUSTIFICATION> for new files."),
        "SEARCH": ("strict-search", "STRICT SEARCH: ONLY modify existing files. Use Glob/Grep. NO new files.")
    }

    for policy, expected_tuple in expected_policies.items():
        actual_tuple = CONFIG["policies"][policy]
        assert actual_tuple == expected_tuple, f"Policy {policy} mismatch: expected {expected_tuple}, got {actual_tuple}"

    print("✅ All policy descriptions match specifications")

def test_policy_blocked_messages():
    """Test policy blocked messages match documented format"""
    expected_blocked = {
        "SEARCH": 'Blocked: STRICT SEARCH policy active. To proceed: 1) Identify what functionality this file provides, 2) Search for existing files handling similar functionality using Glob patterns like "*related-topic*", 3) Use Grep to find files with relevant classes/functions/imports, 4) Modify the most appropriate existing file. Search examples: "*auth*" for authentication, "*api*" for endpoints, "*config*" for settings, "*model*" for data structures.',
        "JUSTIFY": "Blocked: JUSTIFIED CREATION policy requires justification. To proceed: 1) Search for existing files using Glob/Grep related to your functionality, 2) Evaluate if existing files can be extended, 3) If no existing file works, include <AUTOFILE_JUSTIFICATION>Specific technical reason why existing files cannot accommodate this functionality</AUTOFILE_JUSTIFICATION> in your reasoning during the same prompt where you request the file creation, then retry file creation."
    }

    for policy, expected_message in expected_blocked.items():
        actual_message = CONFIG["policy_blocked"][policy]
        assert actual_message == expected_message, f"Policy blocked {policy} mismatch"

    print("✅ All policy blocked messages match specifications")

def test_injection_template():
    """Test injection template contains all required components for three-stage system"""
    template = CONFIG["injection_template"]

    # Check for key phrases
    required_phrases = [
        "UNINTERRUPTED, FULLY AUTONOMOUS, NONINTERACTIVE, PATIENT, AND SAFE EXECUTION",
        "carefully, patiently, concretely, and safely",
        "SYSTEM STOP SIGNAL RULE",
        "Safety Protocol (Risk Assessment & Mitigation)",
        "THREE-STAGE COMPLETION SYSTEM",
        "FILE CREATION POLICY"
    ]

    for phrase in required_phrases:
        assert phrase in template, f"Missing required phrase in injection template: '{phrase}'"

    # Check for three-stage placeholders (updated for dual-key pattern)
    assert "{emergency_stop}" in template, "Missing emergency_stop placeholder"
    assert "{stage1_instruction}" in template, "Missing stage1_instruction placeholder"
    assert "{stage1_message}" in template, "Missing stage1_message placeholder"
    assert "{stage2_instruction}" in template, "Missing stage2_instruction placeholder"
    assert "{stage2_message}" in template, "Missing stage2_message placeholder"
    assert "{stage3_instruction}" in template, "Missing stage3_instruction placeholder"
    assert "{stage3_message}" in template, "Missing stage3_message placeholder"
    assert "{policy_instructions}" in template, "Missing policy_instructions placeholder"

    print("✅ Injection template contains all three-stage system components")

def test_recheck_template():
    """Test recheck template matches expected format"""
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

    print("✅ Recheck template matches expected format")

def test_command_mappings():
    """Test command mappings are correctly configured"""
    expected_mappings = {
        "/autorun": "activate",
        "/autoproc": "activate",
        "/autostop": "stop",
        "/estop": "emergency_stop",
        "/afs": "SEARCH",
        "/afa": "ALLOW",
        "/afj": "JUSTIFY",
        "/afst": "STATUS"  # uppercase to match COMMAND_HANDLERS keys
    }

    for cmd, expected_action in expected_mappings.items():
        actual_action = CONFIG["command_mappings"][cmd]
        assert actual_action == expected_action, f"Command mapping {cmd} mismatch: expected {expected_action}, got {actual_action}"

    print("✅ All command mappings correctly configured")


def test_new_cr_command_mappings():
    """Test new /cr: prefix commands with short and long forms"""
    # Short forms
    short_mappings = {
        "/cr:a": "ALLOW",
        "/cr:j": "JUSTIFY",
        "/cr:f": "SEARCH",
        "/cr:st": "STATUS",
        "/cr:go": "activate",
        "/cr:gp": "activate",
        "/cr:x": "stop",
        "/cr:sos": "emergency_stop",
    }

    # Long forms
    long_mappings = {
        "/cr:allow": "ALLOW",
        "/cr:justify": "JUSTIFY",
        "/cr:find": "SEARCH",
        "/cr:status": "STATUS",
        "/cr:run": "activate",
        "/cr:proc": "activate",
        "/cr:stop": "stop",
        "/cr:estop": "emergency_stop",
    }

    # Test short forms
    for cmd, expected_action in short_mappings.items():
        assert cmd in CONFIG["command_mappings"], f"Missing short command: {cmd}"
        actual_action = CONFIG["command_mappings"][cmd]
        assert actual_action == expected_action, f"Short command {cmd} mismatch: expected {expected_action}, got {actual_action}"

    # Test long forms
    for cmd, expected_action in long_mappings.items():
        assert cmd in CONFIG["command_mappings"], f"Missing long command: {cmd}"
        actual_action = CONFIG["command_mappings"][cmd]
        assert actual_action == expected_action, f"Long command {cmd} mismatch: expected {expected_action}, got {actual_action}"

    print("✅ All /cr: prefix commands correctly configured (short + long forms)")

def test_config_values():
    """Test configuration values are correct"""
    assert CONFIG["max_recheck_count"] == 3, f"max_recheck_count should be 3, got {CONFIG['max_recheck_count']}"
    assert CONFIG["monitor_stop_delay_seconds"] == 300, f"monitor_stop_delay_seconds should be 300, got {CONFIG['monitor_stop_delay_seconds']}"

    print("✅ All configuration values are correct")

def test_command_handlers():
    """Test command handlers produce correct responses"""

    # Use a simple dict instead of shelve for testing
    test_state = {}

    # Test policy commands (uppercase only - main.py doesn't have lowercase for policy commands)
    test_state.clear()
    response = COMMAND_HANDLERS["SEARCH"](test_state)
    expected = "AutoFile policy: strict-search - STRICT SEARCH: ONLY modify existing files. Use Glob/Grep. NO new files."
    assert response == expected, "SEARCH handler response mismatch"
    assert test_state["file_policy"] == "SEARCH", "SEARCH handler should update state"

    test_state.clear()
    response = COMMAND_HANDLERS["ALLOW"](test_state)
    expected = "AutoFile policy: allow-all - ALLOW ALL: Full permission to create/modify files."
    assert response == expected, "ALLOW handler response mismatch"
    assert test_state["file_policy"] == "ALLOW", "ALLOW handler should update state"

    test_state.clear()
    response = COMMAND_HANDLERS["JUSTIFY"](test_state)
    expected = "AutoFile policy: justify-create - JUSTIFIED: Search existing first. Include <AUTOFILE_JUSTIFICATION>reason</AUTOFILE_JUSTIFICATION> for new files."
    assert response == expected, "JUSTIFY handler response mismatch"
    assert test_state["file_policy"] == "JUSTIFY", "JUSTIFY handler should update state"

    # Test status command (both versions available)
    for status_cmd in ["STATUS", "status"]:
        response = COMMAND_HANDLERS[status_cmd](test_state)
        expected = "Current policy: justify-create"
        assert response == expected, f"{status_cmd} handler response mismatch"

    # Test stop commands (both versions available)
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

    # Test activation command
    test_prompt = "/autorun test task description"
    test_state.clear()
    test_state["session_id"] = "test_session"  # Set session_id for monitor
    response = COMMAND_HANDLERS["activate"](test_state, test_prompt)
    assert "UNINTERRUPTED, FULLY AUTONOMOUS" in response, "activate handler should return injection template"
    assert test_state["session_status"] == "active", "activate handler should set session status"
    assert test_state["autorun_stage"] == "INITIAL", "activate handler should set autorun stage"
    assert test_state["activation_prompt"] == test_prompt, "activate handler should store activation prompt"

    print("✅ All command handlers produce correct responses")

def test_handler_variations_available():
    """Test that required handler variations are available"""
    # Policy commands - uppercase only
    policy_handlers = ["SEARCH", "ALLOW", "JUSTIFY"]
    for handler in policy_handlers:
        assert handler in COMMAND_HANDLERS, f"Missing policy handler: {handler}"

    # Commands with both uppercase and lowercase
    expected_pairs = [
        ("STATUS", "status"),
        ("STOP", "stop"),
        ("EMERGENCY_STOP", "emergency_stop")
    ]

    for uppercase, lowercase in expected_pairs:
        assert uppercase in COMMAND_HANDLERS, f"Missing uppercase handler: {uppercase}"
        assert lowercase in COMMAND_HANDLERS, f"Missing lowercase handler: {lowercase}"
        # Both should point to the same function
        assert COMMAND_HANDLERS[uppercase] == COMMAND_HANDLERS[lowercase], f"Handlers for {uppercase}/{lowercase} should be the same function"

    # Activation command
    assert "activate" in COMMAND_HANDLERS, "Missing activate handler"

    print("✅ All required handler variations available")

def test_log_function():
    """Test log_info function writes to correct log files"""
    try:
        log_info("Test log message")
        print("✅ Log info function works correctly")
    except Exception as e:
        print(f"❌ Log info function error: {e}")
        raise

def test_commands_clautorun_fallback_config():
    """Test that commands/clautorun fallback CONFIG matches main CONFIG.

    The commands/clautorun script has a fallback CONFIG in the except ImportError
    block that is used when the clautorun package cannot be imported. This test
    verifies that the fallback values match the main CONFIG to ensure consistency.

    DRY principle: Both should derive from the same source of truth.
    """
    import ast
    import re

    # Read the commands/clautorun file
    commands_path = Path(__file__).parent.parent / "commands" / "clautorun"
    with open(commands_path, 'r') as f:
        content = f.read()

    # Find the fallback CONFIG in the except ImportError block
    # Pattern: "except ImportError:" followed by CONFIG = { ... }
    except_match = re.search(r'except\s+ImportError\s*:', content)
    assert except_match, "Could not find except ImportError block in commands/clautorun"

    # Find CONFIG = { after the except block
    content_after_except = content[except_match.end():]
    config_match = re.search(r'CONFIG\s*=\s*\{', content_after_except)
    assert config_match, "Could not find CONFIG = { after except ImportError"

    # Find matching closing brace by counting braces
    start_pos = config_match.end() - 1  # Position of opening brace (relative to content_after_except)
    brace_count = 0
    end_pos = start_pos
    for i, char in enumerate(content_after_except[start_pos:]):
        if char == '{':
            brace_count += 1
        elif char == '}':
            brace_count -= 1
            if brace_count == 0:
                end_pos = start_pos + i + 1
                break

    fallback_str = content_after_except[start_pos:end_pos]

    # Use ast.literal_eval for safe parsing of Python literals
    fallback_config = ast.literal_eval(fallback_str)

    # Verify critical values match main CONFIG
    # Note: fallback uses "emergency_stop_phrase" but main CONFIG uses "emergency_stop"
    assert fallback_config["completion_marker"] == CONFIG["completion_marker"], \
        f"completion_marker mismatch: fallback={fallback_config['completion_marker']}, CONFIG={CONFIG['completion_marker']}"

    assert fallback_config["emergency_stop_phrase"] == CONFIG["emergency_stop"], \
        f"emergency_stop_phrase mismatch: fallback={fallback_config['emergency_stop_phrase']}, CONFIG={CONFIG['emergency_stop']}"

    # Verify policies match
    for policy_key in ["ALLOW", "JUSTIFY", "SEARCH"]:
        assert policy_key in fallback_config["policies"], f"Missing policy {policy_key} in fallback"
        fallback_name, fallback_desc = fallback_config["policies"][policy_key]
        config_name, config_desc = CONFIG["policies"][policy_key]
        assert fallback_name == config_name, \
            f"Policy {policy_key} name mismatch: fallback={fallback_name}, CONFIG={config_name}"
        assert fallback_desc == config_desc, \
            f"Policy {policy_key} description mismatch: fallback={fallback_desc}, CONFIG={config_desc}"

    print("✅ commands/clautorun fallback CONFIG matches main CONFIG")

def main():
    """Run all compatibility tests"""
    print("🧪 Testing clautorun three-stage system compatibility")
    print("=" * 60)

    test_three_stage_confirmations()
    test_emergency_stop()
    test_completion_marker()
    test_policy_descriptions()
    test_policy_blocked_messages()
    test_injection_template()
    test_recheck_template()
    test_command_mappings()
    test_new_cr_command_mappings()
    test_config_values()
    test_command_handlers()
    test_handler_variations_available()
    test_log_function()
    test_commands_clautorun_fallback_config()

    print("\n🎯 All tests passed! clautorun three-stage system verified")
    print("📋 Verification complete:")
    print("   ✅ Three-stage confirmation markers configured")
    print("   ✅ Descriptive emergency stop string configured")
    print("   ✅ Descriptive completion marker configured")
    print("   ✅ All configuration values match")
    print("   ✅ All command responses match")
    print("   ✅ All state management works correctly")
    print("   ✅ Both uppercase and lowercase handlers available")
    print("   ✅ New /cr: short and long form commands configured")
    print("   ✅ No regressions detected")

if __name__ == "__main__":
    main()