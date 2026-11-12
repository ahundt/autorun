"""The `autorun-skills` plugin: the general skills that left `ar`, and the
upgrade that hands `ar`'s old copies of them over.

Before the split every one of these skills was a tree owned by `ar` in every
harness location `ar` wrote, and in `~/.agents/skills` where the shared root
was used. After an upgrade each one must be reachable exactly once per
harness, owned by `autorun-skills`, with no `ar`-owned copy left behind.

The old layout is not hand-built: it is what the real install walk writes
from this checkout rearranged into the earlier shape (every `autorun-skills`
skill inside `ar`, under its old name, and no second plugin). So a skill added
to or moved between the plugins later is covered without editing this file.

Claude Code is the one harness this file cannot reach: its skills live in a
versioned plugin cache that `claude plugin install` writes, and with no
`claude` binary the walk writes nothing there. What Claude copies is the
plugin's source directory, so `test_the_marketplace_ships_the_skills_plugin`
pins that `ar`'s source no longer carries a moved skill.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest

from autorun.config import CONFIG
from autorun.installer import discovery, orchestrate
from autorun.installer.fs import OWNED_MARKER_NAME, read_marker
from autorun.installer.skills import shippable_skills
from autorun.platforms import PLATFORMS
from skill_routes import reachable_skills

REPO = Path(__file__).resolve().parents[3]
SKILLS_PLUGIN = "autorun-skills"
MARKETPLACES = (Path(".claude-plugin/marketplace.json"), Path(".agents/plugins/marketplace.json"))

#: What `ar` keeps: the skills its own commands invoke or describe.
AR_SKILLS = {"commit", "philosophy", "plannew", "planprocess", "planrefine", "planupdate", "tmux"}


def _marketplace_names(root: Path) -> tuple[str, ...]:
    document = json.loads((root / MARKETPLACES[0]).read_text(encoding="utf-8"))
    return tuple(entry["name"] for entry in document["plugins"])


def _copy_checkout(destination: Path) -> Path:
    """The tracked files of this checkout, outside git, so parallel workers
    and the walk's own git probes cannot change what is installed."""
    listed = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z"], capture_output=True, check=True
    ).stdout.decode("utf-8").split("\0")
    for name in filter(None, listed):
        source = REPO / name
        if not (source.exists() or source.is_symlink()):
            continue
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)
    return destination


@pytest.fixture(scope="module")
def checkouts(tmp_path_factory) -> tuple[Path, Path]:
    """(current, earlier): this checkout, and the same files in the shape an
    earlier release shipped them."""
    current = _copy_checkout(tmp_path_factory.mktemp("current"))
    earlier = _copy_checkout(tmp_path_factory.mktemp("earlier"))
    moved = earlier / "plugins" / SKILLS_PLUGIN
    assert (moved / "skills").is_dir(), f"the checkout has no plugins/{SKILLS_PLUGIN}/skills"
    old_name = {new: old for old, new in CONFIG["skill_name_migrations"].items()}
    for name, skill in shippable_skills(moved).items():
        shutil.move(skill, earlier / "plugins" / "autorun" / "skills" / old_name.get(name, name))
    shutil.rmtree(moved)
    for manifest in MARKETPLACES:
        path = earlier / manifest
        document = json.loads(path.read_text(encoding="utf-8"))
        document["plugins"] = [p for p in document["plugins"] if p["name"] != SKILLS_PLUGIN]
        path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return current, earlier


@pytest.fixture
def homes(tmp_path, monkeypatch):
    """Switch the process to one of several isolated homes by name."""
    for name in ("CODEX_HOME", "XDG_CONFIG_HOME", "GEMINI_CLI_HOME", "QWEN_HOME",
                 "CLAUDE_CONFIG_DIR", "FORGE_CONFIG", "PI_CODING_AGENT_DIR"):
        monkeypatch.delenv(name, raising=False)

    def use(label: str) -> Path:
        home = tmp_path / label
        home.mkdir(exist_ok=True)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))
        monkeypatch.setenv("AUTORUN_HOME", str(tmp_path / f"{label}-ar"))
        return home

    return use


def _install(root: Path, home: Path, harness: str, placement: str = "auto", **extra):
    return orchestrate.install(
        marketplace_root=root,
        plugins=_marketplace_names(root),
        settings={"skill_placement": {"": placement}},
        home=home,
        harnesses=(PLATFORMS[harness],),
        available=(),
        state_dir=home / ".state",
        **extra,
    )


def _reachable(harness: str, root: Path) -> set[str]:
    import os

    return reachable_skills(dict(os.environ), _marketplace_names(root))[harness]


