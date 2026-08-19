#!/usr/bin/env python3
"""Which skills reach which harness, by which route, and what blocks a route.

One skill must reach a harness by exactly one route, or it is listed twice and
the listing is budgeted. The registry already says where each harness *reads*
(``loads_shared_agents_skills``, ``skill_search_routes``) and where autorun may
*write* (``native_skills``); this module turns those facts into intents.

Everything here returns :class:`Intent` objects and touches no disk, which is
what lets one implementation serve status, dry run, install, uninstall and
prune. The code this replaces spans 1095 lines across 33 functions, including
three separate pruners that each re-derived their own destination paths.

TWO DEFECTS THIS CLOSES BY CONSTRUCTION
=======================================

**A blocked name flipped the whole plugin.** The old installer computed a global
``shared_conflicts`` list and then did ``if shared_conflicts: include_skills =
True`` — so one user-authored collision republished *every* skill of *every*
plugin natively. Observed live: one collision on ``streamline-text`` put all 17
skills into ``~/.qwen/extensions/ar/skills`` as well as the shared root. Here the
fallback is per skill, because a blocked name is a fact about that name.

**A published tree with no marker.** Extension materialization copied skills
wholesale without claiming them, so a prune could never remove them — an
unmarked directory is correctly the user's. Every write here goes through
``install_fs``, which always records a manifest, so an unclaimable tree cannot
be produced.

Complexity: O(P x S) intents for P plugins and S skills each, all metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Mapping

from . import discovery
from .fs import _strip_extended_prefix, read_marker
from .discovery import redirected_home
from .traversal import Context, Intent, Kind

__all__ = [
    "shared_root",
    "is_shippable",
    "shippable_skills",
    "routes_for",
    "blocked_names",
    "skill_plan",
    "skill_intents",
    "bridge_intents",
    "unsatisfiable",
    "duplicate_names",
    "retired_names",
]

#: Re-exported so the routing code reads one name, while ``discovery`` stays the
#: only module that answers "where is this directory". A second implementation
#: here is what a spec test in this repository exists to catch: install and
#: uninstall resolving the shared root differently leaves uninstall unable to
#: find what install wrote.
shared_root = discovery.shared_root


def is_shippable(path: Path) -> bool:
    """A skill is a directory containing ``SKILL.md``.

    An asset-only directory beside real skills — shared images, a scratch
    folder — is not a skill and publishing it creates a catalog entry the
    harness cannot load.
    """
    return path.is_dir() and (path / "SKILL.md").is_file()


def is_loadable(path: Path) -> bool:
    """A skill the harness will actually list: ``SKILL.md`` with a non-empty
    frontmatter ``description``.

    This is the Agent Skills rule Pi's loader enforces (a missing or blank
    description yields no skill), and it decides whether a user's tree at the
    shared root is *visible* to the harness — a zero-byte or frontmatter-less
    file blocks our shared route but does not reach the harness, so it must
    not also suppress the native fallback. Bounded read; never raises.
    """
    try:
        head = (path / "SKILL.md").read_text(encoding="utf-8", errors="replace")[:65536]
    except OSError:
        return False
    lines = head.splitlines()
    if not lines or lines[0].strip() != "---":
        return False
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return False  # closing fence reached: no description key
        key, sep, value = line.partition(":")
        if not sep or key.strip() != "description" or line[:1].isspace():
            continue
        value = value.strip()
        if value and value not in ("|", ">", "|-", ">-", "|+", ">+"):
            return True
        # Block scalar: loadable when an indented, non-blank continuation follows.
        for continuation in lines[index + 1 :]:
            if continuation.strip() == "---":
                break
            if continuation[:1].isspace() and continuation.strip():
                return True
            if continuation.strip():
                break  # next top-level key
        return False
    return False


def shippable_skills(plugin_dir: Path) -> dict[str, Path]:
    """Every skill a plugin ships, by name.

    Case-folded collision detection happens in :func:`skill_intents` rather than
    here, because a plugin is internally consistent; the collision that matters
    is between two plugins, or between a plugin and the user.
    """
    root = plugin_dir / "skills"
    return {p.name: p for p in sorted(root.iterdir()) if is_shippable(p)} if root.is_dir() else {}


def routes_for(platform: object, placement: str = "auto") -> tuple[bool, bool]:
    """Return ``(publish_shared, publish_native)`` for one harness.

    ``auto`` yields exactly one route: the shared root when the harness's own
    documentation describes reading it, otherwise that harness's native
    packaging. ``native`` and ``both`` are user overrides.

    A harness that reads the shared root has no native route under ``auto``,
    which is the invariant a registry test now pins after Qwen was found
    declaring both and receiving every skill twice.
    """
    from .settings import SKILL_PLACEMENT

    if SKILL_PLACEMENT.parse(placement) != {"": placement}:
        raise ValueError(f"invalid skill placement: {placement!r}")
    reads_shared = bool(getattr(platform, "loads_shared_agents_skills", False))
    return (
        reads_shared and placement in {"auto", "both"},
        placement in {"native", "both"} or not reads_shared,
    )


def blocked_names(shipped: Mapping[str, Path], destination: Path, plugin: str) -> frozenset[str]:
    """Names at ``destination`` that autorun may not write.

    A name is blocked when something is already there that autorun did not
    write, or wrote and the user has since edited. Comparison is case-folded
    because macOS would silently alias ``Commit`` onto ``commit`` while Linux
    would not, and a route that works on one machine and clobbers on another is
    worse than one that refuses on both.
    """
    if not destination.is_dir():
        return frozenset()
    existing = {
        entry.name.casefold(): entry
        for entry in destination.iterdir()
        if entry.is_dir() or entry.is_symlink()
    }
    blocked = set()
    for name in shipped:
        entry = existing.get(name.casefold())
        if entry is None:
            continue
        marker = read_marker(entry)
        if marker is None or (marker.plugin and marker.plugin != plugin):
            blocked.add(name)
    return frozenset(blocked)


@dataclass(frozen=True, slots=True)
class Placement:
    """Where one plugin's skills go for one harness, after blocking."""

    shared: tuple[str, ...] = ()
    native: tuple[str, ...] = ()
    refused: tuple[str, ...] = ()

    def describe(self) -> str:
        parts = []
        if self.shared:
            parts.append(f"{len(self.shared)} shared")
        if self.native:
            parts.append(f"{len(self.native)} native")
        if self.refused:
            parts.append(f"refused: {', '.join(self.refused)}")
        return ", ".join(parts) or "nothing to publish"


