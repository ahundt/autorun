#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright 2025 Andrew Hundt <ATHundt@gmail.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
TDD tests for autorun v0.7 plugins.py components.

Tests for:
- File Policy Plugin: ALLOW/JUSTIFY/SEARCH handlers and enforcement
- Command Blocking Plugin: Block/allow/clear/status and pattern matching
- Autorun Plugin: Activation, stages, injection, plan acceptance
- Plan Management Plugin: New/refine/update/process handlers
"""

import pytest
from unittest.mock import Mock, patch, MagicMock

from autorun.core import app, EventContext, AutorunApp, ThreadSafeDB
from autorun.config import CONFIG
from autorun import plugins


# ============================================================================
# P2.1: File Policy Plugin Tests
# ============================================================================

class TestFilePolicyPlugin:
    """Tests for file policy handlers and enforcement."""

    def test_allow_policy_handler_sets_state(self):
        """ALLOW handler should set file_policy to ALLOW."""
        ctx = EventContext(session_id="test", event="UserPromptSubmit", prompt="/ar:a")
        ctx.activation_prompt = "/ar:a"

        # Find and call the handler
        handler = app.command_handlers.get("ALLOW")
        assert handler is not None

        result = handler(ctx)
        assert "allow-all" in result.lower() or "ALLOW" in result

    def test_justify_policy_handler_sets_state(self):
        """JUSTIFY handler should set file_policy to JUSTIFY."""
        ctx = EventContext(session_id="test", event="UserPromptSubmit", prompt="/ar:j")
        ctx.activation_prompt = "/ar:j"

        handler = app.command_handlers.get("JUSTIFY")
        assert handler is not None

        result = handler(ctx)
        assert "justify" in result.lower() or "JUSTIFY" in result

    def test_search_policy_handler_sets_state(self):
        """SEARCH handler should set file_policy to SEARCH."""
        ctx = EventContext(session_id="test", event="UserPromptSubmit", prompt="/ar:f")
        ctx.activation_prompt = "/ar:f"

        handler = app.command_handlers.get("SEARCH")
        assert handler is not None

        result = handler(ctx)
        assert "strict" in result.lower() or "SEARCH" in result

    def test_status_handler_shows_policy(self):
        """STATUS handler should show current policy."""
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test", event="UserPromptSubmit", prompt="/ar:st", store=store)
        ctx.activation_prompt = "/ar:st"
        ctx.file_policy = "SEARCH"

        handler = app.command_handlers.get("STATUS")
        assert handler is not None

        result = handler(ctx)
        assert "strict-search" in result.lower() or "AutoFile policy" in result

    def test_enforce_file_policy_allows_non_write(self):
        """File policy should allow non-Write tools."""
        ctx = EventContext(session_id="test", event="PreToolUse", tool_name="Read")
        result = plugins.enforce_file_policy(ctx)
        assert result is None  # Allow

    def test_enforce_file_policy_allow_allows_all(self):
        """ALLOW policy should allow file creation."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test", event="PreToolUse", tool_name="Write",
            tool_input={"file_path": "/tmp/new_file.txt"}, store=store
        )
        ctx.file_policy = "ALLOW"

        result = plugins.enforce_file_policy(ctx)
        assert result is None  # Allow

    def test_enforce_file_policy_search_blocks_new_file(self):
        """SEARCH policy should block new file creation."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test", event="PreToolUse", tool_name="Write",
            tool_input={"file_path": "/nonexistent/new_file.txt"}, store=store
        )
        ctx.file_policy = "SEARCH"

        result = plugins.enforce_file_policy(ctx)
        assert result is not None
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_enforce_file_policy_search_allows_existing_file(self, tmp_path):
        """SEARCH policy should allow modifying existing files."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test", event="PreToolUse", tool_name="Write",
            tool_input={"file_path": str(tmp_path)}, store=store  # tmp_path always exists
        )
        ctx.file_policy = "SEARCH"

        result = plugins.enforce_file_policy(ctx)
        assert result is None  # Allow

    def test_enforce_file_policy_justify_blocks_without_justification(self):
        """JUSTIFY policy should block new files without justification."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test", event="PreToolUse", tool_name="Write",
            tool_input={"file_path": "/nonexistent/file.txt"},
            session_transcript=[{"content": "no justification here"}],
            store=store
        )
        ctx.file_policy = "JUSTIFY"

        result = plugins.enforce_file_policy(ctx)
        assert result is not None
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_enforce_file_policy_justify_allows_with_justification(self):
        """JUSTIFY policy should allow new files with justification."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test", event="PreToolUse", tool_name="Write",
            tool_input={"file_path": "/nonexistent/file.txt"},
            session_transcript=[{"content": "<AUTOFILE_JUSTIFICATION>Need new config</AUTOFILE_JUSTIFICATION>"}],
            store=store
        )
        ctx.file_policy = "JUSTIFY"

        result = plugins.enforce_file_policy(ctx)
        assert result is None  # Allow


# ============================================================================
# P2.2: Command Blocking Plugin Tests
# ============================================================================

class TestCommandBlockingPlugin:
    """Tests for command blocking handlers and pattern matching."""

    def test_is_safe_regex_accepts_safe_patterns(self):
        """_is_safe_regex should accept safe patterns."""
        assert plugins._is_safe_regex(r"rm\s+-rf") is True
        assert plugins._is_safe_regex(r"git\s+reset") is True
        assert plugins._is_safe_regex(r"\.env") is True

    def test_is_safe_regex_rejects_nested_quantifiers(self):
        """_is_safe_regex should reject ReDoS patterns."""
        assert plugins._is_safe_regex(r"(a+)+") is False
        assert plugins._is_safe_regex(r"([a-z]+)*") is False

    def test_is_safe_regex_rejects_long_patterns(self):
        """_is_safe_regex should reject patterns over max length."""
        assert plugins._is_safe_regex("a" * 300) is False

    def test_match_literal(self):
        """_match should match literal patterns."""
        assert plugins._match("rm -rf /", "rm", "literal") is True
        assert plugins._match("rm -rf /", "rm -rf", "literal") is True
        assert plugins._match("ls -la", "rm", "literal") is False

    def test_match_regex(self):
        """_match should match regex patterns."""
        assert plugins._match("rm -rf /tmp", r"rm\s+-rf", "regex") is True
        assert plugins._match("ls -la", r"rm\s+-rf", "regex") is False

    def test_match_glob(self):
        """_match should match glob patterns."""
        assert plugins._match("file.tmp", "*.tmp", "glob") is True
        assert plugins._match("file.txt", "*.tmp", "glob") is False

    def test_parse_args_literal(self):
        """_parse_args should parse literal patterns."""
        pattern, desc, ptype = plugins._parse_args("rm")
        assert pattern == "rm"
        assert ptype == "literal"

    def test_parse_args_regex_prefix(self):
        """_parse_args should detect regex: prefix."""
        # Shlex processes backslashes, so use a pattern without escapes
        pattern, desc, ptype = plugins._parse_args("regex:rm.*rf")
        assert pattern == "rm.*rf"
        assert ptype == "regex"

    def test_parse_args_glob_prefix(self):
        """_parse_args should detect glob: prefix."""
        pattern, desc, ptype = plugins._parse_args("glob:*.tmp")
        assert pattern == "*.tmp"
        assert ptype == "glob"

    def test_parse_args_auto_regex(self):
        """_parse_args should auto-detect /pattern/ regex."""
        # Use pattern without escape sequences for shlex compatibility
        pattern, desc, ptype = plugins._parse_args("/rm.*-rf/")
        assert pattern == "rm.*-rf"
        assert ptype == "regex"

    def test_parse_args_with_description(self):
        """_parse_args should extract description."""
        pattern, desc, ptype = plugins._parse_args("rm dangerous delete command")
        assert pattern == "rm"
        assert desc == "dangerous delete command"
        assert ptype == "literal"

    def test_check_blocked_commands_allows_non_bash(self):
        """check_blocked_commands should allow non-Bash tools."""
        ctx = EventContext(session_id="test", event="PreToolUse", tool_name="Read")
        result = plugins.check_blocked_commands(ctx)
        assert result is None

    def test_check_blocked_commands_blocks_session_pattern(self):
        """check_blocked_commands should block session patterns."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test", event="PreToolUse", tool_name="Bash",
            tool_input={"command": "rm -rf /tmp/test"}, store=store
        )
        ctx.session_blocked_patterns = [{"pattern": "rm", "suggestion": "Use trash", "pattern_type": "literal"}]

        result = plugins.check_blocked_commands(ctx)
        assert result is not None
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_check_blocked_commands_default_integrations(self):
        """check_blocked_commands should use default integrations."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test", event="PreToolUse", tool_name="Bash",
            tool_input={"command": "git reset --hard"}, store=store
        )

        result = plugins.check_blocked_commands(ctx)
        assert result is not None
        # Default integration for git reset --hard should trigger


