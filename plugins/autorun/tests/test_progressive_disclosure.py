#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test progressive disclosure in injection template

The progressive disclosure system ensures AI only sees current stage instructions,
preventing premature output of Stage 2/3 completion strings.
"""
import pytest
import sys
from pathlib import Path

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from autorun import CONFIG
from autorun.plugins import _build_progressive_stage_section, build_injection_prompt
from autorun.core import EventContext


class TestProgressiveDisclosure:
    """Test progressive disclosure prevents AI from seeing future stage strings"""

    @pytest.mark.unit
    def test_stage1_shows_only_stage1(self):
        """Test Stage 1 injection reveals only Stage 1 instructions"""
        # Create mock context in Stage 1
        ctx = EventContext(session_id="test", event="PreToolUse")
        ctx.autorun_active = True
        ctx.autorun_stage = EventContext.STAGE_1
        ctx.file_policy = "allow-all"
        ctx.recheck_count = 0

        # Build progressive stage section
        stage_section = _build_progressive_stage_section(ctx)

        # Should contain Stage 1 instructions and confirmation string
        assert "STAGE 1 - INITIAL IMPLEMENTATION" in stage_section, \
            "Stage 1 section should have Stage 1 header"
        assert CONFIG["stage1_instruction"] in stage_section, \
            "Stage 1 section should have Stage 1 instruction text"
        assert CONFIG["stage1_message"] in stage_section, \
            "Stage 1 section should have Stage 1 completion string"

        # Should NOT contain Stage 2 or Stage 3 strings (progressive disclosure)
        assert CONFIG["stage2_message"] not in stage_section, \
            "Stage 1 should NOT reveal Stage 2 completion string"
        assert CONFIG["stage3_message"] not in stage_section, \
            "Stage 1 should NOT reveal Stage 3 completion string"
        assert "STAGE 2" not in stage_section, \
            "Stage 1 should NOT show Stage 2 section"
        assert "STAGE 3" not in stage_section, \
            "Stage 1 should NOT show Stage 3 section"

        # Should mention Stage 2 will be revealed later
        assert "Stage 2 instructions after" in stage_section, \
            "Should tell AI Stage 2 comes later"

    @pytest.mark.unit
    def test_stage2_shows_only_stage2(self):
        """Test Stage 2 injection reveals only Stage 2 instructions"""
        # Create mock context in Stage 2
        ctx = EventContext(session_id="test", event="PreToolUse")
        ctx.autorun_active = True
        ctx.autorun_stage = EventContext.STAGE_2
        ctx.file_policy = "allow-all"
        ctx.recheck_count = 0

        # Build progressive stage section
        stage_section = _build_progressive_stage_section(ctx)

        # Should contain Stage 2 instructions and confirmation string
        assert "STAGE 2 - CRITICAL EVALUATION" in stage_section, \
            "Stage 2 section should have Stage 2 header"
        assert CONFIG["stage2_instruction"] in stage_section, \
            "Stage 2 section should have Stage 2 instruction text"
        assert CONFIG["stage2_message"] in stage_section, \
            "Stage 2 section should have Stage 2 completion string"

        # Should NOT contain Stage 3 strings yet (progressive disclosure)
        assert CONFIG["stage3_message"] not in stage_section, \
            "Stage 2 should NOT reveal Stage 3 completion string"
        assert "STAGE 3" not in stage_section, \
            "Stage 2 should NOT show Stage 3 section"

        # Should mention Stage 3 will be revealed later
        assert "Stage 3 instructions after" in stage_section, \
            "Should tell AI Stage 3 comes later"

    @pytest.mark.unit
    def test_stage3_shows_stage3(self):
        """Test Stage 3 injection reveals Stage 3 instructions"""
        # Create mock context in Stage 3
        ctx = EventContext(session_id="test", event="PreToolUse")
        ctx.autorun_active = True
        ctx.autorun_stage = EventContext.STAGE_2_COMPLETED
        ctx.file_policy = "allow-all"
        ctx.recheck_count = 0

        # Build progressive stage section
        stage_section = _build_progressive_stage_section(ctx)

        # Should contain Stage 3 instructions and confirmation string
        assert "STAGE 3 - FINAL VERIFICATION" in stage_section, \
            "Stage 3 section should have Stage 3 header"
        assert CONFIG["stage3_instruction"] in stage_section, \
            "Stage 3 section should have Stage 3 instruction text"
        assert CONFIG["stage3_message"] in stage_section, \
            "Stage 3 section should have Stage 3 completion string"

    @pytest.mark.unit
    def test_inactive_stage_defaults_to_stage1(self):
        """Test STAGE_INACTIVE defaults to showing Stage 1"""
        # Create mock context with inactive stage
        ctx = EventContext(session_id="test", event="PreToolUse")
        ctx.autorun_active = False
        ctx.autorun_stage = EventContext.STAGE_INACTIVE
        ctx.file_policy = "allow-all"
        ctx.recheck_count = 0

        # Build progressive stage section
        stage_section = _build_progressive_stage_section(ctx)

        # Should default to Stage 1
        assert "STAGE 1 - INITIAL IMPLEMENTATION" in stage_section, \
            "INACTIVE should default to Stage 1"
        assert CONFIG["stage1_message"] in stage_section, \
            "INACTIVE should show Stage 1 completion string"

    @pytest.mark.unit
    def test_build_injection_prompt_with_progressive_disclosure(self):
        """Test build_injection_prompt uses progressive disclosure by default"""
        # Create mock context
        ctx = EventContext(session_id="test", event="PreToolUse")
        ctx.autorun_active = True
        ctx.autorun_stage = EventContext.STAGE_1
        ctx.file_policy = "allow-all"
        ctx.recheck_count = 0
        ctx.autorun_task = "test task"
        ctx.autorun_mode = "standard"

        # Build injection with progressive disclosure (default)
        injection = build_injection_prompt(ctx, use_progressive_disclosure=True)

        # Should have Stage 1 content
        assert "STAGE 1 - INITIAL IMPLEMENTATION" in injection, \
            "Progressive injection should have Stage 1"
        assert CONFIG["stage1_message"] in injection, \
            "Progressive injection should have Stage 1 completion string"

        # Should NOT have Stage 2/3 strings
        assert CONFIG["stage2_message"] not in injection, \
            "Progressive injection should NOT reveal Stage 2 string"
        assert CONFIG["stage3_message"] not in injection, \
            "Progressive injection should NOT reveal Stage 3 string"

        # Should have safety protocol sections
        assert "MANDATORY PROCESS TO CONTINUE EXECUTION" in injection, \
            "Should have section 1"
        assert "SYSTEM STOP SIGNAL RULE" in injection, \
            "Should have section 2"
        assert "Safety Protocol" in injection, \
            "Should have section 3"
        assert "CRITICAL ESCAPE TO STOP SYSTEM" in injection, \
            "Should have section 4"
        assert "FILE CREATION POLICY" in injection, \
            "Should have section 6"

    @pytest.mark.unit
    def test_build_injection_prompt_without_progressive_disclosure(self):
        """Test build_injection_prompt can use full template if requested"""
        # Create mock context
        ctx = EventContext(session_id="test", event="PreToolUse")
        ctx.autorun_active = True
        ctx.autorun_stage = EventContext.STAGE_1
        ctx.file_policy = "allow-all"
        ctx.recheck_count = 0
        ctx.autorun_task = "test task"
        ctx.autorun_mode = "standard"

        # Build injection without progressive disclosure
        injection = build_injection_prompt(ctx, use_progressive_disclosure=False)

        # Should have ALL stage strings (original behavior)
        assert CONFIG["stage1_message"] in injection, \
            "Full template should have Stage 1 string"
        assert CONFIG["stage2_message"] in injection, \
            "Full template should have Stage 2 string"
        assert CONFIG["stage3_message"] in injection, \
            "Full template should have Stage 3 string"

    @pytest.mark.unit
    def test_stage_transition_reveals_next_stage(self):
        """Test that transitioning stages reveals next stage instructions"""
        # Stage 1 → Stage 2
        ctx = EventContext(session_id="test", event="PreToolUse")
        ctx.autorun_active = True
        ctx.file_policy = "allow-all"
        ctx.recheck_count = 0

        # Start in Stage 1
        ctx.autorun_stage = EventContext.STAGE_1
        stage1_section = _build_progressive_stage_section(ctx)
        assert CONFIG["stage2_message"] not in stage1_section, \
            "Stage 1 should not reveal Stage 2 string"

        # Transition to Stage 2
        ctx.autorun_stage = EventContext.STAGE_2
        stage2_section = _build_progressive_stage_section(ctx)
        assert CONFIG["stage2_message"] in stage2_section, \
            "Stage 2 SHOULD reveal Stage 2 string"
        assert CONFIG["stage3_message"] not in stage2_section, \
            "Stage 2 should not reveal Stage 3 string yet"

        # Transition to Stage 3
        ctx.autorun_stage = EventContext.STAGE_2_COMPLETED
        stage3_section = _build_progressive_stage_section(ctx)
        assert CONFIG["stage3_message"] in stage3_section, \
            "Stage 3 SHOULD reveal Stage 3 string"

    @pytest.mark.unit
    def test_forced_compliance_template_overrides_progressive(self):
        """Test forced compliance template takes precedence over progressive disclosure"""
        # Create mock context with exceeded recheck count
        ctx = EventContext(session_id="test", event="PreToolUse")
        ctx.autorun_active = True
        ctx.autorun_stage = EventContext.STAGE_1
        ctx.file_policy = "allow-all"
        ctx.recheck_count = CONFIG["max_recheck_count"] + 1  # Over limit
        ctx.autorun_task = "test task"
        ctx.autorun_mode = "standard"

        # Build injection (should use forced compliance template)
        injection = build_injection_prompt(ctx, use_progressive_disclosure=True)

        # Should use forced compliance template, not progressive
        assert "forced" in injection.lower() or "must comply" in injection.lower(), \
            "Should use forced compliance template when recheck count exceeded"


# Run with: python3 -m pytest tests/test_progressive_disclosure.py -v --override-ini='addopts='