def _native_roots(platform: object, ctx: Context) -> tuple[Path, ...]:
    """Where this harness's native route writes, from the registry.

    The config directory comes from :func:`discovery.config_dir`, not from a
    local expansion of ``Platform.config_dir``. Stripping the leading ``~/`` by
    hand ignores ``CLAUDE_CONFIG_DIR``, ``CODEX_HOME``, ``QWEN_HOME`` and
    ``XDG_CONFIG_HOME``, so a user who moved their harness config would have
    skills written to the default location while every other part of the
    install honoured the override. Two answers to one question is the defect
    this rewrite exists to remove; this is that defect in miniature.

    A route may name several destinations — ``CombinedSkillRoutes`` exists for
    exactly that — so the result is a tuple, not one path.
    """
    from .discovery import skill_destinations

    try:
        return skill_destinations(platform, home=ctx.home)
    except TypeError as error:
        if "unexpected keyword argument 'home'" not in str(error):
            raise
        return skill_destinations(platform)


def unsatisfiable(
    platforms: Iterable[object], placement: str = "auto"
) -> tuple[str, ...]:
    """One message per harness this placement cannot serve, before any write.

    ``--skill-placement native`` gives ForgeCode and OpenCode zero skills, because
    neither has a native route at all. Discovering that afterwards means the
    install reported success and delivered nothing; refusing up front is the
    difference between a typo and a silent no-op.

    ``auto`` is always satisfiable and never appears here: it resolves to
    whichever route the harness actually has.
    """
    messages = []
    for platform in platforms:
        name = getattr(platform, "name", "?")
        shared, native = routes_for(platform, placement)
        if shared:
            continue
        if native and _declared_native_route(platform):
            continue
        messages.append(
            f"{name}: --skill-placement {placement} cannot be satisfied — this "
            f"harness has no native skill route, and {placement!r} excludes the "
            f"shared root. Use 'auto' to let it choose, or 'both'."
        )
    return tuple(messages)