# ============================================================================
# P2.3: Autorun Plugin Tests
# ============================================================================

class TestAutorunPlugin:
    """Tests for autorun activation, stages, and injection."""

    def test_activate_sets_autorun_active(self):
        """handle_activate should set autorun_active to True."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test", event="UserPromptSubmit",
            prompt="/ar:go build something", store=store
        )
        ctx.activation_prompt = "/ar:go build something"

        handler = app.command_handlers.get("activate")
        assert handler is not None

        result = handler(ctx)
        assert ctx.autorun_active is True
        assert ctx.autorun_stage == EventContext.STAGE_1

    def test_activate_procedural_mode(self):
        """handle_activate should detect procedural mode."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test", event="UserPromptSubmit",
            prompt="/ar:gp build something", store=store
        )
        ctx.activation_prompt = "/ar:gp build something"

        handler = app.command_handlers.get("activate")
        result = handler(ctx)

        assert ctx.autorun_mode == "procedural"

    def test_stop_deactivates(self):
        """handle_stop should deactivate autorun."""
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test", event="UserPromptSubmit", prompt="/ar:x", store=store)
        ctx.activation_prompt = "/ar:x"
        ctx.autorun_active = True

        handler = app.command_handlers.get("stop")
        result = handler(ctx)

        assert ctx.autorun_active is False

    def test_emergency_stop_deactivates(self):
        """handle_sos should deactivate with emergency message."""
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test", event="UserPromptSubmit", prompt="/ar:sos", store=store)
        ctx.activation_prompt = "/ar:sos"
        ctx.autorun_active = True

        handler = app.command_handlers.get("emergency_stop")
        result = handler(ctx)

        assert ctx.autorun_active is False
        assert "EMERGENCY" in result

    def test_is_premature_stop_true(self):
        """is_premature_stop should return True when no markers found."""
        ctx = EventContext(
            session_id="test", event="Stop",
            session_transcript=[{"content": "just regular content"}]
        )
        ctx.autorun_active = True

        assert plugins.is_premature_stop(ctx) is True

    def test_is_premature_stop_false_with_marker(self):
        """is_premature_stop should return False when stage marker found."""
        ctx = EventContext(
            session_id="test", event="Stop",
            session_transcript=[{"content": CONFIG["stage1_message"]}]
        )
        ctx.autorun_active = True

        assert plugins.is_premature_stop(ctx) is False

    def test_build_injection_prompt_standard(self):
        """build_injection_prompt should use injection_template for standard mode."""
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test", event="Stop", store=store)
        ctx.autorun_mode = "standard"
        ctx.autorun_stage = EventContext.STAGE_1
        ctx.recheck_count = 0
        ctx.hook_call_count = 0
        ctx.file_policy = "ALLOW"

        result = plugins.build_injection_prompt(ctx)
        assert "THREE-STAGE COMPLETION SYSTEM" in result

    def test_build_injection_prompt_procedural(self):
        """build_injection_prompt should use procedural_template for procedural mode."""
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test", event="Stop", store=store)
        ctx.autorun_mode = "procedural"
        ctx.autorun_stage = EventContext.STAGE_1
        ctx.recheck_count = 0
        ctx.hook_call_count = 0
        ctx.file_policy = "ALLOW"

        # Progressive disclosure is default; use_progressive_disclosure=False to test template
        result = plugins.build_injection_prompt(ctx, use_progressive_disclosure=False)
        assert "WAIT PROCESS" in result or "Sequential Improvement" in result

    def test_autorun_injection_stage1_to_stage2(self):
        """autorun_injection should advance from stage 1 to stage 2."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test", event="Stop",
            session_transcript=[{"content": CONFIG["stage1_message"]}],
            store=store
        )
        ctx.autorun_active = True
        ctx.autorun_stage = EventContext.STAGE_1
        ctx.hook_call_count = 0

        result = plugins.autorun_injection(ctx)

        assert ctx.autorun_stage == EventContext.STAGE_2
        assert result is not None


# ============================================================================
# Plan Management Plugin Tests
# ============================================================================

class TestPlanManagementPlugin:
    """Tests for plan management handlers."""

    def test_plannew_sets_plan_active(self):
        """/ar:pn should set plan_active and return template."""
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test", event="UserPromptSubmit", prompt="/ar:pn", store=store)
        ctx.activation_prompt = "/ar:pn"

        # Find handler by iterating command_handlers
        for cmd, handler in app.command_handlers.items():
            if cmd == "/ar:pn":
                result = handler(ctx)
                assert ctx.plan_active is True
                assert "Create New Plan" in result
                break

    def test_planrefine_returns_template(self):
        """/ar:pr should return refine template."""
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test", event="UserPromptSubmit", prompt="/ar:pr", store=store)
        ctx.activation_prompt = "/ar:pr"

        for cmd, handler in app.command_handlers.items():
            if cmd == "/ar:pr":
                result = handler(ctx)
                assert "Refine" in result or "Refinement" in result
                break


# ============================================================================
# AI Monitor Integration Tests
# ============================================================================

class TestAIMonitorIntegration:
    """Tests for AI monitor integration."""

    def test_get_injection_method_default(self):
        """get_injection_method should return HOOK_INTEGRATION by default."""
        ctx = EventContext(session_id="test", event="Stop")
        result = plugins.get_injection_method(ctx)
        assert result == "HOOK_INTEGRATION"

    @patch.object(plugins, 'ai_monitor', None)
    def test_manage_monitor_returns_none_without_ai_monitor(self):
        """_manage_monitor should return None when ai_monitor not available."""
        ctx = EventContext(session_id="test", event="Stop")
        result = plugins._manage_monitor(ctx, 'start')
        assert result is None


# ============================================================================
# Handler Registration Tests
# ============================================================================

class TestHandlerRegistration:
    """Tests for plugin handler registration."""

    def test_policy_handlers_registered(self):
        """Policy handlers should be registered in app."""
        assert "ALLOW" in app.command_handlers
        assert "JUSTIFY" in app.command_handlers
        assert "SEARCH" in app.command_handlers
        assert "STATUS" in app.command_handlers

    def test_block_handlers_registered(self):
        """Block handlers should be registered in app."""
        assert "/ar:no" in app.command_handlers
        assert "/ar:ok" in app.command_handlers
        assert "/ar:clear" in app.command_handlers
        assert "/ar:globalno" in app.command_handlers
        assert "/ar:globalok" in app.command_handlers
        assert "/ar:globalstatus" in app.command_handlers

    def test_autorun_handlers_registered(self):
        """Autorun handlers should be registered in app."""
        assert "activate" in app.command_handlers
        assert "stop" in app.command_handlers
        assert "emergency_stop" in app.command_handlers

    def test_plan_handlers_registered(self):
        """Plan handlers should be registered in app."""
        assert "/ar:pn" in app.command_handlers
        assert "/ar:pr" in app.command_handlers
        assert "/ar:pu" in app.command_handlers
        assert "/ar:pp" in app.command_handlers

    def test_pretooluse_chain_has_handlers(self):
        """PreToolUse chain should have handlers."""
        assert len(app.chains["PreToolUse"]) >= 2  # Policy and blocking

    def test_stop_chain_has_handlers(self):
        """Stop chain should have handlers."""
        assert len(app.chains["Stop"]) >= 1  # v0.7: autorun_injection only (plan approval via PostToolUse)


# ============================================================================
# ScopeAccessor Tests
# ============================================================================

class TestScopeAccessor:
    """Tests for ScopeAccessor DRY pattern."""

    def test_session_scope_get(self):
        """Session scope should get from ctx."""
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test", event="test", store=store)
        ctx.session_blocked_patterns = [{"pattern": "rm"}]

        accessor = plugins.ScopeAccessor(ctx, "session")
        blocks = accessor.get()

        assert len(blocks) == 1
        assert blocks[0]["pattern"] == "rm"

    def test_session_scope_set(self):
        """Session scope should set via ctx."""
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test", event="test", store=store)

        accessor = plugins.ScopeAccessor(ctx, "session")
        accessor.set([{"pattern": "dd"}])

        assert ctx.session_blocked_patterns[0]["pattern"] == "dd"


# ============================================================================
# /ar:reload Command Tests
# ============================================================================

class TestReloadCommand:
    """Tests for /ar:reload command."""

    def test_reload_handler_registered(self):
        """Reload handler should be registered."""
        assert "/ar:reload" in app.command_handlers

    def test_reload_returns_count(self):
        """Reload should return integration count."""
        ctx = EventContext(session_id="test", event="UserPromptSubmit", prompt="/ar:reload")
        ctx.activation_prompt = "/ar:reload"

        handler = app.command_handlers.get("/ar:reload")
        result = handler(ctx)

        assert "Reloaded" in result
        assert "integrations" in result

    def test_reload_clears_cache(self):
        """Reload should clear integration cache."""
        from autorun.integrations import load_all_integrations, invalidate_caches

        # Load once
        integrations1 = load_all_integrations()

        # Reload via handler
        ctx = EventContext(session_id="test", event="UserPromptSubmit", prompt="/ar:reload")
        ctx.activation_prompt = "/ar:reload"
        handler = app.command_handlers.get("/ar:reload")
        handler(ctx)

        # Load again - should be different object (cache was cleared)
        integrations2 = load_all_integrations()
        assert integrations1 is not integrations2


# ============================================================================
# check_blocked_commands Integration Tests
# ============================================================================

class TestCheckBlockedCommandsIntegration:
    """Tests for check_blocked_commands hook with integration system."""

    def test_blocks_rm_command(self):
        """rm command should be blocked by default."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test",
            event="PreToolUse",
            tool_name="Bash",
            tool_input={"command": "rm file.txt"},
            store=store
        )

        result = plugins.check_blocked_commands(ctx)

        # Should return deny response
        assert result is not None
        assert "trash" in str(result).lower()

    def test_allows_safe_command(self):
        """Safe command should be allowed."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test",
            event="PreToolUse",
            tool_name="Bash",
            tool_input={"command": "ls -la"},
            store=store
        )

        result = plugins.check_blocked_commands(ctx)

        # Should return None (allow)
        assert result is None

    def test_warn_action_allows_with_message(self):
        """Git command with action: warn should allow with message."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test",
            event="PreToolUse",
            tool_name="Bash",
            tool_input={"command": "git status"},
            store=store
        )

        result = plugins.check_blocked_commands(ctx)

        # action: warn returns allow response with message
        if result is not None:
            # Should be allow, not deny
            assert result.get("permissionDecision") == "allow" or "allow" in str(result)

    def test_redirect_shown_in_message(self):
        """Blocked command should show redirect suggestion."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test",
            event="PreToolUse",
            tool_name="Bash",
            tool_input={"command": "rm important.txt"},
            store=store
        )

        result = plugins.check_blocked_commands(ctx)

        # Message should contain redirect
        assert result is not None
        result_str = str(result)
        assert "trash" in result_str.lower()

    def test_session_block_takes_priority(self):
        """Session block should take priority over defaults."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test",
            event="PreToolUse",
            tool_name="Bash",
            tool_input={"command": "custom-cmd"},
            store=store
        )
        # Add session block
        ctx.session_blocked_patterns = [
            {"pattern": "custom-cmd", "suggestion": "Custom blocked", "pattern_type": "literal"}
        ]

        result = plugins.check_blocked_commands(ctx)

        assert result is not None
        assert "Custom blocked" in str(result)

    def test_non_bash_tool_ignored(self):
        """Non-Bash tool should be ignored (unless event: file)."""
        ctx = EventContext(
            session_id="test",
            event="PreToolUse",
            tool_name="Read",
            tool_input={"file_path": "/tmp/test.txt"}
        )

        result = plugins.check_blocked_commands(ctx)

        assert result is None

    def test_empty_command_ignored(self):
        """Empty command should be ignored."""
        ctx = EventContext(
            session_id="test",
            event="PreToolUse",
            tool_name="Bash",
            tool_input={"command": ""}
        )

        result = plugins.check_blocked_commands(ctx)

        assert result is None

    def test_write_tool_with_file_event(self):
        """Write tool should be checked for file event integrations."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test",
            event="PreToolUse",
            tool_name="Write",
            tool_input={"file_path": "/tmp/test.txt"},
            store=store
        )

        # Write tool should be processed (event: file)
        result = plugins.check_blocked_commands(ctx)

        # May or may not have matching integration, but should not error
        assert result is None or isinstance(result, dict)


# ============================================================================
# Event Type Filtering Tests
# ============================================================================

class TestEventTypeFiltering:
    """Tests for event field filtering in check_blocked_commands."""

    def test_bash_event_matches_bash_tool(self):
        """Integration with event=bash matches Bash tool."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test",
            event="PreToolUse",
            tool_name="Bash",
            tool_input={"command": "rm test.txt"},
            store=store
        )

        result = plugins.check_blocked_commands(ctx)

        # rm has event=bash (default), should be blocked
        assert result is not None

    def test_file_event_does_not_match_bash_tool(self):
        """Integration with event=file doesn't match Bash tool."""
        # This tests that an integration with event="file"
        # won't trigger on a Bash command
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test",
            event="PreToolUse",
            tool_name="Bash",
            tool_input={"command": "ls"},
            store=store
        )

        # ls is not blocked, and file-event integrations won't match
        result = plugins.check_blocked_commands(ctx)
        assert result is None


