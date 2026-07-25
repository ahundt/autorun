"""Uninstall removes what install wrote outside ~/.claude/plugins/.

`uninstall_plugins` removed four things — the Claude plugin, the uv tool, and two
directories under ~/.claude/plugins — and nothing else. Everything install wrote
for the other six harnesses survived, including guidance blocks spliced into
files the *user* owns. The docstring on the Codex memory installer promised
"a future uninstall can strip our block cleanly"; no strip function existed.

Two invariants make this safe to automate:

- Memory blocks are sentinel-delimited, so removal is exact and leaves
  surrounding user content untouched.
- Skill entries autorun links into ~/.claude/skills are symlinks resolving into
  ~/.agents/skills. Uninstall removes links only, never a real directory, so a
  user-authored skill that happens to share a name is never destroyed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autorun.install import (  # noqa: E402
    install_platform_memory,
    platform_memory_sentinels,
    uninstall_plugins,
)
from autorun.platforms import PLATFORMS  # noqa: E402

REAL_PLUGIN_DIR = Path(__file__).resolve().parents[1]

USER_TEXT = "# My own guidance\n\nAlways run `make lint` before committing.\n"


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Point HOME at a scratch tree and stub every external command."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("FORGE_CONFIG", raising=False)
    monkeypatch.setattr(
        "autorun.install.run_cmd",
        lambda *a, **k: __import__("autorun.install", fromlist=["CmdResult"]).CmdResult(
            True, "ok"
        ),
    )
    monkeypatch.setattr("autorun.install._restart_daemon_if_running", lambda: None)
    return tmp_path


def _install_memory(home: Path, platform_name: str, user_text: str = "") -> Path:
    platform = PLATFORMS[platform_name]
    config_dir = home / platform.config_dir.lstrip("~/").rstrip("/")
    config_dir.mkdir(parents=True, exist_ok=True)
    target = config_dir / platform.memory_filename
    if user_text:
        target.write_text(user_text, encoding="utf-8")
    install_platform_memory(platform, REAL_PLUGIN_DIR, config_dir)
    return target


# --------------------------------------------------------------------------
# Memory blocks
# --------------------------------------------------------------------------


@pytest.mark.parametrize("platform_name", ["claude", "codex", "forgecode"])
def test_uninstall_strips_the_memory_block(isolated_home, platform_name):
    target = _install_memory(isolated_home, platform_name)
    start, _ = platform_memory_sentinels(PLATFORMS[platform_name])
    assert start in target.read_text(encoding="utf-8")

    uninstall_plugins("all")

    assert not target.exists() or start not in target.read_text(encoding="utf-8")


@pytest.mark.parametrize("platform_name", ["claude", "codex", "forgecode"])
def test_uninstall_preserves_user_content_around_the_block(
    isolated_home, platform_name
):
    """The whole point of sentinels: the user's own guidance survives."""
    target = _install_memory(isolated_home, platform_name, USER_TEXT)

    uninstall_plugins("all")

    assert target.is_file()
    assert USER_TEXT.rstrip("\n") in target.read_text(encoding="utf-8")


def test_uninstall_leaves_a_memory_file_autorun_never_touched(isolated_home):
    claude_dir = isolated_home / ".claude"
    claude_dir.mkdir(parents=True)
    target = claude_dir / "CLAUDE.md"
    target.write_text(USER_TEXT, encoding="utf-8")

    uninstall_plugins("all")

    assert target.read_text(encoding="utf-8") == USER_TEXT


def test_uninstall_is_idempotent(isolated_home):
    target = _install_memory(isolated_home, "claude", USER_TEXT)
    uninstall_plugins("all")
    first = target.read_text(encoding="utf-8")
    uninstall_plugins("all")
    assert target.read_text(encoding="utf-8") == first


def test_uninstall_on_a_machine_that_never_installed_does_not_raise(isolated_home):
    assert uninstall_plugins("all") == 0


# --------------------------------------------------------------------------
# Skill links
# --------------------------------------------------------------------------


def _make_agents_skill(home: Path, name: str) -> Path:
    skill = home / ".agents" / "skills" / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test\n---\n\nbody\n", encoding="utf-8"
    )
    return skill


def test_uninstall_removes_symlinks_into_agents_skills(isolated_home):
    target = _make_agents_skill(isolated_home, "demo-skill")
    claude_skills = isolated_home / ".claude" / "skills"
    claude_skills.mkdir(parents=True)
    link = claude_skills / "demo-skill"
    link.symlink_to(target)

    uninstall_plugins("all")

    assert not link.exists() and not link.is_symlink()
    assert target.is_dir(), "the source skill must not be removed"


def test_uninstall_never_removes_a_real_skill_directory(isolated_home):
    """A user-authored skill sharing a name must survive."""
    claude_skills = isolated_home / ".claude" / "skills"
    claude_skills.mkdir(parents=True)
    real = claude_skills / "demo-skill"
    real.mkdir()
    (real / "SKILL.md").write_text("mine", encoding="utf-8")

    uninstall_plugins("all")

    assert real.is_dir()
    assert (real / "SKILL.md").read_text(encoding="utf-8") == "mine"


def test_uninstall_leaves_symlinks_pointing_elsewhere_alone(isolated_home):
    """Another tool's link is not autorun's to remove."""
    other = isolated_home / "elsewhere" / "their-skill"
    other.mkdir(parents=True)
    claude_skills = isolated_home / ".claude" / "skills"
    claude_skills.mkdir(parents=True)
    link = claude_skills / "their-skill"
    link.symlink_to(other)

    uninstall_plugins("all")

    assert link.is_symlink()


def test_uninstall_tolerates_a_broken_link(isolated_home):
    claude_skills = isolated_home / ".claude" / "skills"
    claude_skills.mkdir(parents=True)
    link = claude_skills / "dangling"
    link.symlink_to(isolated_home / ".agents" / "skills" / "gone")

    assert uninstall_plugins("all") == 0
