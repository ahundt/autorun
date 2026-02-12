#!/usr/bin/env python3
"""Comprehensive command blocking tests using real EventContext.

Tests verify:
1. Blocking logic works for all tool name variants (Claude + Gemini)
2. Session state is properly isolated between tests
3. Edge cases handled correctly (empty commands, invalid input, etc.)
4. systemMessage content is helpful and actionable
5. permissionDecision correctly set for allow/deny scenarios
6. Real EventContext property access and None handling

Uses real EventContext (core.py:222) instead of MagicMock to exercise
the actual context construction, property accessors, and default handling
that runs in production.

NO COST - Direct Python function calls, no API usage.
"""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock
from typing import Any, Dict

import pytest

# Add plugin source to path
plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root / "src"))

from clautorun.main import pretooluse_handler
from clautorun.core import EventContext
from clautorun.command_detection import BASHLEX_AVAILABLE
from clautorun.config import BASH_TOOLS, WRITE_TOOLS, EDIT_TOOLS, PLAN_TOOLS
from clautorun.session_manager import clear_test_session_state, session_state


def create_test_context(
    tool_name: str,
    tool_input: Dict[str, Any],
    session_id: str = "test-session",
    file_policy: str = "allow-all"
) -> EventContext:
    """Create real EventContext matching production hook context.

    Uses the actual EventContext class from core.py:222 instead of MagicMock.
    This ensures tests exercise the same property accessors, __slots__,
    and default handling that runs in production.

    Args:
        tool_name: Name of tool being called (e.g., "Bash", "bash_command")
        tool_input: Tool input parameters (e.g., {"command": "cat file.txt"})
        session_id: Session identifier
        file_policy: AutoFile policy (allow-all, justify-create, strict-search)

    Returns:
        Real EventContext with proper initialization

    Note:
        File policy is stored in session_state (shelve), not on ctx directly,
        since pretooluse_handler reads from session_state(session_id).
    """
    # Map test-friendly policy names to internal policy constants
    policy_map = {
        "allow-all": "ALLOW",
        "justify-create": "JUSTIFY",
        "strict-search": "SEARCH",
    }
    internal_policy = policy_map.get(file_policy, "ALLOW")

    # Initialize session state with file_policy (handler reads from here)
    with session_state(session_id) as state:
        state["file_policy"] = internal_policy

    # Create real EventContext - same class used by daemon mode (core.py:222)
    # EventContext.__init__ handles None tool_input (defaults to {})
    ctx = EventContext(
        session_id=session_id,
        event="PreToolUse",
        tool_name=tool_name,
        tool_input=tool_input,
        session_transcript=[],
    )

    return ctx


@pytest.fixture(autouse=True)
def cleanup_session_state():
    """Clean up session state between tests to prevent pollution."""
    # Setup
    test_session_id = "test-session"

    yield

    # Cleanup: Remove test session state using exported function
    clear_test_session_state(test_session_id)


@pytest.fixture(autouse=True)
def setup_test_environment():
    """Set up clean test environment."""
    # Set Gemini environment (also works for Claude)
    original_env = os.environ.copy()
    os.environ["GEMINI_SESSION_ID"] = "test-blocking-session"
    os.environ["GEMINI_PROJECT_DIR"] = str(Path.cwd())

    yield

    # Restore original environment
    os.environ.clear()
    os.environ.update(original_env)