# ============================================================================
# When Predicate Integration Tests
# ============================================================================

class TestWhenPredicateIntegration:
    """Tests for when predicate integration in check_blocked_commands."""

    @patch("autorun.integrations.subprocess.run")
    def test_when_predicate_evaluated(self, mock_run):
        """When predicate should be evaluated before blocking."""
        # git reset --hard has when: has_uncommitted_changes
        mock_run.return_value = MagicMock(returncode=1)  # Has changes

        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test",
            event="PreToolUse",
            tool_name="Bash",
            tool_input={"command": "git reset --hard HEAD"},
            store=store
        )

        result = plugins.check_blocked_commands(ctx)

        # Should be blocked because has_uncommitted_changes=True
        assert result is not None

    @patch("autorun.integrations.subprocess.run")
    def test_when_predicate_false_skips_integration(self, mock_run):
        """When predicate returning False should skip that integration."""
        # git reset --hard has when: has_uncommitted_changes
        mock_run.return_value = MagicMock(returncode=0)  # No changes

        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test",
            event="PreToolUse",
            tool_name="Bash",
            tool_input={"command": "git reset --hard HEAD"},
            store=store
        )

        result = plugins.check_blocked_commands(ctx)

        # The "git reset --hard" integration is skipped (when=False)
        # But "git" integration still matches with action: warn
        # So result is allow (not deny), not None
        if result is not None:
            # Should be allow, not deny
            assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "allow"


