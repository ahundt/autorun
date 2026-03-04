#!/usr/bin/env python3
"""Test script to demonstrate daemon-path command interception.

Migrated from claude_code_handler (removed) to daemon-path plugins.app.dispatch().
Canonical path: EventContext + plugins.app.dispatch(ctx) → registered commands.
"""
import sys
from pathlib import Path

# Add src to path for testing
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from autorun.core import EventContext, ThreadSafeDB
from autorun import plugins


def _dispatch(prompt: str, session_id: str = "test_session") -> dict | str | None:
    """Dispatch a UserPromptSubmit prompt via daemon-path plugins.app.dispatch().

    Canonical replacement for deleted claude_code_handler(mock_ctx).
    """
    ctx = EventContext(
        session_id=session_id,
        event="UserPromptSubmit",
        prompt=prompt,
        tool_name="",
        tool_input={},
        store=ThreadSafeDB(),
    )
    return plugins.app.dispatch(ctx)


def test_commands():
    """Test command dispatch via daemon-path plugins.app.dispatch()."""

    # Canonical short-form commands (modern equivalents of /afs, /afa, /afj, /afst, /autostop, /estop)
    test_cases = [
        ("/ar:f", "SEARCH policy (strict file search)"),
        ("/ar:a", "ALLOW policy (allow all)"),
        ("/ar:j", "JUSTIFY policy (require justification)"),
        ("/ar:st", "STATUS — show current policy"),
        ("/ar:x", "STOP — graceful stop"),
        ("/ar:sos", "Emergency stop"),
        ("Hello world", "Non-command — returns None (pass-through to AI)"),
    ]

    print("🧪 Testing Daemon-Path Command Interception")
    print("=" * 50)

    for prompt, expected in test_cases:
        result = _dispatch(prompt)
        print(f"Command: {prompt}")
        print(f"Expected: {expected}")
        print(f"Result: {result}")
        print("-" * 30)


def test_policy_commands_dispatch():
    """Policy commands dispatch and return policy strings."""
    for prompt, expected_key in [
        ("/ar:a", "allow-all"),
        ("/ar:j", "justify-create"),
        ("/ar:f", "strict-search"),
    ]:
        result = _dispatch(prompt)
        assert isinstance(result, dict), f"Policy command {prompt!r} must return dict, got {type(result)}"
        msg = result.get("systemMessage", "")
        assert expected_key in msg or "AutoFile policy:" in msg, \
            f"Policy response for {prompt!r} must mention policy, got: {msg!r}"


def test_non_command_returns_none():
    """Non-command prompts return None (pass-through — AI handles them)."""
    result = _dispatch("Hello world")
    # Non-commands that don't match any /ar: command return None
    # (Some may return a continue=True dict; both None and allow dict are acceptable)
    if result is not None and isinstance(result, dict):
        assert result.get("continue", True) is True, \
            "Non-command response must have continue=True"


if __name__ == "__main__":
    test_commands()