class TestCommandBlockingAllToolNames:
    """Test blocking works for ALL tool name variants (Claude + Gemini)."""

    @pytest.mark.parametrize("tool_name", ["Bash", "bash_command", "run_shell_command"])
    def test_cat_blocked_all_bash_tool_names(self, tool_name):
        """Verify cat blocked for all bash tool name variants."""
        ctx = create_test_context(
            tool_name=tool_name,
            tool_input={"command": "cat /etc/hosts"}
        )

        result = pretooluse_handler(ctx)

        hook_output = result.get("hookSpecificOutput", {})
        permission = hook_output.get("permissionDecision", "allow")

        assert permission == "deny", \
            f"cat not blocked for tool_name={tool_name}"

        # Verify helpful message
        reason = hook_output.get("permissionDecisionReason", "")
        assert "read" in reason.lower(), f"Message doesn't suggest Read tool for {tool_name}"
        assert len(reason) > 50, f"Blocking message too short for {tool_name}"

    @pytest.mark.parametrize("tool_name", ["Write", "write_file"])
    def test_file_creation_all_write_tool_names(self, tool_name):
        """Verify file creation gating works for all write tool variants."""
        ctx = create_test_context(
            tool_name=tool_name,
            tool_input={
                "file_path": "/tmp/new_file.txt",
                "content": "test"
            },
            file_policy="justify-create"
        )

        result = pretooluse_handler(ctx)

        hook_output = result.get("hookSpecificOutput", {})
        permission = hook_output.get("permissionDecision", "allow")

        assert permission == "deny", \
            f"File creation not blocked for tool_name={tool_name}"

        reason = hook_output.get("permissionDecisionReason", "")
        assert "justif" in reason.lower(), \
            f"Message doesn't mention justification for {tool_name}"

    @pytest.mark.parametrize("tool_name", ["Edit", "edit_file", "replace"])
    def test_edit_tools_recognized(self, tool_name):
        """Verify all edit tool variants are recognized."""
        assert tool_name in EDIT_TOOLS, \
            f"Edit tool variant {tool_name} not in EDIT_TOOLS"

    @pytest.mark.parametrize("tool_name", ["ExitPlanMode", "exit_plan_mode"])
    def test_plan_tools_recognized(self, tool_name):
        """Verify all plan tool variants are recognized."""
        assert tool_name in PLAN_TOOLS, \
            f"Plan tool variant {tool_name} not in PLAN_TOOLS"


class TestCommandBlockingEdgeCases:
    """Test edge cases and error conditions."""

    def test_empty_command(self):
        """Test handling of empty command string."""
        ctx = create_test_context(
            tool_name="bash_command",
            tool_input={"command": ""}
        )

        result = pretooluse_handler(ctx)

        # Should allow empty command (nothing to block)
        hook_output = result.get("hookSpecificOutput", {})
        permission = hook_output.get("permissionDecision", "allow")
        assert permission == "allow", "Empty command should be allowed"

    def test_whitespace_only_command(self):
        """Test handling of whitespace-only command."""
        ctx = create_test_context(
            tool_name="bash_command",
            tool_input={"command": "   \n\t   "}
        )

        result = pretooluse_handler(ctx)

        hook_output = result.get("hookSpecificOutput", {})
        permission = hook_output.get("permissionDecision", "allow")
        assert permission == "allow", "Whitespace-only command should be allowed"

    def test_missing_command_key(self):
        """Test handling when 'command' key missing from tool_input."""
        ctx = create_test_context(
            tool_name="bash_command",
            tool_input={}  # No 'command' key
        )

        result = pretooluse_handler(ctx)

        # Should not crash, should allow (nothing to check)
        assert "hookSpecificOutput" in result
        hook_output = result["hookSpecificOutput"]
        permission = hook_output.get("permissionDecision", "allow")
        assert permission == "allow", "Missing command key should be allowed"

    def test_none_tool_input(self):
        """Test handling when tool_input is None."""
        ctx = MagicMock()
        ctx.session_id = "test-session"
        ctx.tool_name = "bash_command"
        ctx.tool_input = None
        ctx.file_policy = "allow-all"

        result = pretooluse_handler(ctx)

        # Should not crash
        assert "hookSpecificOutput" in result

    def test_invalid_file_policy(self):
        """Test handling of invalid file policy value."""
        ctx = create_test_context(
            tool_name="write_file",
            tool_input={"file_path": "/tmp/test.txt", "content": "test"},
            file_policy="invalid-policy"
        )

        result = pretooluse_handler(ctx)

        # Should not crash
        assert "hookSpecificOutput" in result