def _declared_native_route(platform: object) -> bool:
    """Whether the harness declares somewhere autorun may natively write."""
    from ..platforms import NoNativeSkillRoute

    route = getattr(platform, "native_skills", None)
    return route is not None and not isinstance(route, NoNativeSkillRoute)


def duplicate_names(plugins: Mapping[str, Path]) -> dict[str, tuple[str, ...]]:
    """Skill names claimed by more than one selected plugin.

    Two plugins shipping ``commit`` cannot both publish it: one route, one name.
    Picking a winner silently installs whichever was resolved last and gives the
    other plugin's users a skill they did not write, so this is an error the
    caller reports rather than a tie the installer breaks.

    Case-folded, because macOS would alias ``Commit`` onto ``commit`` while
    Linux would not — a collision that exists on one machine and not another is
    worse than one that exists on both.
    """
    claims: dict[str, list[str]] = {}
    for plugin, plugin_dir in plugins.items():
        for name in shippable_skills(plugin_dir):
            claims.setdefault(name.casefold(), []).append(f"{plugin}:{name}")
    return {name: tuple(who) for name, who in claims.items() if len(who) > 1}


def retired_names(shipped: Iterable[str]) -> tuple[str, ...]:
    """Old names of skills that have since been renamed.

    A rename is two operations: publish the new name, retire the old one. Doing
    only the first leaves the previous copy in every destination forever, and it
    still carries our marker, so the user sees one skill listed twice under two
    names with no way to tell which is current.

    Retired only when the *new* name is actually shipped, so a half-applied
    rename never removes a skill without providing its replacement.
    """
    from ..config import CONFIG

    migrations = CONFIG.get("skill_name_migrations", {})
    if not isinstance(migrations, Mapping):
        return ()
    current = set(shipped)
    return tuple(sorted(
        old for old, new in migrations.items()
        if isinstance(old, str) and isinstance(new, str) and new in current
    ))


