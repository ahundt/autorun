#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test autorun edge cases and bug fixes

Tests for bugs #6-10 found in v0.7 autorun workflow analysis.
"""
import pytest
import sys
from pathlib import Path

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from clautorun import CONFIG
from clautorun.plugins import (
    autorun_injection,
    gate_exit_plan_mode,
    handle_activate,
    is_premature_stop
)
from clautorun.core import EventContext


class TestEmergencyStop:
    """Test Bug #6: Emergency stop must immediately halt autorun"""

    @pytest.mark.unit
    def test_emergency_stop_immediately_halts_autorun(self):
        """Test emergency stop string triggers immediate shutdown"""
        # Pass tool_result to constructor (read-only property)
        ctx = EventContext(
            session_id="test",
            event="Stop",
            tool_result=CONFIG["emergency_stop"]
        )
        ctx.autorun_active = True
        ctx.autorun_stage = EventContext.STAGE_1

        result = autorun_injection(ctx)

        assert result is None, "Should return None to allow Claude to stop"
        assert ctx.autorun_active == False, "Should deactivate autorun"
        assert ctx.autorun_stage == EventContext.STAGE_INACTIVE, "Should reset stage"

    @pytest.mark.unit
    def test_emergency_stop_in_transcript(self):
        """Test emergency stop detected in transcript (not just tool_result)"""
        # Create session transcript with emergency stop message
        transcript_data = [
            {"role": "assistant", "content": f"Some work done. {CONFIG['emergency_stop']}"}
        ]
        ctx = EventContext(session_id="test", event="Stop", tool_result="", session_transcript=transcript_data)
        ctx.autorun_active = True
        ctx.autorun_stage = EventContext.STAGE_2

        result = autorun_injection(ctx)

        assert result is None, "Should return None"
        assert ctx.autorun_active == False, "Should deactivate"
        assert ctx.autorun_stage == EventContext.STAGE_INACTIVE, "Should reset"

    @pytest.mark.unit
    def test_emergency_stop_works_in_any_stage(self):
        """Test emergency stop works regardless of current stage"""
        for stage in [EventContext.STAGE_1, EventContext.STAGE_2, EventContext.STAGE_2_COMPLETED]:
            ctx = EventContext(session_id="test", event="Stop", tool_result=CONFIG["emergency_stop"])
            ctx.autorun_active = True
            ctx.autorun_stage = stage

            result = autorun_injection(ctx)

            assert result is None, f"Should stop in stage {stage}"
            assert ctx.autorun_active == False, f"Should deactivate in stage {stage}"


class TestStageTransitionValidation:
    """Test Bug #7: Missing stage transition validation"""

    @pytest.mark.unit
    def test_premature_stage2_message_in_stage1_warns(self):
        """Test Stage 1 warns if AI outputs stage2_message prematurely"""
        ctx = EventContext(session_id="test", event="Stop", tool_result=CONFIG["stage2_message"])  # Wrong marker for Stage 1
        ctx.autorun_active = True
        ctx.autorun_stage = EventContext.STAGE_1

        result = autorun_injection(ctx)

        assert result is not None, "Should inject warning"
        assert "Continue" in str(result) or "Complete Stage 1" in str(result), \
            "Should warn about wrong stage marker"
        # Stage should not advance
        assert ctx.autorun_stage == EventContext.STAGE_1, "Should stay in Stage 1"

    @pytest.mark.unit
    def test_stage1_message_in_stage2_warns_about_regression(self):
        """Test Stage 2 warns if AI outputs stage1_message (regression)"""
        ctx = EventContext(session_id="test", event="Stop", tool_result=CONFIG["stage1_message"])  # Regression marker
        ctx.autorun_active = True
        ctx.autorun_stage = EventContext.STAGE_2

        result = autorun_injection(ctx)

        assert result is not None, "Should inject warning"
        assert "Already in Stage 2" in str(result) or "critical evaluation" in str(result), \
            "Should warn about regression"
        # Stage should not change
        assert ctx.autorun_stage == EventContext.STAGE_2, "Should stay in Stage 2"

    @pytest.mark.unit
    def test_correct_stage_marker_advances(self):
        """Test correct stage markers still work properly"""
        # Stage 1 → Stage 2
        ctx = EventContext(session_id="test", event="Stop", tool_result=CONFIG["stage1_message"])
        ctx.autorun_active = True
        ctx.autorun_stage = EventContext.STAGE_1

        result = autorun_injection(ctx)

        assert result is not None, "Should inject Stage 2 instructions"
        assert ctx.autorun_stage == EventContext.STAGE_2, "Should advance to Stage 2"

        # Stage 2 → Stage 2 COMPLETED
        ctx = EventContext(session_id="test", event="Stop", tool_result=CONFIG["stage2_message"])
        ctx.autorun_active = True
        ctx.autorun_stage = EventContext.STAGE_2
        ctx.hook_call_count = 0  # Reset for test

        result = autorun_injection(ctx)

        assert ctx.autorun_stage == EventContext.STAGE_2_COMPLETED, "Should advance to Stage 2 COMPLETED"