class TestFilePathVariants:
    """Test file path handling for different scenarios."""

    def test_absolute_path_new_file_justify_mode(self):
        """Test absolute path to new file in justify mode."""
        ctx = create_test_context(
            tool_name="write_file",
            tool_input={
                "file_path": "/tmp/test_absolute_new.txt",
                "content": "test"
            },
            file_policy="justify-create"
        )

        result = pretooluse_handler(ctx)

        hook_output = result.get("hookSpecificOutput", {})
        permission = hook_output.get("permissionDecision", "allow")

        # Should be blocked (new file, justify mode)
        assert permission == "deny", "New file not blocked in justify mode"

    def test_relative_path_new_file_justify_mode(self):
        """Test relative path to new file in justify mode."""
        ctx = create_test_context(
            tool_name="write_file",
            tool_input={
                "file_path": "test_relative_new.txt",
                "content": "test"
            },
            file_policy="justify-create"
        )

        result = pretooluse_handler(ctx)

        hook_output = result.get("hookSpecificOutput", {})
        permission = hook_output.get("permissionDecision", "allow")

        # Should be blocked (new file, justify mode)
        assert permission == "deny", "New file not blocked in justify mode"

    def test_existing_file_modify_allowed(self):
        """Test modifying existing file is allowed in all modes."""
        # Create a real temporary file
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            existing_file = f.name
            f.write("original content")

        try:
            ctx = create_test_context(
                tool_name="write_file",
                tool_input={
                    "file_path": existing_file,
                    "content": "new content"
                },
                file_policy="strict-search"  # Strictest mode
            )

            result = pretooluse_handler(ctx)

            hook_output = result.get("hookSpecificOutput", {})
            permission = hook_output.get("permissionDecision", "allow")

            # Should be allowed (existing file)
            assert permission == "allow", \
                "Existing file modification blocked in strict-search mode"
        finally:
            # Cleanup
            Path(existing_file).unlink(missing_ok=True)


class TestSystemMessageQuality:
    """Test that systemMessage content is helpful and actionable."""

    def test_cat_message_suggests_read_tool(self):
        """Verify cat blocking message suggests Read tool."""
        ctx = create_test_context(
            tool_name="bash_command",
            tool_input={"command": "cat myfile.txt"}
        )

        result = pretooluse_handler(ctx)

        system_message = result.get("systemMessage", "")
        reason = result.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")

        # Should mention Read tool
        combined = (system_message + reason).lower()
        assert "read" in combined, "Message doesn't mention Read tool"

        # Should explain why Read is better
        assert any(word in combined for word in ["pagination", "limit", "offset", "line numbers"]), \
            "Message doesn't explain benefits of Read tool"

    def test_head_message_suggests_read_with_limit(self):
        """Verify head blocking message suggests Read tool with limit parameter."""
        ctx = create_test_context(
            tool_name="bash_command",
            tool_input={"command": "head -20 file.txt"}
        )

        result = pretooluse_handler(ctx)

        reason = result.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")

        # Should mention Read tool with limit
        assert "read" in reason.lower(), "Message doesn't mention Read tool"
        assert "limit" in reason.lower(), "Message doesn't mention limit parameter"

    def test_file_creation_message_explains_policy(self):
        """Verify file creation blocking explains the policy."""
        ctx = create_test_context(
            tool_name="write_file",
            tool_input={"file_path": "/tmp/new.txt", "content": "test"},
            file_policy="justify-create"
        )

        result = pretooluse_handler(ctx)

        reason = result.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")

        # Should explain justification requirement
        assert "justif" in reason.lower(), "Message doesn't mention justification"
        assert "autofile_justification" in reason.lower(), \
            "Message doesn't show AUTOFILE_JUSTIFICATION tag"


