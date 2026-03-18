#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test three-stage completion workflow.

Tests the stage-transition logic in plugins.py:
- is_premature_stop() detects missing completion markers
- build_injection_prompt() generates stage-appropriate instructions
- _build_progressive_stage_section() reveals only current stage (progressive disclosure)
- get_stage3_instructions() handles countdown logic
- autorun_injection() orchestrates the full Stop-hook response

Previously tested via main.py:stop_handler (removed in Phase 2, Task #13).
Canonical replacements in plugins.py: is_premature_stop(), build_injection_prompt(),
autorun_injection(), get_stage3_instructions(), _build_progressive_stage_section().
"""
import time

import pytest

from autorun.core import EventContext, ThreadSafeDB
from autorun.config import CONFIG
from autorun.plugins import (
    is_premature_stop,
    build_injection_prompt,
    get_stage3_instructions,
    _build_progressive_stage_section,
)


def _make_stop_ctx(
    session_id: str,
    stage: str = EventContext.STAGE_INACTIVE,
    active: bool = True,
    transcript_text: str = "",
    hook_call_count: int = 0,
    store=None,
) -> EventContext:
    """Create a Stop EventContext for three-stage testing."""
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
    return ctx


class TestIsPrematureStop:
    """Test is_premature_stop() detects missing completion markers."""

    def test_premature_when_no_markers(self):
        """Active autorun with no stage markers = premature stop."""
        ctx = _make_stop_ctx(
            f"test-premature-{time.time()}",
            stage=EventContext.STAGE_1,
            active=True,
            transcript_text="Working on some code changes",
        )
        assert is_premature_stop(ctx) is True

    def test_not_premature_when_stage1_marker_present(self):
        """Stage 1 marker in transcript = not premature."""
        ctx = _make_stop_ctx(
            f"test-s1-marker-{time.time()}",
            stage=EventContext.STAGE_1,
            active=True,
            transcript_text=f"Done: {CONFIG['stage1_message']}",
        )
        assert is_premature_stop(ctx) is False

    def test_not_premature_when_stage2_marker_present(self):
        """Stage 2 marker in transcript = not premature."""
        ctx = _make_stop_ctx(
            f"test-s2-marker-{time.time()}",
            stage=EventContext.STAGE_2,
            active=True,
            transcript_text=f"Done: {CONFIG['stage2_message']}",
        )
        assert is_premature_stop(ctx) is False

    def test_not_premature_when_stage3_marker_present(self):
        """Stage 3 marker in transcript = not premature."""
        ctx = _make_stop_ctx(
            f"test-s3-marker-{time.time()}",
            stage=EventContext.STAGE_2_COMPLETED,
            active=True,
            transcript_text=f"Done: {CONFIG['stage3_message']}",
        )
        assert is_premature_stop(ctx) is False

    def test_not_premature_when_emergency_stop(self):
        """Emergency stop marker = not premature (always allow stop)."""
        ctx = _make_stop_ctx(
            f"test-estop-{time.time()}",
            stage=EventContext.STAGE_1,
            active=True,
            transcript_text=f"User said {CONFIG['emergency_stop']}",
        )
        assert is_premature_stop(ctx) is False

    def test_not_premature_when_inactive(self):
        """Autorun not active = never premature."""
        ctx = _make_stop_ctx(
            f"test-inactive-{time.time()}",
            stage=EventContext.STAGE_INACTIVE,
            active=False,
            transcript_text="No markers here",
        )
        assert is_premature_stop(ctx) is False

    def test_premature_with_wrong_stage_marker(self):
        """Stage 3 marker during stage 1 still counts as 'has marker' (not premature).

        is_premature_stop checks for ANY marker, not stage-specific ones.
        Stage-specific enforcement happens in build_injection_prompt.
        """
        ctx = _make_stop_ctx(
            f"test-wrong-stage-{time.time()}",
            stage=EventContext.STAGE_1,
            active=True,
            transcript_text=f"Premature: {CONFIG['stage3_message']}",
        )
        # Any marker present = not premature (stage validation is elsewhere)
        assert is_premature_stop(ctx) is False


class TestProgressiveDisclosure:
    """Test _build_progressive_stage_section() reveals only current stage."""

    def test_stage1_shows_only_stage1(self):
        """Stage 1: only stage 1 instructions visible."""
        ctx = _make_stop_ctx(f"test-pd-s1-{time.time()}", stage=EventContext.STAGE_1)
        section = _build_progressive_stage_section(ctx)
        assert CONFIG["stage1_message"] in section, "Stage 1 marker should be in output"
        assert CONFIG["stage2_message"] not in section, "Stage 2 marker should NOT be revealed yet"
        assert CONFIG["stage3_message"] not in section, "Stage 3 marker should NOT be revealed yet"

    def test_stage2_shows_only_stage2(self):
        """Stage 2: only stage 2 instructions visible."""
        ctx = _make_stop_ctx(f"test-pd-s2-{time.time()}", stage=EventContext.STAGE_2)
        section = _build_progressive_stage_section(ctx)
        assert CONFIG["stage2_message"] in section, "Stage 2 marker should be in output"
        assert CONFIG["stage1_message"] not in section, "Stage 1 marker should NOT be shown"

    def test_stage3_shows_stage3(self):
        """Stage 2 completed: stage 3 instructions visible."""
        ctx = _make_stop_ctx(
            f"test-pd-s3-{time.time()}",
            stage=EventContext.STAGE_2_COMPLETED,
            hook_call_count=100,  # past countdown
        )
        section = _build_progressive_stage_section(ctx)
        assert CONFIG["stage3_message"] in section, "Stage 3 marker should be in output"

    def test_inactive_defaults_to_stage1(self):
        """Inactive stage shows stage 1 instructions."""
        ctx = _make_stop_ctx(f"test-pd-inactive-{time.time()}", stage=EventContext.STAGE_INACTIVE)
        section = _build_progressive_stage_section(ctx)
        assert CONFIG["stage1_message"] in section


class TestGetStage3Instructions:
    """Test get_stage3_instructions() countdown logic."""

    def test_not_at_stage2_completed(self):
        """Before stage 2 completion: tells user to complete earlier stages."""
        ctx = _make_stop_ctx(f"test-s3i-early-{time.time()}", stage=EventContext.STAGE_1)
        instructions = get_stage3_instructions(ctx)
        assert "complete" in instructions.lower() or "stage" in instructions.lower()

    def test_countdown_not_reached(self):
        """At stage 2 completed but countdown not done: shows remaining count."""
        ctx = _make_stop_ctx(
            f"test-s3i-countdown-{time.time()}",
            stage=EventContext.STAGE_2_COMPLETED,
            hook_call_count=0,
        )
        instructions = get_stage3_instructions(ctx)
        assert "more hook call" in instructions.lower() or "remaining" in instructions.lower() or "after" in instructions.lower()

    def test_countdown_reached(self):
        """At stage 2 completed with countdown done: reveals stage 3."""
        ctx = _make_stop_ctx(
            f"test-s3i-done-{time.time()}",
            stage=EventContext.STAGE_2_COMPLETED,
            hook_call_count=100,  # well past countdown
        )
        instructions = get_stage3_instructions(ctx)
        assert CONFIG["stage3_message"] in instructions or "STAGE 3" in instructions


class TestBuildInjectionPrompt:
    """Test build_injection_prompt() generates complete injection text."""

    def test_includes_stage_section(self):
        """Injection prompt includes stage-specific content."""
        ctx = _make_stop_ctx(
            f"test-inject-{time.time()}",
            stage=EventContext.STAGE_1,
            active=True,
        )
        prompt = build_injection_prompt(ctx, use_progressive_disclosure=True)
        assert CONFIG["stage1_message"] in prompt, "Injection should include stage 1 marker"

    def test_progressive_disclosure_hides_future_stages(self):
        """With progressive disclosure, future stage markers are hidden."""
        ctx = _make_stop_ctx(
            f"test-inject-pd-{time.time()}",
            stage=EventContext.STAGE_1,
            active=True,
        )
        prompt = build_injection_prompt(ctx, use_progressive_disclosure=True)
        assert CONFIG["stage2_message"] not in prompt, "Stage 2 should be hidden during stage 1"
        assert CONFIG["stage3_message"] not in prompt, "Stage 3 should be hidden during stage 1"

    def test_stage2_injection_shows_stage2_marker(self):
        """Stage 2 injection includes stage 2 completion marker."""
        ctx = _make_stop_ctx(
            f"test-inject-s2-{time.time()}",
            stage=EventContext.STAGE_2,
            active=True,
        )
        prompt = build_injection_prompt(ctx, use_progressive_disclosure=True)
        assert CONFIG["stage2_message"] in prompt, "Injection should include stage 2 marker"
