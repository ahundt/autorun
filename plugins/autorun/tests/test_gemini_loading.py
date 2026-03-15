#!/usr/bin/env python3
"""Test autorun loading in simulated Gemini CLI environment."""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock
import pytest

# Add plugin source to path
plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root / 'src'))
sys.path.insert(0, str(plugin_root / 'hooks'))  # For hook_entry module


class TestGeminiEnvironmentSimulation:
    """Test autorun functionality in simulated Gemini CLI environment."""

    @pytest.fixture(autouse=True)
    def setup_gemini_env(self):
        """Setup Gemini environment variables before each test."""
        # Save original values
        original_env = {
            "GEMINI_SESSION_ID": os.environ.get("GEMINI_SESSION_ID"),
            "GEMINI_PROJECT_DIR": os.environ.get("GEMINI_PROJECT_DIR"),
            "CLAUDE_SESSION_ID": os.environ.get("CLAUDE_SESSION_ID"),
            "CLAUDE_PROJECT_DIR": os.environ.get("CLAUDE_PROJECT_DIR"),
        }

        # Setup Gemini environment
        os.environ["GEMINI_SESSION_ID"] = "test-session-gemini-123"
        os.environ["GEMINI_PROJECT_DIR"] = str(Path.cwd())
        os.environ.pop("CLAUDE_SESSION_ID", None)  # Ensure Claude vars not set
        os.environ.pop("CLAUDE_PROJECT_DIR", None)

        yield  # Run test

        # Restore original environment
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_cli_detection(self):
        """Test CLI type detection correctly identifies Gemini."""
        from hook_entry import detect_cli_type

        detected_cli = detect_cli_type()
        assert detected_cli == "gemini", f"Expected 'gemini', got '{detected_cli}'"

    def test_project_dir_detection(self):
        """Test project directory detection works with GEMINI_PROJECT_DIR."""
        from hook_entry import get_project_dir

        project_dir = get_project_dir()
        expected = str(Path.cwd())
        assert project_dir == expected, f"Expected {expected}, got {project_dir}"

    def test_gemini_bash_tool_coverage(self):
        """Test Gemini bash tool names are recognized."""
        from autorun.config import BASH_TOOLS

        assert "bash_command" in BASH_TOOLS, "Gemini's bash_command not in BASH_TOOLS"
        assert "run_shell_command" in BASH_TOOLS, "Gemini's run_shell_command not in BASH_TOOLS"

    def test_gemini_write_tool_coverage(self):
        """Test Gemini write tool names are recognized."""
        from autorun.config import WRITE_TOOLS

        assert "write_file" in WRITE_TOOLS, "Gemini's write_file not in WRITE_TOOLS"

    def test_gemini_edit_tool_coverage(self):
        """Test Gemini edit tool names are recognized."""
        from autorun.config import EDIT_TOOLS

        assert "edit_file" in EDIT_TOOLS, "Gemini's edit_file not in EDIT_TOOLS"
        assert "replace" in EDIT_TOOLS, "Gemini's replace not in EDIT_TOOLS"

    def test_gemini_plan_tool_coverage(self):
        """Test Gemini plan tool names are recognized."""
        from autorun.config import PLAN_TOOLS

        assert "exit_plan_mode" in PLAN_TOOLS, "Gemini's exit_plan_mode not in PLAN_TOOLS"

    def test_gemini_task_tool_coverage(self):
        """Test Gemini task tool names are recognized."""
        from autorun.config import TASK_CREATE_TOOLS

        assert "task_create" in TASK_CREATE_TOOLS, "Gemini's task_create not in TASK_CREATE_TOOLS"

    def test_hook_accepts_gemini_bash_tool(self):
        """Test check_blocked_commands accepts Gemini's bash_command tool (daemon path)."""
        from autorun.core import EventContext, ThreadSafeDB
        from autorun import plugins as _plugins

        # Simulate Gemini BeforeTool event with bash_command — echo is safe, should not be blocked
        ctx = EventContext(
            session_id="test-gemini-bash",
            event="PreToolUse",
            tool_name="bash_command",  # Gemini's bash tool name
            tool_input={"command": "echo test"},
            store=ThreadSafeDB(),
        )

        result = _plugins.check_blocked_commands(ctx)
        # echo is a safe command — should return None (no block) or an allow response
        assert result is None or isinstance(result, dict), \
            f"Unexpected result type: {type(result)}"
        if result is not None:
            perm = result.get("hookSpecificOutput", {}).get("permissionDecision", "allow")
            assert perm == "allow", f"echo should be allowed but got: {perm}"

    def test_bashlex_available(self):
        """Test bashlex dependency is available (skip if not installed)."""
        from autorun.command_detection import BASHLEX_AVAILABLE

        if not BASHLEX_AVAILABLE:
            pytest.skip("bashlex not installed - install with: uv pip install bashlex")

        assert BASHLEX_AVAILABLE, "bashlex check passed"

    def test_pipe_detection_with_head(self):
        """Test command_matches_pattern detects head in piped commands."""
        from autorun.command_detection import command_matches_pattern, BASHLEX_AVAILABLE

        if not BASHLEX_AVAILABLE:
            pytest.skip("bashlex not available")

        piped_cmd = "cargo build 2>&1 | head -50"
        matches_head = command_matches_pattern(piped_cmd, "head")
        assert matches_head, f"Failed to detect 'head' in piped command: {piped_cmd}"


