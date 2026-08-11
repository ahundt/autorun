"""Which skills reach which harness, by which route, and what blocks a route.

One skill must reach a harness by exactly one route or it is listed twice and
the listing is budgeted. These tests pin that, plus the two defects found by
running the real installer: a blocked name that flipped an entire plugin to the
native route, and a published tree with no ownership marker.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autorun.installer.skills import (  # noqa: E402
    blocked_names,
    bridge_intents,
    is_shippable,
    routes_for,
    shippable_skills,
    skill_intents,
)
from autorun.installer.traversal import Context  # noqa: E402


class Route:
    def __init__(self, sub: str) -> None:
        self.sub = sub

    def destinations(self, config: Path) -> tuple[Path, ...]:
        return (config / self.sub,)


@dataclass(frozen=True, slots=True)
class FakePlatform:
    name: str
    loads_shared_agents_skills: bool
    config_dir: str = "~/.fake/"
    # Two fields, because they genuinely differ: Antigravity reads
    # ~/.gemini/config/skills while writing to its plugins directory, and
    # ForgeCode reads ~/forge/skills with no write route at all.
    native_skills: object = None
    skill_search_routes: tuple = ()


def _home_context(tmp_path, monkeypatch, marketplace_root):
    """A Context whose `home` agrees with `$HOME`, which is the only seam.

    Setting the field alone moves some routes and not others: anything anchored
    at `Path.home()` reads `$HOME` directly. `Context` refuses the mismatch, so
    a test that wants an isolated home redirects the variable.
    """
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    # Both names: Path.home() resolves through os.path.expanduser, which reads
    # USERPROFILE on Windows and HOME elsewhere and never consults the other,
    # so setting one isolates this test on one platform and lets it write the
    # real home on the other.
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return Context(marketplace_root=marketplace_root, home=home)


@pytest.fixture
def plugin(tmp_path: Path) -> Path:
    root = tmp_path / "plugins" / "ar"
    (root / "skills").mkdir(parents=True)
    for name in ("commit", "philosophy"):
        (root / "skills" / name).mkdir()
        (root / "skills" / name / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    return root


@pytest.fixture
def shared(tmp_path: Path) -> Path:
    return tmp_path / "shared"


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch) -> Context:
    return _home_context(tmp_path, monkeypatch, tmp_path)


# ─── What counts as a skill ──────────────────────────────────────────────────


def test_a_directory_without_skill_md_is_not_a_skill(tmp_path, plugin):
    """An asset-only directory beside real skills would become a catalog entry
    the harness cannot load."""
    assets = plugin / "skills" / "assets"
    assets.mkdir()
    (assets / "logo.png").write_bytes(b"\x89PNG")

    assert not is_shippable(assets)
    assert set(shippable_skills(plugin)) == {"commit", "philosophy"}


def test_a_plugin_with_no_skills_directory_ships_nothing(tmp_path):
    assert shippable_skills(tmp_path / "empty") == {}


# ─── Exactly one route per harness ───────────────────────────────────────────


@pytest.mark.parametrize(
    "reads_shared, placement, expected",
    [
        (True, "auto", (True, False)),
        (False, "auto", (False, True)),
        (True, "native", (False, True)),
        (False, "native", (False, True)),
        (True, "both", (True, True)),
        (False, "both", (False, True)),
    ],
)
def test_auto_yields_exactly_one_route(reads_shared, placement, expected):
    platform = FakePlatform("x", reads_shared)

    assert routes_for(platform, placement) == expected


def test_auto_gives_every_registered_harness_exactly_one_route():
    """Checked against the real registry, because the contradiction that put
    all 17 skills into Qwen twice was a registry fact, not a code path."""
    from autorun.platforms import PLATFORMS

    for platform in PLATFORMS.values():
        shared, native = routes_for(platform, "auto")
        assert shared != native, f"{platform.name}: auto must yield one route"


def test_codex_native_route_is_inside_the_plugin_skills_directory(ctx):
    from autorun.installer.discovery import codex_plugin_source, skill_destinations
    from autorun.platforms import PLATFORMS

    assert skill_destinations(PLATFORMS["codex"]) == (
        codex_plugin_source() / "skills",
    )


# ─── A blocked name is a fact about the name ─────────────────────────────────


def test_a_user_authored_name_blocks_only_itself(plugin, shared, ctx):
    """The old installer computed a global conflict list and then republished
    every skill of every plugin natively if it was non-empty. One collision on
    `streamline-text` put all 17 skills in the extension as well as the shared
    root."""
    shared.mkdir(parents=True)
    (shared / "commit").mkdir()
    (shared / "commit" / "SKILL.md").write_text("mine\n", encoding="utf-8")

    reader = FakePlatform("reader", True, native_skills=Route("skills"))
    intents = list(skill_intents(reader, ctx, {"ar": plugin}, shared_root_override=shared))
    by_name = {i.target.name: i.target for i in intents}

    assert by_name["philosophy"].parent == shared, "unblocked name keeps the shared route"
    assert by_name["commit"].parent != shared, "only the blocked name falls back"
    assert len(intents) == 2, "no skill lost, none published twice"


def test_both_places_a_skill_on_both_routes(plugin, shared, ctx):
    """`both` exists precisely to show one skill twice. An unconditional
    `continue` after the shared yield made it identical to `auto`, so the mode
    the user chose to get a native copy delivered exactly what `auto` does."""
    reader = FakePlatform("reader", True, native_skills=Route("skills"))

    intents = list(
        skill_intents(reader, ctx, {"ar": plugin}, placement="both", shared_root_override=shared)
    )

    for name in shippable_skills(plugin):
        parents = {i.target.parent for i in intents if i.target.name == name}
        assert shared in parents, f"{name} missing the shared route"
        assert len(parents) == 2, f"{name} reached {len(parents)} route(s), want both"


def test_auto_still_places_a_skill_once(plugin, shared, ctx):
    """The companion to the above: widening `both` must not widen `auto`, which
    is the default and where a duplicate would double every harness's listing."""
    reader = FakePlatform("reader", True, native_skills=Route("skills"))

    intents = list(
        skill_intents(reader, ctx, {"ar": plugin}, placement="auto", shared_root_override=shared)
    )

    assert len(intents) == len(shippable_skills(plugin))
    assert {i.target.parent for i in intents} == {shared}