def _owner(skill_md: Path) -> str | None:
    """The plugin recorded by the nearest ownership marker above a SKILL.md."""
    for directory in skill_md.resolve().parents:
        marker = read_marker(directory)
        if marker is not None:
            return marker.plugin
    return None


def _moved(root: Path) -> set[str]:
    return set(shippable_skills(root / "plugins" / SKILLS_PLUGIN))


def _counts(paths: set[str]) -> Counter:
    return Counter(Path(path).parent.name for path in paths)


def test_the_marketplace_ships_the_skills_plugin():
    """One plugin per job: `ar` keeps the skills its commands use, and every
    general skill lives once, in `autorun-skills`, with a manifest for each
    harness family so Claude, Codex and the Gemini family all receive it."""
    from autorun.installer.entrypoint import parse_selection

    plugin = REPO / "plugins" / SKILLS_PLUGIN
    for manifest in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json",
                     "plugin.json", "gemini-extension.json"):
        document = json.loads((plugin / manifest).read_text(encoding="utf-8"))
        assert document["name"] == SKILLS_PLUGIN, manifest
    assert (plugin / "README.md").is_file() and (plugin / "LICENSE").is_file()
    for manifest in MARKETPLACES:
        entries = json.loads((REPO / manifest).read_text(encoding="utf-8"))["plugins"]
        assert SKILLS_PLUGIN in [entry["name"] for entry in entries], manifest
    assert SKILLS_PLUGIN in parse_selection(REPO, "all")

    ar = set(shippable_skills(REPO / "plugins" / "autorun"))
    moved = _moved(REPO)
    assert ar == AR_SKILLS, sorted(ar ^ AR_SKILLS)
    assert len(moved) == 11 and not ar & moved, sorted(moved)


@pytest.mark.parametrize("harness", sorted(PLATFORMS))
def test_a_fresh_install_reaches_each_moved_skill_once_owned_by_the_skills_plugin(
    checkouts, homes, harness
):
    current, _ = checkouts
    home = homes("fresh")
    assert _install(current, home, harness).ok

    reached = _reachable(harness, current)
    counts = _counts(reached)
    for name in _moved(current):
        assert counts[name] == (1 if reached else 0), (name, sorted(reached))
    for path in reached:
        skill_md = home / path.removeprefix("$HOME/")
        if skill_md.parent.name in _moved(current):
            assert _owner(skill_md) == SKILLS_PLUGIN, path


@pytest.mark.parametrize("earlier_placement", ["auto", "both"])
@pytest.mark.parametrize("harness", sorted(PLATFORMS))
def test_an_upgrade_from_an_ar_owned_layout_reaches_what_a_fresh_install_does(
    checkouts, homes, harness, earlier_placement
):
    """`both` reproduces the earlier shared-root layout, where `ar` also owned
    a copy of every skill in `~/.agents/skills` that Pi, Prime, OpenCode and
    ForgeCode read beside their own roots."""
    current, earlier = checkouts
    fresh_home = homes("fresh")
    assert _install(current, fresh_home, harness).ok
    fresh = _reachable(harness, current)

    home = homes("upgraded")
    assert _install(earlier, home, harness, earlier_placement).ok
    before = _reachable(harness, earlier)
    old_name = {new: old for old, new in CONFIG["skill_name_migrations"].items()}
    assert {old_name.get(n, n) for n in _moved(current)} <= set(_counts(before)) or not before

    result = _install(current, home, harness)
    assert result.ok, result.lines()
    after = _reachable(harness, current)

    assert after == fresh, (sorted(after - fresh), sorted(fresh - after))
    left = [
        tree for tree in home.rglob(OWNED_MARKER_NAME)
        if json.loads(tree.read_text(encoding="utf-8")).get("plugin") == "ar"
        and tree.parent.name in _moved(current) | set(old_name.values())
    ]
    assert not left, left


@pytest.mark.parametrize("harness", sorted(PLATFORMS))
def test_an_edited_ar_owned_copy_of_a_moved_skill_is_kept_and_reported(
    checkouts, homes, harness
):
    """A user edit inside a tree autorun installed is kept and named, by every
    path. Handing the tree to another plugin must not become the one path that
    silently replaces it."""
    current, earlier = checkouts
    home = homes("edited")
    assert _install(earlier, home, harness).ok
    before = sorted(
        home / path.removeprefix("$HOME/")
        for path in _reachable(harness, earlier)
        if Path(path).parent.name == "streamline-text"
    )
    if harness == "claude":
        assert before == [], "this walk writes nothing Claude reads (module docstring)"
        return
    [edited] = before
    edited.write_text(edited.read_text(encoding="utf-8") + "\nMy own rule.\n", encoding="utf-8")
    kept_text = edited.read_text(encoding="utf-8")

    result = _install(current, home, harness)

    assert edited.read_text(encoding="utf-8") == kept_text
    kept = [
        decision for decision in result.decisions
        # Gemini, Qwen and Antigravity reach the tree through a link to its
        # staged source, which is where the decision is made.
        if decision.verdict.value == "keep"
        and edited.resolve().is_relative_to(decision.target.resolve())
    ]
    assert kept and "edited" in kept[0].reason, [d.describe() for d in result.decisions]


