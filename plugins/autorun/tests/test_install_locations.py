"""Install and uninstall locations come from config, not literals.

`Path.home() / ".agents" / "skills"` was written out at three separate call
sites, `~/.agents/plugins/marketplace.json` at one more, and the Claude skills
directory at another. A deployment that puts the shared agents directory
somewhere else — a test harness, a sandboxed CI home, a machine following the
`~/.agent` singular spelling that Antigravity uses — had no way to say so, and
changing it meant finding every literal.

config.py:1060-1062 already states the intent for platform metadata: "adding a
new CLI = adding one Platform() definition there. No parallel maintenance here."
These tests extend the same rule to install locations.

Values are stored `~`-prefixed and expanded at use, matching
`integration_search_paths` (config.py:1049) which stores patterns rather than
resolved paths.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autorun.config import CONFIG  # noqa: E402
from autorun.install import (  # noqa: E402
    _codex_personal_marketplace_path,
    _codex_plugin_source_dir,
    _install_markdown_commands_harness,
    merge_custom_harness_specs,
    parse_custom_harness_spec,
    platform_config_dir,
    skill_search_paths,
    shared_agents_dir,
    shared_agents_skills_dir,
)
from autorun.platforms import PLATFORMS  # noqa: E402


# --------------------------------------------------------------------------
# The keys exist and carry the documented defaults
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key,default",
    [
        ("shared_agents_dir", "~/.agents"),
        ("shared_agents_skills_subdir", "skills"),
        ("shared_agents_plugins_subdir", "plugins"),
        ("codex_plugin_source_dir", "~/plugins"),
    ],
)
def test_config_declares_the_location_key(key, default):
    assert CONFIG.get(key) == default


def test_defaults_match_the_codex_documented_layout(monkeypatch, tmp_path):
    """~/.agents/skills and ~/.agents/plugins/marketplace.json are Codex's.

    Verified against openai/codex core-skills/src/loader.rs:334-345 and
    core-plugins/src/marketplace.rs:20-25.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    assert shared_agents_skills_dir() == tmp_path / ".agents" / "skills"
    assert (
        _codex_personal_marketplace_path()
        == tmp_path / ".agents" / "plugins" / "marketplace.json"
    )


# --------------------------------------------------------------------------
# Overriding the config actually moves the location
# --------------------------------------------------------------------------


@pytest.fixture
def relocated(monkeypatch, tmp_path):
    """Point every configurable install location at a scratch tree."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setitem(CONFIG, "shared_agents_dir", str(tmp_path / "custom-agents"))
    monkeypatch.setitem(CONFIG, "shared_agents_skills_subdir", "my-skills")
    monkeypatch.setitem(CONFIG, "shared_agents_plugins_subdir", "my-plugins")
    monkeypatch.setitem(CONFIG, "codex_plugin_source_dir", str(tmp_path / "custom-src"))
    return tmp_path


def test_shared_agents_dir_honors_the_override(relocated):
    assert shared_agents_dir() == relocated / "custom-agents"


def test_skills_dir_honors_both_the_root_and_the_subdir(relocated):
    assert shared_agents_skills_dir() == relocated / "custom-agents" / "my-skills"


def test_marketplace_path_honors_the_override(relocated):
    assert _codex_personal_marketplace_path() == (
        relocated / "custom-agents" / "my-plugins" / "marketplace.json"
    )


def test_codex_plugin_source_dir_honors_the_override(relocated):
    from autorun.install import _CODEX_PLUGIN_NAME

    assert _codex_plugin_source_dir() == relocated / "custom-src" / _CODEX_PLUGIN_NAME


def test_a_tilde_value_is_expanded(monkeypatch, tmp_path):
    """Config stores ~-prefixed values; callers must get absolute paths."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setitem(CONFIG, "shared_agents_dir", "~/elsewhere")
    assert shared_agents_dir() == tmp_path / "elsewhere"


def test_an_absolute_value_is_left_alone(monkeypatch, tmp_path):
    monkeypatch.setitem(CONFIG, "shared_agents_dir", str(tmp_path / "abs"))
    assert shared_agents_dir() == tmp_path / "abs"


# --------------------------------------------------------------------------
# Per-platform skills directories come from platform data
# --------------------------------------------------------------------------


def test_claude_skills_dir_derives_from_platform_config_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert skill_search_paths(PLATFORMS["claude"]) == (tmp_path / ".claude" / "skills",)


