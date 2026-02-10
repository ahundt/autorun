#!/usr/bin/env python3
"""Verify actual command blocking logic for both Claude Code and Gemini CLI.

Tests the pretooluse_handler function directly using real EventContext to ensure:
1. Commands are actually blocked (permissionDecision='deny')
2. Piped commands are allowed (bashlex detects pipe context)
3. File creation is blocked in justify mode
4. Hook response format works for both Claude Code and Gemini CLI
5. Real EventContext property access matches production behavior

NO COST - Direct Python function calls, no API usage.
"""
import os
import sys
from pathlib import Path

import pytest

# Add plugin source to path
plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root / "src"))

from clautorun.main import pretooluse_handler
from clautorun.core import EventContext
from clautorun.command_detection import BASHLEX_AVAILABLE
from clautorun.config import BASH_TOOLS, WRITE_TOOLS
from clautorun.session_manager import session_state


@pytest.fixture(autouse=True)
def setup_test_environment():
    """Set up test environment variables for both Claude and Gemini."""
    # Set Gemini environment (also works for Claude)
    os.environ["GEMINI_SESSION_ID"] = "test-blocking-session"
    os.environ["GEMINI_PROJECT_DIR"] = str(Path.cwd())
    yield
    # Cleanup
    os.environ.pop("GEMINI_SESSION_ID", None)
    os.environ.pop("GEMINI_PROJECT_DIR", None)


