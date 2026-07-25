"""One memory-install pathway for every harness, differences declared as data.

autorun previously had two hand-written memory installers that disagreed with
each other: the Codex path merged a sentinel-delimited region and preserved user
content, while the ForgeCode path called shutil.copy2 and destroyed it. Claude
Code had none at all.

The differences that actually matter — filename, template, sentinel slug — are
now fields on ``platforms.Platform``, matching how this project already declares
hook protocols, event maps and tool names as data rather than branching code.
Adding a harness needs no new installer.

Content stays per-harness on purpose. The Claude template names Claude Code's
~99% auto-compaction behavior; asserting that to Codex would be false, and
guidance a reader cannot act on is worse than none.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autorun.install import (  # noqa: E402
    _claude_memory_workaround_enabled,
    install_platform_memory,
    platform_memory_sentinels,
    strip_platform_memory,
)
from autorun.platforms import PLATFORMS  # noqa: E402

MEMORY_PLATFORMS = ["claude", "codex", "forgecode"]

REAL_PLUGIN_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(params=MEMORY_PLATFORMS)
def platform(request):
    return PLATFORMS[request.param]


# --------------------------------------------------------------------------
# The declarations themselves
# --------------------------------------------------------------------------


def test_every_memory_platform_declares_a_complete_triple(platform):
    """A partial declaration would silently skip the install."""
    assert platform.memory_filename
    assert platform.memory_template
    assert platform.memory_sentinel_slug


def test_declared_templates_actually_ship(platform):
    """A typo'd template path degrades to a silent no-op."""
    template = REAL_PLUGIN_DIR.joinpath(
        "src", "autorun", *platform.memory_template.split("/")
    )
    assert template.is_file(), f"missing template for {platform.name}: {template}"


def test_sentinel_slugs_are_unique_across_platforms():
    """Two harnesses sharing a slug would strip each other's blocks."""
    slugs = [PLATFORMS[n].memory_sentinel_slug for n in MEMORY_PLATFORMS]
    assert len(set(slugs)) == len(slugs)


def test_codex_slug_is_unchanged_for_backward_compatibility():
    """Changing it orphans blocks already written into users' AGENTS.md."""
    assert PLATFORMS["codex"].memory_sentinel_slug == "codex-agents-md"


def test_platforms_without_a_memory_file_declare_nothing():
    """Gemini/Qwen/Antigravity have no global instructions file autorun owns."""
    for name in ("gemini", "qwen", "antigravity"):
        assert PLATFORMS[name].memory_filename == ""


def test_sentinels_are_derived_from_the_slug(platform):
    start, end = platform_memory_sentinels(platform)
    assert start == f"<!-- autorun:{platform.memory_sentinel_slug}:start -->"
    assert end == f"<!-- autorun:{platform.memory_sentinel_slug}:end -->"


# --------------------------------------------------------------------------
# The shared install/strip pathway, exercised per platform
# --------------------------------------------------------------------------