def test_a_harness_reads_the_shared_root_and_its_own(monkeypatch, tmp_path):
    """Restated from the retired platform_skills_dir, which returned one
    directory or None. ForgeCode used to answer None; it in fact reads two
    roots, and reporting neither is what hid its skills from every sweep."""
    monkeypatch.setenv("HOME", str(tmp_path))
    assert skill_search_paths(PLATFORMS["forgecode"]) == (
        tmp_path / ".agents" / "skills",
        tmp_path / "forge" / "skills",
    )


def test_skill_roots_follow_a_relocated_config_dir(monkeypatch, tmp_path):
    """A harness whose config_dir moves takes its skills directory along."""
    import dataclasses

    monkeypatch.setenv("HOME", str(tmp_path))
    moved = dataclasses.replace(
        PLATFORMS["claude"], config_dir=str(tmp_path / "elsewhere") + "/"
    )
    assert skill_search_paths(moved) == (tmp_path / "elsewhere" / "skills",)


# --------------------------------------------------------------------------
# Uninstall follows the same configuration as install
# --------------------------------------------------------------------------


def test_uninstall_removes_links_under_a_relocated_agents_dir(relocated, monkeypatch):
    """The reported bug shape: uninstall must not hardcode what install configures."""
    from autorun.install import CmdResult, uninstall_plugins

    monkeypatch.setattr("autorun.install.run_cmd", lambda *a, **k: CmdResult(True, "ok"))
    monkeypatch.setattr("autorun.install._restart_daemon_if_running", lambda: None)

    source = relocated / "custom-agents" / "my-skills" / "demo"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\ndescription: d\n---\n", encoding="utf-8")

    claude_skills = relocated / ".claude" / "skills"
    claude_skills.mkdir(parents=True)
    link = claude_skills / "demo"
    link.symlink_to(source)

    uninstall_plugins("all")

    assert not link.is_symlink(), "link under the configured agents dir was not removed"
    assert source.is_dir()


# --------------------------------------------------------------------------
# Per-platform config dirs: CONFIG override > harness-native env var > default
# --------------------------------------------------------------------------


def test_platform_config_dir_defaults_to_the_declared_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    assert platform_config_dir(PLATFORMS["codex"]) == tmp_path / ".codex"


def test_platform_config_dir_honors_the_harness_native_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-work"))
    assert platform_config_dir(PLATFORMS["codex"]) == tmp_path / "codex-work"


def test_platform_config_dir_config_override_beats_the_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "from-env"))
    monkeypatch.setitem(
        CONFIG, "harness_config_dirs", {"codex": str(tmp_path / "from-config")}
    )
    assert platform_config_dir(PLATFORMS["codex"]) == tmp_path / "from-config"