def test_collision_detection_is_case_folded(plugin, shared):
    """macOS would silently alias `Commit` onto `commit` while Linux would not.
    A route that works on one machine and clobbers on the other is worse than
    one that refuses on both."""
    shared.mkdir(parents=True)
    (shared / "Commit").mkdir()
    (shared / "Commit" / "SKILL.md").write_text("theirs\n", encoding="utf-8")

    assert "commit" in blocked_names(shippable_skills(plugin), shared, "ar")


def test_a_directory_we_own_is_not_blocked(plugin, shared, ctx):
    """Our own previous publication must not read as a collision, or a second
    install would refuse to update anything."""
    from autorun.installer.fs import publish_tree

    shared.mkdir(parents=True)
    publish_tree(plugin / "skills" / "commit", shared / "commit", plugin="ar")

    assert blocked_names(shippable_skills(plugin), shared, "ar") == frozenset()


def test_another_plugins_tree_blocks_the_name(plugin, shared):
    from autorun.installer.fs import publish_tree

    shared.mkdir(parents=True)
    publish_tree(plugin / "skills" / "commit", shared / "commit", plugin="pdf-extractor")

    assert "commit" in blocked_names(shippable_skills(plugin), shared, "ar")


# ─── The bridge ──────────────────────────────────────────────────────────────


def test_the_bridge_links_by_default(plugin, shared, ctx):
    """A user editing a skill expects the edit to apply everywhere. A copy
    forks silently, so the harness with the copy keeps showing the old text
    with nothing to indicate why."""
    shared.mkdir(parents=True)
    (shared / "commit").mkdir()
    (shared / "commit" / "SKILL.md").write_text("# commit\n", encoding="utf-8")

    intents = list(bridge_intents(FakePlatform("w", False, native_skills=Route("plugins"),
                                 skill_search_routes=(Route("skills"),)),
                                  ctx, shared_root_override=shared))

    assert intents
    assert all(i.settings["bridge"] == "link" for i in intents)


def test_a_harness_that_reads_the_shared_root_needs_no_bridge(plugin, shared, ctx):
    shared.mkdir(parents=True)

    reader = FakePlatform("r", True, native_skills=Route("skills"),
                          skill_search_routes=(Route("skills"),))

    assert list(bridge_intents(reader, ctx, shared_root_override=shared)) == []