class TestActualCommandBlocking:
    """Test that dangerous commands are ACTUALLY blocked by hooks.

    These tests verify the blocking logic works correctly for both
    Claude Code (PreToolUse) and Gemini CLI (BeforeTool).

    Hook Response Format (universal for both platforms):
    {
        "continue": true,  # Let conversation continue
        "systemMessage": "blocking message...",
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",  # Actually blocks execution
            "permissionDecisionReason": "detailed reason..."
        }
    }
    """

    def test_cat_command_blocked(self):
        """Verify cat command is blocked with permissionDecision='deny'."""
        ctx = EventContext(
            session_id="test-session",
            event="PreToolUse",
            tool_name="bash_command",  # Gemini's bash tool
            tool_input={"command": "cat /etc/hosts"},
        )

        result = pretooluse_handler(ctx)

        # Check hook response format
        assert "hookSpecificOutput" in result, "Missing hookSpecificOutput"
        hook_output = result["hookSpecificOutput"]
        permission_decision = hook_output.get("permissionDecision", "allow")

        assert permission_decision == "deny", \
            f"cat command not blocked! permissionDecision={permission_decision}"

        # Verify blocking message provided
        reason = hook_output.get("permissionDecisionReason", "")
        assert "cat" in reason.lower(), "Blocking reason doesn't mention cat"
        assert "read" in reason.lower(), "Blocking reason doesn't suggest Read tool"

        # Verify continue=True (conversation continues)
        assert result.get("continue") is True, \
            "continue should be True to allow conversation"

    def test_head_command_blocked(self):
        """Verify head command is blocked with permissionDecision='deny'."""
        ctx = EventContext(
            session_id="test-session",
            event="PreToolUse",
            tool_name="bash_command",
            tool_input={"command": "head -10 file.txt"},
        )

        result = pretooluse_handler(ctx)

        hook_output = result.get("hookSpecificOutput", {})
        permission_decision = hook_output.get("permissionDecision", "allow")

        assert permission_decision == "deny", \
            f"head command not blocked! permissionDecision={permission_decision}"

        reason = hook_output.get("permissionDecisionReason", "")
        assert "head" in reason.lower(), "Blocking reason doesn't mention head"

    @pytest.mark.skipif(not BASHLEX_AVAILABLE, reason="bashlex required for pipe detection")
    def test_piped_cat_allowed(self):
        """Verify piped cat is ALLOWED (bashlex detects pipe context)."""
        ctx = EventContext(
            session_id="test-session",
            event="PreToolUse",
            tool_name="bash_command",
            tool_input={"command": "cargo build 2>&1 | cat"},
        )

        result = pretooluse_handler(ctx)

        hook_output = result.get("hookSpecificOutput", {})
        permission_decision = hook_output.get("permissionDecision", "allow")

        assert permission_decision == "allow", \
            f"Piped cat was blocked! permissionDecision={permission_decision}"

    @pytest.mark.skipif(not BASHLEX_AVAILABLE, reason="bashlex required for pipe detection")
    def test_piped_head_allowed(self):
        """Verify piped head is ALLOWED (bashlex detects pipe context)."""
        ctx = EventContext(
            session_id="test-session",
            event="PreToolUse",
            tool_name="bash_command",
            tool_input={"command": "cargo build 2>&1 | head -50"},
        )

        result = pretooluse_handler(ctx)

        hook_output = result.get("hookSpecificOutput", {})
        permission_decision = hook_output.get("permissionDecision", "allow")

        assert permission_decision == "allow", \
            f"Piped head was blocked! permissionDecision={permission_decision}"

    def test_file_creation_blocked_in_justify_mode(self):
        """Verify file creation is blocked when policy is justify-create."""
        session_id = "test-session"

        # Initialize session state with file_policy (handler reads from state)
        with session_state(session_id) as state:
            state["file_policy"] = "JUSTIFY"

        ctx = EventContext(
            session_id=session_id,
            event="PreToolUse",
            tool_name="write_file",  # Gemini's write tool
            tool_input={
                "file_path": "/tmp/test_new_file.txt",
                "content": "test"
            },
        )

        result = pretooluse_handler(ctx)

        hook_output = result.get("hookSpecificOutput", {})
        permission_decision = hook_output.get("permissionDecision", "allow")

        assert permission_decision == "deny", \
            f"File creation not blocked! permissionDecision={permission_decision}"

        reason = hook_output.get("permissionDecisionReason", "")
        assert "justif" in reason.lower(), \
            "Blocking reason doesn't mention justification requirement"

    def test_bashlex_availability(self):
        """Verify bashlex is installed and working."""
        from clautorun.command_detection import BASHLEX_AVAILABLE

        assert BASHLEX_AVAILABLE, \
            "bashlex not available! Install with: uv pip install bashlex"

    def test_tool_name_coverage_bash(self):
        """Verify bash tool names are recognized (Claude + Gemini)."""
        # Claude Code tool name
        assert "Bash" in BASH_TOOLS, "Claude's Bash tool not in BASH_TOOLS"

        # Gemini CLI tool names
        assert "bash_command" in BASH_TOOLS, "Gemini's bash_command not in BASH_TOOLS"
        assert "run_shell_command" in BASH_TOOLS, \
            "Gemini's run_shell_command not in BASH_TOOLS"

    def test_tool_name_coverage_write(self):
        """Verify write tool names are recognized (Claude + Gemini)."""
        # Claude Code tool name
        assert "Write" in WRITE_TOOLS, "Claude's Write tool not in WRITE_TOOLS"

        # Gemini CLI tool name
        assert "write_file" in WRITE_TOOLS, "Gemini's write_file not in WRITE_TOOLS"

    def test_hook_response_format_universal(self):
        """Verify hook response format works for both Claude and Gemini."""
        ctx = EventContext(
            session_id="test-session",
            event="PreToolUse",
            tool_name="bash_command",
            tool_input={"command": "cat test.txt"},
        )

        result = pretooluse_handler(ctx)

        # Required fields for both platforms
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

        # Verify permissionDecision is valid
        assert hook_output["permissionDecision"] in ["allow", "deny"], \
            f"Invalid permissionDecision: {hook_output['permissionDecision']}"


