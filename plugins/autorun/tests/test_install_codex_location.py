"""Where the Codex personal plugin lives, and what the marketplace entry says.

Codex reads ~/.agents/plugins/marketplace.json and resolves a local
source.path relative to its root, the home directory; the path must stay inside
it (codex-rs/core-plugins/src/remote/share/checkout.rs, personal path rules).
Codex's own default for a personal plugin is ~/plugins/<name> (same file,
editable_plugin_path_for_checkout), which is autorun's default too. CONFIG
codex_plugin_source_dir moves it, e.g. to ~/.codex/plugins as Codex's website
documentation suggests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autorun import config as config_module
from autorun.installer import status
from autorun.platforms import PLATFORMS

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("AUTORUN_HOME", str(tmp_path / "ar-home"))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return home


def _install(home):
    from autorun.installer import orchestrate

    return orchestrate.install(
        marketplace_root=REPO,
        plugins=("ar",),
        settings={"skill_placement": {"": "auto"}, "codex_plugin_marketplace": "personal"},
        home=home,
        harnesses=(PLATFORMS["codex"],),
        available=(),
        state_dir=home / ".state",
    )


def _entries(home: Path) -> dict:
    market = home / ".agents" / "plugins" / "marketplace.json"
    if not market.exists():
        return {}
    return {p["name"]: p for p in json.loads(market.read_text())["plugins"]}


def test_the_default_is_codexs_own_personal_plugin_location(sandbox):
    result = _install(sandbox)

    assert result.ok is True
    source = sandbox / "plugins" / "ar"
    assert (source / ".codex-plugin" / "plugin.json").is_file()
    assert _entries(sandbox)["ar"]["source"] == {"source": "local", "path": "./plugins/ar"}


@pytest.mark.parametrize(
    ("setting", "relative"),
    [("~/.codex/plugins", ".codex/plugins/ar"), ("~/tools/codex-plugins", "tools/codex-plugins/ar")],
)
def test_the_marketplace_entry_follows_a_configured_location(
    sandbox, monkeypatch, setting, relative
):
    """The entry was a literal "./plugins/<name>", so any other setting left
    Codex pointing at a directory that does not exist."""
    monkeypatch.setitem(config_module.CONFIG, "codex_plugin_source_dir", setting)

    assert _install(sandbox).ok is True

    assert (sandbox / relative / ".codex-plugin" / "plugin.json").is_file()
    assert _entries(sandbox)["ar"]["source"]["path"] == f"./{relative}"
    assert not (sandbox / "plugins").exists()


def test_moving_the_location_replaces_the_entry_every_earlier_release_wrote(
    sandbox, monkeypatch
):
    """Releases through 1.0.0rc3 wrote "./plugins/ar" whatever the setting.

    Preflight read that same-name entry as a user conflict and refused the
    install that would have replaced it.
    """
    assert _install(sandbox).ok is True
    assert _entries(sandbox)["ar"]["source"]["path"] == "./plugins/ar"
    monkeypatch.setitem(config_module.CONFIG, "codex_plugin_source_dir", "~/.codex/plugins")

    result = _install(sandbox)

    assert result.ok is True, result.findings
    assert _entries(sandbox)["ar"]["source"]["path"] == "./.codex/plugins/ar"


def test_a_location_outside_home_is_refused_before_anything_is_written(
    sandbox, tmp_path, monkeypatch
):
    """Codex rejects a personal plugin path outside the home directory."""
    outside = tmp_path / "elsewhere"
    monkeypatch.setitem(config_module.CONFIG, "codex_plugin_source_dir", str(outside))

    result = _install(sandbox)

    assert result.ok is False
    assert any(str(outside) in finding.detail for finding in result.findings), result.findings
    assert not outside.exists()
    assert "ar" not in _entries(sandbox)


def test_codex_is_not_pointed_at_a_plugins_directory_that_is_not_autoruns(sandbox):
    """A non-empty ~/plugins/ar without autorun's marker is kept as the user's.

    The install still wrote a marketplace entry naming it, so Codex loaded the
    user's directory as autorun's plugin.
    """
    theirs = sandbox / "plugins" / "ar"
    theirs.mkdir(parents=True)
    (theirs / "notes.txt").write_text("mine", encoding="utf-8")

    result = _install(sandbox)

    assert (theirs / "notes.txt").read_text(encoding="utf-8") == "mine"
    assert sorted(p.name for p in theirs.iterdir()) == ["notes.txt"]
    assert "ar" not in _entries(sandbox), "Codex must not be pointed at their directory"
    warnings = [f for f in result.findings if f.level is status.Level.WARN and str(theirs) in f.detail]
    assert warnings, result.findings
    assert "codex_plugin_source_dir" in (warnings[0].fix or ""), warnings[0]


def test_a_directory_that_is_not_autoruns_is_not_registered_with_codex(sandbox):
    """`codex plugin add ar@personal` would install the user's directory."""
    import subprocess

    from autorun.installer import orchestrate

    theirs = sandbox / "plugins" / "ar"
    theirs.mkdir(parents=True)
    (theirs / "notes.txt").write_text("mine", encoding="utf-8")
    calls = []

    def record(argv, *args, **kwargs):
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    orchestrate.install(
        marketplace_root=REPO,
        plugins=("ar",),
        settings={"skill_placement": {"": "auto"}, "codex_plugin_marketplace": "personal",
                  "conductor": False},
        home=sandbox,
        harnesses=(PLATFORMS["codex"],),
        available=("codex",),
        run_command=record,
        state_dir=sandbox / ".state",
    )

    assert not [argv for argv in calls if argv[:3] == ("codex", "plugin", "add")], calls
