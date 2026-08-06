#!/usr/bin/env python3
"""Which steps each harness runs, and the two things a walk cannot express.

``traversal`` walks intents; this module is where the intents come from. It is
the only module that names harnesses, and it names them exactly once, in
:data:`STEPS`. Everything above it (``traversal``) and below it (``skills``,
``extension``, ``codex``, ``memory``) stays harness-blind.

THE TWO PHASES
==============

An :class:`Intent` describes a directory autorun *owns*: publish it, retire it,
keep it if the user edited inside. Two install artifacts are not that shape, and
forcing them into it would mean claiming a file the user wrote:

``Region``   a sentinel-delimited block inside ``AGENTS.md`` or ``CLAUDE.md``.
             The file is the user's; autorun owns a range inside it.
``Hooks``    autorun's entries inside ``~/.codex/hooks.json``. The file is the
             user's, and one unknown key in it disables every hook they have.

Both are still *described* before anything is written, so ``PREVIEW`` reports
them without touching the disk, exactly as the owned-tree walk does.

WHY A SEPARATE MODULE
=====================

The step tuples cannot live on ``Platform``: steps are defined in modules that
import ``platforms``, so a field holding them closes an import cycle, and
encoding them as *names* for later lookup is the defect this registry already
shipped once, where ``install_fn_name`` named a function nobody had written.

Complexity: O(H x S) intents for H harnesses and S skills; staging is O(bytes)
once per plugin, before the walk, so the walk itself stays pure.
"""

from __future__ import annotations

import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence

from . import codex, discovery, extension, memory, skills
from .traversal import Context, Intent, Kind, Mode, Step

__all__ = [
    "STEPS",
    "skills_step", "bridge_step", "commands_step", "extension_step",
    "opencode_shim_step", "stage_opencode_shim", "stage_toml_commands",
    "Region", "Hooks", "regions_for", "hooks_for",
    "apply_regions", "apply_hooks",
    "prepared",
    "COMMANDS_SUBDIR", "TEMPLATE_SUBDIR",
]

#: Where the portable markdown command bundle lives inside the plugin, and
#: where it lands in a harness config directory. Both fixed: a harness that
#: reads ``<config>/commands/*.md`` reads exactly that path.
TEMPLATE_SUBDIR = Path("src") / "autorun" / "forgecode_template"
COMMANDS_SUBDIR = "commands"


# --- steps: pure, no disk ----------------------------------------------------


def skills_step(harness: object, ctx: Context) -> Iterable[Intent]:
    """Every skill this harness receives, by its one route."""
    placement = _placement(ctx, getattr(harness, "name", ""))
    return skills.skill_intents(
        getattr(harness, "platform", harness), ctx, _plugins(ctx), placement=placement
    )


def bridge_step(harness: object, ctx: Context) -> Iterable[Intent]:
    """Mirror the shared root into a harness that cannot read it."""
    mode = str(ctx.settings.get("shared_skills_bridge", "none"))
    return skills.bridge_intents(
        getattr(harness, "platform", harness), ctx, mode=mode
    )


def commands_step(harness: object, ctx: Context) -> Iterable[Intent]:
    """Publish the markdown command bundle into a shared ``commands/``.

    ``Kind.FILES``, never ``Kind.TREE``: the harness reads the user's own
    command files from the same directory, so ownership is per file. Publishing
    the whole directory would either refuse (an unmarked directory holding their
    commands reads as user-authored) or delete what they wrote.
    """
    base = discovery.config_dir(getattr(harness, "platform", harness), home=ctx.home)
    if base is None:
        return
    for plugin, plugin_dir in _plugins(ctx).items():
        source = plugin_dir / TEMPLATE_SUBDIR / COMMANDS_SUBDIR
        if not source.is_dir():
            continue
        yield Intent(
            target=base / COMMANDS_SUBDIR,
            source=source,
            plugin=plugin,
            kind=Kind.FILES,
        )