def test_the_bridge_refuses_a_symlinked_skills_directory(tmp_path, shared, monkeypatch):
    """Claude Code stops loading user skills entirely when that directory is a
    symlink (anthropics/claude-code#38051), so bridging into one disables the
    very skills it is delivering.

    ``$HOME`` is redirected rather than only ``Context.home``: the destination
    is resolved from the registry through ``Path.home()``, so setting the field
    alone would leave the check looking at the real home and pass for the wrong
    reason.
    """
    shared.mkdir(parents=True)
    (shared / "commit").mkdir()
    (shared / "commit" / "SKILL.md").write_text("# commit\n", encoding="utf-8")
    home = tmp_path / "linked"
    (home / ".fake").mkdir(parents=True)
    (home / ".fake" / "skills").symlink_to(shared)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    intents = list(bridge_intents(
        FakePlatform("w", False, native_skills=Route("plugins"),
                                 skill_search_routes=(Route("skills"),)),
        Context(marketplace_root=tmp_path, home=home),
        shared_root_override=shared,
    ))

    assert intents == []


# ─── Refusals that happen before anything is written ────────────────────────


def test_native_placement_is_refused_for_a_harness_with_no_native_route():
    """`--skill-placement native` gave ForgeCode and OpenCode zero skills and
    reported success. Refusing up front is the difference between a typo and a
    silent no-op."""
    from autorun.installer.skills import unsatisfiable
    from autorun.platforms import PLATFORMS

    refused = unsatisfiable(PLATFORMS.values(), "native")

    assert refused, "at least one harness has no native route"
    assert all("no native skill route" in message for message in refused)
    assert any("auto" in message and "both" in message for message in refused), \
        "a refusal names what to do instead"


@pytest.mark.parametrize("placement", ["auto", "both"])
def test_placements_that_keep_the_shared_root_serve_every_harness(placement):
    from autorun.installer.skills import unsatisfiable
    from autorun.platforms import PLATFORMS

    assert unsatisfiable(PLATFORMS.values(), placement) == ()


def test_two_plugins_claiming_one_skill_name_is_an_error(tmp_path, plugin):
    """One route, one name. Picking a winner silently installs whichever was
    resolved last and gives the other plugin's users a skill they did not
    write."""
    from autorun.installer.skills import duplicate_names

    other = tmp_path / "plugins" / "pdf"
    (other / "skills" / "commit").mkdir(parents=True)
    (other / "skills" / "commit" / "SKILL.md").write_text("# theirs\n", encoding="utf-8")

    clashes = duplicate_names({"ar": plugin, "pdf": other})

    assert set(clashes) == {"commit"}
    assert clashes["commit"] == ("ar:commit", "pdf:commit"), "both claimants are named"


def test_a_single_plugin_cannot_clash_with_itself(plugin):
    from autorun.installer.skills import duplicate_names

    assert duplicate_names({"ar": plugin}) == {}


def test_duplicate_detection_is_case_folded(tmp_path, plugin):
    """macOS aliases Commit onto commit; Linux does not. A collision on one
    machine and not the other is worse than one on both."""
    from autorun.installer.skills import duplicate_names

    other = tmp_path / "plugins" / "pdf"
    (other / "skills" / "Commit").mkdir(parents=True)
    (other / "skills" / "Commit" / "SKILL.md").write_text("# theirs\n", encoding="utf-8")

    assert set(duplicate_names({"ar": plugin, "pdf": other})) == {"commit"}


def test_a_rename_retires_the_old_name_only_once_the_new_one_ships():
    """A half-applied rename must never remove a skill without providing its
    replacement."""
    from autorun.installer.skills import retired_names

    assert retired_names([]) == (), "nothing shipped, nothing retired"
    assert isinstance(retired_names(["commit"]), tuple)


def test_bridge_mode_none_produces_nothing(plugin, shared, ctx):
    shared.mkdir(parents=True)

    assert list(bridge_intents(FakePlatform("w", False, native_skills=Route("plugins"),
                                 skill_search_routes=(Route("skills"),)),
                               ctx, mode="none", shared_root_override=shared)) == []


def test_a_broken_bridge_for_a_retired_skill_is_planned_for_removal(
    tmp_path, shared, ctx, monkeypatch
):
    shared.mkdir(parents=True)
    destination = tmp_path / "claude" / "skills"
    destination.mkdir(parents=True)
    stale = destination / "retired"
    stale.symlink_to(shared / "retired")
    monkeypatch.setattr(
        "autorun.installer.discovery.skill_destinations",
        lambda _platform, reading=False: (destination,) if reading else (),
    )

    intents = list(
        bridge_intents(
            FakePlatform("w", False, native_skills=Route("plugins")),
            ctx,
            shared_root_override=shared,
        )
    )

    retired = next(intent for intent in intents if intent.target == stale)
    assert retired.source is None and retired.kind.value == "link"