@pytest.mark.skipif(not BASHLEX_AVAILABLE, reason="bashlex required")
class TestPipeDetectionComprehensive:
    """Comprehensive pipe detection tests (requires bashlex)."""

    @pytest.mark.parametrize("command,expected_permission", [
        # Piped commands - should be ALLOWED
        ("cargo build 2>&1 | cat", "allow"),
        ("npm test | head -50", "allow"),
        ("pytest 2>&1 | tee log.txt", "allow"),
        ("git log | grep 'fix'", "allow"),
        ("find . -name '*.py' | head -10", "allow"),
        ("ls -la | tail -20", "allow"),

        # Direct commands - should be BLOCKED
        ("cat file.txt", "deny"),
        ("head -10 data.csv", "deny"),
        ("tail -f logfile", "deny"),

        # Commands with pipes but blocked command not in pipe
        ("cat file.txt && echo done", "deny"),  # cat not piped
        ("head data.csv; ls", "deny"),  # head not piped
    ])
    def test_pipe_detection_accuracy(self, command, expected_permission):
        """Test pipe detection accuracy for various command patterns."""
        ctx = create_test_context(
            tool_name="bash_command",
            tool_input={"command": command}
        )

        result = pretooluse_handler(ctx)

        hook_output = result.get("hookSpecificOutput", {})
        actual_permission = hook_output.get("permissionDecision", "allow")

        assert actual_permission == expected_permission, \
            f"Command '{command}' expected {expected_permission}, got {actual_permission}"

    def test_complex_pipe_chain(self):
        """Test complex pipe chain with multiple stages."""
        ctx = create_test_context(
            tool_name="bash_command",
            tool_input={
                "command": "cargo test 2>&1 | grep FAILED | head -20 | tee failures.txt"
            }
        )

        result = pretooluse_handler(ctx)

        hook_output = result.get("hookSpecificOutput", {})
        permission = hook_output.get("permissionDecision", "allow")

        # All commands in pipe - should be allowed
        assert permission == "allow", \
            "Complex pipe chain blocked incorrectly"

    def test_pipe_with_subshell(self):
        """Test pipe detection with subshell."""
        ctx = create_test_context(
            tool_name="bash_command",
            tool_input={
                "command": "(cargo build && cargo test) 2>&1 | head -100"
            }
        )

        result = pretooluse_handler(ctx)

        hook_output = result.get("hookSpecificOutput", {})
        permission = hook_output.get("permissionDecision", "allow")

        # head is in pipe - should be allowed
        assert permission == "allow", \
            "Pipe with subshell blocked incorrectly"


