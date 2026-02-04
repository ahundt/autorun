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
TDD tests for clautorun v0.7 plugins.py components.

Tests for:
- File Policy Plugin: ALLOW/JUSTIFY/SEARCH handlers and enforcement
- Command Blocking Plugin: Block/allow/clear/status and pattern matching
- Autorun Plugin: Activation, stages, injection, plan acceptance
- Plan Management Plugin: New/refine/update/process handlers
"""

import pytest
from unittest.mock import Mock, patch, MagicMock

from clautorun.core import app, EventContext, ClautorunApp, ThreadSafeDB
from clautorun.config import CONFIG
from clautorun import plugins


# ============================================================================
# P2.1: File Policy Plugin Tests
# ============================================================================

class TestFilePolicyPlugin:
    """Tests for file policy handlers and enforcement."""

    def test_allow_policy_handler_sets_state(self):
        """ALLOW handler should set file_policy to ALLOW."""
        ctx = EventContext(session_id="test", event="UserPromptSubmit", prompt="/cr:a")
        ctx.activation_prompt = "/cr:a"

        # Find and call the handler
        handler = app.command_handlers.get("ALLOW")
        assert handler is not None

        result = handler(ctx)
        assert "allow-all" in result.lower() or "ALLOW" in result

    def test_justify_policy_handler_sets_state(self):
        """JUSTIFY handler should set file_policy to JUSTIFY."""
        ctx = EventContext(session_id="test", event="UserPromptSubmit", prompt="/cr:j")
        ctx.activation_prompt = "/cr:j"

        handler = app.command_handlers.get("JUSTIFY")
        assert handler is not None

        result = handler(ctx)
        assert "justify" in result.lower() or "JUSTIFY" in result

    def test_search_policy_handler_sets_state(self):
        """SEARCH handler should set file_policy to SEARCH."""
        ctx = EventContext(session_id="test", event="UserPromptSubmit", prompt="/cr:f")
        ctx.activation_prompt = "/cr:f"

        handler = app.command_handlers.get("SEARCH")
        assert handler is not None

        result = handler(ctx)
        assert "strict" in result.lower() or "SEARCH" in result

    def test_status_handler_shows_policy(self):
        """STATUS handler should show current policy."""
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test", event="UserPromptSubmit", prompt="/cr:st", store=store)
        ctx.activation_prompt = "/cr:st"
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

    def test_enforce_file_policy_search_allows_existing_file(self):
        """SEARCH policy should allow modifying existing files."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test", event="PreToolUse", tool_name="Write",
            tool_input={"file_path": "/tmp"}, store=store  # /tmp exists
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
            prompt="/cr:go build something", store=store
        )
        ctx.activation_prompt = "/cr:go build something"

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
            prompt="/cr:gp build something", store=store
        )
        ctx.activation_prompt = "/cr:gp build something"

        handler = app.command_handlers.get("activate")
        result = handler(ctx)

        assert ctx.autorun_mode == "procedural"

    def test_stop_deactivates(self):
        """handle_stop should deactivate autorun."""
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test", event="UserPromptSubmit", prompt="/cr:x", store=store)
        ctx.activation_prompt = "/cr:x"
        ctx.autorun_active = True

        handler = app.command_handlers.get("stop")
        result = handler(ctx)

        assert ctx.autorun_active is False

    def test_emergency_stop_deactivates(self):
        """handle_sos should deactivate with emergency message."""
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test", event="UserPromptSubmit", prompt="/cr:sos", store=store)
        ctx.activation_prompt = "/cr:sos"
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

        result = plugins.build_injection_prompt(ctx)
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
        """/cr:pn should set plan_active and return template."""
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test", event="UserPromptSubmit", prompt="/cr:pn", store=store)
        ctx.activation_prompt = "/cr:pn"

        # Find handler by iterating command_handlers
        for cmd, handler in app.command_handlers.items():
            if cmd == "/cr:pn":
                result = handler(ctx)
                assert ctx.plan_active is True
                assert "Plan Creation" in result
                break

    def test_planrefine_returns_template(self):
        """/cr:pr should return refine template."""
        store = ThreadSafeDB()
        ctx = EventContext(session_id="test", event="UserPromptSubmit", prompt="/cr:pr", store=store)
        ctx.activation_prompt = "/cr:pr"

        for cmd, handler in app.command_handlers.items():
            if cmd == "/cr:pr":
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
        assert "/cr:no" in app.command_handlers
        assert "/cr:ok" in app.command_handlers
        assert "/cr:clear" in app.command_handlers
        assert "/cr:globalno" in app.command_handlers
        assert "/cr:globalok" in app.command_handlers
        assert "/cr:globalstatus" in app.command_handlers

    def test_autorun_handlers_registered(self):
        """Autorun handlers should be registered in app."""
        assert "activate" in app.command_handlers
        assert "stop" in app.command_handlers
        assert "emergency_stop" in app.command_handlers

    def test_plan_handlers_registered(self):
        """Plan handlers should be registered in app."""
        assert "/cr:pn" in app.command_handlers
        assert "/cr:pr" in app.command_handlers
        assert "/cr:pu" in app.command_handlers
        assert "/cr:pp" in app.command_handlers

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
# /cr:reload Command Tests
# ============================================================================

class TestReloadCommand:
    """Tests for /cr:reload command."""

    def test_reload_handler_registered(self):
        """Reload handler should be registered."""
        assert "/cr:reload" in app.command_handlers

    def test_reload_returns_count(self):
        """Reload should return integration count."""
        ctx = EventContext(session_id="test", event="UserPromptSubmit", prompt="/cr:reload")
        ctx.activation_prompt = "/cr:reload"

        handler = app.command_handlers.get("/cr:reload")
        result = handler(ctx)

        assert "Reloaded" in result
        assert "integrations" in result

    def test_reload_clears_cache(self):
        """Reload should clear integration cache."""
        from clautorun.integrations import load_all_integrations, invalidate_caches

        # Load once
        integrations1 = load_all_integrations()

        # Reload via handler
        ctx = EventContext(session_id="test", event="UserPromptSubmit", prompt="/cr:reload")
        ctx.activation_prompt = "/cr:reload"
        handler = app.command_handlers.get("/cr:reload")
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

    @patch("clautorun.integrations.subprocess.run")
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

    @patch("clautorun.integrations.subprocess.run")
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