def extension_step(harness: object, ctx: Context) -> Iterable[Intent]:
    """Materialize a Gemini-family extension from content staged before the walk.

    Staging happens in :func:`prepared`, not here: a step that wrote to a temp
    directory would be doing I/O during a ``PREVIEW`` that promises none.
    """
    staged = ctx.settings.get("_staged_extensions")
    if not isinstance(staged, Mapping) or not staged:
        return ()
    return extension.extension_intents(
        getattr(harness, "platform", harness), ctx, _plugins(ctx), staged=staged
    )


#: OpenCode loads plugins from `<config>/plugin/` (singular, verified against
#: opencode 1.18.13) as plain ES modules with no build step.
OPENCODE_TEMPLATE_SUBDIR = Path("src") / "autorun" / "opencode_template" / "plugin"
OPENCODE_PLUGIN_SUBDIR = "plugin"


def opencode_shim_step(harness: object, ctx: Context) -> Iterable[Intent]:
    """Publish the JavaScript bridge OpenCode loads in-process.

    ``Kind.FILES``, because users keep their own plugins in that directory.

    This is the only install route that writes JavaScript, and deliberately so:
    OpenCode runs on Bun and loads the plugin in-process, so its users already
    have that runtime. Claude, Codex, Qwen, Gemini and ForgeCode users need
    Python alone and must not be handed a second runtime requirement.

    The staged copy already has the daemon socket path substituted absolutely,
    so nothing resolves through the host PATH while a session is running.
    """
    staged = ctx.settings.get("_staged_opencode")
    base = discovery.config_dir(getattr(harness, "platform", harness), home=ctx.home)
    if not isinstance(staged, Mapping) or base is None:
        return
    for plugin, source in staged.items():
        yield Intent(
            target=base / OPENCODE_PLUGIN_SUBDIR,
            source=source,
            plugin=plugin,
            kind=Kind.FILES,
        )


#: The one place a harness is named. A harness absent here contributes no
#: intents, which is how an unsupported one stays out of the walk without a
#: branch testing for it. A custom harness reuses its flavor's tuple through
#: ``settings.steps_for_custom``, so adding one adds no entry either.
STEPS: Mapping[str, tuple[Step, ...]] = {
    "claude": (skills_step, bridge_step),
    "codex": (skills_step, commands_step),
    "gemini": (skills_step, extension_step),
    "qwen": (skills_step, extension_step),
    "antigravity": (skills_step, bridge_step, extension_step),
    "forgecode": (skills_step, commands_step),
    "opencode": (skills_step, commands_step, opencode_shim_step),
}


# --- the two phases a walk cannot express ------------------------------------


@dataclass(frozen=True, slots=True)
class Region:
    """One sentinel-delimited block inside a file the user owns."""

    path: Path
    block: memory.Block
    body: str

    def describe(self) -> str:
        return f"{self.path} [{self.block.slug}]"


@dataclass(frozen=True, slots=True)
class Hooks:
    """Autorun's entries in a hooks file the user owns."""

    path: Path
    events: Mapping[str, Sequence[str]] = field(default_factory=dict)

    def describe(self) -> str:
        return f"{self.path} ({', '.join(sorted(self.events)) or 'none'})"


def regions_for(harness: object, ctx: Context) -> tuple[Region, ...]:
    """The guidance blocks this harness reads, if it reads any.

    A harness with no memory file gets none, which is a real answer rather than
    a missing case: Claude reads ``CLAUDE.md``, the Gemini family reads
    ``GEMINI.md``, and the rest read ``AGENTS.md``.
    """
    platform = getattr(harness, "platform", harness)
    base = discovery.config_dir(platform, home=ctx.home)
    filename = str(getattr(platform, "memory_filename", "") or "")
    if base is None or not filename:
        return ()
    guidance = str(ctx.settings.get("_guidance", "") or "")
    if not guidance:
        return ()
    # --- BUG #54673 WORKAROUND START --- DELETE WHEN FIXED ---
    # No token counts reach hooks, so the model states a guess about remaining
    # capacity as fact. Disable: see memory.CONTEXT_GUIDANCE_FLAG.
    if not memory.context_guidance_enabled():
        return ()
    # --- BUG #54673 WORKAROUND END ---
    return (Region(base / filename, memory.CONTEXT_GUIDANCE, guidance),)