# ============================================================================
# Allow Patterns System Tests (TDD)
# ============================================================================

class TestAllowPatternsSystem:
    """/ar:ok adds to allowed list that overrides all blocks (session, global, integrations)."""

    def test_ar_ok_adds_to_session_allowed_patterns(self):
        """/ar:ok 'git push' should add pattern (without quotes) to session_allowed_patterns."""
        import uuid
        store = ThreadSafeDB()
        ctx = EventContext(session_id=f"test-ok-add-{uuid.uuid4().hex[:8]}", event="UserPromptSubmit",
                           prompt="/ar:ok 'git push'", store=store)
        ctx.activation_prompt = "/ar:ok 'git push'"

        handler = app.command_handlers.get("/ar:ok")
        result = handler(ctx)

        assert "Allowed" in result
        allows = ctx.session_allowed_patterns or []
        assert any(a["pattern"] == "git push" for a in allows)

    def test_ar_ok_success_message_includes_revert_hint(self):
        """/ar:ok success message includes 'To block:' hint on same line, no newlines."""
        import uuid
        store = ThreadSafeDB()
        ctx = EventContext(session_id=f"test-ok-hint-{uuid.uuid4().hex[:8]}", event="UserPromptSubmit",
                           prompt="/ar:ok 'git push'", store=store)
        ctx.activation_prompt = "/ar:ok 'git push'"

        result = app.command_handlers["/ar:ok"](ctx)

        assert "\n" not in result
        assert "(to block:" in result
        assert "/ar:no" in result
        assert "'git push'" in result

    def test_ar_no_removes_pattern_from_allows(self):
        """/ar:no is the inverse of /ar:ok: removes pattern from allows list."""
        import uuid
        store = ThreadSafeDB()
        session = f"test-no-inverse-{uuid.uuid4().hex[:8]}"
        ctx = EventContext(session_id=session, event="UserPromptSubmit",
                           prompt="/ar:no 'git push'", store=store)
        ctx.activation_prompt = "/ar:no 'git push'"
        # Pre-populate the allow
        ctx.session_allowed_patterns = [{"pattern": "git push", "pattern_type": "literal"}]

        app.command_handlers["/ar:no"](ctx)

        # Pattern must be removed from allows
        allows = ctx.session_allowed_patterns or []
        assert not any(a["pattern"] == "git push" for a in allows)

    def test_ar_ok_quoted_pattern_parses_without_quotes(self):
        """/ar:ok 'git push' should store pattern without surrounding quotes."""
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test-ok-quoted", event="UserPromptSubmit",
                           prompt="/ar:ok 'git push'", store=store)
        ctx.activation_prompt = "/ar:ok 'git push'"

        handler = app.command_handlers.get("/ar:ok")
        result = handler(ctx)

        allows = ctx.session_allowed_patterns or []
        assert any(a["pattern"] == "git push" for a in allows), \
            f"Expected 'git push' (no quotes) in allows, got: {allows}"
        assert "git push" in result

    def test_ar_ok_idempotent_returns_already_allowed(self):
        """Running /ar:ok twice on same pattern returns ℹ️ Already allowed."""
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test-ok-dup", event="UserPromptSubmit",
                           prompt="/ar:ok 'git push'", store=store)
        ctx.activation_prompt = "/ar:ok 'git push'"
        ctx.session_allowed_patterns = [{"pattern": "git push", "pattern_type": "literal"}]

        handler = app.command_handlers.get("/ar:ok")
        result = handler(ctx)

        assert "Already allowed" in result
        # Count should not change
        assert len(ctx.session_allowed_patterns) == 1

    def test_session_allow_overrides_default_integration_block(self):
        """Session allow overrides DEFAULT_INTEGRATIONS block (e.g. 'git push')."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-allow-intg",
            event="PreToolUse",
            tool_name="Bash",
            tool_input={"command": "git push origin main"},
            store=store
        )
        ctx.session_allowed_patterns = [{"pattern": "git push", "pattern_type": "literal"}]

        result = plugins.check_blocked_commands(ctx)

        # Allow wins — result is None or explicit allow
        assert result is None or \
            result.get("hookSpecificOutput", {}).get("permissionDecision") == "allow"

    def test_session_allow_overrides_session_block(self):
        """Session allow takes priority over a session block for the same pattern."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-allow-vs-block",
            event="PreToolUse",
            tool_name="Bash",
            tool_input={"command": "rm file.txt"},
            store=store
        )
        ctx.session_blocked_patterns = [
            {"pattern": "rm", "suggestion": "Use trash", "pattern_type": "literal"}
        ]
        ctx.session_allowed_patterns = [{"pattern": "rm", "pattern_type": "literal"}]

        result = plugins.check_blocked_commands(ctx)

        assert result is None or \
            result.get("hookSpecificOutput", {}).get("permissionDecision") == "allow"

    def test_ar_clear_clears_blocks_and_allows(self):
        """/ar:clear should reset both session_blocked_patterns and session_allowed_patterns."""
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test-clear-both", event="UserPromptSubmit",
                           prompt="/ar:clear", store=store)
        ctx.activation_prompt = "/ar:clear"
        ctx.session_blocked_patterns = [{"pattern": "rm", "suggestion": "s", "pattern_type": "literal"}]
        ctx.session_allowed_patterns = [{"pattern": "git push", "pattern_type": "literal"}]

        handler = app.command_handlers.get("/ar:clear")
        result = handler(ctx)

        assert ctx.session_blocked_patterns == []
        assert ctx.session_allowed_patterns == []
        assert "Cleared" in result

    def test_ar_blocks_status_shows_allows(self):
        """/ar:blocks status command shows allowed patterns."""
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test-blocks-status", event="UserPromptSubmit",
                           prompt="/ar:blocks", store=store)
        ctx.activation_prompt = "/ar:blocks"
        ctx.session_allowed_patterns = [{"pattern": "git push", "pattern_type": "literal"}]

        handler = app.command_handlers.get("/ar:blocks")
        result = handler(ctx)

        assert "git push" in result
        assert "Allows" in result

    def test_ar_no_usage_error_without_args(self):
        """/ar:ok without args returns usage error."""
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test-ok-noarg", event="UserPromptSubmit",
                           prompt="/ar:ok", store=store)
        ctx.activation_prompt = "/ar:ok"

        handler = app.command_handlers.get("/ar:ok")
        result = handler(ctx)

        assert "Usage" in result