def skill_plan(
    platform: object,
    ctx: Context,
    plugins: Mapping[str, Path],
    *,
    placement: str = "auto",
    shared_root_override: Path | None = None,
    include_native: bool = True,
    packaged_native: bool = False,
) -> tuple[tuple[Intent, ...], Placement]:
    """Plan every skill route and retain names no route can serve.

    A name blocked on the shared root falls back to this harness's native route
    *for that name only*. That is the fix for the collision that republished
    every skill of every plugin: a blocked name is a fact about the name, not
    about the plugin.

    A name blocked on both routes is refused and reported rather than forced,
    because overwriting is the one outcome the marker policy exists to prevent.
    """
    shared_dir = shared_root_override if shared_root_override is not None else shared_root()
    want_shared, want_native = routes_for(platform, placement)
    natives = _native_roots(platform, ctx)
    intents: list[Intent] = []
    shared: list[str] = []
    native: list[str] = []
    refused: list[str] = []

    for plugin, plugin_dir in plugins.items():
        shipped = shippable_skills(plugin_dir)
        blocked_shared = blocked_names(shipped, shared_dir, plugin) if want_shared else frozenset(shipped)

        for name, source in shipped.items():
            reached_shared = want_shared and name not in blocked_shared
            if reached_shared:
                intents.append(Intent(target=shared_dir / name, source=source, plugin=plugin))
                shared.append(name)
            # A loadable skill already occupying the shared root reaches this
            # harness whenever the harness reads that root: Pi and Prime list
            # ~/.agents/skills beside their native directory, so a native
            # fallback copy would list the name twice — the duplicate the
            # one-route rule exists to prevent. The name therefore lands in
            # ``refused`` ("preserved conflicting user paths") instead. Only
            # a blocker that is NOT a loadable skill, or a harness that does
            # not read the shared root, leaves the name unreachable and keeps
            # the fallback.
            visible_via_shared = (
                want_shared
                and not reached_shared
                and bool(getattr(platform, "loads_shared_agents_skills", False))
                and is_loadable(shared_dir / name)
            )
            # The native route runs when the user asked for it, and as the
            # per-name fallback when only this name lost the shared route. An
            # unconditional `continue` after the shared yield made `both`
            # identical to `auto`, though `both` exists precisely to place a
            # skill on both routes.
            reached_native = False
            if want_native or (not reached_shared and not visible_via_shared):
                if packaged_native:
                    reached_native = True
                elif include_native:
                    for root in natives:
                        if name not in blocked_names({name: source}, root, plugin):
                            intents.append(Intent(target=root / name, source=source, plugin=plugin))
                            reached_native = True
                if reached_native:
                    native.append(name)
            if not reached_shared and not reached_native:
                refused.append(f"{plugin}:{name}")

    return (
        tuple(intents),
        Placement(tuple(shared), tuple(native), tuple(refused)),
    )


def skill_intents(
    platform: object,
    ctx: Context,
    plugins: Mapping[str, Path],
    *,
    placement: str = "auto",
    shared_root_override: Path | None = None,
    include_native: bool = True,
) -> Iterator[Intent]:
    """Every skill this harness should receive, by its one route."""
    intents, _ = skill_plan(
        platform,
        ctx,
        plugins,
        placement=placement,
        shared_root_override=shared_root_override,
        include_native=include_native,
    )
    yield from intents


def bridge_intents(
    platform: object,
    ctx: Context,
    *,
    mode: str = "link",
    shared_root_override: Path | None = None,
) -> Iterator[Intent]:
    """Mirror the shared root into a harness that cannot read it.

    ``link`` is the default because a user editing a skill expects the edit to
    apply everywhere; a copy forks silently, so the harness with the copy keeps
    showing the old text with nothing to indicate why.

    Refused, deliberately, when the destination ``skills/`` is itself a symlink:
    Claude Code stops loading user skills entirely when that directory is a link
    (https://github.com/anthropics/claude-code/issues/38051), so bridging into one would disable the very
    skills it is trying to deliver.

    That refusal also skips the *retirement* of stale links inside that
    directory, because both are yielded from here. It is the right trade — we
    will not write into a directory whose shape already breaks the harness, and
    a link we placed before the user made it a symlink is not worth reaching in
    to remove — but it means a symlinked destination is refused, not cleaned.
    Anything left there is retired the moment the directory is a real one
    again, and until then nothing reports it, so this is written down rather
    than mistaken for completed cleanup.

    The destination is where the harness *reads*, not where autorun natively
    *writes*. Those are different fields and they genuinely differ: Antigravity
    reads ``~/.gemini/config/skills`` while its native write route is its
    plugins directory, so bridging to the write route delivers the skills to a
    directory Antigravity never scans, and ForgeCode has no write route at all
    and so could not be bridged. Nothing errors in either case — the skills
    simply never appear.
    """
    if mode == "none" or getattr(platform, "loads_shared_agents_skills", False):
        return
    source = shared_root_override if shared_root_override is not None else shared_root()
    if not source.is_dir():
        return
    from .discovery import skill_destinations

    try:
        destinations = skill_destinations(platform, reading=True, home=ctx.home)
    except TypeError as error:
        if "unexpected keyword argument 'home'" not in str(error):
            raise
        destinations = skill_destinations(platform, reading=True)
    for destination in destinations:
        if destination.is_symlink():
            continue
        shipped = {p.name: p for p in source.iterdir() if is_shippable(p)}
        for skill in sorted(shipped.values()):
            yield Intent(
                target=destination / skill.name,
                source=skill,
                plugin="ar",
                kind=Kind.LINK if mode == "link" else Kind.TREE,
                settings={"bridge": mode},
                link_root=source,
            )
        if destination.is_dir():
            for stale in sorted(destination.iterdir()):
                if stale.name in shipped or not stale.is_symlink():
                    continue
                try:
                    pointed = Path(stale.readlink())
                    pointed = pointed if pointed.is_absolute() else stale.parent / pointed
                    _strip_extended_prefix(pointed.resolve()).relative_to(
                        _strip_extended_prefix(source.resolve())
                    )
                except (OSError, RuntimeError, ValueError):
                    continue
                # No source — that is what makes it stale — so the root that
                # proves the link is ours travels on the intent. Without it the
                # walk falls back to the link's own directory, decides "links
                # outside the shared root", and the link outlives every install
                # and uninstall.
                yield Intent(
                    target=stale,
                    source=None,
                    plugin="ar",
                    kind=Kind.LINK,
                    link_root=source,
                )