def hooks_for(harness: object, ctx: Context) -> tuple[Hooks, ...]:
    """Autorun's hook entries for a harness that reads a user-owned hooks file.

    Only Codex today. Claude and the Gemini family carry their hooks inside the
    plugin or extension autorun owns outright, so those are ordinary intents.
    """
    platform = getattr(harness, "platform", harness)
    if getattr(platform, "name", "") != "codex":
        return ()
    if str(ctx.settings.get("codex_hook_source", "user")) not in ("user", "both"):
        return ()
    base = discovery.config_dir(platform, home=ctx.home)
    command = str(ctx.settings.get("_codex_hook_command", "") or "")
    if base is None or not command:
        return ()
    events = {name: (command,) for name in _CODEX_EVENTS}
    return (Hooks(base / "hooks.json", events),)


#: The events autorun handles on Codex. Registering one it has no handler for
#: spawns a subprocess per occurrence for nothing; omitting one it does handle
#: is a silent gap, so this list is the contract and lives in one place.
_CODEX_EVENTS = (
    "PreToolUse", "PostToolUse", "UserPromptSubmit",
    "SessionStart", "Stop", "SubagentStop",
)


def apply_regions(regions: Iterable[Region], mode: Mode) -> list[str]:
    """Write or remove each region, or say what would change. Returns the notes.

    ``PREVIEW`` reports and writes nothing, the same contract the owned-tree
    walk keeps, so ``--status`` and ``--dry-run`` remain the same code path.
    """
    notes: list[str] = []
    for region in regions:
        if mode is Mode.PREVIEW:
            present = region.path.is_file() and memory.bounds(
                region.path.read_text(encoding="utf-8"), region.block
            ) is not None
            notes.append(f"{'present' if present else 'would add'} {region.describe()}")
        elif mode is Mode.UNINSTALL:
            if memory.strip(region.path, region.block):
                notes.append(f"removed {region.describe()}")
        elif memory.splice(region.path, region.body, region.block):
            notes.append(f"wrote {region.describe()}")
    return notes


def apply_hooks(entries: Iterable[Hooks], mode: Mode) -> list[str]:
    """Merge or withdraw autorun's hook entries. Returns the notes.

    An uninstall passes empty command lists rather than deleting the file: it is
    the user's file and may hold their own hooks and other tools'.
    """
    notes: list[str] = []
    for entry in entries:
        if mode is Mode.PREVIEW:
            notes.append(f"would merge {entry.describe()}")
            continue
        events = {} if mode is Mode.UNINSTALL else entry.events
        # Every event is swept either way, including ones no longer shipped.
        wanted = {name: events.get(name, ()) for name in entry.events}
        try:
            codex.merge_hooks(entry.path, wanted)
        except ValueError as error:
            notes.append(f"refused {entry.path}: {error}")
            continue
        notes.append(f"{'withdrew' if mode is Mode.UNINSTALL else 'merged'} {entry.describe()}")
    return notes


# --- staging: the I/O that has to happen before a pure walk ------------------


#: Where the Gemini-family template lives inside a plugin. Its `hooks/` holds
#: the entry point and the event manifest the harness actually reads.
GEMINI_TEMPLATE_SUBDIR = Path("src") / "autorun" / "gemini_template"