class TestCountdownOffByOne:
    """Test Bug #8: Countdown off-by-one fix"""

    @pytest.mark.unit
    def test_countdown_shows_correct_remaining_count(self):
        """Test countdown starts at correct value after Stage 2 completion"""
        # Complete Stage 2
        ctx = EventContext(session_id="test", event="Stop", tool_result=CONFIG["stage2_message"])
        ctx.autorun_active = True
        ctx.autorun_stage = EventContext.STAGE_2
        autorun_injection(ctx)

        # Check that hook_call_count was reset to -1
        assert ctx.hook_call_count == -1, "Should reset to -1 (not 0)"

        # Next hook call - no completion marker
        ctx = EventContext(session_id="test", event="Stop", tool_result="")
        ctx.autorun_active = True
        ctx.autorun_stage = EventContext.STAGE_2_COMPLETED
        ctx.hook_call_count = -1
        result = autorun_injection(ctx)

        # After increment: hook_call_count becomes 0
        # remaining = 3 - 0 = 3
        countdown_max = CONFIG.get("stage3_countdown_calls", 3)
        expected_remaining = countdown_max

        # Check result contains correct countdown
        assert str(expected_remaining) in str(result), \
            f"Should show {expected_remaining} remaining calls"

    @pytest.mark.unit
    def test_countdown_progression(self):
        """Test countdown decrements correctly through multiple hooks"""
        ctx = EventContext(session_id="test", event="Stop", tool_result="")
        ctx.autorun_active = True
        ctx.autorun_stage = EventContext.STAGE_2_COMPLETED
        ctx.hook_call_count = -1  # Simulate just entering Stage 2 COMPLETED

        countdown_max = CONFIG.get("stage3_countdown_calls", 3)

        for expected_remaining in range(countdown_max, 0, -1):
            result = autorun_injection(ctx)

            # Countdown message appears every 2 hooks (hook_call_count % 2 == 0)
            # Just verify we're still in countdown and not revealed Stage 3
            assert "Stage 3 countdown" in str(result) or "continue" in str(result).lower(), \
                f"Should still be in countdown at {expected_remaining}"


class TestExitPlanModeGate:
    """Test Bug #9: ExitPlanMode gate checks current stage"""

    @pytest.mark.unit
    def test_exit_plan_mode_gate_checks_current_stage(self):
        """Test gate validates current stage, not just transcript"""
        # Create session transcript with stage3_message (from previous session)
        transcript_data = [
            {"role": "assistant", "content": f"Previous work. {CONFIG['stage3_message']}"}
        ]
        ctx = EventContext(session_id="test", event="PreToolUse", tool_name="ExitPlanMode",
                           session_transcript=transcript_data)
        ctx.autorun_active = True
        ctx.autorun_stage = EventContext.STAGE_1  # Still in Stage 1

        result = gate_exit_plan_mode(ctx)

        # Should DENY because current stage is not STAGE_2_COMPLETED
        assert result is not None, "Should block ExitPlanMode"
        # Check for deny decision (result is a dict with continue/hookSpecificOutput structure)
        assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny", \
            "Should deny ExitPlanMode"
        reason = result.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
        assert "Stage 3 not reached" in reason, \
            "Should explain stage not reached"

    @pytest.mark.unit
    def test_exit_plan_mode_allowed_when_both_checks_pass(self):
        """Test ExitPlanMode allowed when transcript AND stage both indicate Stage 3"""
        # Create session transcript with stage3_message
        transcript_data = [
            {"role": "assistant", "content": f"Work done. {CONFIG['stage3_message']}"}
        ]
        ctx = EventContext(session_id="test", event="PreToolUse", tool_name="ExitPlanMode",
                           session_transcript=transcript_data)
        ctx.autorun_active = True
        ctx.autorun_stage = EventContext.STAGE_2_COMPLETED  # Correct stage

        result = gate_exit_plan_mode(ctx)

        # Should allow (return None)
        assert result is None, "Should allow ExitPlanMode when both checks pass"

    @pytest.mark.unit
    def test_exit_plan_mode_allowed_when_autorun_not_active(self):
        """Test ExitPlanMode allowed when autorun not active (regression protection)"""
        ctx = EventContext(session_id="test", event="PreToolUse", tool_name="ExitPlanMode")
        ctx.autorun_active = False  # Not in autorun mode

        result = gate_exit_plan_mode(ctx)

        # Should allow (return None) - no gating when autorun not active
        assert result is None, "Should allow when autorun not active (legacy behavior)"