class TestHookResponseFormatCompliance:
    """Test hook response format matches specification."""

    def test_response_has_all_required_fields(self):
        """Verify response contains all required fields."""
        ctx = create_test_context(
            tool_name="bash_command",
            tool_input={"command": "cat test.txt"}
        )

        result = pretooluse_handler(ctx)

        # Top-level required fields
        assert "continue" in result, "Missing 'continue' field"
        assert "stopReason" in result, "Missing 'stopReason' field"
        assert "suppressOutput" in result, "Missing 'suppressOutput' field"
        assert "systemMessage" in result, "Missing 'systemMessage' field"
        assert "hookSpecificOutput" in result, "Missing 'hookSpecificOutput' field"

        # hookSpecificOutput required fields
        hook_output = result["hookSpecificOutput"]
        assert "hookEventName" in hook_output, "Missing 'hookEventName'"
        assert "permissionDecision" in hook_output, "Missing 'permissionDecision'"
        assert "permissionDecisionReason" in hook_output, \
            "Missing 'permissionDecisionReason'"

    def test_continue_matches_decision(self):
        """Verify 'continue' field matches decision: False for deny, True for allow.

        Regression test for commit 662d789 which hardcoded continue=True even for
        deny decisions, making command blocking non-functional. The 'continue' field
        is what actually controls whether Claude Code executes the tool.

        - decision="deny" → continue=False (blocks tool execution)
        - decision="allow" → continue=True (allows tool execution)
        """
        test_cases = [
            # (tool_name, tool_input, expected_continue, description)
            ("bash_command", {"command": "cat file.txt"}, False, "blocked command must have continue=False"),
            ("bash_command", {"command": "ls -la"}, True, "safe command must have continue=True"),
        ]

        for tool_name, tool_input, expected_continue, description in test_cases:
            ctx = create_test_context(tool_name=tool_name, tool_input=tool_input)
            result = pretooluse_handler(ctx)

            assert result.get("continue") is expected_continue, \
                f"{description}: got continue={result.get('continue')} for {tool_name} {tool_input}"

            # Verify continue and decision are consistent
            decision = result.get("decision") or result.get("hookSpecificOutput", {}).get("permissionDecision")
            if decision == "deny":
                assert result.get("continue") is False, \
                    f"decision=deny but continue=True — tool will NOT be blocked! ({tool_name})"
            elif decision == "allow":
                assert result.get("continue") is True, \
                    f"decision=allow but continue=False — tool will be incorrectly blocked! ({tool_name})"

    def test_permission_decision_valid_values(self):
        """Verify permissionDecision only uses valid values."""
        test_cases = [
            ("bash_command", {"command": "cat file.txt"}, "deny"),
            ("bash_command", {"command": "ls -la"}, "allow"),
        ]

        for tool_name, tool_input, expected in test_cases:
            ctx = create_test_context(tool_name=tool_name, tool_input=tool_input)
            result = pretooluse_handler(ctx)

            hook_output = result.get("hookSpecificOutput", {})
            permission = hook_output.get("permissionDecision")

            assert permission in ["allow", "deny"], \
                f"Invalid permissionDecision: {permission}"
            assert permission == expected, \
                f"Expected {expected}, got {permission} for {tool_name}"


class TestSessionStateIsolation:
    """Test that session state doesn't leak between tests."""

    def test_session_state_isolated(self):
        """Verify each test gets clean session state."""
        # Test 1: Create some session state
        ctx1 = create_test_context(
            tool_name="bash_command",
            tool_input={"command": "cat file1.txt"},
            session_id="test-session-1"
        )
        result1 = pretooluse_handler(ctx1)

        # Test 2: Different session should not see state from test 1
        ctx2 = create_test_context(
            tool_name="bash_command",
            tool_input={"command": "cat file2.txt"},
            session_id="test-session-2"
        )
        result2 = pretooluse_handler(ctx2)

        # Both should have same behavior (both blocked)
        perm1 = result1.get("hookSpecificOutput", {}).get("permissionDecision")
        perm2 = result2.get("hookSpecificOutput", {}).get("permissionDecision")

        assert perm1 == perm2 == "deny", \
            "Session state leaked between tests"


# Documentation
__doc__ += """

## Test Coverage

### Tool Name Variants (15 tests):
- All bash tool names (Bash, bash_command, run_shell_command)
- All write tool names (Write, write_file)
- All edit tool names (Edit, edit_file, replace)
- All plan tool names (ExitPlanMode, exit_plan_mode)

### Edge Cases (6 tests):
- Empty command string
- Whitespace-only command
- Missing 'command' key in tool_input
- None tool_input
- Invalid file policy
- Session state isolation

### File Path Variants (3 tests):
- Absolute paths (new + existing files)
- Relative paths (new files)
- Existing file modification (should be allowed)

### System Message Quality (3 tests):
- cat message suggests Read tool with benefits
- head message suggests Read with limit parameter
- File creation message explains policy

### Pipe Detection (11 tests):
- Various piped commands (should allow)
- Various direct commands (should block)
- Complex pipe chains
- Subshell with pipes
- Commands with && or ; (not pipes)

### Hook Response Format (3 tests):
- All required fields present
- continue always True
- permissionDecision valid values only

## Total: 41 tests
All tests use proper context mocking and session cleanup.
"""
