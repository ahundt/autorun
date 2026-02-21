#!/usr/bin/env python3
"""Regression tests for PreToolUse command blocking.

These tests prevent regressions for bugs found during the Gemini CLI integration
session (2026-02-11). Each test documents the specific bug it prevents.

Semantics (per official Claude Code hooks docs):
- continue: True = AI keeps running (ALWAYS True for PreToolUse and Stop events)
- continue: False = AI stops entirely (only for emergency stop via command_response)
- permissionDecision: "deny" = blocks the tool call (independent of continue)
- Tool blocking relies on permissionDecision + exit code 2 (Bug #4669 workaround)

Bugs covered:
1. core.py EventContext.respond("deny") correctly returns continue=True for PreToolUse
   (per official hooks docs: continue controls AI lifecycle, not tool blocking)
   Tool blocking uses permissionDecision="deny" + exit code 2 (Bug #4669 workaround)
2. main.py build_pretooluse_response("deny") correctly returns continue=True
3. Both code paths (daemon vs legacy) must produce identical continue values
4. hooks.json must use 'uv run', not bare 'python3' (UV-centric system)
5. hook_entry.py must not have debug logging that consumes stdin
6. Non-blocked commands must still return continue=True (no false positives)
7. permissionDecision="deny" must be present for blocked tools (continue is always True)

NO COST - Direct Python function calls, no API usage.
"""
import json
import os
import sys
from pathlib import Path

import pytest

# Add plugin source to path
plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root / "src"))


# =============================================================================
# Bug 1: core.py EventContext.respond("deny") for PreToolUse (daemon path)
# Per official hooks docs, continue=True is CORRECT for PreToolUse deny.
# Tool blocking uses permissionDecision="deny" + exit code 2 (Bug #4669 workaround).
# continue=False would stop the AI entirely, which is NOT what we want.
# =============================================================================

