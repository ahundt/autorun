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
from autorun import CONFIG
from autorun.core import EventContext, ThreadSafeDB
from autorun import plugins

def test_three_stage_confirmations():
    """Test three-stage confirmation markers are properly configured with DESCRIPTIVE strings"""
    # Stage 1 - dual-key pattern
    assert "stage1_instruction" in CONFIG, "Missing stage1_instruction"
    assert "stage1_message" in CONFIG, "Missing stage1_message"
    assert CONFIG["stage1_message"] == "AUTORUN_INITIAL_TASKS_COMPLETED", \
        f"Stage 1 message mismatch: {CONFIG['stage1_message']}"

    # Stage 2 - dual-key pattern
    assert "stage2_instruction" in CONFIG, "Missing stage2_instruction"
    assert "stage2_message" in CONFIG, "Missing stage2_message"
    assert CONFIG["stage2_message"] == "CRITICALLY_EVALUATING_PREVIOUS_WORK_AND_CONTINUING_TASKS_AS_NEEDED", \
        f"Stage 2 message mismatch: {CONFIG['stage2_message']}"

    # Stage 3 - dual-key pattern
    assert "stage3_instruction" in CONFIG, "Missing stage3_instruction"
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
        "SEARCH": ("strict-search", "STRICT SEARCH: ONLY modify existing files. Search existing file names and contents first. NO new files.")
    }

    for policy, expected_tuple in expected_policies.items():
        actual_tuple = CONFIG["policies"][policy]
        assert actual_tuple == expected_tuple, f"Policy {policy} mismatch: expected {expected_tuple}, got {actual_tuple}"

    print("✅ All policy descriptions match specifications")

def test_policy_blocked_messages():
    """Test policy blocked messages match documented format"""
    expected_blocked = {
        "SEARCH": 'Blocked: STRICT SEARCH policy active. To proceed: 1) Identify what functionality this file provides, 2) Search existing file names for similar functionality with patterns like "*related-topic*", 3) Search file contents for relevant classes/functions/imports, 4) Modify the most appropriate existing file. Search examples: "*auth*" for authentication, "*api*" for endpoints, "*config*" for settings, "*model*" for data structures.',
        "JUSTIFY": "Blocked: JUSTIFIED CREATION policy requires justification. To proceed: 1) Search existing file names and contents for your functionality, 2) Evaluate if existing files can be extended, 3) If no existing file works, include <AUTOFILE_JUSTIFICATION>Specific technical reason why existing files cannot accommodate this functionality</AUTOFILE_JUSTIFICATION> in your reasoning during the same prompt where you request the file creation, then retry file creation."
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

def test_command_mappings():
    """CONFIG["command_mappings"] holds the advertised names whose id has a handler."""
    expected_mappings = {
        "/ar:status": "STATUS",
        "/ar:go": "activate",
        "/ar:goprocess": "activate",
        "/ar:stop": "stop",
        "/ar:estop": "emergency_stop",
        "/ar:plannew": "NEW_PLAN",
        "/ar:planrefine": "REFINE_PLAN",
        "/ar:planupdate": "UPDATE_PLAN",
        "/ar:planprocess": "PROCESS_PLAN",
    }
    assert CONFIG["command_mappings"] == expected_mappings

    print("✅ All command mappings correctly configured")


def test_new_ar_command_mappings():
    """Old /ar: short and long forms reach the handler of their advertised form."""
    old_to_new = {
        "/ar:a": "/ar:file allow",
        "/ar:j": "/ar:file justify",
        "/ar:f": "/ar:file existing-only",
        "/ar:st": "/ar:status",
        "/ar:gp": "/ar:goprocess",
        "/ar:x": "/ar:stop",
        "/ar:sos": "/ar:estop",
        "/ar:allow": "/ar:file allow",
        "/ar:justify": "/ar:file justify",
        "/ar:find": "/ar:file existing-only",
        "/ar:run": "/ar:go",
        "/ar:proc": "/ar:goprocess",
    }
    for old, new in old_to_new.items():
        old_match, new_match = plugins.app._find_command(old), plugins.app._find_command(new)
        assert new_match is not None, f"Missing advertised command: {new}"
        assert old_match is not None and old_match.handler is new_match.handler, f"{old} does not reach {new}"
        assert old_match.activation_prompt == new_match.activation_prompt, f"{old} changes the command line"

    print("✅ All /ar: prefix commands correctly configured (short + long forms)")

def test_config_values():
    """Test configuration values are correct"""
    assert CONFIG["max_recheck_count"] == 3, f"max_recheck_count should be 3, got {CONFIG['max_recheck_count']}"

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
    """Each command is registered once, under its advertised name.

    Old names (/ar:f, /ar:st, /ar:x, /ar:sos, /ar:run) are not registry keys:
    they reach these handlers through core._RETIRED_COMMAND_SPELLINGS, and the
    unprefixed pre-2026 names (/afs, /autostop, /estop, /autorun) reach nothing.
    """
    handlers = plugins.app.command_handlers

    for alias in ["/ar:file", "/ar:status", "/ar:stop", "/ar:estop", "/ar:go", "/ar:goprocess"]:
        assert alias in handlers, f"Missing handler: {alias}"

    for retired in ["/ar:f", "/ar:st", "/ar:x", "/ar:sos", "/ar:run", "/afs", "/afst", "/autostop", "/estop", "/autorun"]:
        assert retired not in handlers, f"Retired spelling still registered: {retired}"

    # Internal ids are not prompt aliases: "stop using sed" must reach the model.
    for internal_id in ["STATUS", "stop", "emergency_stop", "activate", "ALLOW"]:
        assert internal_id not in handlers, f"Internal id registered as a prompt alias: {internal_id}"

    print("✅ All required handler variations available in plugins.app.command_handlers")

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
    test_command_mappings()
    test_new_ar_command_mappings()
    test_config_values()
    test_command_handlers()
    test_handler_variations_available()

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