@contextmanager
def prepared(
    ctx: Context, *, plugins: Mapping[str, Path], placement: str = "auto"
) -> Iterator[Context]:
    """Stage generated content, yield a Context pointing at it, then clean up.

    Extensions are generated, not shipped: the manifest is translated per
    harness family and the hook entry is copied to the path the harness
    hardcodes. Generating inside a step would make ``PREVIEW`` write, so it
    happens once here and the step only points at the result. The staging
    directory lives exactly as long as the walk that reads it.

    ``skills`` is passed explicitly and is empty under the default placement.
    Copying the plugin's whole ``skills/`` in here is what once gave shared-root
    harnesses a second, unmarked, unprunable copy of every skill.
    """
    with tempfile.TemporaryDirectory(prefix="autorun-stage-") as tmp:
        staged: dict[str, Path] = {}
        for plugin, plugin_dir in plugins.items():
            template = plugin_dir / GEMINI_TEMPLATE_SUBDIR
            if not template.is_dir():
                continue  # not a plugin that ships an extension; not an error
            manifest = extension.Manifest(
                name=plugin,
                version=_version(plugin_dir),
                description=_description(plugin_dir),
            )
            native = skills.shippable_skills(plugin_dir) if placement != "auto" else {}
            try:
                directory = extension.stage_extension(
                    template, plugin_dir, Path(tmp) / plugin, manifest, skills=native
                )
                stage_toml_commands(directory, plugin)
            except OSError:
                # One plugin that cannot be staged does not stop the others, and
                # every non-extension route for it still runs.
                continue
            staged[plugin] = directory
        shims = stage_opencode_shim(
            Path(tmp) / "_opencode", plugins,
            socket=str(ctx.settings.get("_daemon_socket", "") or ""),
            command=str(ctx.settings.get("_hook_command", "") or ""),
        )
        yield _with(ctx, _staged_extensions=staged, _staged_opencode=shims)


def stage_opencode_shim(
    staging: Path, plugins: Mapping[str, Path], *, socket: str, command: str
) -> Mapping[str, Path]:
    """Write the substituted JavaScript bridge, ready to publish.

    Both values are substituted absolutely at install time rather than resolved
    at runtime: the plugin runs inside OpenCode's process, so a path resolved
    through the host ``PATH`` mid-session picks up whatever the user's shell
    happens to have, which is how one machine's session reached a different
    autorun than the one installed.
    """
    import json

    shims: dict[str, Path] = {}
    for plugin, plugin_dir in plugins.items():
        source = plugin_dir / OPENCODE_TEMPLATE_SUBDIR / "autorun.js"
        if not source.is_file():
            continue
        directory = staging / plugin
        directory.mkdir(parents=True, exist_ok=True)
        text = (
            source.read_text(encoding="utf-8")
            .replace("__AUTORUN_SOCKET__", socket)
            # JSON-encoded: the command is embedded in a JS literal, and a path
            # containing a quote or backslash would otherwise break the module.
            .replace("__AUTORUN_HOOK_ENTRY_COMMAND__", json.dumps(command))
        )
        (directory / "autorun.js").write_text(text, encoding="utf-8")
        shims[plugin] = directory
    return shims


def stage_toml_commands(extension_dir: Path, namespace: str) -> tuple[str, ...]:
    """Write the Gemini-family TOML twin of each markdown command.

    The family reads ``commands/<namespace>/<name>.toml``; Claude reads
    ``commands/<name>.md``. One source, two renderings, generated here so the
    command set cannot drift between harnesses — which is what happened while
    each format had its own list.

    The namespace directory is what makes the command ``/ar:status`` rather
    than ``/status``, so it is the plugin's registered name and not a literal.

    Returns the names written. Generated into the staging directory, never into
    an installed one: publication stays a single atomic rename.
    """
    from ..command_docs import iter_command_docs
    from .harness import Command, render_toml_command

    source = extension_dir / "commands"
    if not source.is_dir():
        return ()
    destination = source / namespace
    destination.mkdir(parents=True, exist_ok=True)
    written = []
    for doc in iter_command_docs(source):
        # The filename, not the declared name: the file's stem is what the
        # harness turns into the command, and the two differ for aliases.
        command = Command(doc.path.stem, doc.description, doc.body)
        (destination / f"{command.name}.toml").write_text(
            render_toml_command(command), encoding="utf-8"
        )
        written.append(command.name)
    return tuple(written)


def _manifest_field(plugin_dir: Path, key: str, default: str) -> str:
    """One declared field from a plugin manifest, or a usable default."""
    import json

    try:
        value = json.loads(
            (plugin_dir / discovery.PLUGIN_MANIFEST).read_text(encoding="utf-8")
        ).get(key)
    except (OSError, AttributeError, ValueError):
        value = None
    return value if isinstance(value, str) and value else default


