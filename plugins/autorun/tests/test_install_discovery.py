"""Where autorun installs from, and where each plugin lives inside it.

Both questions are answered today by long procedural searches, one of which has
a 70-line inline second copy that swallows the errors the shared one reports.
These tests pin the priority order and the two failure modes found by running
the new resolver against this repository's real manifest.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.request import url2pathname

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


def test_an_editable_checkout_whose_path_has_a_space_still_resolves(tmp_path):
    """`direct_url.json` records a URL, and a URL percent-encodes a space.

    An editable install writes `file:///Users/me/My%20Projects/autorun`, and
    stripping only the `file://` prefix leaves the `%20` in the path — so the
    resolver looked for a directory named `My%20Projects` and found nothing.
    Directory names with spaces are ordinary on macOS and Windows, and the
    failure is silent: development installs there simply never resolve their
    own source and fall through to the last-resort search.

    `url2pathname` is the decoder for this, and it also fixes the Windows
    shape, where `file:///C:/src` left a leading slash on the drive letter.
    """
    from autorun.installer.discovery import _from_editable_install

    checkout = tmp_path / "My Projects" / "autorun"
    (checkout / "plugins" / "autorun").mkdir(parents=True)
    assert "%20" in checkout.as_uri(), "this platform did not encode the space"

    # Where the strategy looks: two levels above the running file.
    running = tmp_path / "site-packages" / "autorun" / "discovery.py"
    # A synthetic version: the glob is `autorun*.dist-info`, and naming the
    # real one here would make this file a release-checklist obligation.
    dist_info = tmp_path / "site-packages" / "autorun-0.0.0.dist-info"
    dist_info.mkdir(parents=True)
    running.parent.mkdir(parents=True)
    (dist_info / "direct_url.json").write_text(
        json.dumps({"url": checkout.as_uri(), "dir_info": {"editable": True}}),
        encoding="utf-8",
    )

    offered = list(_from_editable_install(running))

    assert checkout in offered, offered
    assert not any("%20" in str(path) for path in offered), offered


def test_an_editable_checkout_on_a_network_share_keeps_its_host(tmp_path):
    """A `file://` URL's authority is the server, and dropping it changes machine.

    `pip install -e \\\\build01\\share\\autorun` records
    `file://build01/share/autorun`. Feeding only `urlparse(url).path` to
    `url2pathname` decodes `/share/autorun` and discards `build01`, so the
    resolver goes looking on the *local* disk for a path that is meant to be on
    a file server. It finds nothing, says nothing, and falls through to the
    last-resort search — the same silent failure the `%20` fix above closed,
    for the other half of the URL.

    A UNC checkout is the ordinary shape for a Windows team share, so Windows
    gets the path. POSIX has no syntax for a remote authority, and Python 3.14
    made that explicit by raising `URLError: file:// scheme is supported only
    on localhost` rather than inventing one — so the record is skipped there,
    which is what "we cannot resolve this" has to look like: no candidate,
    rather than a local path pointing somewhere else.
    """
    from autorun.installer.discovery import _from_editable_install

    running = tmp_path / "site-packages" / "autorun" / "discovery.py"
    running.parent.mkdir(parents=True)
    dist_info = tmp_path / "site-packages" / "autorun-0.0.0.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "direct_url.json").write_text(
        json.dumps(
            {"url": "file://build01/share/My%20Projects", "dir_info": {"editable": True}}
        ),
        encoding="utf-8",
    )

    offered = [str(path) for path in _from_editable_install(running)]

    if os.name == "nt":
        assert offered[0] == "\\\\build01\\share\\My Projects", offered
        assert not any("%20" in path for path in offered), offered
    else:
        assert offered == [], (
            f"a host-qualified URL resolved to a local path on POSIX: {offered}"
        )


def test_a_localhost_authority_is_not_mistaken_for_a_file_server(tmp_path):
    """`file://localhost/opt/src` and `file:///opt/src` name the same path.

    RFC 8089 lets a local path carry either an empty authority or `localhost`,
    and pip has written both. Preserving the authority blindly would turn the
    second spelling into `//localhost/opt/src`, which is a network path on
    Windows and an implementation-defined one on POSIX.
    """
    from autorun.installer.discovery import _from_editable_install

    running = tmp_path / "site-packages" / "autorun" / "discovery.py"
    running.parent.mkdir(parents=True)
    dist_info = tmp_path / "site-packages" / "autorun-0.0.0.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "direct_url.json").write_text(
        json.dumps({"url": "file://localhost/opt/src", "dir_info": {"editable": True}}),
        encoding="utf-8",
    )

    offered = [str(path) for path in _from_editable_install(running)]

    assert "localhost" not in offered[0], offered
    assert offered[0] == str(Path(url2pathname("/opt/src"))), offered


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

    # Both names: Path.home() resolves through os.path.expanduser, which reads
    # USERPROFILE on Windows and HOME elsewhere and never consults the other,
    # so setting one isolates this test on one platform and lets it write the
    # real home on the other.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
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
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
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
