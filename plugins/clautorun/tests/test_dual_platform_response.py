#!/usr/bin/env python3
"""TDD for platform-correct response logic in core.py."""
import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add src to path
plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root / 'src'))

from clautorun.core import EventContext

class TestDualPlatformResponse:
    """Verify that respond() logic returns platform-correct JSON."""

    def test_stop_injection_claude(self):
        """Claude Stop event should use decision='block' and continue=True."""
        ctx = EventContext("test", "Stop")
        # Simulate Claude environment
        with patch('clautorun.config.detect_cli_type', return_value='claude'):
            resp = ctx.respond("block", "Keep working")
            assert resp["decision"] == "block"
            assert resp["continue"] is True
            # Stop event uses systemMessage for the injection
            assert resp["systemMessage"] == "Keep working"

    def test_stop_injection_gemini(self):
        """Gemini Stop event should use decision='deny' and continue=True."""
        ctx = EventContext("test", "Stop")
        # Simulate Gemini environment
        with patch('clautorun.config.detect_cli_type', return_value='gemini'):
            resp = ctx.respond("block", "Keep working")
            # CRITICAL: For Gemini, AfterAgent (Stop) needs 'deny' to trigger turn retry
            assert resp["decision"] == "deny"
            assert resp["continue"] is True
            assert resp["reason"] == "Keep working"

    def test_pretooluse_deny_claude(self):
        """Claude PreToolUse deny should return block/deny schema."""
        ctx = EventContext("test", "PreToolUse")
        with patch('clautorun.config.detect_cli_type', return_value='claude'):
            resp = ctx.respond("deny", "Blocked")
            assert resp["decision"] == "block"
            assert resp["permissionDecision"] == "deny"
            assert resp["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_pretooluse_deny_gemini(self):
        """Gemini PreToolUse deny should return simple decision='deny'."""
        ctx = EventContext("test", "PreToolUse")
        with patch('clautorun.config.detect_cli_type', return_value='gemini'):
            resp = ctx.respond("deny", "Blocked")
            assert resp["decision"] == "deny"
            # Gemini schema shouldn't have hso if we can avoid it (lenient but cleaner)
            assert "hookSpecificOutput" not in resp

    def test_ask_mapping_gemini(self):
        """Gemini doesn't support 'ask', should map to 'deny'."""
        ctx = EventContext("test", "PreToolUse")
        with patch('clautorun.config.detect_cli_type', return_value='gemini'):
            resp = ctx.respond("ask", "Are you sure?")
            assert resp["decision"] == "deny"

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