def test_install_writes_the_declared_filename(platform, tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    assert install_platform_memory(platform, REAL_PLUGIN_DIR, config_dir) is True
    assert (config_dir / platform.memory_filename).is_file()


def test_install_preserves_user_content(platform, tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    target = config_dir / platform.memory_filename
    user_text = "# Mine\n\nAlways run the linter.\n"
    target.write_text(user_text, encoding="utf-8")

    install_platform_memory(platform, REAL_PLUGIN_DIR, config_dir)

    assert user_text.rstrip("\n") in target.read_text(encoding="utf-8")


def test_install_then_strip_round_trips(platform, tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    target = config_dir / platform.memory_filename
    user_text = "# Mine\n\nAlways run the linter.\n"
    target.write_text(user_text, encoding="utf-8")

    install_platform_memory(platform, REAL_PLUGIN_DIR, config_dir)
    assert strip_platform_memory(platform, config_dir) is True

    assert target.read_text(encoding="utf-8").split() == user_text.split()


def test_install_is_idempotent(platform, tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    install_platform_memory(platform, REAL_PLUGIN_DIR, config_dir)
    first = (config_dir / platform.memory_filename).read_text(encoding="utf-8")
    install_platform_memory(platform, REAL_PLUGIN_DIR, config_dir)
    assert (config_dir / platform.memory_filename).read_text(encoding="utf-8") == first


def test_install_returns_false_for_a_platform_without_a_memory_file(tmp_path):
    assert install_platform_memory(PLATFORMS["gemini"], REAL_PLUGIN_DIR, tmp_path) is False


def test_install_returns_false_when_the_template_is_missing(platform, tmp_path):
    """Older builds and partial extracts must not raise."""
    assert install_platform_memory(platform, tmp_path / "no-plugin", tmp_path) is False


def test_strip_returns_false_for_a_platform_without_a_memory_file(tmp_path):
    assert strip_platform_memory(PLATFORMS["gemini"], tmp_path) is False


def test_one_platforms_strip_leaves_anothers_block_alone(tmp_path):
    """Slugs are per-platform, so blocks must not cross-delete.

    Codex and ForgeCode both use AGENTS.md, so a shared config dir is the
    realistic collision case.
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    codex, forge = PLATFORMS["codex"], PLATFORMS["forgecode"]

    install_platform_memory(codex, REAL_PLUGIN_DIR, config_dir)
    install_platform_memory(forge, REAL_PLUGIN_DIR, config_dir)
    strip_platform_memory(codex, config_dir)

    remaining = (config_dir / "AGENTS.md").read_text(encoding="utf-8")
    forge_start, _ = platform_memory_sentinels(forge)
    codex_start, _ = platform_memory_sentinels(codex)
    assert forge_start in remaining
    assert codex_start not in remaining


# --------------------------------------------------------------------------
# Claude-specific content — the guidance that must NOT reach other harnesses
# --------------------------------------------------------------------------


def _claude_template_text() -> str:
    return REAL_PLUGIN_DIR.joinpath(
        "src", "autorun", *PLATFORMS["claude"].memory_template.split("/")
    ).read_text(encoding="utf-8")


def test_claude_template_addresses_context_capacity_claims():
    """BUG #54673: no token measurement reaches this model family."""
    text = _claude_template_text().lower()
    assert "context capacity is not a reason to stop" in text
    assert "total_tokens" in text
    assert "compact" in text


def test_claude_template_addresses_continuing_when_told_to_continue():
    """The stop-gate half: resume unless a concrete blocker exists."""
    text = _claude_template_text().lower()
    assert "continue" in text
    assert "blocker" in text


def test_claude_template_never_tells_the_model_to_assert_something_untrue():
    """Escaping the stop gate must not require a false statement.

    Measured: 15.4% of stuck turns were cases where every available exit
    required asserting something the model knew to be untrue.
    """
    text = _claude_template_text().lower()
    assert "never assert something untrue" in text


@pytest.mark.parametrize("name", ["codex", "forgecode"])
def test_claude_specific_guidance_does_not_reach_other_harnesses(name):
    """Claude Code's compaction behavior is not a fact about Codex."""
    other = REAL_PLUGIN_DIR.joinpath(
        "src", "autorun", *PLATFORMS[name].memory_template.split("/")
    ).read_text(encoding="utf-8").lower()
    assert "total_tokens" not in other
    assert "auto-compacts" not in other


def test_claude_template_is_small_enough_to_stay_resident():
    """It loads in every session, in every project; keep it cheap."""
    assert len(_claude_template_text()) < 4000


# --------------------------------------------------------------------------
# The workaround gate
# --------------------------------------------------------------------------


def test_memory_workaround_is_on_by_default(monkeypatch):
    monkeypatch.delenv(
        "AUTORUN_BUG_CLAUDE_CODE_NO_TOKEN_COUNT_FOR_HOOKS_BUG_54673_WORKAROUND_ENABLED",
        raising=False,
    )
    assert _claude_memory_workaround_enabled() is True


@pytest.mark.parametrize("value", ["false", "0", "never"])
def test_memory_workaround_can_be_disabled(monkeypatch, value):
    monkeypatch.setenv(
        "AUTORUN_BUG_CLAUDE_CODE_NO_TOKEN_COUNT_FOR_HOOKS_BUG_54673_WORKAROUND_ENABLED",
        value,
    )
    assert _claude_memory_workaround_enabled() is False


@pytest.mark.parametrize("value", ["true", "1", "auto", "always"])
def test_memory_workaround_accepts_the_shared_enable_tokens(monkeypatch, value):
    monkeypatch.setenv(
        "AUTORUN_BUG_CLAUDE_CODE_NO_TOKEN_COUNT_FOR_HOOKS_BUG_54673_WORKAROUND_ENABLED",
        value,
    )
    assert _claude_memory_workaround_enabled() is True