class TestHandleActivateEdgeCases:
    """Test Bug #10: Handle activate with None/empty prompt"""

    @pytest.mark.unit
    def test_handle_activate_with_empty_prompt_doesnt_crash(self):
        """Test handle_activate handles empty prompt gracefully"""
        ctx = EventContext(session_id="test", event="UserPromptSubmit")
        ctx._prompt = ""  # Empty prompt
        ctx.activation_prompt = None
        ctx.file_policy = "ALLOW"

        # Should not crash
        result = handle_activate(ctx)

        assert isinstance(result, str), "Should return string"
        assert "Autorun" in result, "Should return activation message"
        assert ctx.autorun_active == True, "Should activate autorun"

    @pytest.mark.unit
    def test_handle_activate_with_none_prompt_doesnt_crash(self):
        """Test handle_activate handles None prompt gracefully"""
        ctx = EventContext(session_id="test", event="UserPromptSubmit")
        ctx._prompt = None  # None prompt (edge case)
        ctx.activation_prompt = None
        ctx.file_policy = "ALLOW"

        # Should not crash with TypeError
        try:
            result = handle_activate(ctx)
            assert isinstance(result, str), "Should return string"
            assert ctx.autorun_active == True, "Should activate autorun"
        except TypeError as e:
            pytest.fail(f"Should not crash with TypeError: {e}")

    @pytest.mark.unit
    def test_handle_activate_extracts_task_correctly(self):
        """Test handle_activate correctly extracts task from prompt"""
        ctx = EventContext(session_id="test", event="UserPromptSubmit")
        ctx._prompt = "/cr:go Fix the login bug"
        ctx.activation_prompt = "/cr:go Fix the login bug"
        ctx.file_policy = "ALLOW"

        result = handle_activate(ctx)

        assert ctx.autorun_task == "Fix the login bug", "Should extract task correctly"
        assert ctx.autorun_active == True, "Should activate"


class TestPrematureStopDetection:
    """Test is_premature_stop correctly handles all markers"""

    @pytest.mark.unit
    def test_is_premature_stop_false_with_stage_markers(self):
        """Test is_premature_stop returns False when any stage marker present"""
        for marker in [CONFIG["stage1_message"], CONFIG["stage2_message"], CONFIG["stage3_message"]]:
            ctx = EventContext(session_id="test", event="Stop", tool_result=marker)
            ctx.autorun_active = True

            assert is_premature_stop(ctx) == False, \
                f"Should not be premature stop when {marker[:30]}... present"

    @pytest.mark.unit
    def test_is_premature_stop_false_with_emergency_stop(self):
        """Test is_premature_stop returns False when emergency_stop present"""
        ctx = EventContext(session_id="test", event="Stop", tool_result=CONFIG["emergency_stop"])
        ctx.autorun_active = True

        assert is_premature_stop(ctx) == False, \
            "Should not be premature stop when emergency_stop present"

    @pytest.mark.unit
    def test_is_premature_stop_true_without_markers(self):
        """Test is_premature_stop returns True when no markers present"""
        ctx = EventContext(session_id="test", event="Stop", tool_result="Some work done but no completion marker")
        ctx.autorun_active = True

        # Create mock transcript without markers
        class MockTranscript:
            text = "Working on task..."
        ctx._transcript = MockTranscript()

        assert is_premature_stop(ctx) == True, \
            "Should be premature stop when no markers present"


# Run with: python3 -m pytest tests/test_autorun_edge_cases.py -v --override-ini='addopts='