class TestPipeDetectionRobustness:
    """Test pipe detection handles edge cases correctly."""

    @pytest.mark.skipif(not BASHLEX_AVAILABLE, reason="bashlex required")
    def test_pipe_with_stderr_redirect(self):
        """Test pipe detection with stderr redirect (2>&1 | command)."""
        ctx = EventContext(
            session_id="test-session",
            event="PreToolUse",
            tool_name="bash_command",
            tool_input={"command": "npm test 2>&1 | head -100"},
        )

        result = pretooluse_handler(ctx)

        hook_output = result.get("hookSpecificOutput", {})
        permission_decision = hook_output.get("permissionDecision", "allow")

        assert permission_decision == "allow", \
            "Piped command with stderr redirect was blocked"

    @pytest.mark.skipif(not BASHLEX_AVAILABLE, reason="bashlex required")
    def test_pipe_with_tee(self):
        """Test pipe with tee command (common logging pattern)."""
        ctx = EventContext(
            session_id="test-session",
            event="PreToolUse",
            tool_name="bash_command",
            tool_input={"command": "pytest 2>&1 | tee log.txt"},
        )

        result = pretooluse_handler(ctx)

        hook_output = result.get("hookSpecificOutput", {})
        permission_decision = hook_output.get("permissionDecision", "allow")

        assert permission_decision == "allow", \
            "Piped command with tee was blocked"

    def test_direct_cat_blocked_without_pipe(self):
        """Verify direct cat is still blocked even if bashlex unavailable."""
        ctx = EventContext(
            session_id="test-session",
            event="PreToolUse",
            tool_name="bash_command",
            tool_input={"command": "cat myfile.txt"},
        )

        result = pretooluse_handler(ctx)

        hook_output = result.get("hookSpecificOutput", {})
        permission_decision = hook_output.get("permissionDecision", "allow")

        assert permission_decision == "deny", \
            "Direct cat command was not blocked"


class TestGeminiPayloadNormalization:
    """Test normalize_hook_payload handles Gemini CLI format correctly."""

    def test_gemini_event_name_mapping(self):
        """Verify BeforeTool maps to PreToolUse."""
        from clautorun.core import normalize_hook_payload
        result = normalize_hook_payload({"type": "BeforeTool"})
        assert result["hook_event_name"] == "PreToolUse"

    def test_gemini_aftertool_mapping(self):
        """Verify AfterTool maps to PostToolUse."""
        from clautorun.core import normalize_hook_payload
        result = normalize_hook_payload({"type": "AfterTool"})
        assert result["hook_event_name"] == "PostToolUse"

    def test_gemini_beforeagent_mapping(self):
        """Verify BeforeAgent maps to UserPromptSubmit."""
        from clautorun.core import normalize_hook_payload
        result = normalize_hook_payload({"type": "BeforeAgent"})
        assert result["hook_event_name"] == "UserPromptSubmit"

    def test_gemini_camelcase_keys(self):
        """Verify Gemini camelCase keys are normalized to snake_case."""
        from clautorun.core import normalize_hook_payload
        payload = {
            "type": "BeforeTool",
            "toolName": "bash_command",
            "toolInput": {"command": "cat file.txt"},
            "sessionId": "gemini-session-123",
        }
        result = normalize_hook_payload(payload)
        assert result["hook_event_name"] == "PreToolUse"
        assert result["tool_name"] == "bash_command"
        assert result["tool_input"] == {"command": "cat file.txt"}
        assert result["session_id"] == "gemini-session-123"

    def test_claude_format_passthrough(self):
        """Verify Claude Code format passes through unchanged."""
        from clautorun.core import normalize_hook_payload
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "session_id": "claude-session",
        }
        result = normalize_hook_payload(payload)
        assert result["hook_event_name"] == "PreToolUse"
        assert result["tool_name"] == "Bash"
        assert result["tool_input"] == {"command": "ls"}
        assert result["session_id"] == "claude-session"

    def test_gemini_cat_blocked_through_normalization(self):
        """End-to-end: Gemini format cat command blocked via pretooluse_handler."""
        from clautorun.core import normalize_hook_payload
        payload = {
            "type": "BeforeTool",
            "toolName": "bash_command",
            "toolInput": {"command": "cat /etc/hosts"},
            "sessionId": "test-normalization",
        }
        normalized = normalize_hook_payload(payload)

        ctx = EventContext(
            session_id=normalized["session_id"],
            event=normalized["hook_event_name"],
            tool_name=normalized["tool_name"],
            tool_input=normalized["tool_input"],
        )

        result = pretooluse_handler(ctx)

        assert "hookSpecificOutput" in result, \
            "Missing hookSpecificOutput - normalization failed"
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny", \
            "cat command not blocked after Gemini format normalization"

    def test_gemini_head_blocked_through_normalization(self):
        """End-to-end: Gemini format head command blocked."""
        from clautorun.core import normalize_hook_payload
        payload = {
            "type": "BeforeTool",
            "toolName": "bash_command",
            "toolInput": {"command": "head -10 file.txt"},
            "sessionId": "test-normalization",
        }
        normalized = normalize_hook_payload(payload)

        ctx = EventContext(
            session_id=normalized["session_id"],
            event=normalized["hook_event_name"],
            tool_name=normalized["tool_name"],
            tool_input=normalized["tool_input"],
        )

        result = pretooluse_handler(ctx)

        assert "hookSpecificOutput" in result
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny", \
            "head command not blocked after Gemini format normalization"

    def test_gemini_safe_command_allowed(self):
        """End-to-end: Gemini format safe command allowed."""
        from clautorun.core import normalize_hook_payload
        payload = {
            "type": "BeforeTool",
            "toolName": "bash_command",
            "toolInput": {"command": "ls -la"},
            "sessionId": "test-normalization",
        }
        normalized = normalize_hook_payload(payload)

        ctx = EventContext(
            session_id=normalized["session_id"],
            event=normalized["hook_event_name"],
            tool_name=normalized["tool_name"],
            tool_input=normalized["tool_input"],
        )

        result = pretooluse_handler(ctx)

        hook_output = result.get("hookSpecificOutput", {})
        permission = hook_output.get("permissionDecision", "allow")
        assert permission == "allow", \
            f"Safe ls command was blocked! permissionDecision={permission}"


