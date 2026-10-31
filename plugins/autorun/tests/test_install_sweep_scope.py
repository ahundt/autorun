"""What the retirement sweep may visit when only some harnesses are selected.

The sweep (`traversal.retirements` over `orchestrate._config_roots`) retires
every marked tree no intent claims, as "no longer shipped". A tree another,
unselected harness still uses is claimed by nothing in a targeted run, so the
sweep must not reach it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autorun.installer import fs, orchestrate
from autorun.platforms import PLATFORMS

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("AUTORUN_HOME", str(tmp_path / "ar-home"))
    for name in ("CODEX_HOME", "XDG_CONFIG_HOME", "GEMINI_CLI_HOME", "PI_CODING_AGENT_DIR"):
        monkeypatch.delenv(name, raising=False)
    return home


def _marked_tree(target: Path, plugin: str = "ar") -> Path:
    source = target.parent / f".src-{target.name}"
    source.mkdir(parents=True)
    (source / "README.md").write_text("ours\n", encoding="utf-8")
    fs.publish_tree(source, target, plugin=plugin)
    assert fs.read_marker(target) is not None
    return target


def _install(home: Path, *harnesses: str):
    return orchestrate.install(
        marketplace_root=REPO,
        plugins=("ar",),
        settings={"skill_placement": {"": "auto"}},
        home=home,
        harnesses=tuple(PLATFORMS[name] for name in harnesses),
        available=(),
        state_dir=home / ".state",
    )


def test_a_gemini_only_install_leaves_antigravitys_trees_inside_gemini_home(sandbox):
    """~/.gemini/config/ is Antigravity's root, nested in Gemini's ~/.gemini/.

    `autorun --install --gemini` swept ~/.gemini/ and retired Antigravity's
    ~/.gemini/config/plugins/ar as "no longer shipped" while Antigravity's
    import_manifest.json still registered it.
    """
    antigravity = _marked_tree(sandbox / ".gemini" / "config" / "plugins" / "ar")
    stale = _marked_tree(sandbox / ".gemini" / "retired-route" / "ar")

    _install(sandbox, "gemini")

    assert (antigravity / "README.md").is_file(), "an unselected harness's tree was retired"
    assert not stale.exists(), "a stale tree in Gemini's own root still retires"

    orchestrate.uninstall(
        marketplace_root=REPO, plugins=("ar",), home=sandbox,
        harnesses=(PLATFORMS["gemini"],), available=(), teardown_enabled=False,
    )
    assert (antigravity / "README.md").is_file(), "--uninstall --gemini removed Antigravity's"


def test_an_install_without_the_custom_harness_flag_keeps_its_extension_source(
    sandbox, tmp_path
):
    """A custom Gemini-family harness given only via --custom-harness links its
    extension to extension-sources/<name>/ar. The next install without the
    flag swept that subtree (its name is neither registered nor selected),
    leaving the harness a dangling extension link. Uninstall still sweeps it.
    """
    sources = tmp_path / "sources"
    custom = _marked_tree(sources / "work" / "ar")
    common = dict(
        marketplace_root=REPO,
        plugins=("ar",),
        settings={"skill_placement": {"": "auto"}, "_extension_source_root": str(sources)},
        home=sandbox,
        harnesses=(PLATFORMS["gemini"],),
        available=(),
    )

    orchestrate.install(state_dir=sandbox / ".state", **common)
    assert (custom / "README.md").is_file(), "an unregistered harness's source was swept"

    orchestrate.uninstall(teardown_enabled=False, **common)
    assert not custom.exists(), "uninstall retires every staging subtree"


def test_a_codex_flavor_custom_harness_keeps_the_codex_plugin_and_its_entry(sandbox):
    """`--install --claude` with a Codex-flavor custom harness selected.

    prepared() staged the Codex plugin only for a harness *named* codex,
    while _config_roots swept ~/plugins for any harness of Codex *flavor*, so
    ~/plugins/ar was retired while the personal marketplace still listed it.
    """
    import json

    from autorun.installer import settings, steps

    spec = settings.CustomHarness("work", "codex", "codex", "~/.codex-work")
    common = dict(
        marketplace_root=REPO,
        plugins=("ar",),
        settings={"skill_placement": {"": "auto"}, "codex_plugin_marketplace": "personal"},
        home=sandbox,
        available=(),
        state_dir=sandbox / ".state",
    )
    assert orchestrate.install(harnesses=(PLATFORMS["codex"],), **common).ok
    plugin = sandbox / "plugins" / "ar" / ".codex-plugin" / "plugin.json"
    market = sandbox / ".agents" / "plugins" / "marketplace.json"
    assert plugin.is_file()

    result = orchestrate.install(
        harnesses=(PLATFORMS["claude"], settings.synthesize(spec)),
        step_table=settings.steps_for_custom(spec, steps.STEPS),
        **common,
    )

    assert result.ok, result.findings
    assert plugin.is_file(), "the Codex plugin a listed marketplace entry names was retired"
    assert [p["name"] for p in json.loads(market.read_text())["plugins"]] == ["ar"]
