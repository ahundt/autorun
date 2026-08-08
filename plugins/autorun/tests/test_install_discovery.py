"""Where autorun installs from, and where each plugin lives inside it.

Both questions are answered today by long procedural searches, one of which has
a 70-line inline second copy that swallows the errors the shared one reports.
These tests pin the priority order and the two failure modes found by running
the new resolver against this repository's real manifest.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autorun.installer.discovery import (  # noqa: E402
    MARKETPLACE_MANIFEST,
    codex_plugin_source,
    config_dir,
    marketplace_root,
    personal_marketplace,
    plugin_dir,
    resolve_plugins,
    shared_root,
    skill_destinations,
)
from autorun.platforms import ConfigDirSkills  # noqa: E402


def make_root(path: Path, plugins: list[dict] | None = None) -> Path:
    (path / MARKETPLACE_MANIFEST).parent.mkdir(parents=True, exist_ok=True)
    (path / MARKETPLACE_MANIFEST).write_text(
        json.dumps({"plugins": plugins or []}), encoding="utf-8"
    )
    return path


# ─── Which directory we install from ─────────────────────────────────────────


def test_the_outermost_nested_root_wins(tmp_path):
    """This repository nests two roots: one at the top and one inside
    plugins/autorun/. The top one is the marketplace that lists both plugins,
    so taking the nearest resolves a checkout to a single plugin directory and
    finds no sibling at all."""
    repo = make_root(tmp_path / "repo")
    inner = make_root(repo / "plugins" / "autorun")
    running = inner / "src" / "autorun" / "install.py"
    running.parent.mkdir(parents=True)

    assert marketplace_root(running) == repo
    assert inner.is_dir(), "the inner root still exists; it just does not win"


def test_a_copy_with_no_root_above_it_installs_from_itself(tmp_path):
    """The materialized-extension and plugin-cache case: a Gemini extension
    keeps running from its own files and must install from them, not from a
    development checkout elsewhere on the machine."""
    lone = make_root(tmp_path / "ext" / "ar")
    running = lone / "src" / "x.py"
    running.parent.mkdir(parents=True)

    assert marketplace_root(running) == lone


def test_a_backup_copy_never_wins_unless_we_are_running_from_it(tmp_path):
    """A tree kept for reference is not the thing to install from — but a
    developer working inside a directory named `reference` still expects their
    own tree to be used."""
    repo = make_root(tmp_path / "repo")
    backup = make_root(tmp_path / "backup-repo")
    running = repo / "src" / "x.py"
    running.parent.mkdir(parents=True)

    assert marketplace_root(running) == repo
    assert marketplace_root(backup / "src" / "x.py") == backup


def test_no_root_anywhere_returns_none_rather_than_guessing(tmp_path):
    stray = tmp_path / "nothing" / "x.py"
    stray.parent.mkdir(parents=True)

    assert marketplace_root(stray) is None


# ─── Which directory a plugin lives in ───────────────────────────────────────


def test_a_github_source_object_resolves_a_name_that_matches_no_directory(tmp_path):
    """This repository's own manifest registers `ar` with a source *object*
    whose `subdirectory` is plugins/autorun. The resolver this replaces did
    `root / source` unconditionally, raising TypeError on the object form and
    swallowing it with a bare except, so the declared location was discarded
    and only directory-name matches ever worked."""
    root = make_root(tmp_path / "m", [{
        "name": "ar",
        "source": {"source": "github", "repo": "x/y", "subdirectory": "plugins/autorun"},
    }])
    (root / "plugins" / "autorun").mkdir(parents=True)

    assert plugin_dir(root, "ar") == (root / "plugins" / "autorun").resolve()


def test_the_manifest_outranks_a_same_named_directory(tmp_path):
    root = make_root(tmp_path / "m", [{"name": "ar", "source": "./plugins/autorun"}])
    (root / "plugins" / "autorun").mkdir(parents=True)
    (root / "ar").mkdir()

    assert plugin_dir(root, "ar") == (root / "plugins" / "autorun").resolve()


@pytest.mark.parametrize(
    "layout, name",
    [("plugins/pdf", "pdf"), ("flat", "flat")],
)
def test_layouts_resolve_when_the_manifest_says_nothing(tmp_path, layout, name):
    root = make_root(tmp_path / "p")
    (root / layout).mkdir(parents=True)

    assert plugin_dir(root, name) == root / layout


def test_an_unknown_plugin_resolves_to_none(tmp_path):
    assert plugin_dir(make_root(tmp_path / "p"), "ghost") is None


def test_a_broken_manifest_raises_instead_of_falling_through(tmp_path):
    """Swallowing the error means a typo in the manifest silently installs a
    directory the manifest never declared."""
    root = tmp_path / "b"
    (root / MARKETPLACE_MANIFEST).parent.mkdir(parents=True)
    (root / MARKETPLACE_MANIFEST).write_text("{not json", encoding="utf-8")
    (root / "ar").mkdir()

    with pytest.raises(json.JSONDecodeError):
        plugin_dir(root, "ar")


def test_two_names_for_one_directory_install_it_once(tmp_path):
    """A registered name and its directory name resolve to the same tree;
    installing it twice writes it twice."""
    root = make_root(tmp_path / "m", [{"name": "ar", "source": "./plugins/autorun"}])
    (root / "plugins" / "autorun").mkdir(parents=True)

    found, missing = resolve_plugins(root, ["ar", "autorun", "ghost"])

    assert len(found) == 1, found
    assert missing == ("ghost",), "a missing plugin is named, not counted"


# ─── Agreement with the implementation being replaced ────────────────────────


def test_the_real_repository_resolves_every_manifest_plugin():
    root = marketplace_root()

    assert root is not None
    assert plugin_dir(root, "ar") == (root / "plugins" / "autorun").resolve()
    assert plugin_dir(root, "pdf-extractor") == (
        root / "plugins" / "pdf-extractor"
    ).resolve()
    assert plugin_dir(root, "ghost") is None


def test_shared_and_codex_locations_follow_their_one_config_authority(
    tmp_path, monkeypatch
):
    from autorun.config import CONFIG

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setitem(CONFIG, "shared_agents_dir", "~/shared")
    monkeypatch.setitem(CONFIG, "shared_agents_skills_subdir", "skill-box")
    monkeypatch.setitem(CONFIG, "codex_plugin_source_dir", "~/plugin-box")

    assert shared_root() == tmp_path / "shared" / "skill-box"
    assert personal_marketplace() == tmp_path / "shared" / "plugins" / "marketplace.json"
    assert codex_plugin_source("ar") == tmp_path / "plugin-box" / "ar"


def test_config_and_skill_routes_share_override_env_default_precedence(
    tmp_path, monkeypatch
):
    from autorun.config import CONFIG

    monkeypatch.setenv("HOME", str(tmp_path))
    platform = SimpleNamespace(
        name="demo",
        config_dir="~/.demo",
        config_dir_env_vars=("DEMO_CONFIG",),
        config_dir_env_var_subdir="demo",
        native_skills=ConfigDirSkills("skills"),
    )

    assert config_dir(platform, env={}) == tmp_path / ".demo"
    assert config_dir(platform, env={"DEMO_CONFIG": "~/env"}) == tmp_path / "env" / "demo"

    monkeypatch.setitem(CONFIG, "harness_config_dirs", {"demo": "~/configured"})
    assert config_dir(platform, env={"DEMO_CONFIG": "~/env"}) == tmp_path / "configured"
    assert skill_destinations(platform) == (tmp_path / "configured" / "skills",)