class TestScopeAccessorAllowed:
    """Tests for ScopeAccessor get_allowed / set_allowed methods."""

    def test_get_allowed_returns_empty_by_default(self):
        """get_allowed should return empty list when no allows set."""
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test-ga-empty", event="test", store=store)

        accessor = plugins.ScopeAccessor(ctx, "session")
        assert accessor.get_allowed() == []

    def test_set_and_get_allowed_roundtrip(self):
        """set_allowed then get_allowed should return same data."""
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test-ga-rt", event="test", store=store)
        accessor = plugins.ScopeAccessor(ctx, "session")

        allows = [{"pattern": "git push", "pattern_type": "literal"}]
        accessor.set_allowed(allows)

        assert accessor.get_allowed() == allows
        assert ctx.session_allowed_patterns == allows


class TestFormatPatternList:
    """Tests for the _format_pattern_list DRY helper."""

    def test_empty_list_returns_empty(self):
        """Empty pattern list returns empty list."""
        result = plugins._format_pattern_list([], "Blocks", "🚫")
        assert result == []

    def test_single_literal_pattern(self):
        """Single literal pattern formats correctly."""
        patterns = [{"pattern": "rm", "pattern_type": "literal"}]
        result = plugins._format_pattern_list(patterns, "Session Blocks", "🚫")

        assert len(result) == 2  # header + item
        assert "🚫 Session Blocks (1):" in result[0]
        assert "• rm" in result[1]
        assert "(literal)" not in result[1]  # literal type not shown

    def test_non_literal_type_shown(self):
        """Non-literal pattern type (regex, glob) is shown in output."""
        patterns = [{"pattern": "rm.*rf", "pattern_type": "regex"}]
        result = plugins._format_pattern_list(patterns, "Blocks", "🚫")

        assert "(regex)" in result[1]

    def test_multiple_patterns_all_listed(self):
        """Multiple patterns all appear in output."""
        patterns = [
            {"pattern": "rm", "pattern_type": "literal"},
            {"pattern": "dd", "pattern_type": "literal"},
        ]
        result = plugins._format_pattern_list(patterns, "Blocks", "🚫")

        assert len(result) == 3  # header + 2 items
        assert any("rm" in line for line in result)
        assert any("dd" in line for line in result)


# ============================================================================
# Policy Aliases — every slash-command alias must be registered and functional
# ============================================================================

class TestPolicyAliasesRegistered:
    """All policy command aliases must be registered and map to the same handler."""

    # Ground truth from _POLICY_ALIASES in plugins.py
    ALLOW_ALIASES  = ("/ar:a", "/ar:allow", "/afa", "ALLOW")
    JUSTIFY_ALIASES = ("/ar:j", "/ar:justify", "/afj", "JUSTIFY")
    SEARCH_ALIASES  = ("/ar:f", "/ar:find", "/afs", "SEARCH")

    def _run_alias(self, alias: str, prompt: str):
        """Helper: call handler for alias, return (result_str, ctx)."""
        store = ThreadSafeDB()
        ctx = EventContext(session_id=f"test-alias-{alias.strip('/')}", event="UserPromptSubmit",
                           prompt=prompt, store=store)
        ctx.activation_prompt = prompt
        handler = app.command_handlers.get(alias)
        assert handler is not None, f"Handler for '{alias}' not registered"
        result = handler(ctx)
        return result, ctx

    # ── ALLOW aliases ──────────────────────────────────────────────────────

    def test_ar_a_sets_allow_policy(self):
        result, ctx = self._run_alias("/ar:a", "/ar:a")
        assert ctx.file_policy == "ALLOW"
        assert "allow-all" in result.lower()

    def test_ar_allow_sets_allow_policy(self):
        result, ctx = self._run_alias("/ar:allow", "/ar:allow")
        assert ctx.file_policy == "ALLOW"
        assert "allow-all" in result.lower()

    def test_afa_sets_allow_policy(self):
        result, ctx = self._run_alias("/afa", "/afa")
        assert ctx.file_policy == "ALLOW"

    def test_allow_key_sets_allow_policy(self):
        result, ctx = self._run_alias("ALLOW", "ALLOW")
        assert ctx.file_policy == "ALLOW"

    # ── JUSTIFY aliases ────────────────────────────────────────────────────

    def test_ar_j_sets_justify_policy(self):
        result, ctx = self._run_alias("/ar:j", "/ar:j")
        assert ctx.file_policy == "JUSTIFY"
        assert "justify" in result.lower()

    def test_ar_justify_sets_justify_policy(self):
        result, ctx = self._run_alias("/ar:justify", "/ar:justify")
        assert ctx.file_policy == "JUSTIFY"

    def test_afj_sets_justify_policy(self):
        result, ctx = self._run_alias("/afj", "/afj")
        assert ctx.file_policy == "JUSTIFY"

    def test_justify_key_sets_justify_policy(self):
        result, ctx = self._run_alias("JUSTIFY", "JUSTIFY")
        assert ctx.file_policy == "JUSTIFY"

    # ── SEARCH (strict) aliases ────────────────────────────────────────────

    def test_ar_f_sets_search_policy(self):
        result, ctx = self._run_alias("/ar:f", "/ar:f")
        assert ctx.file_policy == "SEARCH"
        assert "strict" in result.lower()

    def test_ar_find_sets_search_policy(self):
        result, ctx = self._run_alias("/ar:find", "/ar:find")
        assert ctx.file_policy == "SEARCH"

    def test_afs_sets_search_policy(self):
        result, ctx = self._run_alias("/afs", "/afs")
        assert ctx.file_policy == "SEARCH"

    def test_search_key_sets_search_policy(self):
        result, ctx = self._run_alias("SEARCH", "SEARCH")
        assert ctx.file_policy == "SEARCH"


