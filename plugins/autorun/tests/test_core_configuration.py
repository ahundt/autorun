#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test core configuration constants and values
"""
import sys
from pathlib import Path

# Add src directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import uuid
from autorun import CONFIG, log_info
from autorun.core import EventContext, ThreadSafeDB
from autorun import plugins

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
        "SEARCH": ("strict-search", "STRICT SEARCH: ONLY modify existing files. Use {glob} and {grep} tools. NO new files.")
    }

    for policy, expected_tuple in expected_policies.items():
        actual_tuple = CONFIG["policies"][policy]
        assert actual_tuple == expected_tuple, f"Policy {policy} mismatch: expected {expected_tuple}, got {actual_tuple}"

    print("✅ All policy descriptions match specifications")

def test_policy_blocked_messages():
    """Test policy blocked messages match documented format"""
    expected_blocked = {
        "SEARCH": 'Blocked: STRICT SEARCH policy active. To proceed: 1) Identify what functionality this file provides, 2) Search for existing files handling similar functionality using the {glob} tool with patterns like "*related-topic*", 3) Use the {grep} tool to find files with relevant classes/functions/imports, 4) Modify the most appropriate existing file. Search examples: "*auth*" for authentication, "*api*" for endpoints, "*config*" for settings, "*model*" for data structures.',
        "JUSTIFY": "Blocked: JUSTIFIED CREATION policy requires justification. To proceed: 1) Search for existing files using the {glob} tool and {grep} tool related to your functionality, 2) Evaluate if existing files can be extended, 3) If no existing file works, include <AUTOFILE_JUSTIFICATION>Specific technical reason why existing files cannot accommodate this functionality</AUTOFILE_JUSTIFICATION> in your reasoning during the same prompt where you request the file creation, then retry file creation."
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


def test_new_ar_command_mappings():
    """Test new /ar: prefix commands with short and long forms"""
    # Short forms
    short_mappings = {
        "/ar:a": "ALLOW",
        "/ar:j": "JUSTIFY",
        "/ar:f": "SEARCH",
        "/ar:st": "STATUS",
        "/ar:go": "activate",
        "/ar:gp": "activate",
        "/ar:x": "stop",
        "/ar:sos": "emergency_stop",
    }

    # Long forms
    long_mappings = {
        "/ar:allow": "ALLOW",
        "/ar:justify": "JUSTIFY",
        "/ar:find": "SEARCH",
        "/ar:status": "STATUS",
        "/ar:run": "activate",
        "/ar:proc": "activate",
        "/ar:stop": "stop",
        "/ar:estop": "emergency_stop",
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

    print("✅ All /ar: prefix commands correctly configured (short + long forms)")

def test_config_values():
    """Test configuration values are correct"""
    assert CONFIG["max_recheck_count"] == 3, f"max_recheck_count should be 3, got {CONFIG['max_recheck_count']}"
    assert CONFIG["monitor_stop_delay_seconds"] == 300, f"monitor_stop_delay_seconds should be 300, got {CONFIG['monitor_stop_delay_seconds']}"

    print("✅ All configuration values are correct")

def _dispatch(prompt: str, session_id: str = None) -> dict:
    """Canonical dispatch via daemon-path plugins.app.dispatch().

    Replaces deleted COMMAND_HANDLERS[cmd](state) calls.
    Canonical path: EventContext + plugins.app.dispatch(ctx).
    """
    sid = session_id or f"test-cfg-{uuid.uuid4().hex[:8]}"
    store = ThreadSafeDB()
    ctx = EventContext(
        session_id=sid,
        event="UserPromptSubmit",
        prompt=prompt,
        tool_name="",
        tool_input={},
        store=store,
    )
    return plugins.app.dispatch(ctx)


def test_command_handlers():
    """Test command handlers produce correct responses via canonical daemon-path dispatch.

    Canonical replacement for deleted COMMAND_HANDLERS[cmd](state):
      - SEARCH → /ar:f  (plugins._make_policy_handler("SEARCH"))
      - ALLOW  → /ar:a  (plugins._make_policy_handler("ALLOW"))
      - JUSTIFY → /ar:j  (plugins._make_policy_handler("JUSTIFY"))
      - STATUS → /ar:st  (plugins.handle_status)
      - stop/STOP → /ar:x  (plugins.handle_stop)
      - emergency_stop → /ar:sos  (plugins.handle_sos)
      - activate → /ar:go <task>  (plugins.handle_activate)
    All via: EventContext + plugins.app.dispatch(ctx).
    """
    # Test policy commands via canonical path
    result = _dispatch("/ar:f")
    assert result is not None, "SEARCH handler must return a result"
    assert result["continue"] is True, "Policy commands continue to AI"
    assert "strict-search" in result["systemMessage"], "SEARCH response must mention strict-search"
    assert "AutoFile policy:" in result["systemMessage"], "SEARCH response must include policy header"

    result = _dispatch("/ar:a")
    assert result is not None, "ALLOW handler must return a result"
    assert result["continue"] is True
    assert "allow-all" in result["systemMessage"], "ALLOW response must mention allow-all"

    result = _dispatch("/ar:j")
    assert result is not None, "JUSTIFY handler must return a result"
    assert result["continue"] is True
    assert "justify-create" in result["systemMessage"], "JUSTIFY response must mention justify-create"

    # Test status command
    result = _dispatch("/ar:st")
    assert result is not None, "STATUS handler must return a result"
    assert result["continue"] is True
    assert "AutoFile policy:" in result["systemMessage"], "STATUS response must show current policy"

    # Test stop commands
    result = _dispatch("/ar:x")
    assert result is not None, "stop handler must return a result"
    assert result["continue"] is False, "Stop command must NOT continue to AI"
    assert "Stopped" in result["systemMessage"], "Stop response must mention 'Stopped'"

    result = _dispatch("/ar:sos")
    assert result is not None, "emergency stop handler must return a result"
    assert result["continue"] is False, "Emergency stop must NOT continue to AI"
    assert "EMERGENCY STOP" in result["systemMessage"], "Emergency stop response must mention 'EMERGENCY STOP'"

    # Test activation command
    result = _dispatch("/ar:go test task description")
    assert result is not None, "activate handler must return a result"
    assert result["continue"] is True, "Autorun activation continues to AI"
    assert "UNINTERRUPTED" in result["systemMessage"] or "Autorun" in result["systemMessage"], \
        "Activate response must contain injection template or task confirmation"

    print("✅ All command handlers produce correct responses")

def test_handler_variations_available():
    """Test that required handler variations are registered in canonical daemon-path.

    Canonical replacement for COMMAND_HANDLERS dict:
      Handlers are now registered in plugins.app.command_handlers via @app.command() decorator.
      Both short (/ar:*) and legacy (/afs, /afa, etc.) aliases are registered.
    """
    handlers = plugins.app.command_handlers

    # Policy commands — short and legacy aliases must be registered
    policy_aliases = {
        "SEARCH": ["/ar:f", "/ar:find", "/afs"],
        "ALLOW":  ["/ar:a", "/ar:allow", "/afa"],
        "JUSTIFY": ["/ar:j", "/ar:justify", "/afj"],
    }
    for policy, aliases in policy_aliases.items():
        for alias in aliases:
            assert alias in handlers, f"Missing policy handler alias: {alias} (policy: {policy})"

    # Status command — multiple aliases
    for alias in ["/ar:st", "/ar:status", "/afst", "STATUS"]:
        assert alias in handlers, f"Missing status handler alias: {alias}"

    # Stop commands
    for alias in ["/ar:x", "/ar:stop", "/autostop", "stop"]:
        assert alias in handlers, f"Missing stop handler alias: {alias}"

    # Emergency stop
    for alias in ["/ar:sos", "/ar:estop", "/estop", "emergency_stop"]:
        assert alias in handlers, f"Missing emergency_stop handler alias: {alias}"

    # Activation command
    for alias in ["/ar:go", "/ar:run", "/autorun"]:
        assert alias in handlers, f"Missing activate handler alias: {alias}"

    print("✅ All required handler variations available in plugins.app.command_handlers")

def test_log_function():
    """Test log_info function writes to correct log files"""
    try:
        log_info("Test log message")
        print("✅ Log info function works correctly")
    except Exception as e:
        print(f"❌ Log info function error: {e}")
        raise

def test_commands_autorun_fallback_config():
    """Test that commands/autorun fallback CONFIG matches main CONFIG.

    The commands/autorun script has a fallback CONFIG in the except ImportError
    block that is used when the autorun package cannot be imported. This test
    verifies that the fallback values match the main CONFIG to ensure consistency.

    DRY principle: Both should derive from the same source of truth.
    """
    import ast
    import re

    # Read the commands/autorun file
    commands_path = Path(__file__).parent.parent / "commands" / "autorun"
    with open(commands_path, 'r') as f:
        content = f.read()

    # Find the fallback CONFIG in the except ImportError block
    # Pattern: "except ImportError:" followed by CONFIG = { ... }
    except_match = re.search(r'except\s+ImportError\s*:', content)
    assert except_match, "Could not find except ImportError block in commands/autorun"

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

    print("✅ commands/autorun fallback CONFIG matches main CONFIG")

def main():
    """Run all compatibility tests"""
    print("🧪 Testing autorun three-stage system compatibility")
    print("=" * 60)

    test_three_stage_confirmations()
    test_emergency_stop()
    test_completion_marker()
    test_policy_descriptions()
    test_policy_blocked_messages()
    test_injection_template()
    test_recheck_template()
    test_command_mappings()
    test_new_ar_command_mappings()
    test_config_values()
    test_command_handlers()
    test_handler_variations_available()
    test_log_function()
    test_commands_autorun_fallback_config()

    print("\n🎯 All tests passed! autorun three-stage system verified")
    print("📋 Verification complete:")
    print("   ✅ Three-stage confirmation markers configured")
    print("   ✅ Descriptive emergency stop string configured")
    print("   ✅ Descriptive completion marker configured")
    print("   ✅ All configuration values match")
    print("   ✅ All command responses match")
    print("   ✅ All state management works correctly")
    print("   ✅ Both uppercase and lowercase handlers available")
    print("   ✅ New /ar: short and long form commands configured")
    print("   ✅ No regressions detected")

if __name__ == "__main__":
    main()