@pytest.mark.parametrize("harness", sorted(PLATFORMS))
def test_a_users_own_skill_with_a_moved_name_is_kept_and_reported(
    checkouts, homes, harness
):
    """A directory the user made is theirs whatever its name. Where the
    harness reads it, autorun publishes no second copy and says which path it
    kept; where the harness does not, autorun's copy is published as usual."""
    current, earlier = checkouts
    home = homes("theirs")
    assert _install(earlier, home, harness).ok
    platform = PLATFORMS[harness]
    mine = discovery.shared_root(home=home) / "streamline-text"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text(
        "---\nname: streamline-text\ndescription: my own version\n---\nmine\n",
        encoding="utf-8",
    )

    result = _install(current, home, harness)

    assert result.ok, result.lines()
    assert (mine / "SKILL.md").read_text(encoding="utf-8").endswith("mine\n")
    assert read_marker(mine) is None
    reached = _reachable(harness, current)
    if not reached:
        assert harness == "claude", "only Claude's skills are outside this walk"
        return
    assert _counts(reached)["streamline-text"] == 1, sorted(reached)
    if platform.loads_shared_agents_skills:
        assert f"$HOME/{mine.relative_to(home).as_posix()}/SKILL.md" in reached
        warned = [f for f in result.findings if str(mine) in f.detail]
        assert warned, [f.detail for f in result.findings]


# --- the command line --------------------------------------------------------


@pytest.fixture
def cli(checkouts, homes, monkeypatch, capsys):
    """`autorun.__main__.main` against the copied checkout, with OpenCode as the
    one harness found on PATH and every subprocess stood in for."""
    from autorun.__main__ import main
    from autorun.installer import entrypoint

    current, _ = checkouts
    home = homes("cli")
    monkeypatch.setenv("AUTORUN_USE_DAEMON", "0")
    monkeypatch.setenv("AUTORUN_BUILD_COMMIT", "test-commit")
    monkeypatch.setattr(entrypoint, "_marketplace_root", lambda: current)
    monkeypatch.setattr(
        entrypoint, "_run", lambda argv, **_: subprocess.CompletedProcess(argv, 0, "", "")
    )
    monkeypatch.setattr(
        entrypoint.shutil, "which", lambda name: "/bin/opencode" if name == "opencode" else None
    )

    def run(*argv: str) -> str:
        capsys.readouterr()
        code = main(list(argv))
        out = capsys.readouterr().out
        assert code == 0, out
        return out

    return home, run


def _trees(home: Path) -> dict[str, dict[Path, str]]:
    """Each plugin's owned trees, with the marker text that records them."""
    found: dict[str, dict[Path, str]] = {}
    for marker in home.rglob(OWNED_MARKER_NAME):
        text = marker.read_text(encoding="utf-8")
        found.setdefault(json.loads(text).get("plugin", ""), {})[marker.parent] = text
    return found


def test_install_with_no_selection_installs_both_plugins(cli):
    home, run = cli
    run("--install")

    trees = _trees(home)
    skills = home / ".config" / "opencode" / "skills"
    assert {path.name for path in trees[SKILLS_PLUGIN]} == _moved(REPO)
    assert {path.name for path in trees["ar"] if path.parent == skills} == AR_SKILLS


def test_each_plugin_installs_and_uninstalls_alone(cli):
    home, run = cli
    run("--install")
    full = _trees(home)

    run("--uninstall", SKILLS_PLUGIN)
    assert SKILLS_PLUGIN not in _trees(home)
    assert _trees(home)["ar"] == full["ar"], "uninstalling the skills plugin touched ar"

    run("--install", "ar")
    assert SKILLS_PLUGIN not in _trees(home), "installing ar alone installed the skills plugin"

    run("--install", SKILLS_PLUGIN)
    assert _trees(home)[SKILLS_PLUGIN] == full[SKILLS_PLUGIN]

    run("--uninstall", "ar")
    assert "ar" not in _trees(home)
    assert _trees(home)[SKILLS_PLUGIN] == full[SKILLS_PLUGIN], "uninstalling ar touched the skills plugin"


def test_status_lists_the_skills_plugin(cli):
    _home, run = cli
    run("--install")
    assert SKILLS_PLUGIN in run("--status")