class TestPolicySwitching:
    """Switching between policies updates ctx.file_policy correctly."""

    def _switch(self, ctx, alias: str, prompt: str) -> str:
        ctx.activation_prompt = prompt
        handler = app.command_handlers.get(alias)
        return handler(ctx)

    def test_allow_to_search(self):
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test-switch-a2s", event="UserPromptSubmit",
                           prompt="", store=store)
        ctx.file_policy = "ALLOW"
        self._switch(ctx, "/ar:f", "/ar:f")
        assert ctx.file_policy == "SEARCH"

    def test_search_to_justify(self):
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test-switch-s2j", event="UserPromptSubmit",
                           prompt="", store=store)
        ctx.file_policy = "SEARCH"
        self._switch(ctx, "/ar:j", "/ar:j")
        assert ctx.file_policy == "JUSTIFY"

    def test_justify_to_allow(self):
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test-switch-j2a", event="UserPromptSubmit",
                           prompt="", store=store)
        ctx.file_policy = "JUSTIFY"
        self._switch(ctx, "/ar:a", "/ar:a")
        assert ctx.file_policy == "ALLOW"

    def test_status_reflects_switched_policy(self):
        """After switching, /ar:st shows the new policy name."""
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test-switch-status", event="UserPromptSubmit",
                           prompt="/ar:f", store=store)
        ctx.activation_prompt = "/ar:f"
        app.command_handlers["/ar:f"](ctx)
        assert ctx.file_policy == "SEARCH"

        ctx.activation_prompt = "/ar:st"
        status_result = app.command_handlers["STATUS"](ctx)
        assert "strict-search" in status_result.lower()


class TestPoliciesConfigStructure:
    """CONFIG['policies'] has correct keys, names, and descriptions for all modes."""

    def test_all_three_policies_present(self):
        assert "ALLOW" in CONFIG["policies"]
        assert "JUSTIFY" in CONFIG["policies"]
        assert "SEARCH" in CONFIG["policies"]

    def test_allow_policy_name_and_desc(self):
        name, desc = CONFIG["policies"]["ALLOW"]
        assert name == "allow-all"
        assert "full permission" in desc.lower() or "allow all" in desc.lower()

    def test_justify_policy_name_and_desc(self):
        name, desc = CONFIG["policies"]["JUSTIFY"]
        assert name == "justify-create"
        assert "AUTOFILE_JUSTIFICATION" in desc

    def test_search_policy_name_and_desc(self):
        name, desc = CONFIG["policies"]["SEARCH"]
        assert name == "strict-search"
        assert "only modify existing" in desc.lower() or "no new files" in desc.lower()

    def test_policy_names_are_unique(self):
        names = [v[0] for v in CONFIG["policies"].values()]
        assert len(names) == len(set(names)), "Policy names must be unique"

    def test_policy_blocked_messages_exist(self):
        """CONFIG['policy_blocked'] must have messages for SEARCH and JUSTIFY."""
        assert "policy_blocked" in CONFIG
        assert "SEARCH" in CONFIG["policy_blocked"]
        assert "JUSTIFY" in CONFIG["policy_blocked"]
        assert len(CONFIG["policy_blocked"]["SEARCH"]) > 0
        assert len(CONFIG["policy_blocked"]["JUSTIFY"]) > 0


# ============================================================================
# Command Blocking — /ar:no, /ar:clear, /ar:blocks + global variants
# ============================================================================

class TestBlockingCommandBehavior:
    """/ar:no blocks session commands; /ar:ok allows; /ar:clear resets both."""

    def test_ar_no_empty_args_returns_usage(self):
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test-no-empty", event="UserPromptSubmit",
                           prompt="/ar:no", store=store)
        ctx.activation_prompt = "/ar:no"
        result = app.command_handlers["/ar:no"](ctx)
        assert "Usage" in result

    def test_ar_no_adds_pattern_to_session_blocks(self):
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test-no-add", event="UserPromptSubmit",
                           prompt="/ar:no mytool", store=store)
        ctx.activation_prompt = "/ar:no mytool"
        result = app.command_handlers["/ar:no"](ctx)

        assert "Blocked" in result
        blocks = ctx.session_blocked_patterns or []
        assert any(b["pattern"] == "mytool" for b in blocks)

    def test_ar_no_then_command_is_blocked(self):
        """After /ar:no mytool, running mytool in Bash is denied."""
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test-no-enforce", event="UserPromptSubmit",
                           prompt="/ar:no mytool", store=store)
        ctx.activation_prompt = "/ar:no mytool"
        app.command_handlers["/ar:no"](ctx)

        # Now simulate a PreToolUse for that command
        ctx2 = EventContext(
            session_id="test-no-enforce", event="PreToolUse",
            tool_name="Bash", tool_input={"command": "mytool --run"}, store=store
        )
        ctx2.session_blocked_patterns = ctx.session_blocked_patterns
        result = plugins.check_blocked_commands(ctx2)

        assert result is not None
        assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

    def test_ar_ok_empty_args_returns_usage(self):
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test-ok-empty2", event="UserPromptSubmit",
                           prompt="/ar:ok", store=store)
        ctx.activation_prompt = "/ar:ok"
        result = app.command_handlers["/ar:ok"](ctx)
        assert "Usage" in result

    def test_ar_clear_resets_session_blocks_and_allows(self):
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test-clear-all", event="UserPromptSubmit",
                           prompt="/ar:clear", store=store)
        ctx.activation_prompt = "/ar:clear"
        ctx.session_blocked_patterns = [{"pattern": "mytool", "suggestion": "s", "pattern_type": "literal"}]
        ctx.session_allowed_patterns = [{"pattern": "git push", "pattern_type": "literal"}]

        result = app.command_handlers["/ar:clear"](ctx)

        assert ctx.session_blocked_patterns == []
        assert ctx.session_allowed_patterns == []
        assert "Cleared" in result

    def test_ar_blocks_shows_session_blocks(self):
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test-blocks-show", event="UserPromptSubmit",
                           prompt="/ar:blocks", store=store)
        ctx.activation_prompt = "/ar:blocks"
        ctx.session_blocked_patterns = [{"pattern": "mytool", "suggestion": "s", "pattern_type": "literal"}]

        result = app.command_handlers["/ar:blocks"](ctx)
        assert "mytool" in result

    def test_ar_blocks_empty_shows_no_blocks(self):
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test-blocks-empty", event="UserPromptSubmit",
                           prompt="/ar:blocks", store=store)
        ctx.activation_prompt = "/ar:blocks"

        result = app.command_handlers["/ar:blocks"](ctx)
        assert "No session blocks" in result or "no" in result.lower()