def test_platform_config_dir_expands_tilde_in_the_override(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setitem(CONFIG, "harness_config_dirs", {"qwen": "~/qwen-home"})
    monkeypatch.delenv("QWEN_HOME", raising=False)
    assert platform_config_dir(PLATFORMS["qwen"]) == tmp_path / "qwen-home"


@pytest.mark.parametrize(
    "name,var",
    [
        ("claude", "CLAUDE_CONFIG_DIR"),
        ("codex", "CODEX_HOME"),
        ("qwen", "QWEN_HOME"),
        ("forgecode", "FORGE_CONFIG"),
    ],
)
def test_each_harness_declares_its_native_config_dir_env_var(name, var):
    """The env vars each harness itself documents; see qwen-code storage.ts
    getGlobalQwenDir (QWEN_HOME), codex-rs config (CODEX_HOME), Claude Code
    settings docs (CLAUDE_CONFIG_DIR), forge_config/src/reader.rs
    (FORGE_CONFIG)."""
    assert var in PLATFORMS[name].config_dir_env_vars


def test_config_declares_harness_config_dirs_empty_by_default():
    assert CONFIG.get("harness_config_dirs") == {}


def test_skills_dir_follows_the_config_dir_env_var(monkeypatch, tmp_path):
    """Relocating a harness config dir moves its skills directory with it."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-alt"))
    assert skill_search_paths(PLATFORMS["claude"]) == (
        tmp_path / "claude-alt" / "skills",
    )


# --------------------------------------------------------------------------
# Custom harnesses persist in CONFIG and merge with CLI flags
# --------------------------------------------------------------------------


def test_config_declares_custom_harnesses_empty_by_default():
    assert CONFIG.get("custom_harnesses") == ()


def test_claude_flavor_parses():
    target = parse_custom_harness_spec("workcode=claude:opencode:~/.opencode-work")
    assert target.flavor == "claude"
    assert target.binary == "opencode"


def test_multiple_instances_of_one_flavor_carry_separate_config_dirs(monkeypatch):
    monkeypatch.setitem(
        CONFIG,
        "custom_harnesses",
        (
            "codex-home=codex:codex:~/.codex-home",
            "codex-work=codex:codex:~/.codex-work",
        ),
    )
    targets = [parse_custom_harness_spec(s) for s in merge_custom_harness_specs()]
    assert {t.name for t in targets} == {"codex-home", "codex-work"}
    assert {t.flavor for t in targets} == {"codex"}
    assert len({t.config_dir for t in targets}) == 2


def test_cli_spec_overrides_the_config_spec_with_the_same_name(monkeypatch, tmp_path):
    monkeypatch.setitem(
        CONFIG, "custom_harnesses", ("codex-work=codex:codex:~/.codex-work",)
    )
    merged = merge_custom_harness_specs(
        [f"codex-work=codex:codex:{tmp_path / 'elsewhere'}"]
    )
    targets = {parse_custom_harness_spec(s).name: parse_custom_harness_spec(s) for s in merged}
    assert list(targets) == ["codex-work"]
    assert targets["codex-work"].config_dir == tmp_path / "elsewhere"


def test_claude_flavor_installs_markdown_commands_and_agents_md(tmp_path):
    """The claude flavor gets the portable bundle, renamed for its harness."""
    marketplace_root = Path(__file__).resolve().parents[3]
    base = tmp_path / "opencode-home"

    ok, msg = _install_markdown_commands_harness(
        marketplace_root, ["autorun"], base=base, display_name="OpenCode"
    )

    assert ok, msg
    assert (base / "AGENTS.md").is_file()
    guidance = (base / "AGENTS.md").read_text(encoding="utf-8")
    assert "OpenCode" in guidance
    assert "ForgeCode" not in guidance
    assert list((base / "commands").glob("ar-*.md"))


def test_opencode_install_lands_in_the_resolved_config_dir(monkeypatch, tmp_path):
    """OpenCode gets the portable bundle at ~/.config/opencode by default."""
    from autorun.install import _install_for_opencode

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("OPENCODE_CONFIG_DIR", raising=False)
    marketplace_root = Path(__file__).resolve().parents[3]

    ok, msg = _install_for_opencode(marketplace_root, ["autorun"])

    assert ok, msg
    base = tmp_path / ".config" / "opencode"
    guidance = (base / "AGENTS.md").read_text(encoding="utf-8")
    assert "OpenCode" in guidance
    assert "ForgeCode" not in guidance
    assert list((base / "commands").glob("ar-*.md"))


def test_opencode_install_follows_the_config_dir_env_var(monkeypatch, tmp_path):
    """OpenCode resolves its config root from XDG_CONFIG_HOME/opencode.

    Probed against opencode 1.18.13: with OPENCODE_CONFIG_DIR set to an empty
    directory, `opencode serve` still loaded ~/.config/opencode/opencode.json,
    and with XDG_CONFIG_HOME set it loaded <XDG>/opencode/opencode.json.
    Installing to OPENCODE_CONFIG_DIR would write where OpenCode never reads.
    """
    from autorun.install import _install_for_opencode

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("OPENCODE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    marketplace_root = Path(__file__).resolve().parents[3]

    ok, msg = _install_for_opencode(marketplace_root, ["autorun"])

    assert ok, msg
    assert (tmp_path / "xdg" / "opencode" / "AGENTS.md").is_file()


def test_opencode_ignores_the_env_var_it_does_not_read(monkeypatch, tmp_path):
    """OPENCODE_CONFIG_DIR must not steer the install: the binary ignores it."""
    from autorun.install import _install_for_opencode

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(tmp_path / "never-read"))
    marketplace_root = Path(__file__).resolve().parents[3]

    ok, msg = _install_for_opencode(marketplace_root, ["autorun"])

    assert ok, msg
    assert not (tmp_path / "never-read").exists()
    assert (tmp_path / ".config" / "opencode" / "AGENTS.md").is_file()
