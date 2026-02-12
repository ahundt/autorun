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

        # Verify continue=False when decision is deny (blocks tool execution)
        assert result.get("continue") is False, \
            "continue should be False when permissionDecision is deny"

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
        """Verify hook response format works for both Claude Code and Gemini CLI.

        Claude Code reads: hookSpecificOutput.permissionDecision
        Gemini CLI reads: top-level decision field
        Both must be present and consistent.

        References:
        - Claude Code: https://code.claude.com/docs/en/hooks#pretooluse-decision-control
        - Gemini CLI: https://geminicli.com/docs/hooks/reference/
        """
        ctx = EventContext(
            session_id="test-session",
            event="PreToolUse",
            tool_name="bash_command",
            tool_input={"command": "cat test.txt"},
        )

        result = pretooluse_handler(ctx)

        # === Universal fields (both platforms) ===
        assert "continue" in result, "Missing 'continue' field"
        assert "stopReason" in result, "Missing 'stopReason' field"
        assert "suppressOutput" in result, "Missing 'suppressOutput' field"
        assert "systemMessage" in result, "Missing 'systemMessage' field"

        # === Gemini CLI format: top-level decision (required for Gemini blocking) ===
        assert "decision" in result, \
            "Missing top-level 'decision' field (required by Gemini CLI)"
        assert "reason" in result, \
            "Missing top-level 'reason' field (required by Gemini CLI)"
        assert result["decision"] in ["allow", "deny"], \
            f"Invalid top-level decision: {result['decision']}"

        # === Claude Code format: hookSpecificOutput.permissionDecision ===
        assert "hookSpecificOutput" in result, "Missing 'hookSpecificOutput' field"
        hook_output = result["hookSpecificOutput"]
        assert "hookEventName" in hook_output, "Missing 'hookEventName'"
        assert "permissionDecision" in hook_output, "Missing 'permissionDecision'"
        assert "permissionDecisionReason" in hook_output, \
            "Missing 'permissionDecisionReason'"
        assert hook_output["permissionDecision"] in ["allow", "deny"], \
            f"Invalid permissionDecision: {hook_output['permissionDecision']}"

        # === Cross-platform consistency: both formats must agree ===
        assert result["decision"] == hook_output["permissionDecision"], \
            f"Format mismatch! top-level decision={result['decision']} " \
            f"vs hookSpecificOutput.permissionDecision={hook_output['permissionDecision']}"

    def test_gemini_top_level_decision_deny(self):
        """Verify blocked commands include top-level decision='deny' for Gemini CLI.

        Gemini CLI reads 'decision' at the top level (not inside hookSpecificOutput).
        Without this field, Gemini CLI will allow all commands regardless of our response.
        See: https://geminicli.com/docs/hooks/reference/
        """
        ctx = EventContext(
            session_id="test-session",
            event="PreToolUse",
            tool_name="bash_command",
            tool_input={"command": "cat /etc/hosts"},
        )

        result = pretooluse_handler(ctx)

        # Top-level decision MUST be "deny" for Gemini CLI to actually block
        assert result.get("decision") == "deny", \
            f"Top-level decision is '{result.get('decision')}', not 'deny'. " \
            "Gemini CLI will NOT block this command!"
        assert result.get("reason"), \
            "Top-level reason is empty. Gemini CLI uses this as tool error feedback."

    def test_gemini_top_level_decision_allow(self):
        """Verify safe commands include top-level decision='allow' for Gemini CLI."""
        ctx = EventContext(
            session_id="test-session",
            event="PreToolUse",
            tool_name="bash_command",
            tool_input={"command": "ls -la"},
        )

        result = pretooluse_handler(ctx)

        assert result.get("decision") == "allow", \
            f"Safe command has top-level decision='{result.get('decision')}', expected 'allow'"


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
        """End-to-end: Gemini camelCase format cat command blocked."""
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

        # Claude Code format
        assert "hookSpecificOutput" in result, \
            "Missing hookSpecificOutput - normalization failed"
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny", \
            "cat command not blocked after Gemini format normalization"
        # Gemini CLI format (top-level decision)
        assert result.get("decision") == "deny", \
            "cat command: top-level decision not 'deny' (Gemini CLI won't block)"

    def test_gemini_head_blocked_through_normalization(self):
        """End-to-end: Gemini camelCase format head command blocked."""
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

        assert result["hookSpecificOutput"]["permissionDecision"] == "deny", \
            "head command not blocked after Gemini format normalization"
        assert result.get("decision") == "deny", \
            "head command: top-level decision not 'deny' (Gemini CLI won't block)"

    def test_gemini_safe_command_allowed(self):
        """End-to-end: Gemini camelCase format safe command allowed."""
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

        assert result.get("decision") == "allow", \
            f"Safe ls command was blocked! decision={result.get('decision')}"
        hook_output = result.get("hookSpecificOutput", {})
        assert hook_output.get("permissionDecision") == "allow", \
            f"Safe ls command was blocked! permissionDecision={hook_output.get('permissionDecision')}"


