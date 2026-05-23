"""Tests for Codex install pathway (v0.11.0 / C4).

Codex's user-level hooks live at ~/.codex/hooks.json (always active,
no marketplace trust required). The autorun installer:
  - creates/merges ~/.codex/hooks.json with autorun's hook commands
  - uses ${PLUGIN_ROOT} (Codex env var; ${CLAUDE_PLUGIN_ROOT} is set as
    a compat alias by Codex itself)
  - prints a user-facing trust-prompt message (Codex requires /hooks
    approval for new hook hashes per HookStateToml verification)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from autorun.install import (
    _install_for_codex,
    detect_available_clis,
    determine_target_clis,
)


def _read_codex_hooks(home: Path) -> dict:
    p = home / ".codex" / "hooks.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text())


# ─── detect_available_clis includes all PLATFORMS ────────────────────────────

def test_detect_available_clis_includes_codex_and_forgecode(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda b: f"/usr/bin/{b}")
    avail = detect_available_clis()
    assert "claude" in avail
    assert "gemini" in avail
    assert "codex" in avail
    assert "forgecode" in avail


def test_determine_target_clis_default_returns_all_available():
    available = {"claude": True, "gemini": True, "codex": True, "forgecode": False}
    targets = determine_target_clis(False, False, available)
    assert "codex" in targets
    assert "forgecode" not in targets


# ─── _install_for_codex installation ─────────────────────────────────────────

def test_install_for_codex_creates_user_hooks_json(tmp_path, monkeypatch):
    """First-time install must create ~/.codex/hooks.json with autorun's hooks."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # Re-import Path-using helpers after env change (Path.home() reads HOME)
    fake_marketplace = tmp_path / "marketplace"
    fake_plugin = fake_marketplace / "plugins" / "autorun"
    fake_plugin.mkdir(parents=True)
    (fake_plugin / "hooks").mkdir()
    (fake_plugin / "hooks" / "hook_entry.py").write_text("#!/usr/bin/env python3\n")

    ok, msg = _install_for_codex(fake_marketplace, ["autorun"], force=False)
    assert ok, f"install failed: {msg}"

    hooks = _read_codex_hooks(tmp_path)
    assert "hooks" in hooks
    # Must contain at least PreToolUse, PostToolUse, Stop, SessionStart
    event_names = set(hooks["hooks"].keys())
    for ev in ("PreToolUse", "PostToolUse", "Stop", "SessionStart"):
        assert ev in event_names, f"~/.codex/hooks.json missing {ev}"
    # Hook command must reference --cli codex
    pretool_hooks = hooks["hooks"]["PreToolUse"]
    cmd_text = json.dumps(pretool_hooks)
    assert "--cli codex" in cmd_text or '"--cli", "codex"' in cmd_text
    assert "hook_entry.py" in cmd_text


def test_install_for_codex_preserves_user_hooks(tmp_path, monkeypatch):
    """Re-install must NOT clobber a user's existing custom hooks."""
    monkeypatch.setenv("HOME", str(tmp_path))
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    existing = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "user-pre.sh"}]}
            ],
            "UserCustomEvent": [{"type": "command", "command": "user-custom.sh"}],
        }
    }
    (codex_dir / "hooks.json").write_text(json.dumps(existing, indent=2))

    fake_marketplace = tmp_path / "marketplace"
    fake_plugin = fake_marketplace / "plugins" / "autorun"
    fake_plugin.mkdir(parents=True)
    (fake_plugin / "hooks").mkdir()
    (fake_plugin / "hooks" / "hook_entry.py").write_text("#!/usr/bin/env python3\n")

    ok, _msg = _install_for_codex(fake_marketplace, ["autorun"], force=False)
    assert ok

    merged = _read_codex_hooks(tmp_path)
    # User's custom event preserved
    assert "UserCustomEvent" in merged["hooks"]
    # User's PreToolUse hook preserved AND autorun's added
    pretool = merged["hooks"]["PreToolUse"]
    cmd_text = json.dumps(pretool)
    assert "user-pre.sh" in cmd_text
    assert "hook_entry.py" in cmd_text


def test_install_for_codex_idempotent(tmp_path, monkeypatch):
    """Running install twice must not duplicate autorun hook entries."""
    monkeypatch.setenv("HOME", str(tmp_path))
    fake_marketplace = tmp_path / "marketplace"
    fake_plugin = fake_marketplace / "plugins" / "autorun"
    fake_plugin.mkdir(parents=True)
    (fake_plugin / "hooks").mkdir()
    (fake_plugin / "hooks" / "hook_entry.py").write_text("#!/usr/bin/env python3\n")

    _install_for_codex(fake_marketplace, ["autorun"], force=False)
    snapshot = json.loads(((tmp_path / ".codex" / "hooks.json").read_text()))
    _install_for_codex(fake_marketplace, ["autorun"], force=False)
    after = json.loads(((tmp_path / ".codex" / "hooks.json").read_text()))
    assert snapshot == after, "Re-install must be idempotent"


def test_install_for_codex_prints_trust_reminder(tmp_path, monkeypatch, capsys):
    """User must be told to run /hooks in Codex CLI to trust the new hashes."""
    monkeypatch.setenv("HOME", str(tmp_path))
    fake_marketplace = tmp_path / "marketplace"
    fake_plugin = fake_marketplace / "plugins" / "autorun"
    fake_plugin.mkdir(parents=True)
    (fake_plugin / "hooks").mkdir()
    (fake_plugin / "hooks" / "hook_entry.py").write_text("#!/usr/bin/env python3\n")

    _install_for_codex(fake_marketplace, ["autorun"], force=False)
    captured = capsys.readouterr().out
    assert "/hooks" in captured, (
        "install output must remind user that Codex needs /hooks approval "
        "for new hook hashes (Codex HookStateToml trust model)"
    )