# Documentation
__doc__ += """

## Test Coverage

### Command Blocking (8 tests):
1. test_cat_command_blocked - Verifies cat is blocked
2. test_head_command_blocked - Verifies head is blocked
3. test_piped_cat_allowed - Verifies pipe detection works
4. test_piped_head_allowed - Verifies pipe detection works
5. test_pipe_with_stderr_redirect - Edge case testing
6. test_pipe_with_tee - Common logging pattern
7. test_direct_cat_blocked_without_pipe - Fallback behavior
8. test_file_creation_blocked_in_justify_mode - File policy enforcement

### Infrastructure (4 tests):
1. test_bashlex_availability - Verifies bashlex installed
2. test_tool_name_coverage_bash - Verifies Claude + Gemini bash tools
3. test_tool_name_coverage_write - Verifies Claude + Gemini write tools
4. test_hook_response_format_universal - Verifies response format

## Running Tests

```bash
# Run all blocking tests
uv run pytest plugins/clautorun/tests/test_actual_command_blocking.py -v

# Run only bashlex-dependent tests
uv run pytest plugins/clautorun/tests/test_actual_command_blocking.py -k pipe -v

# Run without bashlex (some tests will be skipped)
uv run pytest plugins/clautorun/tests/test_actual_command_blocking.py -v
```

## Hook Response Format

The hook response format is UNIVERSAL for both Claude Code and Gemini CLI:

```python
{
    "continue": True,  # Always True - let conversation continue
    "stopReason": "",
    "suppressOutput": False,
    "systemMessage": "blocking message shown to user",
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",  # Same for both platforms
        "permissionDecision": "deny",  # or "allow"
        "permissionDecisionReason": "detailed explanation"
    }
}
```

**How it works:**
- Claude Code: Fires PreToolUse event, reads permissionDecision
- Gemini CLI: Fires BeforeTool event, reads permissionDecision
- **Same Python code** works for both!

## Graceful Fallback

If bashlex is not installed:
- Pipe detection tests are SKIPPED
- Direct blocking still works (cat, head, tail blocked)
- Fallback to simple command parsing
- Tests document requirement with @pytest.mark.skipif
"""
