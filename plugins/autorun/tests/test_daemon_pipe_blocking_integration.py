#!/usr/bin/env python3
"""Integration tests for daemon pipe blocking bug.

Tests the COMPLETE pathway from command -> daemon -> integration check -> block/allow decision.
This reproduces the actual bug where pipes are being blocked.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add src to path
plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root / 'src'))

from autorun.integrations import _not_in_pipe
from autorun.command_detection import command_matches_pattern, BASHLEX_AVAILABLE
from autorun.config import CONFIG
from autorun.core import EventContext, ThreadSafeDB
from autorun import plugins


# Daemon-path helper — replaces deleted should_block_command
def _check_block(session_id, cmd):
    """Returns deny result if command is blocked, None otherwise."""
    if not cmd or not cmd.strip():
        return None
    ctx = EventContext(
        session_id=session_id, event="PreToolUse", tool_name="Bash",
        tool_input={"command": cmd}, store=ThreadSafeDB(),
    )
    result = plugins.check_blocked_commands(ctx)
    if result is None:
        return None
    perm = result.get("hookSpecificOutput", {}).get("permissionDecision", "allow")
    return result if perm == "deny" else None


class TestDaemonPipeBlockingIntegration:
    """Test complete integration pathway for pipe blocking."""

    def test_bashlex_is_available(self):
        """CRITICAL: Verify bashlex is installed."""
        assert BASHLEX_AVAILABLE, (
            "bashlex MUST be installed for tests. "
            "Run: python3 -m pip install bashlex"
        )

    def test_config_has_pipe_predicate(self):
        """Verify CONFIG has head/tail/grep/cat with _not_in_pipe predicate."""
        default_integrations = CONFIG.get('default_integrations', {})

        # Check head/tail/grep/cat integrations exist
        assert 'head' in default_integrations, "head integration missing from CONFIG"
        assert 'tail' in default_integrations, "tail integration missing from CONFIG"
        assert 'grep' in default_integrations, "grep integration missing from CONFIG"
        assert 'cat' in default_integrations, "cat integration missing from CONFIG"

        # Verify they use _not_in_pipe predicate
        assert default_integrations['head'].get('when') == '_not_in_pipe', \
            f"head integration has wrong when: {default_integrations['head'].get('when')}"
        assert default_integrations['tail'].get('when') == '_not_in_pipe', \
            f"tail integration has wrong when: {default_integrations['tail'].get('when')}"
        assert default_integrations['grep'].get('when') == '_not_in_pipe', \
            f"grep integration has wrong when: {default_integrations['grep'].get('when')}"
        assert default_integrations['cat'].get('when') == '_not_in_pipe', \
            f"cat integration has wrong when: {default_integrations['cat'].get('when')}"

    def test_command_matches_pattern_with_pipes(self):
        """Test command_matches_pattern correctly identifies commands in pipes."""
        # Should MATCH (grep is actual command)
        assert command_matches_pattern("git log | grep fix", "grep") == True
        assert command_matches_pattern("ps aux | grep python", "grep") == True
        assert command_matches_pattern("git diff | head -50", "head") == True
        assert command_matches_pattern("cargo build 2>&1 | tail -100", "tail") == True

        # Should NOT match (not the actual command being checked)
        assert command_matches_pattern("git log | grep fix", "head") == False
        assert command_matches_pattern("git diff | head -50", "grep") == False

    def test_not_in_pipe_predicate_with_pipes(self):
        """Test _not_in_pipe() predicate allows commands in pipes."""
        def make_ctx(cmd):
            ctx = MagicMock()
            ctx.tool_input = {'command': cmd}
            return ctx

        # Commands in pipes should return False (ALLOW)
        pipe_commands = [
            "git log | head -50",
            "git diff | tail -30",
            "ps aux | grep python",
            "cargo build 2>&1 | head -50",
            "gemini extensions list | grep -A 2 -B 2 autorun || echo 'Not found'",
        ]

        for cmd in pipe_commands:
            result = _not_in_pipe(make_ctx(cmd))
            assert result == False, (
                f"Pipe command should be ALLOWED (return False): {cmd}\n"
                f"_not_in_pipe() returned {result}"
            )

    def test_not_in_pipe_predicate_without_pipes(self):
        """Test _not_in_pipe() predicate blocks direct file operations."""
        def make_ctx(cmd):
            ctx = MagicMock()
            ctx.tool_input = {'command': cmd}
            return ctx

        # Direct commands should return True (BLOCK)
        direct_commands = [
            "head file.txt",
            "tail -100 /var/log/system.log",
            "grep pattern file.py",
            "cat README.md",
        ]

        for cmd in direct_commands:
            result = _not_in_pipe(make_ctx(cmd))
            assert result == True, (
                f"Direct command should be BLOCKED (return True): {cmd}\n"
                f"_not_in_pipe() returned {result}"
            )

    def test_should_block_command_pipes_allowed(self):
        """Test _check_block() allows pipes (end-to-end)."""
        # These should NOT be blocked
        pipe_commands = [
            "git log | head -50",
            "git diff | tail -30",
            "ps aux | grep python",
            "cargo build 2>&1 | head -50",
            "gemini extensions list | grep -A 2 -B 2 autorun || echo 'Not found'",
        ]

        for cmd in pipe_commands:
            result = _check_block("test-session", cmd)
            # result should be None (not blocked)
            assert result is None, (
                f"Pipe command should be ALLOWED: {cmd}\n"
                f"_check_block() returned: {result}"
            )

    def test_should_block_command_direct_blocked(self):
        """Test _check_block() blocks direct file operations."""
        # These SHOULD be blocked
        direct_commands = [
            "head file.txt",
            "tail -100 /var/log/system.log",
            "grep pattern file.py",
            "cat README.md",
        ]

        for cmd in direct_commands:
            result = _check_block("test-session", cmd)
            # result should be a dict with block info when blocked
            assert result is not None, (
                f"Direct command should be BLOCKED: {cmd}\n"
                f"_check_block() returned: {result}"
            )

    def test_logical_operators_with_pipes(self):
        """Test || and && operators don't break pipe detection."""
        def make_ctx(cmd):
            ctx = MagicMock()
            ctx.tool_input = {'command': cmd}
            return ctx

        # Commands with logical operators should still detect pipes
        logical_pipe_commands = [
            "git log | grep fix || echo 'not found'",
            "cat file.txt | grep pattern && echo 'found'",
            "gemini extensions list | grep -A 2 -B 2 autorun || echo 'No autorun'",
            "ps aux | grep python && kill -9 $(pidof python) || echo 'not running'",
        ]

        for cmd in logical_pipe_commands:
            result = _not_in_pipe(make_ctx(cmd))
            assert result == False, (
                f"Pipe with logical operator should be ALLOWED: {cmd}\n"
                f"_not_in_pipe() returned {result}"
            )

    def test_heredoc_doesnt_cause_false_positives(self):
        """Test heredocs with command names don't cause false matches."""
        heredoc_cmd = """python3 << 'EOF'
pattern = "grep"
result = "head -50"
EOF"""

        # Should NOT match grep or head (they're in heredoc content)
        assert command_matches_pattern(heredoc_cmd, "grep") == False, (
            "grep in heredoc content should NOT match"
        )
        assert command_matches_pattern(heredoc_cmd, "head") == False, (
            "head in heredoc content should NOT match"
        )

        # Should match python3 (actual command)
        assert command_matches_pattern(heredoc_cmd, "python3") == True

    def test_real_world_blocked_command(self):
        """Reproduce the actual user-reported bug."""
        # This is the EXACT command user reported as blocked
        blocked_cmd = "gemini extensions list | grep -A 2 -B 2 autorun || echo 'No autorun found'"

        def make_ctx(cmd):
            ctx = MagicMock()
            ctx.tool_input = {'command': cmd}
            ctx.tool_name = 'Bash'
            return ctx

        # Test _not_in_pipe predicate
        ctx = make_ctx(blocked_cmd)
        predicate_result = _not_in_pipe(ctx)
        assert predicate_result == False, (
            f"User command should be ALLOWED by _not_in_pipe():\n"
            f"  Command: {blocked_cmd}\n"
            f"  _not_in_pipe() returned: {predicate_result} (should be False)"
        )

        # Test full integration
        integration_result = _check_block("test-session", blocked_cmd)
        assert integration_result is None, (
            f"User command should be ALLOWED by _check_block():\n"
            f"  Command: {blocked_cmd}\n"
            f"  _check_block() returned: {integration_result}"
        )

    def test_extraction_shows_grep_in_pipe(self):
        """Debug test: Show what command_detection extracts from pipe commands."""
        from autorun.command_detection import extract_commands

        test_cmd = "git log | grep fix"
        # extract_commands returns (command_names, command_strings)
        command_names, command_strings = extract_commands(test_cmd)

        print(f"\nExtraction for: {test_cmd}")
        print(f"  command_names: {command_names}")
        print(f"  command_strings: {command_strings}")

        # Both git and grep should be in command_names
        assert "grep" in command_names, (
            f"grep should be in command_names: {command_names}"
        )
        assert "git" in command_names, (
            f"git should be in command_names: {command_names}"
        )


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v', '-s'])
