"""Where each registered harness reads skills, as a set of SKILL.md paths.

One definition, shared by the release test (a wheel install reaches what a
clone install reaches) and the skills-plugin upgrade tests (an upgrade reaches
what a fresh install reaches), so the two cannot disagree about what
"reachable" means.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping


def reachable_skills(
    env: Mapping[str, str], names: Iterable[str]
) -> dict[str, set[str]]:
    """Per harness, every SKILL.md in a location its registry entry reads.

    Built from the entry's routes (`skill_search_routes`, `native_skills`, the
    shared root when `loads_shared_agents_skills`), never from a recursive
    walk, so a copy somewhere a harness does not look is not counted and a
    second copy where it does look is. Paths are reported relative to `$HOME`.
    """
    from autorun.installer import discovery
    from autorun.platforms import (
        PLATFORMS, CombinedSkillRoutes, ConfigPackageSkills, ExtensionSkills,
        PluginPackageSkills,
    )

    names = tuple(names)
    home = Path(env["HOME"])

    def flat(route):
        if isinstance(route, CombinedSkillRoutes):
            for inner in route.routes:
                yield from flat(inner)
        elif route is not None:
            yield route

    found: dict[str, set[str]] = {}
    for name, platform in PLATFORMS.items():
        base = discovery.config_dir(platform, env=env, home=home)
        roots: set[Path] = set()
        for route in (*platform.skill_search_routes, platform.native_skills):
            for leaf in flat(route):
                if isinstance(leaf, ExtensionSkills):
                    for directory in leaf.destinations(base, home=home):
                        roots.update(unit / "skills" for unit in directory.glob("*"))
                elif isinstance(leaf, ConfigPackageSkills):
                    for plugin in names:
                        roots.update(leaf.plugin_destinations(base, plugin))
                elif isinstance(leaf, PluginPackageSkills):
                    # The resolver names ar's staged package; every plugin
                    # gets a sibling one under the same source directory.
                    if leaf.resolver is not None:
                        roots.update(
                            discovery.codex_plugin_source(plugin, home=home) / "skills"
                            for plugin in names
                        )
                else:
                    roots.update(leaf.destinations(base, home=home))
        if platform.loads_shared_agents_skills:
            roots.add(home / ".agents" / "skills")
        found[name] = {
            "$HOME/" + skill.relative_to(home).as_posix()
            for directory in roots
            for skill in directory.glob("*/SKILL.md")
        }
    return found