class TestGeminiOfficialSnakeCaseFormat:
    """Test the official Gemini CLI v0.26+ snake_case hook input format.

    Per https://geminicli.com/docs/hooks/reference/, Gemini CLI sends:
    - hook_event_name: "BeforeTool" (snake_case key, PascalCase value)
    - tool_name: "bash_command" or "run_shell_command"
    - tool_input: {"command": "..."}
    - session_id: "..."
    - cwd: "..."
    - transcript_path: "..."

    And expects the response to include:
    - decision: "deny" at the TOP LEVEL (not inside hookSpecificOutput)
    - reason: "..." at the TOP LEVEL
    """

    def test_official_format_cat_blocked(self):
        """Verify cat is blocked with official Gemini CLI snake_case input."""
        from clautorun.core import normalize_hook_payload
        payload = {
            "hook_event_name": "BeforeTool",
            "tool_name": "bash_command",
            "tool_input": {"command": "cat /etc/hosts"},
            "session_id": "gemini-official-test",
            "cwd": "/tmp",
            "transcript_path": "/tmp/transcript.jsonl",
        }
        normalized = normalize_hook_payload(payload)

        ctx = EventContext(
            session_id=normalized["session_id"],
            event=normalized["hook_event_name"],
            tool_name=normalized["tool_name"],
            tool_input=normalized["tool_input"],
        )

        result = pretooluse_handler(ctx)

        # Gemini CLI reads top-level decision
        assert result.get("decision") == "deny", \
            f"Gemini official format: top-level decision={result.get('decision')}, expected 'deny'"
        assert result.get("reason"), \
            "Gemini official format: top-level reason is empty"

    def test_official_format_run_shell_command_blocked(self):
        """Verify run_shell_command (Gemini's other bash tool) blocks cat."""
        from clautorun.core import normalize_hook_payload
        payload = {
            "hook_event_name": "BeforeTool",
            "tool_name": "run_shell_command",
            "tool_input": {"command": "cat /etc/passwd"},
            "session_id": "gemini-official-test",
        }
        normalized = normalize_hook_payload(payload)

        ctx = EventContext(
            session_id=normalized["session_id"],
            event=normalized["hook_event_name"],
            tool_name=normalized["tool_name"],
            tool_input=normalized["tool_input"],
        )

        result = pretooluse_handler(ctx)

        assert result.get("decision") == "deny", \
            f"run_shell_command with cat not blocked! decision={result.get('decision')}"

    def test_official_format_safe_command_allowed(self):
        """Verify safe commands produce decision='allow' with official format."""
        from clautorun.core import normalize_hook_payload
        payload = {
            "hook_event_name": "BeforeTool",
            "tool_name": "bash_command",
            "tool_input": {"command": "git status"},
            "session_id": "gemini-official-test",
        }
        normalized = normalize_hook_payload(payload)

        ctx = EventContext(
            session_id=normalized["session_id"],
            event=normalized["hook_event_name"],
            tool_name=normalized["tool_name"],
            tool_input=normalized["tool_input"],
        )

        result = pretooluse_handler(ctx)

        assert result.get("decision") == "allow", \
            f"Safe command blocked! decision={result.get('decision')}"

    def test_official_format_write_file_blocked_justify(self):
        """Verify write_file is blocked in JUSTIFY mode with official format."""
        from clautorun.core import normalize_hook_payload
        session_id = "gemini-justify-test"

        with session_state(session_id) as state:
            state["file_policy"] = "JUSTIFY"

        payload = {
            "hook_event_name": "BeforeTool",
            "tool_name": "write_file",
            "tool_input": {"file_path": "/tmp/new_file.txt", "content": "test"},
            "session_id": session_id,
        }
        normalized = normalize_hook_payload(payload)

        ctx = EventContext(
            session_id=normalized["session_id"],
            event=normalized["hook_event_name"],
            tool_name=normalized["tool_name"],
            tool_input=normalized["tool_input"],
        )

        result = pretooluse_handler(ctx)

        assert result.get("decision") == "deny", \
            f"write_file not blocked in JUSTIFY mode! decision={result.get('decision')}"

    def test_event_name_preserved_in_normalization(self):
        """Verify BeforeTool maps to PreToolUse even with hook_event_name key."""
        from clautorun.core import normalize_hook_payload
        # This is the exact format Gemini CLI sends per official docs
        payload = {"hook_event_name": "BeforeTool"}
        result = normalize_hook_payload(payload)
        assert result["hook_event_name"] == "PreToolUse", \
            f"BeforeTool not mapped to PreToolUse: got {result['hook_event_name']}"


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

The hook response format supports BOTH Claude Code and Gemini CLI:

```python
{
    # Top-level fields for Gemini CLI (reads 'decision' at top level)
    "decision": "deny",  # or "allow" - Gemini CLI reads THIS
    "reason": "detailed explanation",  # Gemini CLI reads THIS
    # Universal fields
    "continue": True,  # Always True - let conversation continue
    "stopReason": "",
    "suppressOutput": False,
    "systemMessage": "blocking message shown to user",
    # Claude Code reads hookSpecificOutput.permissionDecision
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
