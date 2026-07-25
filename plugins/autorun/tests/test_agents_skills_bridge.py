"""Bridging shared ~/.agents skills into a harness that does not read them.

Codex, OpenCode, Command Code and Gemini CLI all scan the shared agents skills
directory. Claude Code does not — it reads its own config-dir skills folder
only. autorun already *writes* the shared directory (for Codex) and never read
it back, so a skill authored there was invisible to Claude Code.

Constraints verified against Claude Code 2.1.220 and its issue tracker:

- Linking an individual skill directory works; the harness follows the link and
  reads SKILL.md from the target.
- Linking the whole skills directory does NOT work — user skills stop loading
  entirely (anthropics/claude-code#38051, a regression around v2.1.69). So a
  symlinked skills directory must be refused, loudly, rather than silently
  producing nothing.
- Discovery is top level only (anthropics/claude-code#18192), so links are flat.
- Deduplication is by resolved path, not by name. A skill a plugin already
  provides would therefore be listed twice, which is why the selection rule
  excludes plugin-provided names rather than relying on the harness to notice.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autorun.config import CONFIG  # noqa: E402
from autorun.install import (  # noqa: E402
    _AGENTS_SKILLS_SETTING,
    bridge_agents_skills,
)
from autorun.platforms import PLATFORMS  # noqa: E402

CLAUDE = PLATFORMS["claude"]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _shared_skill(home: Path, name: str, *, skill_md: bool = True) -> Path:
    skill = home / ".agents" / "skills" / name
    skill.mkdir(parents=True)
    if skill_md:
        (skill / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: d\n---\n\nbody\n", encoding="utf-8"
        )
    return skill


def _claude_skills(home: Path) -> Path:
    d = home / ".claude" / "skills"
    d.mkdir(parents=True, exist_ok=True)
    return d


# --------------------------------------------------------------------------
# The setting declaration
# --------------------------------------------------------------------------


def test_setting_declares_the_documented_contract():
    assert _AGENTS_SKILLS_SETTING.env_var == "AUTORUN_CLAUDE_AGENTS_SKILLS"
    assert _AGENTS_SKILLS_SETTING.default == "none"
    assert set(_AGENTS_SKILLS_SETTING.choices) == {"link", "copy", "none"}


def test_default_is_off_so_no_install_changes_a_users_skills_directory(home):
    _shared_skill(home, "demo")
    claude = _claude_skills(home)
    result = bridge_agents_skills(CLAUDE, mode="none")
    assert result.linked == ()
    assert list(claude.iterdir()) == []


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------


def test_links_a_shared_skill_the_harness_cannot_otherwise_see(home):
    target = _shared_skill(home, "demo")
    claude = _claude_skills(home)

    result = bridge_agents_skills(CLAUDE, mode="link")

    link = claude / "demo"
    assert link.is_symlink()
    assert link.resolve() == target.resolve()
    assert "demo" in result.linked


def test_skips_a_name_the_harness_already_has_as_a_real_directory(home):
    _shared_skill(home, "demo")
    claude = _claude_skills(home)
    real = claude / "demo"
    real.mkdir()
    (real / "SKILL.md").write_text("mine", encoding="utf-8")

    result = bridge_agents_skills(CLAUDE, mode="link")

    assert not (claude / "demo").is_symlink()
    assert (real / "SKILL.md").read_text(encoding="utf-8") == "mine"
    assert "demo" in result.skipped_existing


def test_skips_a_name_a_plugin_already_provides(home):
    """Dedup is by resolved path, so this would double-list the skill."""
    _shared_skill(home, "demo")
    _claude_skills(home)

    result = bridge_agents_skills(CLAUDE, mode="link", plugin_skill_names={"demo"})

    assert "demo" in result.skipped_plugin
    assert not (home / ".claude" / "skills" / "demo").exists()


def test_ignores_a_directory_without_a_skill_md(home):
    _shared_skill(home, "not-a-skill", skill_md=False)
    _claude_skills(home)
    result = bridge_agents_skills(CLAUDE, mode="link")
    assert result.linked == ()


# --------------------------------------------------------------------------
# Idempotence and repair
# --------------------------------------------------------------------------


def test_running_twice_is_idempotent(home):
    _shared_skill(home, "demo")
    _claude_skills(home)
    bridge_agents_skills(CLAUDE, mode="link")
    second = bridge_agents_skills(CLAUDE, mode="link")
    assert second.linked == ()
    assert "demo" in second.already_linked


def test_repairs_a_link_whose_target_moved(home):
    target = _shared_skill(home, "demo")
    claude = _claude_skills(home)
    stale = claude / "demo"
    stale.symlink_to(home / ".agents" / "skills" / "gone")

    bridge_agents_skills(CLAUDE, mode="link")

    assert (claude / "demo").resolve() == target.resolve()


# --------------------------------------------------------------------------
# Refusals and degradation
# --------------------------------------------------------------------------


def test_refuses_when_the_harness_skills_directory_is_itself_a_symlink(home):
    """anthropics/claude-code#38051 — nothing would load, so say so."""
    _shared_skill(home, "demo")
    real = home / "elsewhere-skills"
    real.mkdir()
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "skills").symlink_to(real)

    result = bridge_agents_skills(CLAUDE, mode="link")

    assert result.refused_reason
    assert result.linked == ()
    assert not list(real.iterdir()), "must not write into a layout that cannot load"


def test_copy_mode_produces_real_directories(home):
    """The fallback where symlink creation needs privileges (Windows)."""
    _shared_skill(home, "demo")
    claude = _claude_skills(home)

    bridge_agents_skills(CLAUDE, mode="copy")

    copied = claude / "demo"
    assert copied.is_dir() and not copied.is_symlink()
    assert (copied / "SKILL.md").is_file()


def test_falls_back_to_copy_when_symlink_creation_fails(home, monkeypatch):
    _shared_skill(home, "demo")
    claude = _claude_skills(home)

    def _no_symlinks(self, target, **kwargs):
        raise OSError("symlink privilege required")

    monkeypatch.setattr(Path, "symlink_to", _no_symlinks)

    result = bridge_agents_skills(CLAUDE, mode="link")

    assert (claude / "demo").is_dir()
    assert result.fell_back_to_copy


def test_absent_shared_directory_is_a_no_op(home):
    _claude_skills(home)
    result = bridge_agents_skills(CLAUDE, mode="link")
    assert result.linked == ()
    assert not result.refused_reason


def test_creates_the_harness_skills_directory_when_missing(home):
    _shared_skill(home, "demo")
    result = bridge_agents_skills(CLAUDE, mode="link")
    assert (home / ".claude" / "skills" / "demo").is_symlink()
    assert result.created_skills_dir


def test_platform_without_a_skills_dir_is_a_no_op(home):
    _shared_skill(home, "demo")
    result = bridge_agents_skills(PLATFORMS["forgecode"], mode="link")
    assert result.linked == ()


def test_dry_run_reports_without_writing(home):
    _shared_skill(home, "demo")
    claude = _claude_skills(home)
    result = bridge_agents_skills(CLAUDE, mode="link", dry_run=True)
    assert "demo" in result.linked
    assert list(claude.iterdir()) == []


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


def test_follows_a_relocated_shared_agents_directory(home, monkeypatch):
    monkeypatch.setitem(CONFIG, "shared_agents_dir", str(home / "custom"))
    skill = home / "custom" / "skills" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\ndescription: d\n---\n", encoding="utf-8")
    claude = _claude_skills(home)

    bridge_agents_skills(CLAUDE, mode="link")

    assert (claude / "demo").resolve() == skill.resolve()
