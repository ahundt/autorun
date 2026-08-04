"""The Stop-block text must reach the AI again without printing to the user twice.

Observed: the "CANNOT STOP — incomplete tasks" block appeared twice in a row —
once as the Stop hook's own denial output, then again as PostToolUse
additionalContext on the very next tool call.

Neither half is wrong on its own:

- `handle_stop` denies and Claude Code prints the denial. The user sees it.
- `pending_stop_injection` re-delivers the same text on the next PostToolUse
  because Claude Code silently drops `additionalContext` on Stop
  (anthropics/claude-code#18534). Without that re-delivery the AI never receives
  the block at all.

The duplicate comes from the #18534 workaround itself: it upgrades
`channel="ai"` into `systemMessage` so AI-targeted text actually arrives, which
also makes it user-visible. For the stop injection specifically the user has
already seen that exact text, so the upgrade produces a verbatim repeat.

The fix is a distinct channel rather than text comparison. task_lifecycle.py's
handler docstring is explicit that byte-identical text must NOT be suppressed
across Stop-block generations: an AI stuck repeating a mistake produces an
unchanged task list and therefore identical text every time, and suppressing
that would stop feeding it the override actions. `ai-echo` suppresses only the
duplicate *human* copy; the AI still receives every block.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autorun.core import AI_ECHO_CHANNEL, _bug_18534_human_channels  # noqa: E402

BLOCK = "🛑 CANNOT STOP — incomplete tasks: 1. #23 ..."


def test_ai_echo_is_not_upgraded_to_the_human_channel():
    """The whole point: #18534's upgrade must skip this channel."""
    channels = _bug_18534_human_channels("claude")
    assert "ai" in channels, "the workaround must still upgrade ordinary ai text"
    assert AI_ECHO_CHANNEL not in channels


def test_ai_echo_is_not_human_visible_on_any_cli():
    for cli in ("claude", "gemini", "codex", "qwen"):
        assert AI_ECHO_CHANNEL not in _bug_18534_human_channels(cli)


def _respond_with(ctx_factory, channel):
    ctx = ctx_factory()
    ctx.add_chain_notification(BLOCK, channel=channel)
    return ctx.respond("allow")


@pytest.fixture
def ctx_factory(monkeypatch):
    from autorun.core import EventContext

    def _make():
        monkeypatch.setenv("AUTORUN_CLI_TYPE", "claude")
        return EventContext(
            session_id="test-echo-channel",
            event="PostToolUse",
            cli_type="claude",
        )

    return _make


def test_ai_echo_reaches_the_ai(ctx_factory):
    resp = _respond_with(ctx_factory, AI_ECHO_CHANNEL)
    blob = str(resp)
    assert BLOCK in blob, "the AI must still receive the block"


def test_ai_echo_does_not_reach_the_user(ctx_factory):
    resp = _respond_with(ctx_factory, AI_ECHO_CHANNEL)
    hook_output = resp.get("hookSpecificOutput", {})
    assert BLOCK not in (resp.get("systemMessage") or "")
    assert BLOCK in (hook_output.get("additionalContext") or "")


def test_ordinary_ai_channel_still_reaches_the_user_under_the_workaround(ctx_factory):
    """Regression guard: #18534's upgrade must keep working for normal text."""
    resp = _respond_with(ctx_factory, "ai")
    assert BLOCK in (resp.get("systemMessage") or "")


def test_human_channel_is_unaffected(ctx_factory):
    resp = _respond_with(ctx_factory, "human")
    assert BLOCK in (resp.get("systemMessage") or "")


def test_every_generation_still_delivers_to_the_ai(ctx_factory):
    """Identical text across generations must NOT be suppressed.

    Suppressing it would stop feeding the AI the override actions (/ar:sos,
    /ar:task ignore) from the second block onward — the "infinite
    non-overridable stop failure" the re-arm exists to prevent.
    """
    for _ in range(3):
        resp = _respond_with(ctx_factory, AI_ECHO_CHANNEL)
        hook_output = resp.get("hookSpecificOutput", {})
        assert BLOCK in (hook_output.get("additionalContext") or "")