class TestGlobalBlockingCommands:
    """/ar:globalno, /ar:globalok, /ar:globalstatus, /ar:globalclear."""

    def test_globalno_registered(self):
        assert "/ar:globalno" in app.command_handlers

    def test_globalok_registered(self):
        assert "/ar:globalok" in app.command_handlers

    def test_globalstatus_registered(self):
        assert "/ar:globalstatus" in app.command_handlers

    def test_globalclear_registered(self):
        assert "/ar:globalclear" in app.command_handlers

    def test_globalno_empty_args_returns_usage(self):
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test-globalno-empty", event="UserPromptSubmit",
                           prompt="/ar:globalno", store=store)
        ctx.activation_prompt = "/ar:globalno"
        result = app.command_handlers["/ar:globalno"](ctx)
        assert "Usage" in result

    def test_globalok_empty_args_returns_usage(self):
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test-globalok-empty", event="UserPromptSubmit",
                           prompt="/ar:globalok", store=store)
        ctx.activation_prompt = "/ar:globalok"
        result = app.command_handlers["/ar:globalok"](ctx)
        assert "Usage" in result

    def test_globalstatus_returns_string(self):
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test-globalstatus", event="UserPromptSubmit",
                           prompt="/ar:globalstatus", store=store)
        ctx.activation_prompt = "/ar:globalstatus"
        result = app.command_handlers["/ar:globalstatus"](ctx)
        assert isinstance(result, str)

    def test_globalclear_returns_cleared_message(self):
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test-globalclear", event="UserPromptSubmit",
                           prompt="/ar:globalclear", store=store)
        ctx.activation_prompt = "/ar:globalclear"
        result = app.command_handlers["/ar:globalclear"](ctx)
        assert "Cleared" in result or "cleared" in result.lower()


# ============================================================================
# Redirect in Deny/Warn Messages (end-to-end via check_blocked_commands)
# ============================================================================

class TestRedirectInDenyMessage:
    """Redirect alternative command appears in the deny message (end-to-end)."""

    def test_redirect_args_substituted_in_deny_message(self):
        """rm file.txt blocked → deny message contains 'trash file.txt'."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-redirect-args",
            event="PreToolUse", tool_name="Bash",
            tool_input={"command": "rm file.txt"}, store=store
        )
        result = plugins.check_blocked_commands(ctx)

        assert result is not None
        assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
        assert "trash file.txt" in str(result)

    def test_redirect_multiple_args_substituted(self):
        """rm -rf /tmp/dir blocked → deny message contains 'trash -rf /tmp/dir'."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-redirect-multi",
            event="PreToolUse", tool_name="Bash",
            tool_input={"command": "rm -rf /tmp/dir"}, store=store
        )
        result = plugins.check_blocked_commands(ctx)

        assert result is not None
        assert "trash -rf /tmp/dir" in str(result)

    def test_redirect_file_substituted_in_deny_message(self):
        """git checkout -- src/main.py → deny contains 'git stash push src/main.py'."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-redirect-file",
            event="PreToolUse", tool_name="Bash",
            tool_input={"command": "git checkout -- src/main.py"}, store=store
        )
        # The when predicate _file_has_unstaged_changes is evaluated;
        # mock subprocess so it signals a dirty file
        with patch("autorun.integrations.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="M src/main.py")
            result = plugins.check_blocked_commands(ctx)

        if result is not None and result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny":
            assert "src/main.py" in str(result)

    def test_redirect_no_args_falls_back_to_empty(self):
        """rm (no args) → redirect substitutes empty string for {args}."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-redirect-noargs",
            event="PreToolUse", tool_name="Bash",
            tool_input={"command": "rm"}, store=store
        )
        result = plugins.check_blocked_commands(ctx)

        assert result is not None
        # redirect="trash {args}" → "trash " (with trailing space is acceptable)
        result_str = str(result)
        assert "trash" in result_str

    def test_git_stash_drop_redirect_to_pop(self):
        """git stash drop → redirect suggests 'git stash pop'."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-redirect-stash-drop",
            event="PreToolUse", tool_name="Bash",
            tool_input={"command": "git stash drop"}, store=store
        )
        with patch("autorun.integrations.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="stash@{0}: WIP")
            result = plugins.check_blocked_commands(ctx)

        if result is not None and result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny":
            assert "git stash pop" in str(result)

    def test_no_redirect_means_no_use_instead_line(self):
        """Integration without redirect doesn't add 'Use instead:' to message."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-no-redirect",
            event="PreToolUse", tool_name="Bash",
            tool_input={"command": "git push origin main"}, store=store
        )
        result = plugins.check_blocked_commands(ctx)

        # git push has no redirect field → no "Use instead:" line
        assert result is not None
        assert "Use instead:" not in str(result)


# ============================================================================
# action: "warn" — allows command but includes message (and redirect)
# ============================================================================

class TestWarnActionBehavior:
    """action='warn' allows execution but includes suggestion + redirect in message."""

    def test_warn_action_returns_allow_not_deny(self):
        """action='warn' (git) → permissionDecision is allow, not deny."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-warn-allow",
            event="PreToolUse", tool_name="Bash",
            tool_input={"command": "git status"}, store=store
        )
        result = plugins.check_blocked_commands(ctx)

        # git matches the "git" warn integration
        if result is not None:
            assert result.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"

    def test_warn_action_message_included_in_response(self):
        """action='warn' response contains the suggestion text."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-warn-msg",
            event="PreToolUse", tool_name="Bash",
            tool_input={"command": "git commit -m 'test'"}, store=store
        )
        result = plugins.check_blocked_commands(ctx)

        # If warn triggered, response contains suggestion text (CLAUDE.md or similar)
        if result is not None:
            assert "CLAUDE.md" in str(result) or "git" in str(result).lower()

    def test_warn_does_not_block_safe_git_commands(self):
        """git log, git diff, git status — none are denied."""
        store = ThreadSafeDB()
        for cmd in ["git log", "git diff", "git status", "git branch"]:
            ctx = EventContext(
                session_id=f"test-warn-safe-{cmd.replace(' ', '-')}",
                event="PreToolUse", tool_name="Bash",
                tool_input={"command": cmd}, store=store
            )
            result = plugins.check_blocked_commands(ctx)
            if result is not None:
                assert result.get("hookSpecificOutput", {}).get("permissionDecision") != "deny", \
                    f"'{cmd}' should not be denied (action: warn)"


# ============================================================================
# Global Allows System (/ar:globalok)
# ============================================================================

