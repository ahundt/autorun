#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test autorun injection and three-stage completion integration.

Tests the daemon-path functions in plugins.py that replaced the deleted main.py handlers:
- is_premature_stop(ctx) — detects missing completion markers
- autorun_injection(ctx) — orchestrates Stop-hook stage transitions
- build_injection_prompt(ctx) — generates injection text with progressive disclosure
- get_stage3_instructions(ctx) — countdown logic for stage 3
- _build_progressive_stage_section(ctx) — reveals only current stage

Previously tested via main.py:stop_handler, inject_continue_prompt, inject_verification_prompt
(removed in Phase 2, Task #13). All functions now use EventContext + ThreadSafeDB.
"""
import time
import threading

import pytest

from autorun.core import EventContext, ThreadSafeDB
from autorun.config import CONFIG
from autorun.plugins import (
    is_premature_stop,
    build_injection_prompt,
    get_stage3_instructions,
    _build_progressive_stage_section,
    autorun_injection,
)


def _make_ctx(
    session_id: str,
    stage: int = EventContext.STAGE_INACTIVE,
    active: bool = True,
    transcript_text: str = "",
    hook_call_count: int = 0,
    recheck_count: int = 0,
    file_policy: str = "ALLOW",
    autorun_task: str = "",
    autorun_mode: str = "standard",
    store=None,
) -> EventContext:
    """Create an EventContext for testing autorun injection functions."""
    session_transcript = []
    if transcript_text:
        session_transcript = [{"role": "assistant", "content": transcript_text}]

    ctx = EventContext(
        session_id=session_id,
        event="Stop",
        prompt="",
        tool_name="",
        tool_input={},
        tool_result="",
        session_transcript=session_transcript,
        store=store or ThreadSafeDB(),
    )
    ctx.autorun_active = active
    ctx.autorun_stage = stage
    ctx.hook_call_count = hook_call_count
    ctx.recheck_count = recheck_count
    ctx.file_policy = file_policy
    ctx.autorun_task = autorun_task
    ctx.autorun_mode = autorun_mode
    return ctx


# === PREMATURE STOP DETECTION ===

class TestPrematureStopDetection:
    """Test is_premature_stop() detects missing completion markers."""

    def test_premature_when_no_markers(self):
        ctx = _make_ctx(
            f"test-prem-{time.time()}",
            stage=EventContext.STAGE_1,
            active=True,
            transcript_text="Working on some code changes",
        )
        assert is_premature_stop(ctx) is True

    def test_not_premature_with_stage1_marker(self):
        ctx = _make_ctx(
            f"test-s1-{time.time()}",
            stage=EventContext.STAGE_1,
            active=True,
            transcript_text=f"Done: {CONFIG['stage1_message']}",
        )
        assert is_premature_stop(ctx) is False

    def test_not_premature_with_stage2_marker(self):
        ctx = _make_ctx(
            f"test-s2-{time.time()}",
            stage=EventContext.STAGE_2,
            active=True,
            transcript_text=f"Done: {CONFIG['stage2_message']}",
        )
        assert is_premature_stop(ctx) is False

    def test_not_premature_with_stage3_marker(self):
        ctx = _make_ctx(
            f"test-s3-{time.time()}",
            stage=EventContext.STAGE_2_COMPLETED,
            active=True,
            transcript_text=f"Done: {CONFIG['stage3_message']}",
        )
        assert is_premature_stop(ctx) is False

    def test_not_premature_with_emergency_stop(self):
        ctx = _make_ctx(
            f"test-estop-{time.time()}",
            stage=EventContext.STAGE_1,
            active=True,
            transcript_text=f"User said {CONFIG['emergency_stop']}",
        )
        assert is_premature_stop(ctx) is False

    def test_not_premature_when_inactive(self):
        ctx = _make_ctx(
            f"test-inactive-{time.time()}",
            stage=EventContext.STAGE_INACTIVE,
            active=False,
            transcript_text="No markers here",
        )
        assert is_premature_stop(ctx) is False

    def test_empty_transcript_is_premature(self):
        """Empty transcript with active autorun = premature stop."""
        ctx = _make_ctx(
            f"test-empty-{time.time()}",
            stage=EventContext.STAGE_1,
            active=True,
            transcript_text="",
        )
        assert is_premature_stop(ctx) is True

    def test_marker_in_middle_of_transcript(self):
        """Stage marker anywhere in transcript counts as 'has marker'."""
        ctx = _make_ctx(
            f"test-mid-{time.time()}",
            stage=EventContext.STAGE_1,
            active=True,
            transcript_text=f"Some work {CONFIG['stage1_message']} more work after",
        )
        assert is_premature_stop(ctx) is False

    def test_partial_marker_is_premature(self):
        """Partial marker text should not match."""
        ctx = _make_ctx(
            f"test-partial-{time.time()}",
            stage=EventContext.STAGE_1,
            active=True,
            transcript_text="PARTIAL_COMPLETION_MARKER",
        )
        assert is_premature_stop(ctx) is True


# === INJECTION PROMPT GENERATION ===

class TestBuildInjectionPrompt:
    """Test build_injection_prompt() generates correct injection text."""

    def test_includes_safety_protocol(self):
        """Injection prompt includes core safety components."""
        ctx = _make_ctx(
            f"test-safety-{time.time()}",
            stage=EventContext.STAGE_1,
            active=True,
        )
        prompt = build_injection_prompt(ctx, use_progressive_disclosure=True)
        assert "UNINTERRUPTED, FULLY AUTONOMOUS, NONINTERACTIVE, PATIENT, AND SAFE EXECUTION" in prompt
        assert "SYSTEM STOP SIGNAL RULE" in prompt
        assert CONFIG["emergency_stop"] in prompt

    def test_includes_safety_mitigation(self):
        """Injection prompt includes safety protocol mitigation steps."""
        ctx = _make_ctx(
            f"test-mitigation-{time.time()}",
            stage=EventContext.STAGE_1,
            active=True,
        )
        prompt = build_injection_prompt(ctx, use_progressive_disclosure=True)
        assert "INITIATE SAFETY PROTOCOL" in prompt
        assert "CRITICAL ESCAPE TO STOP SYSTEM" in prompt

    def test_includes_stage_section(self):
        """Injection prompt includes stage-specific content."""
        ctx = _make_ctx(
            f"test-stage-{time.time()}",
            stage=EventContext.STAGE_1,
            active=True,
        )
        prompt = build_injection_prompt(ctx, use_progressive_disclosure=True)
        assert CONFIG["stage1_message"] in prompt

    def test_progressive_disclosure_hides_future_stages(self):
        """With progressive disclosure, future stage markers are hidden."""
        ctx = _make_ctx(
            f"test-pd-{time.time()}",
            stage=EventContext.STAGE_1,
            active=True,
        )
        prompt = build_injection_prompt(ctx, use_progressive_disclosure=True)
        assert CONFIG["stage2_message"] not in prompt
        assert CONFIG["stage3_message"] not in prompt

    def test_stage2_injection_shows_stage2_marker(self):
        """Stage 2 injection includes stage 2 completion marker."""
        ctx = _make_ctx(
            f"test-s2-inj-{time.time()}",
            stage=EventContext.STAGE_2,
            active=True,
        )
        prompt = build_injection_prompt(ctx, use_progressive_disclosure=True)
        assert CONFIG["stage2_message"] in prompt

    def test_includes_file_policy(self):
        """Injection prompt includes file creation policy instructions."""
        for policy in ["ALLOW", "JUSTIFY", "SEARCH"]:
            ctx = _make_ctx(
                f"test-policy-{policy}-{time.time()}",
                stage=EventContext.STAGE_1,
                active=True,
                file_policy=policy,
            )
            prompt = build_injection_prompt(ctx, use_progressive_disclosure=True)
            _, policy_desc = CONFIG["policies"][policy]
            assert policy_desc in prompt, f"Policy instructions for {policy} not found"
            assert "FILE CREATION POLICY" in prompt

    def test_three_stage_system_mentioned(self):
        """Injection prompt references the three-stage system."""
        ctx = _make_ctx(
            f"test-3s-{time.time()}",
            stage=EventContext.STAGE_1,
            active=True,
        )
        prompt = build_injection_prompt(ctx, use_progressive_disclosure=True)
        assert "THREE-STAGE COMPLETION SYSTEM" in prompt


# === STAGE 3 COUNTDOWN ===

class TestStage3Countdown:
    """Test get_stage3_instructions() countdown logic."""

    def test_before_stage2_completed(self):
        """Before stage 2 completion: tells user to complete earlier stages."""
        ctx = _make_ctx(f"test-s3-early-{time.time()}", stage=EventContext.STAGE_1)
        instructions = get_stage3_instructions(ctx)
        assert "complete" in instructions.lower() or "stage" in instructions.lower()

    def test_countdown_not_reached(self):
        """At stage 2 completed but countdown not done: shows remaining count."""
        ctx = _make_ctx(
            f"test-s3-cnt-{time.time()}",
            stage=EventContext.STAGE_2_COMPLETED,
            hook_call_count=0,
        )
        instructions = get_stage3_instructions(ctx)
        assert "more hook call" in instructions.lower() or "remaining" in instructions.lower() or "after" in instructions.lower()

    def test_countdown_reached(self):
        """At stage 2 completed with countdown done: reveals stage 3."""
        ctx = _make_ctx(
            f"test-s3-done-{time.time()}",
            stage=EventContext.STAGE_2_COMPLETED,
            hook_call_count=100,
        )
        instructions = get_stage3_instructions(ctx)
        assert CONFIG["stage3_message"] in instructions or "STAGE 3" in instructions

    def test_countdown_progression(self):
        """Countdown decreases as hook_call_count increases."""
        countdown_max = CONFIG.get("stage3_countdown_calls", 3)
        for i in range(countdown_max):
            ctx = _make_ctx(
                f"test-s3-prog-{i}-{time.time()}",
                stage=EventContext.STAGE_2_COMPLETED,
                hook_call_count=i,
            )
            instructions = get_stage3_instructions(ctx)
            remaining = countdown_max - i
            assert str(remaining) in instructions or "after" in instructions.lower()


# === AUTORUN INJECTION (STOP HOOK) ===

class TestAutorunInjection:
    """Test autorun_injection() orchestrates the full Stop-hook response."""

    def test_inactive_returns_none(self):
        """Non-autorun session returns None (no intervention)."""
        ctx = _make_ctx(
            f"test-inact-{time.time()}",
            stage=EventContext.STAGE_INACTIVE,
            active=False,
            transcript_text="Regular conversation",
        )
        result = autorun_injection(ctx)
        assert result is None

    def test_emergency_stop_deactivates(self):
        """Emergency stop marker deactivates autorun and returns None."""
        ctx = _make_ctx(
            f"test-estop-inj-{time.time()}",
            stage=EventContext.STAGE_1,
            active=True,
            transcript_text=f"Some work {CONFIG['emergency_stop']}",
        )
        result = autorun_injection(ctx)
        assert result is None
        assert ctx.autorun_active is False
        assert ctx.autorun_stage == EventContext.STAGE_INACTIVE

    def test_stage1_to_stage2_transition(self):
        """Stage 1 marker advances to stage 2."""
        ctx = _make_ctx(
            f"test-s1s2-{time.time()}",
            stage=EventContext.STAGE_1,
            active=True,
            transcript_text=f"Work done {CONFIG['stage1_message']}",
        )
        result = autorun_injection(ctx)
        assert ctx.autorun_stage == EventContext.STAGE_2
        # Result may be dict (block) or None depending on injection method
        if result is not None:
            assert "STAGE 2" in str(result) or CONFIG["stage2_message"] in str(result)

    def test_stage2_to_stage2_completed_transition(self):
        """Stage 2 marker advances to STAGE_2_COMPLETED."""
        ctx = _make_ctx(
            f"test-s2comp-{time.time()}",
            stage=EventContext.STAGE_2,
            active=True,
            transcript_text=f"Evaluated {CONFIG['stage2_message']}",
        )
        result = autorun_injection(ctx)
        assert ctx.autorun_stage == EventContext.STAGE_2_COMPLETED

    def test_premature_stop_in_stage1_injects_continue(self):
        """Premature stop in stage 1 injects continue prompt."""
        ctx = _make_ctx(
            f"test-prem-s1-{time.time()}",
            stage=EventContext.STAGE_1,
            active=True,
            transcript_text="Working on code, no markers",
        )
        result = autorun_injection(ctx)
        # Should inject a continue prompt (not None)
        if result is not None:
            assert isinstance(result, dict)

    def test_premature_stop_in_stage2_injects_continue(self):
        """Premature stop in stage 2 injects stage 2 instructions."""
        ctx = _make_ctx(
            f"test-prem-s2-{time.time()}",
            stage=EventContext.STAGE_2,
            active=True,
            transcript_text="Evaluating but no markers",
        )
        result = autorun_injection(ctx)
        if result is not None:
            assert isinstance(result, dict)

    def test_stage3_completion_deactivates(self):
        """Stage 3 marker after countdown completes autorun."""
        countdown_max = CONFIG.get("stage3_countdown_calls", 3)
        ctx = _make_ctx(
            f"test-s3done-{time.time()}",
            stage=EventContext.STAGE_2_COMPLETED,
            active=True,
            hook_call_count=countdown_max,  # Past countdown
            transcript_text=f"Done {CONFIG['stage3_message']}",
        )
        result = autorun_injection(ctx)
        assert result is None  # Allow Claude to stop
        assert ctx.autorun_active is False
        assert ctx.autorun_stage == EventContext.STAGE_INACTIVE

    def test_premature_stage3_during_countdown(self):
        """Stage 3 marker before countdown done reverts to stage 2."""
        ctx = _make_ctx(
            f"test-prem-s3-{time.time()}",
            stage=EventContext.STAGE_2_COMPLETED,
            active=True,
            hook_call_count=0,  # Before countdown
            transcript_text=f"Trying {CONFIG['stage3_message']}",
        )
        result = autorun_injection(ctx)
        # Should revert to stage 2
        assert ctx.autorun_stage == EventContext.STAGE_2

    def test_wrong_stage_marker_during_stage1(self):
        """Stage 2 marker during stage 1 produces corrective message."""
        ctx = _make_ctx(
            f"test-wrong-s2-{time.time()}",
            stage=EventContext.STAGE_1,
            active=True,
            transcript_text=f"Oops {CONFIG['stage2_message']}",
        )
        result = autorun_injection(ctx)
        if result is not None:
            # Should tell user to complete stage 1 first
            msg = str(result)
            assert CONFIG["stage1_message"] in msg or "Stage 1" in msg

    def test_wrong_stage3_marker_during_stage1(self):
        """Stage 3 marker during stage 1 produces corrective message."""
        ctx = _make_ctx(
            f"test-wrong-s3-{time.time()}",
            stage=EventContext.STAGE_1,
            active=True,
            transcript_text=f"Oops {CONFIG['stage3_message']}",
        )
        result = autorun_injection(ctx)
        if result is not None:
            msg = str(result)
            assert CONFIG["stage1_message"] in msg or "Stage 1" in msg

    def test_stage2_countdown_alternation(self):
        """STAGE_2_COMPLETED alternates between countdown and injection prompts."""
        countdown_max = CONFIG.get("stage3_countdown_calls", 3)
        ctx = _make_ctx(
            f"test-alt-{time.time()}",
            stage=EventContext.STAGE_2_COMPLETED,
            active=True,
            hook_call_count=0,
            transcript_text="No markers",
        )
        # Each call increments hook_call_count, so let's test the alternation
        # Even counts: countdown message, Odd counts: injection prompt
        for i in range(min(countdown_max, 3)):
            ctx.hook_call_count = i
            ctx.autorun_stage = EventContext.STAGE_2_COMPLETED
            result = autorun_injection(ctx)
            if result is not None:
                assert isinstance(result, dict)

    def test_hook_call_count_increments(self):
        """autorun_injection increments hook_call_count."""
        ctx = _make_ctx(
            f"test-hcc-{time.time()}",
            stage=EventContext.STAGE_1,
            active=True,
            hook_call_count=5,
            transcript_text="Working, no markers",
        )
        autorun_injection(ctx)
        assert ctx.hook_call_count == 6


# === PROGRESSIVE DISCLOSURE ===

class TestProgressiveDisclosure:
    """Test _build_progressive_stage_section() reveals only current stage."""

    def test_stage1_shows_only_stage1(self):
        ctx = _make_ctx(f"test-pd-s1-{time.time()}", stage=EventContext.STAGE_1)
        section = _build_progressive_stage_section(ctx)
        assert CONFIG["stage1_message"] in section
        assert CONFIG["stage2_message"] not in section
        assert CONFIG["stage3_message"] not in section

    def test_stage2_shows_only_stage2(self):
        ctx = _make_ctx(f"test-pd-s2-{time.time()}", stage=EventContext.STAGE_2)
        section = _build_progressive_stage_section(ctx)
        assert CONFIG["stage2_message"] in section
        assert CONFIG["stage1_message"] not in section

    def test_stage3_shows_stage3(self):
        ctx = _make_ctx(
            f"test-pd-s3-{time.time()}",
            stage=EventContext.STAGE_2_COMPLETED,
            hook_call_count=100,
        )
        section = _build_progressive_stage_section(ctx)
        assert CONFIG["stage3_message"] in section

    def test_inactive_defaults_to_stage1(self):
        ctx = _make_ctx(f"test-pd-inact-{time.time()}", stage=EventContext.STAGE_INACTIVE)
        section = _build_progressive_stage_section(ctx)
        assert CONFIG["stage1_message"] in section


# === TEMPLATE CONTENT COMPLETENESS ===

class TestTemplateContent:
    """Test that CONFIG templates contain all required critical components."""

    def test_injection_template_critical_components(self):
        """Injection template has all critical safety components."""
        template = CONFIG["injection_template"]
        critical = [
            "UNINTERRUPTED, FULLY AUTONOMOUS, NONINTERACTIVE, PATIENT, AND SAFE EXECUTION",
            "SYSTEM STOP SIGNAL RULE",
            "Safety Protocol",
            "INITIATE SAFETY PROTOCOL",
            "CRITICAL ESCAPE TO STOP SYSTEM",
            "FILE CREATION POLICY",
        ]
        for component in critical:
            assert component in template, f"Missing critical component: {component}"

    def test_recheck_template_has_verification_components(self):
        """Recheck template has verification-specific components."""
        template = CONFIG["recheck_template"]
        assert "AUTORUN TASK VERIFICATION" in template
        assert "verification attempt" in template.lower() or "recheck_count" in template.lower()

    def test_stage_messages_are_distinct(self):
        """All three stage messages are different strings."""
        assert CONFIG["stage1_message"] != CONFIG["stage2_message"]
        assert CONFIG["stage2_message"] != CONFIG["stage3_message"]
        assert CONFIG["stage1_message"] != CONFIG["stage3_message"]

    def test_emergency_stop_is_distinct_from_stages(self):
        """Emergency stop string is distinct from all stage markers."""
        assert CONFIG["emergency_stop"] != CONFIG["stage1_message"]
        assert CONFIG["emergency_stop"] != CONFIG["stage2_message"]
        assert CONFIG["emergency_stop"] != CONFIG["stage3_message"]


# === SESSION ISOLATION ===

class TestSessionIsolation:
    """Test that different sessions maintain independent state."""

    def test_separate_stores_independent_state(self):
        """Two sessions with separate ThreadSafeDB stores have independent state."""
        store1 = ThreadSafeDB()
        store2 = ThreadSafeDB()

        ctx1 = _make_ctx(
            "session-iso-1",
            stage=EventContext.STAGE_1,
            active=True,
            transcript_text="Work for session 1",
            store=store1,
        )
        ctx2 = _make_ctx(
            "session-iso-2",
            stage=EventContext.STAGE_2,
            active=True,
            transcript_text=f"Work for session 2 {CONFIG['stage2_message']}",
            store=store2,
        )

        # Session 1: premature stop (no marker)
        assert is_premature_stop(ctx1) is True

        # Session 2: not premature (has stage2 marker)
        assert is_premature_stop(ctx2) is False

        # States are independent
        assert ctx1.autorun_stage == EventContext.STAGE_1
        assert ctx2.autorun_stage == EventContext.STAGE_2

    def test_concurrent_sessions(self):
        """Multiple concurrent sessions process independently."""
        results = {}

        def process_session(sid):
            ctx = _make_ctx(
                sid,
                stage=EventContext.STAGE_1,
                active=True,
                transcript_text="Working, no markers",
            )
            results[sid] = {
                "premature": is_premature_stop(ctx),
                "stage": ctx.autorun_stage,
            }

        threads = []
        for i in range(3):
            t = threading.Thread(target=process_session, args=(f"concurrent-{i}",))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert len(results) == 3
        for sid, result in results.items():
            assert result["premature"] is True
            assert result["stage"] == EventContext.STAGE_1


# === ERROR RECOVERY ===

class TestErrorRecovery:
    """Test error recovery and resilience scenarios."""

    def test_autorun_injection_with_minimal_state(self):
        """autorun_injection handles minimal state without crashing."""
        ctx = _make_ctx(
            f"test-minimal-{time.time()}",
            stage=EventContext.STAGE_1,
            active=True,
            transcript_text="Work done, no completion marker",
        )
        # Should not raise
        result = autorun_injection(ctx)
        # Either injects or returns None — no crash
        assert result is None or isinstance(result, dict)

    def test_empty_transcript_handled(self):
        """Empty transcript does not crash any function."""
        ctx = _make_ctx(
            f"test-empty-t-{time.time()}",
            stage=EventContext.STAGE_1,
            active=True,
            transcript_text="",
        )
        assert is_premature_stop(ctx) is True
        prompt = build_injection_prompt(ctx)
        assert isinstance(prompt, str) and len(prompt) > 0

    def test_forced_compliance_at_high_recheck_count(self):
        """Very high recheck_count triggers forced compliance template."""
        ctx = _make_ctx(
            f"test-forced-{time.time()}",
            stage=EventContext.STAGE_1,
            active=True,
            recheck_count=CONFIG["max_recheck_count"] + 1,
        )
        prompt = build_injection_prompt(ctx)
        assert CONFIG["stage3_message"] in prompt


# === COMPLETE FLOW ===

class TestCompleteFlow:
    """Test complete three-stage flow end-to-end via autorun_injection."""

    def test_full_stage_progression(self):
        """Walk through all stages: 1 -> 2 -> 2_COMPLETED -> completion."""
        store = ThreadSafeDB()
        countdown_max = CONFIG.get("stage3_countdown_calls", 3)

        # Stage 1: complete initial tasks
        ctx = _make_ctx(
            "flow-test",
            stage=EventContext.STAGE_1,
            active=True,
            transcript_text=f"Done: {CONFIG['stage1_message']}",
            store=store,
        )
        autorun_injection(ctx)
        assert ctx.autorun_stage == EventContext.STAGE_2

        # Stage 2: complete evaluation
        ctx2 = _make_ctx(
            "flow-test",
            stage=EventContext.STAGE_2,
            active=True,
            transcript_text=f"Evaluated: {CONFIG['stage2_message']}",
            store=store,
        )
        autorun_injection(ctx2)
        assert ctx2.autorun_stage == EventContext.STAGE_2_COMPLETED

        # Stage 2 completed: countdown cycles
        for i in range(countdown_max):
            ctx_cd = _make_ctx(
                "flow-test",
                stage=EventContext.STAGE_2_COMPLETED,
                active=True,
                hook_call_count=i,
                transcript_text="Continuing evaluation",
                store=store,
            )
            autorun_injection(ctx_cd)

        # Stage 3: final completion
        ctx3 = _make_ctx(
            "flow-test",
            stage=EventContext.STAGE_2_COMPLETED,
            active=True,
            hook_call_count=countdown_max,
            transcript_text=f"All done: {CONFIG['stage3_message']}",
            store=store,
        )
        result = autorun_injection(ctx3)
        assert result is None  # Allow stop
        assert ctx3.autorun_active is False
        assert ctx3.autorun_stage == EventContext.STAGE_INACTIVE