def _version(plugin_dir: Path) -> str:
    return _manifest_field(plugin_dir, "version", "0.0.0")


def _description(plugin_dir: Path) -> str:
    return _manifest_field(plugin_dir, "description", "")


def _with(ctx: Context, **settings: object) -> Context:
    """A copy of ``ctx`` carrying extra resolved settings."""
    from dataclasses import replace

    return replace(ctx, settings={**ctx.settings, **settings})


def _plugins(ctx: Context) -> Mapping[str, Path]:
    """Selected plugins by registered name, from the directories in ``ctx``."""
    return {discovery.plugin_name(d): d for d in ctx.plugin_dirs}


def _placement(ctx: Context, harness: str) -> str:
    """This harness's skill placement, from the one resolved mapping."""
    placement = ctx.settings.get("skill_placement", {})
    if not isinstance(placement, Mapping):
        return "auto"
    return str(placement.get(harness) or placement.get("") or "auto")


def demo() -> None:
    """Self-check: the table is data, and both extra phases preview cleanly."""
    from .traversal import targets

    # Every named harness is registered, or the table points at nothing.
    from ..platforms import PLATFORMS

    unknown = sorted(set(STEPS) - set(PLATFORMS))
    assert not unknown, f"steps for harnesses that do not exist: {unknown}"

    # Every registered harness has an entry, or it silently installs nothing.
    missing = sorted(set(PLATFORMS) - set(STEPS))
    assert not missing, f"registered harnesses with no steps: {missing}"

    # Pairing is data: a harness absent from the table gets no steps.
    paired = targets(PLATFORMS.values(), {})
    assert all(t.install_steps == () for t in paired), "no table, no steps"
    paired = targets(PLATFORMS.values(), STEPS)
    assert all(t.install_steps for t in paired), "every harness runs something"

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ctx = Context(marketplace_root=root, home=root / "home")

        # A harness with no memory file, or no guidance to write, gets no region.
        class Bare:
            name = "bare"
            config_dir = ""
            memory_filename = ""

        assert regions_for(Bare(), ctx) == ()
        assert hooks_for(Bare(), ctx) == ()

        # PREVIEW writes nothing, and says what it would do.
        target = root / "AGENTS.md"
        region = Region(target, memory.Block("guidance"), "autorun guidance")
        assert apply_regions([region], Mode.PREVIEW) == [f"would add {region.describe()}"]
        assert not target.exists(), "preview never writes"

        # INSTALL writes it, UNINSTALL takes exactly it away.
        target.write_text("# Theirs\n\nkeep me\n", encoding="utf-8")
        assert apply_regions([region], Mode.INSTALL)
        assert "autorun guidance" in target.read_text()
        assert apply_regions([region], Mode.UNINSTALL)
        assert target.read_text() == "# Theirs\n\nkeep me\n", "their file, untouched"

        # A hooks file the user owns keeps their entries through both directions.
        hooks = root / "hooks.json"
        theirs = {"hooks": [{"type": "command", "command": "/usr/local/bin/mine.sh"}]}
        import json

        hooks.write_text(json.dumps({"hooks": {"Stop": [theirs]}}), encoding="utf-8")
        entry = Hooks(hooks, {"Stop": ("uv run hook_entry.py --cli codex",)})
        assert apply_hooks([entry], Mode.PREVIEW) == [f"would merge {entry.describe()}"]
        assert json.loads(hooks.read_text())["hooks"]["Stop"] == [theirs], "preview wrote"

        apply_hooks([entry], Mode.INSTALL)
        after = json.loads(hooks.read_text())["hooks"]["Stop"]
        assert theirs in after and len(after) == 2

        apply_hooks([entry], Mode.UNINSTALL)
        assert json.loads(hooks.read_text())["hooks"]["Stop"] == [theirs], "ours gone, theirs kept"

    print("installer.steps: all self-checks passed")


if __name__ == "__main__":
    demo()