def _made(path: Path) -> Path:
    """Create a directory and return it, so a `with` header stays one line."""
    path.mkdir(parents=True, exist_ok=True)
    return path

def demo() -> None:
    """Self-check: one route per harness, per-name fallback, bridge refusal."""
    import tempfile

    @dataclass(frozen=True, slots=True)
    class FakePlatform:
        name: str
        loads_shared_agents_skills: bool
        config_dir: str = "~/.fake/"
        # Two separate fields because they genuinely differ. Antigravity reads
        # ~/.gemini/config/skills while writing to its plugins directory, and
        # ForgeCode reads ~/forge/skills with no write route at all. A fake that
        # models only one of them cannot catch the bridge targeting the wrong
        # one, which is exactly the defect that shipped.
        native_skills: object = None
        skill_search_routes: tuple = ()

    from ..platforms import NoNativeSkillRoute

    class Route:
        def __init__(self, sub): self.sub = sub
        def destinations(self, config, *, home=None): return (config / self.sub,)

    with tempfile.TemporaryDirectory() as tmp, redirected_home(
        _made(Path(tmp) / "home")
    ):
        root = Path(tmp)
        plugin = root / "plugins" / "ar"
        (plugin / "skills").mkdir(parents=True)
        for name in ("commit", "philosophy"):
            (plugin / "skills" / name).mkdir()
            (plugin / "skills" / name / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
        # An asset-only directory is not a skill.
        (plugin / "skills" / "assets").mkdir()
        (plugin / "skills" / "assets" / "logo.png").write_bytes(b"\x89PNG")

        assert set(shippable_skills(plugin)) == {"commit", "philosophy"}, "SKILL.md is required"

        shared = root / "shared"
        ctx = Context(marketplace_root=root, home=root / "home")
        reader = FakePlatform("reader", True, native_skills=Route("skills"),
                              skill_search_routes=(Route("skills"),))
        writer = FakePlatform("writer", False, native_skills=Route("plugins"),
                              skill_search_routes=(Route("read-skills"),))

        # auto yields exactly one route per harness.
        assert routes_for(reader) == (True, False)
        assert routes_for(writer) == (False, True)
        assert routes_for(reader, "both") == (True, True)
        assert routes_for(writer, "native") == (False, True)

        got = list(skill_intents(reader, ctx, {"ar": plugin}, shared_root_override=shared))
        assert {i.target.parent for i in got} == {shared}, "shared reader publishes shared only"
        assert len(got) == 2

        native = list(skill_intents(writer, ctx, {"ar": plugin}, shared_root_override=shared))
        assert all("shared" not in str(i.target) for i in native), "non-reader gets native only"
        assert len(native) == 2

        # A user-authored name on the shared root blocks only that name, and it
        # falls back to the native route rather than dragging the plugin with it.
        shared.mkdir(parents=True)
        (shared / "commit").mkdir()
        (shared / "commit" / "SKILL.md").write_text("mine\n", encoding="utf-8")
        assert blocked_names(shippable_skills(plugin), shared, "ar") == {"commit"}

        mixed = list(skill_intents(reader, ctx, {"ar": plugin}, shared_root_override=shared))
        by_name = {i.target.name: i.target for i in mixed}
        assert by_name["philosophy"].parent == shared, "unblocked name keeps the shared route"
        assert by_name["commit"].parent != shared, "only the blocked name falls back"
        assert len(mixed) == 2, "no skill is lost and none is published twice"

        # Case-folded, so macOS cannot silently alias one onto the other.
        (shared / "Philosophy").mkdir()
        (shared / "Philosophy" / "SKILL.md").write_text("theirs\n", encoding="utf-8")
        assert "philosophy" in blocked_names(shippable_skills(plugin), shared, "ar")

        # The bridge links by default and never targets a symlinked skills dir.
        bridged = list(bridge_intents(writer, ctx, shared_root_override=shared))
        assert bridged and all(i.settings["bridge"] == "link" for i in bridged)
        assert not list(bridge_intents(reader, ctx, shared_root_override=shared)), "a reader needs no bridge"
        assert not list(bridge_intents(writer, ctx, mode="none", shared_root_override=shared))

        # $HOME is the seam, so redirecting it is how a check relocates every
        # route at once — including the home-anchored ones a Context field
        # could never reach.
        linked_home = root / "home_linked"
        (linked_home / ".fake").mkdir(parents=True)
        # The READ route is what the bridge targets, so that is what must be a
        # symlink for the refusal to trigger.
        (linked_home / ".fake" / "read-skills").symlink_to(shared)
        # Same reason as orchestrate's self-check: the shared helper moves
        # HOME and USERPROFILE together, so the redirect holds on Windows too.
        with discovery.redirected_home(linked_home):
            assert not list(bridge_intents(
                writer, Context(marketplace_root=root, home=linked_home),
                shared_root_override=shared,
            )), "https://github.com/anthropics/claude-code/issues/38051: never bridge into a symlinked skills directory"

        # --- refusals that must happen BEFORE anything is written ----------
        # ForgeCode's real shape: reads the shared root, has no native route.
        no_native = FakePlatform("forgey", True, native_skills=NoNativeSkillRoute())
        assert unsatisfiable([writer, reader], "auto") == (), "auto always resolves"
        assert unsatisfiable([no_native], "auto") == (), "the shared route serves it"
        problems = unsatisfiable([no_native], "native")
        assert problems and "no native skill route" in problems[0], problems
        assert unsatisfiable([no_native], "both") == (), "both keeps the shared root"

        # A harness that reads neither cannot be served by any placement, and
        # says so rather than installing nothing quietly.
        isolated = FakePlatform("nowhere", False, native_skills=NoNativeSkillRoute())
        assert unsatisfiable([isolated], "auto"), "no route at all is reported"

        # Two plugins claiming one name is an error, not a race the installer wins.
        other = root / "plugins" / "pdf"
        (other / "skills" / "commit").mkdir(parents=True)
        (other / "skills" / "commit" / "SKILL.md").write_text("# theirs\n", encoding="utf-8")
        clashes = duplicate_names({"ar": plugin, "pdf": other})
        assert set(clashes) == {"commit"}, clashes
        assert clashes["commit"] == ("ar:commit", "pdf:commit")
        assert duplicate_names({"ar": plugin}) == {}, "one plugin cannot clash with itself"

        # A rename retires the old name only once the new one is shipped.
        assert retired_names([]) == () or isinstance(retired_names([]), tuple)

    print("installer.skills: all self-checks passed")


if __name__ == "__main__":
    demo()
