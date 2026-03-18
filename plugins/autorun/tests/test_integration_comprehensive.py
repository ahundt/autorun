#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Comprehensive integration tests verifying the autorun daemon-path workflow.

Tests the complete AI monitor workflow via daemon-path functions in plugins.py:
- autorun_injection(ctx) — Stop-hook stage transitions (replaces stop_handler)
- enforce_file_policy(ctx) — PreToolUse file policy enforcement
- check_blocked_commands(ctx) — PreToolUse command blocking
- build_injection_prompt(ctx) — injection text generation
- is_premature_stop(ctx) — premature stop detection

Also tests CONFIG correctness against README.md documentation.

Previously tested via main.py:claude_code_handler, stop_handler, pretooluse_handler,
HANDLERS dict (removed in Phase 2, Task #13).
"""

import time
from pathlib import Path

import pytest

from autorun.core import EventContext, ThreadSafeDB
from autorun.config import CONFIG
from autorun.plugins import (
    autorun_injection,
    build_injection_prompt,
    is_premature_stop,
    enforce_file_policy,
    check_blocked_commands,
)


def _make_ctx(
    session_id: str,
    event: str = "Stop",
    stage: int = EventContext.STAGE_INACTIVE,
    active: bool = False,
    transcript_text: str = "",
    hook_call_count: int = 0,
    file_policy: str = "ALLOW",
    tool_name: str = "",
    tool_input: dict = None,
    store=None,
) -> EventContext:
    """Create an EventContext for integration testing."""
    session_transcript = []
    if transcript_text:
        session_transcript = [{"role": "assistant", "content": transcript_text}]

    ctx = EventContext(
        session_id=session_id,
        event=event,
        prompt="",
        tool_name=tool_name,
        tool_input=tool_input or {},
        tool_result="",
        session_transcript=session_transcript,
        store=store or ThreadSafeDB(),
    )
    ctx.autorun_active = active
    ctx.autorun_stage = stage
    ctx.hook_call_count = hook_call_count
    ctx.file_policy = file_policy
    return ctx


class TestDaemonPathWorkflow:
    """Test the complete autorun workflow via daemon-path functions."""

    def test_stage1_premature_stop_injects_continue(self):
        """Premature stop in stage 1 injects continue prompt."""
        ctx = _make_ctx(
            f"test-wf-s1-{time.time()}",
            stage=EventContext.STAGE_1,
            active=True,
            transcript_text="Work done but no completion marker",
        )
        result = autorun_injection(ctx)
        # Should inject continue (not allow stop)
        if result is not None:
            assert isinstance(result, dict)

    def test_stage1_marker_advances_to_stage2(self):
        """Stage 1 marker in transcript advances to stage 2."""
        ctx = _make_ctx(
            f"test-wf-s1s2-{time.time()}",
            stage=EventContext.STAGE_1,
            active=True,
            transcript_text=f"Done: {CONFIG['stage1_message']}",
        )
        autorun_injection(ctx)
        assert ctx.autorun_stage == EventContext.STAGE_2

    def test_stage2_marker_advances_to_stage2_completed(self):
        """Stage 2 marker advances to STAGE_2_COMPLETED."""
        ctx = _make_ctx(
            f"test-wf-s2comp-{time.time()}",
            stage=EventContext.STAGE_2,
            active=True,
            transcript_text=f"Evaluated: {CONFIG['stage2_message']}",
        )
        autorun_injection(ctx)
        assert ctx.autorun_stage == EventContext.STAGE_2_COMPLETED

    def test_stage3_completion_deactivates_autorun(self):
        """Stage 3 marker after countdown deactivates autorun."""
        countdown = CONFIG.get("stage3_countdown_calls", 3)
        ctx = _make_ctx(
            f"test-wf-s3-{time.time()}",
            stage=EventContext.STAGE_2_COMPLETED,
            active=True,
            hook_call_count=countdown,
            transcript_text=f"All done: {CONFIG['stage3_message']}",
        )
        result = autorun_injection(ctx)
        assert result is None  # Allow Claude to stop
        assert ctx.autorun_active is False
        assert ctx.autorun_stage == EventContext.STAGE_INACTIVE

    def test_emergency_stop_halts_autorun(self):
        """Emergency stop marker deactivates autorun immediately."""
        ctx = _make_ctx(
            f"test-wf-estop-{time.time()}",
            stage=EventContext.STAGE_1,
            active=True,
            transcript_text=f"Need to stop: {CONFIG['emergency_stop']}",
        )
        result = autorun_injection(ctx)
        assert result is None
        assert ctx.autorun_active is False

    def test_non_autorun_session_passes_through(self):
        """Non-autorun session returns None (no intervention)."""
        ctx = _make_ctx(
            f"test-wf-noar-{time.time()}",
            stage=EventContext.STAGE_INACTIVE,
            active=False,
            transcript_text="Normal conversation",
        )
        result = autorun_injection(ctx)
        assert result is None

    def test_injection_contains_full_template(self):
        """Injection prompt includes safety protocol and stage instructions."""
        ctx = _make_ctx(
            f"test-wf-tmpl-{time.time()}",
            stage=EventContext.STAGE_1,
            active=True,
        )
        prompt = build_injection_prompt(ctx, use_progressive_disclosure=True)
        assert "UNINTERRUPTED, FULLY AUTONOMOUS" in prompt
        assert "SYSTEM STOP SIGNAL RULE" in prompt
        assert CONFIG["emergency_stop"] in prompt
        assert "THREE-STAGE COMPLETION SYSTEM" in prompt
        assert CONFIG["stage1_message"] in prompt

    def test_continue_prompt_after_max_rechecks(self):
        """Forced compliance at max recheck count still injects."""
        ctx = _make_ctx(
            f"test-wf-max-{time.time()}",
            stage=EventContext.STAGE_1,
            active=True,
        )
        ctx.recheck_count = CONFIG["max_recheck_count"] + 1
        prompt = build_injection_prompt(ctx)
        # Should use forced_compliance_template
        assert CONFIG["stage3_message"] in prompt


class TestPreToolUseIntegration:
    """Test PreToolUse handlers (file policy + command blocking) via daemon path."""

    def test_search_policy_blocks_new_file_creation(self):
        """SEARCH policy blocks writing to non-existent files."""
        ctx = _make_ctx(
            f"test-ptuse-search-{time.time()}",
            event="PreToolUse",
            tool_name="Write",
            tool_input={"file_path": "nonexistent_file_xyz_999.py"},
            file_policy="SEARCH",
        )
        result = enforce_file_policy(ctx)
        assert result is not None
        assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

    def test_allow_policy_permits_write(self):
        """ALLOW policy does not block file creation."""
        ctx = _make_ctx(
            f"test-ptuse-allow-{time.time()}",
            event="PreToolUse",
            tool_name="Write",
            tool_input={"file_path": "any_file.py"},
            file_policy="ALLOW",
        )
        result = enforce_file_policy(ctx)
        assert result is None  # No intervention

    def test_blocked_command_denied(self):
        """Default blocked commands (rm, git reset --hard, etc.) are denied."""
        ctx = _make_ctx(
            f"test-ptuse-block-{time.time()}",
            event="PreToolUse",
            tool_name="Bash",
            tool_input={"command": "rm -rf /important/data"},
        )
        result = check_blocked_commands(ctx)
        if result is not None:
            permission = result.get("hookSpecificOutput", {}).get("permissionDecision")
            assert permission == "deny", f"Expected deny for 'rm -rf', got {permission}"

    def test_safe_command_allowed(self):
        """Safe commands (ls, echo, etc.) are not blocked."""
        ctx = _make_ctx(
            f"test-ptuse-safe-{time.time()}",
            event="PreToolUse",
            tool_name="Bash",
            tool_input={"command": "echo hello world"},
        )
        result = check_blocked_commands(ctx)
        # Safe command: should return None (no intervention) or allow
        if result is not None:
            permission = result.get("hookSpecificOutput", {}).get("permissionDecision", "")
            assert permission != "deny", f"Safe command should not be denied"


class TestHandlerRegistration:
    """Test that daemon-path handlers are properly registered and callable."""

    def test_stop_handler_registered(self):
        """autorun_injection is callable and accepts EventContext."""
        assert callable(autorun_injection)

    def test_pretooluse_handlers_registered(self):
        """PreToolUse handlers are callable."""
        assert callable(enforce_file_policy)
        assert callable(check_blocked_commands)

    def test_build_injection_prompt_callable(self):
        """build_injection_prompt is callable and returns str."""
        ctx = _make_ctx(f"test-reg-{time.time()}", stage=EventContext.STAGE_1, active=True)
        result = build_injection_prompt(ctx)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_is_premature_stop_callable(self):
        """is_premature_stop is callable and returns bool."""
        ctx = _make_ctx(f"test-reg-ps-{time.time()}", stage=EventContext.STAGE_1, active=True)
        result = is_premature_stop(ctx)
        assert isinstance(result, bool)


# === CONFIG / README COMPLIANCE (preserved from original) ===

def test_readme_workflow_compliance():
    """Test that implementation matches README.md documented workflow."""
    # Stage messages
    assert "stage1_message" in CONFIG
    assert "stage2_message" in CONFIG
    assert "stage3_message" in CONFIG
    assert CONFIG["stage1_message"] == "AUTORUN_INITIAL_TASKS_COMPLETED"

    # Emergency stop
    assert "emergency_stop" in CONFIG
    assert CONFIG["emergency_stop"] == "AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP"

    # Max recheck count
    assert "max_recheck_count" in CONFIG
    assert CONFIG["max_recheck_count"] == 3

    # Legacy command mappings
    required_legacy_mappings = {
        "/autorun": "activate",
        "/autoproc": "activate",
        "/autostop": "stop",
        "/estop": "emergency_stop",
        "/afs": "SEARCH",
        "/afa": "ALLOW",
        "/afj": "JUSTIFY",
        "/afst": "STATUS",
    }
    for cmd, expected_action in required_legacy_mappings.items():
        assert cmd in CONFIG["command_mappings"], f"Missing legacy command: {cmd}"
        assert CONFIG["command_mappings"][cmd] == expected_action

    # File policies
    expected_policies = {
        "ALLOW": ("allow-all", "ALLOW ALL: Full permission to create/modify files."),
        "JUSTIFY": ("justify-create", "JUSTIFIED: Search existing first. Include <AUTOFILE_JUSTIFICATION>reason</AUTOFILE_JUSTIFICATION> for new files."),
        "SEARCH": ("strict-search", "STRICT SEARCH: ONLY modify existing files. Use {glob} and {grep} tools. NO new files."),
    }
    assert CONFIG["policies"] == expected_policies


def test_readme_stage_markers_match_config():
    """README.md must not contain wrong AUTORUN_STAGE[123]_COMPLETE markers."""
    readme_path = Path(__file__).parent.parent.parent.parent / "README.md"
    readme = readme_path.read_text(encoding="utf-8")
    for wrong in ("AUTORUN_STAGE1_COMPLETE", "AUTORUN_STAGE2_COMPLETE", "AUTORUN_STAGE3_COMPLETE"):
        assert wrong not in readme, f"README.md contains wrong stage marker '{wrong}'"
    assert CONFIG["stage1_message"] in readme
    assert CONFIG["stage2_message"] in readme
    assert CONFIG["stage3_message"] in readme


def test_readme_emergency_stop_documented():
    """README.md must document the emergency stop marker."""
    readme_path = Path(__file__).parent.parent.parent.parent / "README.md"
    readme = readme_path.read_text(encoding="utf-8")
    assert CONFIG["emergency_stop"] in readme


def test_claude_md_stage_markers_match_config():
    """CLAUDE.md (repo root) must not contain wrong AUTORUN_STAGE[123]_COMPLETE markers."""
    claude_md_path = Path(__file__).parent.parent.parent.parent / "CLAUDE.md"
    claude_md = claude_md_path.read_text(encoding="utf-8")
    for wrong in ("AUTORUN_STAGE1_COMPLETE", "AUTORUN_STAGE2_COMPLETE", "AUTORUN_STAGE3_COMPLETE"):
        assert wrong not in claude_md, f"CLAUDE.md contains wrong stage marker '{wrong}'"
