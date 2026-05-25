"""Tests for Codex install pathway (v0.11.0 / C4 + post-0.11.0 hot-fix).

Codex's user-level hooks live at ~/.codex/hooks.json (always active,
no marketplace trust required). The autorun installer:
  - creates/merges ~/.codex/hooks.json with autorun's hook commands
  - uses ABSOLUTE paths resolved at install time. ${PLUGIN_ROOT} is
    set ONLY for plugin-bundled Codex hooks; user-level hooks receive
    no autorun-relevant env vars per
    https://developers.openai.com/codex/hooks
  - uses the canonical {hooks: [{type, command, timeout}]} wrapper for
    EVERY event (PreToolUse, PostToolUse, UserPromptSubmit,
    SessionStart, Stop, SubagentStop) — bare {type, command} dicts are
    silently dropped by Codex's schema
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


# ─── Hot-fix regression tests: schema correctness + path resolution ──────────

def _iter_command_strings(hooks_json: dict):
    """Yield every command string under any event in hooks.json."""
    events = hooks_json.get("hooks", {})
    for _event, entries in events.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            inner = entry.get("hooks", []) if isinstance(entry, dict) else []
            for h in inner if isinstance(inner, list) else []:
                if isinstance(h, dict) and h.get("type") == "command":
                    yield h.get("command", "")


def _install_into_tmp(tmp_path, monkeypatch) -> Path:
    """Run _install_for_codex against a fake marketplace rooted at tmp_path."""
    monkeypatch.setenv("HOME", str(tmp_path))
    fake_marketplace = tmp_path / "marketplace"
    fake_plugin = fake_marketplace / "plugins" / "autorun"
    fake_plugin.mkdir(parents=True)
    (fake_plugin / "hooks").mkdir()
    (fake_plugin / "hooks" / "hook_entry.py").write_text("#!/usr/bin/env python3\n")
    _install_for_codex(fake_marketplace, ["autorun"], force=False)
    return tmp_path


def test_codex_hooks_use_absolute_paths(tmp_path, monkeypatch):
    """Every command in ~/.codex/hooks.json must be an ABSOLUTE path.

    The earlier implementation emitted ${PLUGIN_ROOT} placeholders that
    Codex does not expand for user-level hooks, producing the runtime
    error 'Failed to spawn: /hooks/hook_entry.py — No such file or directory'.
    """
    home = _install_into_tmp(tmp_path, monkeypatch)
    hooks = _read_codex_hooks(home)
    assert hooks, "hooks.json missing"

    for cmd in _iter_command_strings(hooks):
        assert "${PLUGIN_ROOT}" not in cmd, (
            f"command still contains unexpanded ${{PLUGIN_ROOT}}: {cmd!r}\n"
            "Codex sets PLUGIN_ROOT only for plugin-bundled hooks; "
            "user-level hooks must use absolute paths resolved at install time."
        )
        assert "${CLAUDE_PLUGIN_ROOT}" not in cmd, (
            f"command still contains unexpanded ${{CLAUDE_PLUGIN_ROOT}}: {cmd!r}"
        )
        # hook_entry.py path must be absolute (starts with /)
        assert "/hooks/hook_entry.py" in cmd
        # The substring just before "/hooks/hook_entry.py" must be an
        # absolute path (starts with "/"), not empty.
        idx = cmd.index("/hooks/hook_entry.py")
        # Walk back from idx to the last whitespace to extract the path
        path_start = cmd.rfind(" ", 0, idx) + 1
        path = cmd[path_start:idx + len("/hooks/hook_entry.py")]
        assert path.startswith("/"), (
            f"hook_entry.py path is not absolute in command: {cmd!r}"
        )


def test_codex_hooks_use_canonical_wrapper_for_every_event(tmp_path, monkeypatch):
    """Every event entry must be a list of {hooks: [...]} dicts.

    Earlier implementation emitted bare {type, command} dicts for
    UserPromptSubmit/SessionStart/Stop/SubagentStop which Codex's
    strict schema silently dropped, producing 0/0 install counts in
    the /hooks TUI view.
    """
    home = _install_into_tmp(tmp_path, monkeypatch)
    hooks = _read_codex_hooks(home)
    events = hooks.get("hooks", {})
    assert events, "hooks.<events> map missing"

    for event_name, entries in events.items():
        assert isinstance(entries, list), (
            f"{event_name}: expected list of matcher-groups, got {type(entries).__name__}"
        )
        for entry in entries:
            assert isinstance(entry, dict), (
                f"{event_name}: each entry must be a dict, got {type(entry).__name__}"
            )
            assert "hooks" in entry, (
                f"{event_name}: each entry must have a 'hooks' list. "
                f"Bare {{type, command}} entries are silently dropped by Codex. "
                f"Got: {entry!r}"
            )
            assert isinstance(entry["hooks"], list)
            for h in entry["hooks"]:
                assert h.get("type") == "command"
                assert isinstance(h.get("command"), str) and h["command"]


def test_codex_hooks_no_sessionend_event(tmp_path, monkeypatch):
    """SessionEnd is NOT a valid Codex hook event — must not appear."""
    home = _install_into_tmp(tmp_path, monkeypatch)
    hooks = _read_codex_hooks(home)
    assert "SessionEnd" not in hooks.get("hooks", {}), (
        "SessionEnd is not in the Codex hook event list "
        "(https://developers.openai.com/codex/hooks) and must not be emitted."
    )


def test_codex_hooks_all_required_events_present(tmp_path, monkeypatch):
    """All six events autorun currently uses must be installed."""
    home = _install_into_tmp(tmp_path, monkeypatch)
    keys = set(_read_codex_hooks(home).get("hooks", {}).keys())
    required = {"PreToolUse", "PostToolUse", "UserPromptSubmit",
                "SessionStart", "Stop", "SubagentStop"}
    missing = required - keys
    assert not missing, f"Codex hooks.json missing events: {sorted(missing)}"