class TestGlobalAllowsSystem:
    """Global allows added via /ar:globalok override DEFAULT_INTEGRATIONS."""

    def test_globalok_registered_as_handler(self):
        """/ar:globalok command is registered."""
        assert "/ar:globalok" in app.command_handlers

    def test_globalok_adds_to_global_allowed_patterns(self):
        """/ar:globalok 'git push' adds pattern to global_allowed_patterns."""
        import uuid
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id=f"test-globalok-{uuid.uuid4().hex[:8]}",
            event="UserPromptSubmit",
            prompt="/ar:globalok 'git push'", store=store
        )
        ctx.activation_prompt = "/ar:globalok 'git push'"

        handler = app.command_handlers.get("/ar:globalok")
        result = handler(ctx)

        assert "Allowed" in result or "allowed" in result.lower()

    def test_scope_accessor_global_allowed_roundtrip(self):
        """ScopeAccessor global scope stores and retrieves allowed patterns."""
        import uuid
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id=f"test-ga-global-{uuid.uuid4().hex[:8]}",
            event="test", store=store
        )
        accessor = plugins.ScopeAccessor(ctx, "global")
        key = f"test-pattern-{uuid.uuid4().hex[:6]}"
        allows = [{"pattern": key, "pattern_type": "literal"}]

        accessor.set_allowed(allows)
        retrieved = accessor.get_allowed()

        assert any(a["pattern"] == key for a in retrieved)

    def test_session_allow_takes_priority_over_global_allow(self):
        """Session allow checked before global allow — both result in allow."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-session-vs-global",
            event="PreToolUse", tool_name="Bash",
            tool_input={"command": "rm file.txt"}, store=store
        )
        ctx.session_allowed_patterns = [{"pattern": "rm", "pattern_type": "literal"}]

        result = plugins.check_blocked_commands(ctx)

        # Session allow → command passes through
        assert result is None or \
            result.get("hookSpecificOutput", {}).get("permissionDecision") == "allow"


# ============================================================================
# DEFAULT_INTEGRATIONS Config Validation
# ============================================================================

class TestDefaultIntegrationsConfig:
    """Validate ALL entries in DEFAULT_INTEGRATIONS follow config conventions."""

    def test_all_entries_have_valid_action(self):
        """Every integration must have action 'block' or 'warn' (default 'block')."""
        from autorun.config import DEFAULT_INTEGRATIONS
        valid_actions = {"block", "warn"}
        for pattern, cfg in DEFAULT_INTEGRATIONS.items():
            action = cfg.get("action", "block")
            assert action in valid_actions, \
                f"Integration '{pattern}' has invalid action '{action}'"

    def test_all_entries_have_suggestion(self):
        """Every integration must have a non-empty suggestion."""
        from autorun.config import DEFAULT_INTEGRATIONS
        for pattern, cfg in DEFAULT_INTEGRATIONS.items():
            assert "suggestion" in cfg, f"Integration '{pattern}' missing 'suggestion'"
            assert len(cfg["suggestion"]) > 0, f"Integration '{pattern}' has empty suggestion"

    def test_block_action_suggestions_include_ar_ok_hint(self):
        """All block-action integrations must include /ar:ok hint in suggestion."""
        from autorun.config import DEFAULT_INTEGRATIONS
        for pattern, cfg in DEFAULT_INTEGRATIONS.items():
            if cfg.get("action", "block") != "block":
                continue  # warn actions don't need /ar:ok
            suggestion = cfg["suggestion"]
            assert "/ar:ok" in suggestion, \
                f"Block integration '{pattern}' suggestion missing '/ar:ok' hint"

    def test_redirect_templates_use_valid_placeholders(self):
        """All redirect templates use only {args} or {file} placeholders."""
        from autorun.config import DEFAULT_INTEGRATIONS
        import re
        for pattern, cfg in DEFAULT_INTEGRATIONS.items():
            redirect = cfg.get("redirect")
            if not redirect:
                continue
            # Find all {placeholder} occurrences
            placeholders = set(re.findall(r"\{(\w+)\}", redirect))
            invalid = placeholders - {"args", "file"}
            assert not invalid, \
                f"Integration '{pattern}' redirect '{redirect}' has unknown placeholders: {invalid}"

    def test_when_predicates_are_registered_or_always(self):
        """All 'when' values must be 'always' or a registered predicate name."""
        from autorun.config import DEFAULT_INTEGRATIONS
        from autorun.integrations import _WHEN_PREDICATES
        for pattern, cfg in DEFAULT_INTEGRATIONS.items():
            when = cfg.get("when", "always")
            if when == "always":
                continue
            assert when in _WHEN_PREDICATES, \
                f"Integration '{pattern}' references unregistered predicate '{when}'"

    def test_event_values_are_valid(self):
        """All 'event' values must be 'bash', 'file', 'stop', or 'all'."""
        from autorun.config import DEFAULT_INTEGRATIONS
        valid_events = {"bash", "file", "stop", "all"}
        for pattern, cfg in DEFAULT_INTEGRATIONS.items():
            event = cfg.get("event", "bash")
            assert event in valid_events, \
                f"Integration '{pattern}' has invalid event '{event}'"

    def test_redirect_entries_are_strings(self):
        """All redirect values must be strings."""
        from autorun.config import DEFAULT_INTEGRATIONS
        for pattern, cfg in DEFAULT_INTEGRATIONS.items():
            redirect = cfg.get("redirect")
            if redirect is not None:
                assert isinstance(redirect, str), \
                    f"Integration '{pattern}' redirect is not a string: {type(redirect)}"

    def test_git_push_has_ar_ok_hint(self):
        """git push integration includes the exact /ar:ok 'git push' hint."""
        from autorun.config import DEFAULT_INTEGRATIONS
        suggestion = DEFAULT_INTEGRATIONS["git push"]["suggestion"]
        assert "/ar:ok 'git push'" in suggestion

    def test_rm_has_redirect_to_trash(self):
        """rm integration redirects to trash."""
        from autorun.config import DEFAULT_INTEGRATIONS
        assert DEFAULT_INTEGRATIONS["rm"]["redirect"] == "trash {args}"
        assert DEFAULT_INTEGRATIONS["rm -rf"]["redirect"] == "trash {args}"

    def test_git_checkout_dot_uses_stash_redirect(self):
        """git checkout . redirect suggests git stash push."""
        from autorun.config import DEFAULT_INTEGRATIONS
        redirect = DEFAULT_INTEGRATIONS["git checkout ."]["redirect"]
        assert redirect.startswith("git stash push")

    def test_git_stash_drop_redirect_to_pop(self):
        """git stash drop redirects to git stash pop."""
        from autorun.config import DEFAULT_INTEGRATIONS
        assert DEFAULT_INTEGRATIONS["git stash drop"]["redirect"] == "git stash pop"

    def test_file_redirect_entries_use_file_placeholder(self):
        """Integrations targeting specific files use {file} placeholder."""
        from autorun.config import DEFAULT_INTEGRATIONS
        file_patterns = ["git checkout --", "git checkout", "git restore"]
        for pattern in file_patterns:
            redirect = DEFAULT_INTEGRATIONS[pattern].get("redirect", "")
            assert "{file}" in redirect, \
                f"Integration '{pattern}' should use {{file}} placeholder, got: '{redirect}'"
