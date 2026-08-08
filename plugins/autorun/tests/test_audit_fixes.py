"""Regressions found by the maintainer-readiness audit.

Each test pins one defect that shipped. Grouped by what goes wrong, worst first.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autorun.core import AI_ECHO_CHANNEL, EventContext  # noqa: E402
from autorun.installer.memory import Block, context_guidance_enabled, splice, strip  # noqa: E402

START = "<!-- autorun:audit-test:start -->"
END = "<!-- autorun:audit-test:end -->"
SENTINEL = Block("audit-test")


def install_sentinel_block(target, body, *, start, end):
    assert (start, end) == (SENTINEL.start, SENTINEL.end)
    return splice(target, body, SENTINEL)


def strip_sentinel_block(target, *, start, end):
    assert (start, end) == (SENTINEL.start, SENTINEL.end)
    return strip(target, SENTINEL)


# --------------------------------------------------------------------------
# S1.3 — a stray marker made install non-idempotent and un-strippable
# --------------------------------------------------------------------------


def test_a_stray_end_marker_does_not_cause_unbounded_growth(tmp_path):
    """A truncated previous write left a lone `end` above a good block.

    `find(end)` then returned the stray one, `_sentinel_bounds` saw
    close_at < open_at, reported "no region", and every install appended
    another full copy of the guidance forever.
    """
    target = tmp_path / "CLAUDE.md"
    target.write_text(f"# mine\n\n{END}\n", encoding="utf-8")

    for _ in range(3):
        install_sentinel_block(target, "body", start=START, end=END)

    text = target.read_text(encoding="utf-8")
    assert text.count("body") == 1, "guidance duplicated on re-install"
    assert text.count(START) == 1
    assert "# mine" in text


def test_a_block_after_a_stray_end_marker_can_still_be_stripped(tmp_path):
    """Un-strippable meant uninstall could never clean it."""
    target = tmp_path / "CLAUDE.md"
    target.write_text(f"# mine\n\n{END}\n", encoding="utf-8")
    install_sentinel_block(target, "body", start=START, end=END)

    assert strip_sentinel_block(target, start=START, end=END) is True

    text = target.read_text(encoding="utf-8")
    assert "body" not in text
    assert "# mine" in text


def test_user_content_survives_a_stray_marker_repair(tmp_path):
    target = tmp_path / "CLAUDE.md"
    target.write_text(f"# keep me\n\n{END}\n\n## also keep\n", encoding="utf-8")
    install_sentinel_block(target, "body", start=START, end=END)
    text = target.read_text(encoding="utf-8")
    assert "# keep me" in text
    assert "## also keep" in text


# --------------------------------------------------------------------------
# S2.5 / S2.6 — AI_ECHO_CHANNEL was honored on one response pathway only
# --------------------------------------------------------------------------


def _ctx(event: str) -> EventContext:
    return EventContext(session_id="audit", event=event, cli_type="claude")


BLOCK = "🛑 CANNOT STOP — incomplete tasks"


@pytest.mark.parametrize("event", ["PostToolUse", "Stop", "SessionStart"])
def test_echoed_text_never_reaches_the_user_on_any_event(event):
    """Pathways 3 and 4 discarded the channel and printed everything."""
    ctx = _ctx(event)
    ctx.add_chain_notification(BLOCK, channel=AI_ECHO_CHANNEL)
    resp = ctx.respond("allow")
    assert BLOCK not in (resp.get("systemMessage") or ""), (
        f"{event} reprinted already-shown text to the user"
    )


def test_echo_suppression_survives_a_co_occurring_notification():
    """The suppression was disabled whenever anything else was queued.

    `len(echo_notifs) == len(ai_notifs)` was False as soon as another handler
    queued a channel="both" message on the same turn, so the fallback printed
    the echoed block again.
    """
    ctx = _ctx("PostToolUse")
    ctx.add_chain_notification(BLOCK, channel=AI_ECHO_CHANNEL)
    ctx.add_chain_notification("unrelated export note", channel="both")

    resp = ctx.respond("allow")
    sys_msg = resp.get("systemMessage") or ""

    assert BLOCK not in sys_msg, "echoed text leaked once another message was queued"
    assert "unrelated export note" in sys_msg, "the other message must still show"


def test_echoed_text_still_reaches_the_ai_alongside_other_notifications():
    ctx = _ctx("PostToolUse")
    ctx.add_chain_notification(BLOCK, channel=AI_ECHO_CHANNEL)
    ctx.add_chain_notification("unrelated export note", channel="both")

    resp = ctx.respond("allow")
    ai_text = (resp.get("hookSpecificOutput") or {}).get("additionalContext") or ""

    assert BLOCK in ai_text
    assert "unrelated export note" in ai_text


# --------------------------------------------------------------------------
# S2.4 — the #54673 workaround had no CONFIG key, breaking the repo's policy
# --------------------------------------------------------------------------


def test_the_54673_workaround_key_exists_in_config():
    """plugins/autorun/CLAUDE.md requires ONE key as both env var and CONFIG entry."""
    from autorun.config import CONFIG

    assert (
        "AUTORUN_BUG_CLAUDE_CODE_NO_TOKEN_COUNT_FOR_HOOKS_BUG_54673_WORKAROUND_ENABLED"
        in CONFIG
    )


def test_the_54673_workaround_can_be_disabled_via_config(monkeypatch):
    """Env-only meant it could not be disabled for a hook subprocess."""
    from autorun.config import CONFIG
    key = "AUTORUN_BUG_CLAUDE_CODE_NO_TOKEN_COUNT_FOR_HOOKS_BUG_54673_WORKAROUND_ENABLED"
    monkeypatch.delenv(key, raising=False)
    monkeypatch.setitem(CONFIG, key, False)
    assert context_guidance_enabled() is False


def test_every_bug_workaround_key_in_config_is_read_by_the_code():
    """A declared gate nobody reads is a promise the code does not keep."""
    from autorun.config import CONFIG

    src_dir = Path(__file__).resolve().parents[1] / "src" / "autorun"
    blob = "\n".join(
        p.read_text(encoding="utf-8")
        for p in src_dir.rglob("*.py")
        if "__pycache__" not in p.parts
    )
    for key in CONFIG:
        if key.startswith("AUTORUN_BUG_"):
            assert key in blob, f"CONFIG[{key!r}] is declared but never read"


def test_retired_marketplace_names_are_absent_from_current_install_surfaces():
    """The old marketplace names made every plugin install miss its source."""
    repo = Path(__file__).resolve().parents[3]
    paths = [
        repo / "README.md",
        repo / ".claude-plugin" / "marketplace.json",
        repo / "plugins" / "known_marketplaces.json",
    ]
    paths.extend((repo / "plugins" / "autorun" / "src" / "autorun").rglob("*.py"))
    paths.extend((repo / "plugins" / "autorun" / "skills").rglob("*.md"))

    for path in paths:
        content = path.read_text(encoding="utf-8")
        assert "autorun-dev" not in content, path
        assert "clautorun" not in content, path
