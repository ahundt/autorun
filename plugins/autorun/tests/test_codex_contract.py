"""Contracts autorun relies on in the Codex CLI, pinned against its source.

Verified against openai/codex @ 20dafe201d (2026-07-25), checked out at
~/source/codex. Each test names the upstream file:line it encodes, so a future
Codex change that breaks one of these is traceable rather than mysterious.

Three upstream behaviors are load-bearing and easy to violate by accident:

1. ``~/.codex/AGENTS.override.md`` is tried BEFORE ``AGENTS.md`` and the first
   non-blank one wins — the other is never read
   (codex-home/src/instructions/mod.rs:9-10, :26-62). Installing guidance into
   AGENTS.md while an override file exists is a silent no-op.
2. ``hooks.json`` uses ``#[serde(deny_unknown_fields)]`` at the top level and
   accepts only ``description`` and ``hooks`` (hooks/src/engine/hook_config.rs:10-17).
   Any other top-level key makes Codex drop EVERY hook in the file
   (hooks/src/engine/discovery.rs:327-336).
3. Hook trust state is keyed positionally as
   ``{key_source}:{event_label}:{group_index}:{handler_index}``
   (hooks/src/lib.rs:105-113). Prepending to an event array renumbers every
   later handler and invalidates the user's approvals, forcing re-approval.
   autorun must append.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autorun.install import (  # noqa: E402
    _CODEX_AGENTS_END,
    _CODEX_AGENTS_START,
    _build_codex_hook_block,
    _codex_agents_override_shadow,
    _merge_codex_hooks,
)

CODEX_ALLOWED_TOP_LEVEL_KEYS = {"description", "hooks"}


# --------------------------------------------------------------------------
# hooks.json — top-level keys and ordering
# --------------------------------------------------------------------------


def test_merge_never_introduces_a_disallowed_top_level_key(tmp_path):
    """Codex drops every hook in a file carrying an unknown top-level key.

    hooks/src/engine/hook_config.rs:10-17 declares HooksFile with
    deny_unknown_fields; discovery.rs:327-336 returns None for the whole file.
    A stray "$schema" or "version" written by an installer therefore disables
    the user's entire hook set silently.
    """
    block = _build_codex_hook_block(tmp_path / "plugin")
    merged = _merge_codex_hooks({}, block)
    assert set(merged) <= CODEX_ALLOWED_TOP_LEVEL_KEYS


def test_merge_preserves_a_user_description_key(tmp_path):
    """`description` is the one non-`hooks` key Codex allows."""
    block = _build_codex_hook_block(tmp_path / "plugin")
    merged = _merge_codex_hooks({"description": "my hooks"}, block)
    assert merged["description"] == "my hooks"
    assert set(merged) <= CODEX_ALLOWED_TOP_LEVEL_KEYS


def test_autorun_entries_are_appended_not_prepended(tmp_path):
    """Prepending would renumber the user's handlers and void their trust.

    hooks/src/lib.rs:105-113 keys trust state by group and handler index.
    """
    block = _build_codex_hook_block(tmp_path / "plugin")
    user_entry = {"matcher": "^Bash$", "hooks": [{"type": "command", "command": "echo mine"}]}
    merged = _merge_codex_hooks({"hooks": {"PreToolUse": [user_entry]}}, block)

    entries = merged["hooks"]["PreToolUse"]
    assert entries[0] == user_entry, "user handler must keep index 0"
    assert len(entries) > 1


def test_reinstall_keeps_user_handler_indices_stable(tmp_path):
    """Re-running the installer must not shuffle user handlers."""
    block = _build_codex_hook_block(tmp_path / "plugin")
    user_entry = {"matcher": "^Bash$", "hooks": [{"type": "command", "command": "echo mine"}]}

    once = _merge_codex_hooks({"hooks": {"PreToolUse": [user_entry]}}, block)
    twice = _merge_codex_hooks(once, block)

    assert twice["hooks"]["PreToolUse"][0] == user_entry
    assert twice["hooks"]["PreToolUse"] == once["hooks"]["PreToolUse"]


def test_hook_events_are_all_names_codex_accepts(tmp_path):
    """Unknown event names are ignored silently, so a typo is invisible.

    Event list from hooks/src/engine/hook_config.rs:37-58.
    """
    codex_events = {
        "PreToolUse",
        "PermissionRequest",
        "PostToolUse",
        "PreCompact",
        "PostCompact",
        "SessionStart",
        "SessionEnd",
        "UserPromptSubmit",
        "SubagentStart",
        "SubagentStop",
        "Stop",
    }
    assert set(_build_codex_hook_block(tmp_path / "plugin")) <= codex_events


def test_hook_handlers_declare_a_type_codex_can_parse(tmp_path):
    """An unknown handler `type` fails the tagged enum and drops the file.

    hooks/src/engine/hook_config.rs:148-176 accepts command, prompt, agent;
    discovery.rs:590-597 parses then skips prompt and agent.
    """
    for entries in _build_codex_hook_block(tmp_path / "plugin").values():
        for entry in entries:
            for handler in entry["hooks"]:
                assert handler["type"] == "command"
                assert handler["command"]


# --------------------------------------------------------------------------
# AGENTS.override.md shadowing
# --------------------------------------------------------------------------


def test_no_shadow_reported_when_only_agents_md_exists(tmp_path):
    (tmp_path / "AGENTS.md").write_text("x", encoding="utf-8")
    assert _codex_agents_override_shadow(tmp_path) is None


def test_no_shadow_reported_for_an_empty_override(tmp_path):
    """Codex falls through to AGENTS.md when the override is blank.

    codex-home/src/instructions/mod.rs:26-62 takes the first *non-blank* file.
    """
    (tmp_path / "AGENTS.override.md").write_text("   \n\n", encoding="utf-8")
    assert _codex_agents_override_shadow(tmp_path) is None


def test_shadow_reported_when_a_non_blank_override_exists(tmp_path):
    """This is the case where autorun's guidance is never read."""
    override = tmp_path / "AGENTS.override.md"
    override.write_text("# my override\n", encoding="utf-8")
    assert _codex_agents_override_shadow(tmp_path) == override


def test_shadow_check_tolerates_a_missing_directory(tmp_path):
    assert _codex_agents_override_shadow(tmp_path / "nope") is None


def test_install_warns_when_the_override_shadows_our_block(tmp_path, monkeypatch, capsys):
    """A silent no-op is the worst outcome; the user must be told."""
    from autorun.install import _install_codex_agents_md

    plugin_dir = tmp_path / "plugin"
    template_dir = plugin_dir / "src" / "autorun" / "codex_template"
    template_dir.mkdir(parents=True)
    (template_dir / "AGENTS.md").write_text(
        f"{_CODEX_AGENTS_START}\nguidance\n{_CODEX_AGENTS_END}\n", encoding="utf-8"
    )

    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "AGENTS.override.md").write_text("# mine\n", encoding="utf-8")

    assert _install_codex_agents_md(plugin_dir, codex_dir) is True
    out = capsys.readouterr().out.lower()
    assert "agents.override.md" in out
    # The block is still written, so removing the override activates it.
    assert "guidance" in (codex_dir / "AGENTS.md").read_text(encoding="utf-8")