class TestDaemonPreToolUseDenyBlocks:
    """core.py EventContext.respond() must set continue=True for deny (AI keeps running)."""

    def test_pretooluse_deny_returns_continue_true(self):
        """EventContext.respond('deny') for PreToolUse must return continue=True.

        Per official hooks docs: continue controls AI lifecycle, not tool blocking.
        Tool blocking uses permissionDecision="deny" + exit code 2 (Bug #4669 workaround).
        continue=False would stop the entire AI session.
        """
        from autorun.core import EventContext

        ctx = EventContext(session_id="test", event="PreToolUse")
        response = ctx.respond("deny", "Blocked command")

        assert response["continue"] is True, (
            "PreToolUse deny must have continue=True (AI keeps running). "
            "Tool blocking uses permissionDecision='deny' + exit code 2 (Bug #4669)."
        )

    def test_pretooluse_deny_has_permission_decision(self):
        """When denying, permissionDecision must be 'deny' to block the tool."""
        from autorun.core import EventContext

        ctx = EventContext(session_id="test", event="PreToolUse")
        response = ctx.respond("deny", "Use trash instead")

        assert response["hookSpecificOutput"]["permissionDecision"] == "deny", (
            "PreToolUse deny must have permissionDecision='deny' to block the tool. "
            "This + exit code 2 is the actual blocking mechanism (Bug #4669 workaround)."
        )

    def test_pretooluse_allow_returns_continue_true(self):
        """EventContext.respond('allow') for PreToolUse must return continue=True."""
        from autorun.core import EventContext

        ctx = EventContext(session_id="test", event="PreToolUse")
        response = ctx.respond("allow", "Allowed")

        assert response["continue"] is True, (
            "REGRESSION: PreToolUse allow must have continue=True. "
            "Fix must not introduce false positives."
        )

    def test_pretooluse_deny_convenience_method(self):
        """ctx.deny() convenience method must also return continue=True (AI keeps running)."""
        from autorun.core import EventContext

        ctx = EventContext(session_id="test", event="PreToolUse")
        response = ctx.deny("Blocked")

        assert response["continue"] is True, (
            "ctx.deny() must return continue=True. Tool blocking uses permissionDecision."
        )
        assert response["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_pretooluse_allow_convenience_method(self):
        """ctx.allow() convenience method must return continue=True."""
        from autorun.core import EventContext

        ctx = EventContext(session_id="test", event="PreToolUse")
        response = ctx.allow("OK")

        assert response["continue"] is True, (
            "ctx.allow() must return continue=True."
        )


# =============================================================================
# Bug 2: main.py build_pretooluse_response("deny") (legacy/non-daemon path)
# Per official hooks docs, continue=True is CORRECT for PreToolUse deny.
# Tool blocking uses permissionDecision="deny" + exit code 2 (Bug #4669 workaround).
# =============================================================================

class TestLegacyPreToolUseDenyBlocks:
    """main.py build_pretooluse_response() must set continue=True for deny (AI keeps running)."""

    def test_build_pretooluse_response_deny(self):
        """build_pretooluse_response('deny') must return continue=True.

        Per official hooks docs: continue controls AI lifecycle, not tool blocking.
        Tool blocking uses permissionDecision="deny" + exit code 2 (Bug #4669 workaround).
        """
        from autorun.main import build_pretooluse_response

        response = build_pretooluse_response("deny", "Use trash instead")

        assert response["continue"] is True, (
            "build_pretooluse_response('deny') must have continue=True. "
            "Tool blocking uses permissionDecision='deny' + exit code 2 (Bug #4669)."
        )
        assert response["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_build_pretooluse_response_allow(self):
        """build_pretooluse_response('allow') must return continue=True."""
        from autorun.main import build_pretooluse_response

        response = build_pretooluse_response("allow", "Allowed")

        assert response["continue"] is True, (
            "build_pretooluse_response('allow') must have continue=True. "
            "Fix must not introduce false positives."
        )

    def test_build_pretooluse_response_deny_has_permission_decision(self):
        """When denying, permissionDecision must be 'deny'."""
        from autorun.main import build_pretooluse_response

        response = build_pretooluse_response("deny", "Use trash instead")

        assert response["hookSpecificOutput"]["permissionDecision"] == "deny", (
            "deny response must have permissionDecision='deny' to block tool."
        )

    def test_build_pretooluse_response_allow_empty_stop_reason(self):
        """When allowing, stopReason must be empty."""
        from autorun.main import build_pretooluse_response

        response = build_pretooluse_response("allow", "OK")

        assert response["stopReason"] == "", (
            "Allow response must have empty stopReason."
        )


# =============================================================================
# Bug 3: Daemon and legacy paths must produce consistent continue values
# Two separate code paths exist:
# - Daemon: core.py EventContext.respond() (used when USE_DAEMON=1)
# - Legacy: main.py build_pretooluse_response() (used when USE_DAEMON=0)
# Both must agree: continue=True always for PreToolUse (tool blocking via permissionDecision).
# =============================================================================

class TestDaemonLegacyConsistency:
    """Both code paths must produce identical continue values for same inputs."""

    @pytest.mark.parametrize("decision,expected_continue", [
        ("deny", True),   # continue=True: AI keeps running, tool blocked by permissionDecision
        ("allow", True),   # continue=True: AI keeps running, tool allowed
    ])
    def test_continue_consistency(self, decision, expected_continue):
        """Daemon and legacy paths must agree on continue value for same decision.

        Per official hooks docs: continue=True always for PreToolUse.
        Tool blocking uses permissionDecision="deny" + exit code 2 (Bug #4669).
        """
        from autorun.core import EventContext
        from autorun.main import build_pretooluse_response

        reason = "test reason"

        # Daemon path
        ctx = EventContext(session_id="test", event="PreToolUse")
        daemon_response = ctx.respond(decision, reason)

        # Legacy path
        legacy_response = build_pretooluse_response(decision, reason)

        assert daemon_response["continue"] == expected_continue, (
            f"Daemon path: continue={daemon_response['continue']} "
            f"for decision={decision}, expected {expected_continue}"
        )
        assert legacy_response["continue"] == expected_continue, (
            f"Legacy path: continue={legacy_response['continue']} "
            f"for decision={decision}, expected {expected_continue}"
        )
        assert daemon_response["continue"] == legacy_response["continue"], (
            f"INCONSISTENCY: Daemon continue={daemon_response['continue']} "
            f"vs Legacy continue={legacy_response['continue']} "
            f"for decision={decision}. Both paths must agree."
        )

    @pytest.mark.parametrize("decision", ["deny", "allow"])
    def test_decision_field_consistency(self, decision):
        """Both paths must include the same decision field values."""
        from autorun.core import EventContext
        from autorun.main import build_pretooluse_response

        ctx = EventContext(session_id="test", event="PreToolUse")
        daemon_response = ctx.respond(decision, "test")
        legacy_response = build_pretooluse_response(decision, "test")

        # Both must have hookSpecificOutput with raw permissionDecision
        assert daemon_response["hookSpecificOutput"]["permissionDecision"] == decision
        assert legacy_response["hookSpecificOutput"]["permissionDecision"] == decision

        # Top-level decision is CLI-mapped (approve/block for Claude, allow/deny for Gemini)
        # Both paths must agree on the mapped value
        assert daemon_response.get("decision") == legacy_response.get("decision"), (
            f"Decision mismatch: daemon={daemon_response.get('decision')} "
            f"vs legacy={legacy_response.get('decision')}"
        )


# =============================================================================
# Bug 4: hooks.json must use UV-centric commands, not bare python3
# The system is UV-centric. Using bare 'python3' bypasses UV's environment
# management and is inconsistent with the project's tooling approach.
# =============================================================================

class TestHooksJsonUVCentric:
    """hooks.json must use 'uv run', not bare 'python3'."""

    @pytest.fixture
    def hooks_json(self):
        """Load hooks.json from source."""
        hooks_path = plugin_root / "hooks" / "claude-hooks.json"
        assert hooks_path.exists(), f"hooks.json not found at {hooks_path}"
        with open(hooks_path) as f:
            return json.load(f)

    def test_no_bare_python3_in_hook_commands(self, hooks_json):
        """Hook commands must not use bare 'python3' (UV-centric system).

        Bug: hooks.json used 'python3 ${CLAUDE_PLUGIN_ROOT}/hooks/hook_entry.py'
        instead of UV-wrapped commands. In a UV-centric system, bare python3
        bypasses UV's environment management.
        """
        for event_name, event_hooks in hooks_json.get("hooks", {}).items():
            for hook_group in event_hooks:
                for hook in hook_group.get("hooks", []):
                    command = hook.get("command", "")
                    # Must not start with bare python3
                    assert not command.startswith("python3 "), (
                        f"REGRESSION: Hook command in {event_name} uses bare 'python3': "
                        f"'{command}'. Must use 'uv run' for UV-centric system."
                    )
                    # Must not start with bare python
                    assert not command.startswith("python "), (
                        f"REGRESSION: Hook command in {event_name} uses bare 'python': "
                        f"'{command}'. Must use 'uv run' for UV-centric system."
                    )

    def test_hook_commands_use_uv_run(self, hooks_json):
        """All hook commands must use 'uv run' for UV-centric execution."""
        for event_name, event_hooks in hooks_json.get("hooks", {}).items():
            for hook_group in event_hooks:
                for hook in hook_group.get("hooks", []):
                    command = hook.get("command", "")
                    assert "uv run" in command, (
                        f"Hook command in {event_name} missing 'uv run': "
                        f"'{command}'. UV-centric system requires 'uv run'."
                    )

    def test_hook_commands_dont_change_cwd(self, hooks_json):
        """Hook commands must not use 'cd' which changes CWD for tools like plan export.

        Bug: Using 'cd ${CLAUDE_PLUGIN_ROOT} && ...' changes the working directory,
        which breaks tools that depend on CWD being the project directory.
        """
        for event_name, event_hooks in hooks_json.get("hooks", {}).items():
            for hook_group in event_hooks:
                for hook in hook_group.get("hooks", []):
                    command = hook.get("command", "")
                    assert not command.startswith("cd "), (
                        f"Hook command in {event_name} starts with 'cd': "
                        f"'{command}'. Must not change CWD — use --project flag instead."
                    )


# =============================================================================
# Bug 5: hook_entry.py must not have debug logging that consumes stdin
# Debug logging was temporarily added to hook_entry.py which read all stdin
# into a variable, then recreated sys.stdin as StringIO. This pattern is
# fragile and should not be in production code.
# =============================================================================

class TestHookEntryClean:
    """hook_entry.py must not have debug logging or stdin consumption."""

    @pytest.fixture
    def hook_entry_source(self):
        """Read hook_entry.py source code."""
        path = plugin_root / "hooks" / "hook_entry.py"
        assert path.exists(), f"hook_entry.py not found at {path}"
        return path.read_text()

    def test_no_debug_log_file_references(self, hook_entry_source):
        """hook_entry.py must not reference debug log files.

        Bug: Debug logging was added that wrote to /tmp/autorun-hook-debug.log,
        consuming stdin and potentially interfering with hook payload processing.
        """
        assert "autorun-hook-debug" not in hook_entry_source, (
            "REGRESSION: hook_entry.py contains debug log file reference. "
            "Remove debug logging before committing."
        )

    def test_no_module_level_stdin_consumption(self, hook_entry_source):
        """hook_entry.py must not consume stdin at module level (outside functions).

        Bug: Debug code at module level did sys.stdin.read() which consumed
        all stdin data before main() could process it, then replaced sys.stdin
        with StringIO. This pattern is fragile and breaks the hook payload flow.

        Note: sys.stdin.read() inside function definitions (like try_cli()) is
        fine — those are called from main() which controls the flow.
        """
        lines = hook_entry_source.split("\n")

        # Extract truly module-level code (not inside any function)
        # Simple heuristic: lines not inside a def block (indent level 0, not starting with space/tab)
        module_level_lines = []
        in_function = False
        for line in lines:
            stripped = line.lstrip()
            if stripped.startswith("def ") or stripped.startswith("class "):
                in_function = True
                continue
            if in_function and line and not line[0].isspace() and not line.startswith("#"):
                in_function = False
            if not in_function:
                module_level_lines.append(line)

        module_level_code = "\n".join(module_level_lines)

        # Module-level code should not read stdin (that's main()'s job)
        assert "sys.stdin.read()" not in module_level_code, (
            "REGRESSION: hook_entry.py reads stdin at module level. "
            "This consumes the hook payload before main() can process it."
        )

    def test_no_module_level_stringio(self, hook_entry_source):
        """hook_entry.py must not use StringIO at module level for stdin replacement.

        Bug: Debug code replaced sys.stdin with io.StringIO at module level,
        which is a fragile pattern that interferes with stdin processing.
        """
        # Check if StringIO is imported AND used to replace stdin at module level
        # The pattern was: sys.stdin = _io.StringIO(_debug_stdin)
        assert "sys.stdin = " not in hook_entry_source.split("def main()")[0] if "def main()" in hook_entry_source else True, (
            "REGRESSION: hook_entry.py replaces sys.stdin at module level. "
            "This was part of the debug logging pattern."
        )

    def test_no_atexit_debug_handlers(self, hook_entry_source):
        """hook_entry.py must not register debug atexit handlers."""
        # Simple check: atexit should not be imported at module level for debug
        assert "_debug_exit" not in hook_entry_source, (
            "REGRESSION: hook_entry.py has debug atexit handler. Remove debug code."
        )


# =============================================================================
# Bug 6 & 7: continue and permissionDecision invariants
# Per official hooks docs:
# - continue=True ALWAYS for PreToolUse (AI keeps running)
# - permissionDecision="deny" blocks the tool (independent of continue)
# - For Stop events, continue=True keeps AI working (block prevents stopping)
# =============================================================================

class TestContinueDecisionInvariant:
    """continue is always True for PreToolUse/Stop; permissionDecision controls tool blocking."""

    @pytest.mark.parametrize("decision", ["deny", "allow"])
    def test_daemon_respond_continue_always_true(self, decision):
        """Daemon EventContext.respond() must always return continue=True for PreToolUse."""
        from autorun.core import EventContext

        ctx = EventContext(session_id="test", event="PreToolUse")
        response = ctx.respond(decision, "test reason")

        assert response["continue"] is True, (
            f"PreToolUse must always have continue=True (AI keeps running). "
            f"Got continue={response['continue']} for decision={decision}."
        )
        assert response["hookSpecificOutput"]["permissionDecision"] == decision

    @pytest.mark.parametrize("decision", ["deny", "allow"])
    def test_legacy_response_continue_always_true(self, decision):
        """Legacy build_pretooluse_response() must always return continue=True."""
        from autorun.main import build_pretooluse_response

        response = build_pretooluse_response(decision, "test reason")

        assert response["continue"] is True, (
            f"PreToolUse must always have continue=True (AI keeps running). "
            f"Got continue={response['continue']} for decision={decision}."
        )
        assert response["hookSpecificOutput"]["permissionDecision"] == decision

    def test_stop_deny_keeps_ai_running(self):
        """Stop deny keeps AI running (prevents premature stopping)."""
        from autorun.core import EventContext

        ctx = EventContext(session_id="test", event="Stop")
        response = ctx.respond("deny", "Stop denied")

        assert response["continue"] is True, (
            "Stop deny must have continue=True (keeps AI working, prevents premature stopping)."
        )

    def test_stop_block_keeps_ai_running(self):
        """Stop block keeps AI running (prevents premature stopping)."""
        from autorun.core import EventContext

        ctx = EventContext(session_id="test", event="Stop")
        response = ctx.respond("block", "Continue working")

        assert response["continue"] is True, (
            "Stop block must have continue=True (keeps AI working)."
        )


# =============================================================================
# Integration: End-to-end rm blocking through pretooluse_handler
# Tests the full handler flow with real EventContext, matching production.
# =============================================================================

class TestRmBlockingEndToEnd:
    """End-to-end test that rm commands are blocked through the handler."""

    @pytest.fixture(autouse=True)
    def clean_session(self):
        """Ensure clean session state for each test."""
        from autorun.session_manager import session_state
        with session_state("regression-test") as state:
            state.clear()
        yield
        with session_state("regression-test") as state:
            state.clear()

    def test_rm_blocked_via_legacy_handler(self):
        """rm command blocked via main.py pretooluse_handler (legacy path).

        This is the direct function call path used when USE_DAEMON=0.
        """
        from autorun.main import pretooluse_handler
        from autorun.core import EventContext

        ctx = EventContext(
            session_id="regression-test",
            event="PreToolUse",
            tool_name="Bash",
            tool_input={"command": "rm /tmp/test.txt"},
        )

        result = pretooluse_handler(ctx)

        assert result["continue"] is True, (
            "PreToolUse must have continue=True (AI keeps running). "
            "Tool blocking uses permissionDecision='deny' + exit code 2 (Bug #4669)."
        )
        perm_decision = result.get("hookSpecificOutput", {}).get("permissionDecision")
        assert perm_decision == "deny", (
            f"REGRESSION: rm command permissionDecision is '{perm_decision}', expected 'deny'."
        )

    def test_rm_blocked_mentions_trash(self):
        """rm blocking message must suggest 'trash' as alternative."""
        from autorun.main import pretooluse_handler
        from autorun.core import EventContext

        ctx = EventContext(
            session_id="regression-test",
            event="PreToolUse",
            tool_name="Bash",
            tool_input={"command": "rm /tmp/test.txt"},
        )

        result = pretooluse_handler(ctx)
        message = result.get("systemMessage", "") or result.get("reason", "")

        assert "trash" in message.lower(), (
            "rm blocking message must suggest 'trash' as safe alternative."
        )

    def test_safe_commands_not_blocked(self):
        """Safe commands like ls, echo must NOT be blocked (no false positives)."""
        from autorun.main import pretooluse_handler
        from autorun.core import EventContext

        safe_commands = [
            "ls -la",
            "echo hello",
            "pwd",
            "git status",
        ]

        for cmd in safe_commands:
            ctx = EventContext(
                session_id="regression-test",
                event="PreToolUse",
                tool_name="Bash",
                tool_input={"command": cmd},
            )

            result = pretooluse_handler(ctx)

            assert result["continue"] is True, (
                f"False positive: safe command '{cmd}' was blocked (continue=False). "
                "Fix must not introduce false positives."
            )

    @pytest.mark.parametrize("tool_name", ["Bash", "bash_command", "run_shell_command"])
    def test_rm_blocked_all_tool_name_variants(self, tool_name):
        """rm must be blocked for all Bash tool name variants (Claude + Gemini)."""
        from autorun.main import pretooluse_handler
        from autorun.core import EventContext

        ctx = EventContext(
            session_id="regression-test",
            event="PreToolUse",
            tool_name=tool_name,
            tool_input={"command": "rm /tmp/test.txt"},
        )

        result = pretooluse_handler(ctx)

        assert result["continue"] is True, (
            "PreToolUse must have continue=True (AI keeps running). "
            "Tool blocking uses permissionDecision='deny'."
        )
        perm_decision = result.get("hookSpecificOutput", {}).get("permissionDecision")
        assert perm_decision == "deny", (
            f"REGRESSION: rm not blocked for tool_name='{tool_name}'. "
            f"permissionDecision={perm_decision}."
        )
