"""Tests for ForgeCode install pathway (v0.11.0 / C6).

The installer:
  - resolves the base path via FORGE_CONFIG env > ~/forge/ (legacy if
    present) > ~/.forge/ (default)
  - copies forgecode_template/commands/*.md to <base>/commands/
  - copies/merges forgecode_template/AGENTS.md to <base>/AGENTS.md
    (idempotent — re-running does not duplicate content)
  - prints a notice that ForgeCode integration is advisory (no hooks)
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from autorun.install import _install_for_forgecode


def _make_marketplace(tmp_path: Path) -> Path:
    """Build a minimal marketplace tree that includes the autorun plugin."""
    root = tmp_path / "marketplace"
    plugin = root / "plugins" / "autorun"
    plugin.mkdir(parents=True)
    # Symlink to the actual forgecode_template so the installer copies the real content
    real_template = (
        Path(__file__).parent.parent
        / "src" / "autorun" / "forgecode_template"
    )
    plugin_src_dir = plugin / "src" / "autorun"
    plugin_src_dir.mkdir(parents=True)
    os.symlink(real_template, plugin_src_dir / "forgecode_template")
    return root


def test_install_for_forgecode_creates_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("FORGE_CONFIG", raising=False)
    marketplace = _make_marketplace(tmp_path)

    ok, _msg = _install_for_forgecode(marketplace, ["autorun"], force=False)
    assert ok

    cmds_dir = tmp_path / ".forge" / "commands"
    assert cmds_dir.is_dir()
    for name in ("ar-go", "ar-st", "ar-allow", "ar-find", "ar-commit", "ar-ph"):
        assert (cmds_dir / f"{name}.md").is_file(), f"missing {name}.md"


def test_install_for_forgecode_writes_agents_md(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("FORGE_CONFIG", raising=False)
    marketplace = _make_marketplace(tmp_path)

    ok, _msg = _install_for_forgecode(marketplace, ["autorun"], force=False)
    assert ok

    agents_md = tmp_path / ".forge" / "AGENTS.md"
    assert agents_md.is_file()
    content = agents_md.read_text()
    assert "autorun" in content.lower()


def test_install_for_forgecode_respects_FORGE_CONFIG(tmp_path, monkeypatch):
    """FORGE_CONFIG env var should override the default ~/.forge/ path."""
    monkeypatch.setenv("HOME", str(tmp_path))
    custom = tmp_path / "custom_forge"
    custom.mkdir()
    monkeypatch.setenv("FORGE_CONFIG", str(custom))
    marketplace = _make_marketplace(tmp_path)

    ok, _msg = _install_for_forgecode(marketplace, ["autorun"], force=False)
    assert ok
    assert (custom / "commands" / "ar-go.md").is_file()
    assert (custom / "AGENTS.md").is_file()
    # Default path should NOT be populated
    assert not (tmp_path / ".forge" / "commands" / "ar-go.md").is_file()


def test_install_for_forgecode_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("FORGE_CONFIG", raising=False)
    marketplace = _make_marketplace(tmp_path)

    _install_for_forgecode(marketplace, ["autorun"], force=False)
    first_agents = (tmp_path / ".forge" / "AGENTS.md").read_text()
    _install_for_forgecode(marketplace, ["autorun"], force=False)
    second_agents = (tmp_path / ".forge" / "AGENTS.md").read_text()
    assert first_agents == second_agents, "Re-install must be idempotent"


def test_install_for_forgecode_prints_advisory_notice(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("FORGE_CONFIG", raising=False)
    marketplace = _make_marketplace(tmp_path)

    _install_for_forgecode(marketplace, ["autorun"], force=False)
    out = capsys.readouterr().out
    # User must understand ForgeCode integration is advisory (no hook enforcement)
    assert "hooks" in out.lower() or "advisory" in out.lower() or "agents.md" in out.lower()