class TestGeminiHookResponseFormat:
    """Verify hook responses use Gemini-compatible fields.

    Gemini CLI reads top-level 'decision' field for permission control.
    References:
        - Gemini hooks API: https://geminicli.com/docs/hooks/reference/
        - Hook support in extensions: https://github.com/google-gemini/gemini-cli/issues/14449
    """

    @pytest.fixture(autouse=True)
    def setup_gemini_env(self):
        original = {
            "GEMINI_SESSION_ID": os.environ.get("GEMINI_SESSION_ID"),
            "GEMINI_PROJECT_DIR": os.environ.get("GEMINI_PROJECT_DIR"),
        }
        os.environ["GEMINI_SESSION_ID"] = "test-gemini-hook-resp"
        os.environ["GEMINI_PROJECT_DIR"] = str(Path.cwd())
        os.environ.pop("CLAUDE_SESSION_ID", None)
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        yield
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_deny_response_has_decision_field(self):
        """Gemini deny response must include top-level 'decision: deny' field."""
        from autorun.core import EventContext, ThreadSafeDB
        from autorun import plugins as _plugins

        ctx = EventContext(
            session_id="test-gemini-deny",
            event="PreToolUse",
            tool_name="run_shell_command",
            tool_input={"command": "rm -rf /"},
            store=ThreadSafeDB(),
            cli_type="gemini",
        )
        result = _plugins.check_blocked_commands(ctx)
        assert result is not None, "rm -rf should be blocked"
        assert result.get("decision") == "deny", \
            f"Gemini deny must have top-level decision field, got: {result.get('decision')}"

    def test_deny_response_has_reason_field(self):
        """Gemini deny must include 'reason' field with block message."""
        from autorun.core import EventContext, ThreadSafeDB
        from autorun import plugins as _plugins

        ctx = EventContext(
            session_id="test-gemini-reason",
            event="PreToolUse",
            tool_name="run_shell_command",
            tool_input={"command": "rm file.txt"},
            store=ThreadSafeDB(),
            cli_type="gemini",
        )
        result = _plugins.check_blocked_commands(ctx)
        assert result is not None
        reason = result.get("reason", "")
        assert "trash" in reason.lower(), f"Should suggest 'trash' alternative, got: {reason}"

    def test_allow_returns_none(self):
        """Safe commands should return None (no hook interference)."""
        from autorun.core import EventContext, ThreadSafeDB
        from autorun import plugins as _plugins

        ctx = EventContext(
            session_id="test-gemini-allow",
            event="PreToolUse",
            tool_name="run_shell_command",
            tool_input={"command": "echo hello"},
            store=ThreadSafeDB(),
            cli_type="gemini",
        )
        result = _plugins.check_blocked_commands(ctx)
        assert result is None, f"echo should not trigger hook, got: {result}"

    def test_response_validated_for_gemini_schema(self):
        """validate_hook_response must preserve Gemini fields (decision, reason)."""
        from autorun.core import validate_hook_response

        response = {
            "decision": "deny",
            "reason": "Blocked",
            "continue": True,
            "systemMessage": "test",
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Blocked",
            },
        }
        validated = validate_hook_response("PreToolUse", response, cli_type="gemini")
        assert validated.get("decision") == "deny"
        assert validated.get("reason") == "Blocked"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
