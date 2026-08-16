"""Materializing a Gemini-family extension, and keeping it fresh.

Gemini, Qwen and Antigravity install an *extension directory* rather than
loading a plugin in place. Autorun therefore builds that directory from a
template and publishes it, which is why generation lives here and not in the
traversal: an :class:`Intent` names a source path, and generated content has
none until it has been staged.

TWO NATIVE LAYOUTS SHAPE THIS FILE
==================================

Gemini hardcodes the hook manifest at ``<ext>/hooks/hooks.json`` and ignores the
manifest's own ``hooks`` field. The materialized extension therefore carries a
real ``hooks/`` directory. Its commands call the already-installed ``autorun``
entry point; pointing uv at the generated extension was invalid because that
directory deliberately has no ``pyproject.toml``.
https://github.com/google-gemini/gemini-cli/issues/14449 (closed COMPLETED by
PR #14460, which merged the convention this already targets).

Claude's plugin loader scans the marketplace *source* ``hooks/`` as well as its
cache, and its strict schema rejects Gemini event names with ``invalid_key``,
which disables every hook in the file. Gemini events therefore live under
``src/autorun/gemini_template/`` — outside Claude's scan path — and are copied
into the extension from there. That is why this module templates from a
directory rather than from ``plugins/autorun/hooks/``.
https://github.com/anthropics/claude-code/issues/24115 (closed NOT_PLANNED; a
closed issue does not change the two harnesses' incompatible native layouts).

Complexity: O(files) to stage, one hash pass at publish. The receipt check is
two file reads and no subprocess, which is what lets refresh run for a harness
whose CLI is not installed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Mapping

from . import discovery
from .fs import compare, owned_trees, owns, read_marker, scan_tree
from .traversal import Context, Intent

__all__ = [
    "Manifest",
    "receipt_names_source",
    "receipt_names_any_source",
    "native_receipt_names_source",
    "antigravity_receipt_names_plugin",
    "bundle_hooks_are_ours",
    "extension_dir",
    "registration_source_dir",
    "extension_intents",
    "refreshable",
    "materialization_unchanged",
    "materialization_matches_source",
    "materialization_tracks_source",
    "RECEIPT_GLOB",
    "MANIFEST_NAME",
]

#: Gemini-family CLIs drop ``<cli>-extension-install.json`` recording where the
#: extension came from. The filename is per harness (``.gemini-``, ``.qwen-``),
#: hence a glob rather than threading the CLI name through another parameter.
RECEIPT_GLOB = ".*-extension-install.json"

#: The manifest filename every Gemini-family harness reads. Fixed, and NOT
#: derived from the plugin name: Gemini, Qwen and Antigravity all look for
#: ``gemini-extension.json`` whatever the extension is called. Writing
#: ``<name>-extension.json`` instead produces a directory the harness loads
#: nothing from — no commands, no hooks, no skills — and reports no error,
#: because as far as the CLI is concerned there is no extension there.
#:
#: Qwen additionally writes its own ``qwen-extension.json`` when its CLI
#: installs an extension. That file is the harness's, not ours; autorun writes
#: only the name below.
MANIFEST_NAME = "gemini-extension.json"


@dataclass(frozen=True, slots=True)
class Manifest:
    """The extension manifest, in the shape the installed one actually has.

    ``hooks`` is a *path string*, not an object. Gemini ignores it and reads
    ``<ext>/hooks/hooks.json`` regardless (bug #14449), so the field is written
    for correctness and the file is placed where the harness will actually look.
    """

    name: str
    version: str
    description: str = ""
    context_file: str = "GEMINI.md"

    def as_document(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "contextFileName": self.context_file,
            "commands": "./commands/",
            "skills": "./skills/",
            "hooks": "./hooks/hooks.json",
        }


def extension_dir(ctx: Context, platform: object, name: str) -> Path | None:
    """Where this harness keeps the extension named ``name``, or None.

    ``None`` when the harness has no extension directory at all — Claude,
    Codex, ForgeCode and OpenCode declare that by leaving ``extensions_subdir``
    empty, and a caller must not invent one for them.

    Resolution goes through :func:`discovery.extensions_dir` rather than
    expanding ``Platform.config_dir`` here. Stripping the leading ``~/`` by hand
    ignores ``CODEX_HOME``, ``QWEN_HOME``, ``CLAUDE_CONFIG_DIR``,
    ``XDG_CONFIG_HOME`` and the ``harness_config_dirs`` override, so a user who
    relocated their harness root would keep getting extensions in the default
    place while every other part of the install honoured the move.
    """
    from .discovery import extensions_dir

    base = extensions_dir(platform, home=ctx.home)
    return base / name if base is not None else None


def registration_source_dir(ctx: Context, harness: str, name: str) -> Path:
    """Persistent generated source handed to one native extension installer.

    Gemini and Qwen create absolute links to the path passed to
    ``extensions install``. A temporary staging path therefore becomes a
    broken installed extension as soon as the installer returns. Keep each
    harness's translated bundle separate under AUTORUN_HOME instead.
    """
    configured = ctx.settings.get("_extension_source_root")
    root = (
        Path(str(configured))
        if configured
        else ctx.home / ".autorun" / "installer" / "extension-sources"
    )
    return root / harness / name


def receipt_names_source(installed: Path, source: Path) -> bool:
    """Whether the harness's own receipt says this extension came from ``source``.

    Only an exact resolved-path match counts. A prefix or name test would let a
    differently-rooted checkout claim an extension the user installed from
    somewhere else, and refreshing that would overwrite their copy with ours.
    """
    for receipt in installed.glob(RECEIPT_GLOB):
        try:
            recorded = json.loads(receipt.read_text(encoding="utf-8")).get("source")
        except (OSError, ValueError):
            continue
        if not isinstance(recorded, str):
            continue
        try:
            if Path(recorded).resolve() == source.resolve():
                return True
        except (OSError, RuntimeError):
            continue
    return False


def receipt_names_any_source(installed: Path, sources) -> bool:
    """Whether a harness receipt exactly names one explicitly allowed source."""
    return any(receipt_names_source(installed, source) for source in sources)


def native_receipt_names_source(
    ctx: Context,
    platform: object,
    plugin: str,
    installed: Path,
    source: Path,
) -> bool:
    """Match one native CLI's exact receipt to the generated source.

    Gemini and Qwen write the source path inside the materialized extension.
    Agy instead writes a shared import manifest beside ``plugins/`` and copies
    only the bundle contents, so its receipt names the plugin but never a
    path. Adoption therefore needs the exact manifest entry plus evidence
    inside the copy that the bundle is ours: it still matches today's source
    byte for byte, or every hook it declares runs autorun's own hook entry, or
    a tree inside it (a copied native skill) carries autorun's marker for this
    plugin — the last is what a hookless bundle such as pdf-extractor's
    leaves. Content match alone was the whole test once, and it can only pass
    until the source next changes — after that a copy Agy made before
    :func:`record_tree` stamped it looked like a stranger's plugin, was
    skipped on every install, and left that harness on the first bundle it
    ever imported. Once adopted, :func:`record_tree` supplies the ordinary
    edit-sensitive marker.
    """
    flavor = (
        getattr(platform, "install_flavor", "")
        or getattr(platform, "name", "")
    )
    if flavor != "antigravity":
        return receipt_names_source(installed, source)
    if not antigravity_receipt_names_plugin(ctx, platform, plugin):
        return False
    if not installed.is_dir():
        return False
    return (
        scan_tree(installed) == scan_tree(source)
        or bundle_hooks_are_ours(installed)
        or any(owned_trees(installed, plugin=plugin, max_depth=3))
    )


def bundle_hooks_are_ours(installed: Path) -> bool:
    """Whether every hook command a materialized bundle declares is autorun's.

    Reads the manifests a Gemini-family bundle can carry (``hooks.json`` at
    the root, ``hooks/hooks.json`` beside the entry script) and requires at
    least one command, all of which name the hook entry — the same mark
    :func:`codex.is_ours` uses to recognise our entries in Codex's shared
    ``hooks.json``. A bundle with no hooks, an unreadable manifest, or one
    foreign command is not ours.
    """
    from .codex import COMMAND_MARK

    commands: list[str] = []

    def collect(value: object) -> None:
        if isinstance(value, Mapping):
            command = value.get("command")
            if isinstance(command, str):
                commands.append(command)
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    for candidate in (installed / "hooks.json", installed / "hooks" / "hooks.json"):
        if not candidate.is_file():
            continue
        try:
            collect(json.loads(candidate.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return False
    return bool(commands) and all(COMMAND_MARK in command for command in commands)


def antigravity_receipt_names_plugin(
    ctx: Context,
    platform: object,
    plugin: str,
) -> bool:
    """Whether Agy's shared receipt names this exact installed plugin."""
    base = discovery.config_dir(platform, home=ctx.home)
    if base is None:
        return False
    try:
        document = json.loads(
            (base / "import_manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return False
    imports = document.get("imports", ()) if isinstance(document, Mapping) else ()
    return any(
        isinstance(entry, Mapping)
        and entry.get("name") == plugin
        and entry.get("source") in {"antigravity", "gemini-cli"}
        for entry in imports
    )


def materialization_unchanged(
    installed: Path,
    source: Path,
    *,
    plugin: str,
    legacy_sources=(),
    receipt_proof: Callable[[Path], bool] | None = None,
) -> bool:
    """Prove a native extension belongs to autorun and has no user edits."""
    if not installed.is_dir():
        return False
    allowed_sources = (source, *tuple(legacy_sources))
    marker = read_marker(installed)
    if marker is not None and owns(marker, plugin):
        if marker.files:
            return not any(compare(installed, marker))
        return receipt_names_any_source(installed, allowed_sources)
    if installed.is_symlink():
        try:
            if installed.resolve() == source.resolve():
                source_marker = read_marker(source)
                return (
                    source_marker is not None
                    and owns(source_marker, plugin)
                    and not any(compare(source, source_marker))
                )
        except (OSError, RuntimeError):
            return False
    if receipt_proof is not None:
        return receipt_proof(installed)
    return receipt_names_any_source(installed, allowed_sources)


def materialization_tracks_source(installed: Path, source: Path) -> bool:
    """Whether changes to the persistent source reach the native install."""
    if not installed.is_symlink():
        return False
    try:
        return installed.resolve() == source.resolve()
    except (OSError, RuntimeError):
        return False


def materialization_matches_source(installed: Path, source: Path) -> bool:
    """Whether a native copied tree already contains the generated bundle."""
    return bool(
        installed.is_dir()
        and source.is_dir()
        and scan_tree(installed) == scan_tree(source)
    )


def refreshable(
    installed: Path,
    source: Path,
    *,
    plugin: str = "",
    legacy_sources=(),
) -> bool:
    """Whether a materialized extension may be refreshed from ``source``.

    Either autorun's own marker claims it, or the harness's receipt names our
    template as its source. Both are required to be *positive* evidence: an
    extension with neither is the user's, and an extension keeps running from
    its files after the CLI is uninstalled, so refusing to refresh on a missing
    CLI would leave stale hook code live.
    """
    if not installed.is_dir():
        return False
    marker = read_marker(installed)
    if marker is not None and (not plugin or not marker.plugin or marker.plugin == plugin):
        return True
    return receipt_names_any_source(installed, (source, *tuple(legacy_sources)))


def stage_extension(
    template: Path,
    plugin_dir: Path,
    staging: Path,
    manifest: Manifest,
    *,
    skills: Mapping[str, Path] | None = None,
    hook_manifest: Mapping[str, object] | None = None,
    manifest_name: str = MANIFEST_NAME,
    hooks_at_root: bool = False,
) -> Path:
    """Build one extension directory, ready to be published.

    Writes into ``staging`` and returns it. Nothing here touches the
    destination, so a failure part-way leaves the installed extension alone —
    publication is a separate atomic step.

    ``skills`` is passed in rather than read from ``plugin_dir``, and that is
    the whole point. The installer this replaces copied the plugin's entire
    ``skills/`` directory here as part of materializing the extension,
    independently of the skill-route system — so a harness that reads the shared
    root received every skill twice, and those copies carried no ownership
    marker, which made them unprunable. Passing exactly the skills this harness
    should receive natively makes the route the single authority over where a
    skill lands, and makes double exposure unrepresentable rather than merely
    unlikely.
    """
    import shutil

    staging.mkdir(parents=True, exist_ok=True)
    if (commands := plugin_dir / "commands").is_dir():
        shutil.copytree(commands, staging / "commands", symlinks=True, dirs_exist_ok=True)
    for name, source in (skills or {}).items():
        shutil.copytree(source, staging / "skills" / name, symlinks=True, dirs_exist_ok=True)

    # BUG #14449: the manifest's `hooks` field is ignored, so the real files go
    # where the harness hardcodes its lookup.
    hooks = staging / "hooks"
    hooks.mkdir(exist_ok=True)
    serialized_hooks = json.dumps(hook_manifest) if hook_manifest is not None else "hook_entry.py"
    if "hook_entry.py" in serialized_hooks:
        entry = plugin_dir / "hooks" / "hook_entry.py"
        if not entry.is_file():
            entry = template / "hooks" / "hook_entry.py"
        if entry.is_file():
            shutil.copy2(entry, hooks / "hook_entry.py")
    if hook_manifest is not None:
        (hooks / "hooks.json").write_text(
            json.dumps(hook_manifest, indent=2) + "\n", encoding="utf-8"
        )
    elif (source_manifest := template / "hooks" / "hooks.json").is_file():
        shutil.copy2(source_manifest, hooks / "hooks.json")

    document = manifest.as_document()
    if manifest_name == "plugin.json":
        document.pop("contextFileName", None)
        document["hooks"] = "./hooks.json" if hooks_at_root else "./hooks/hooks.json"
    (staging / manifest_name).write_text(
        json.dumps(document, indent=2) + "\n", encoding="utf-8"
    )
    if hooks_at_root and (hooks / "hooks.json").is_file():
        shutil.copy2(hooks / "hooks.json", staging / "hooks.json")
    return staging


def translated_hooks(
    template: Path,
    platform: object,
    *,
    hook_command: str | None = None,
) -> Mapping[str, object] | None:
    """Translate the shared Gemini hook manifest through registry contracts."""
    source_path = template / "hooks" / "hooks.json"
    if not source_path.is_file():
        return None
    document = json.loads(source_path.read_text(encoding="utf-8"))
    target_name = str(
        getattr(platform, "install_flavor", "")
        or getattr(platform, "name", "gemini")
    )

    def rewrite(value):
        if isinstance(value, dict):
            return {key: rewrite(item) for key, item in value.items()}
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, str) and "hook_entry.py" in value:
            return hook_command or f"autorun --cli {target_name}"
        return value

    from ..platforms import GEMINI

    event_map = {
        source_event: getattr(platform, "autorun_to_harness_cli_events", {}).get(
            autorun_event
        )
        for source_event, autorun_event in GEMINI.harness_cli_to_autorun_events.items()
    }
    translated = getattr(platform, "hook_protocol").translate_manifest(
        rewrite(document), event_map
    )
    installed_events = getattr(platform, "installed_hook_events", frozenset())
    container_key = getattr(
        getattr(platform, "hook_protocol"),
        "hook_manifest_container_key",
        "hooks",
    )
    events = translated.get(container_key) if isinstance(translated, dict) else None
    if installed_events and isinstance(events, dict):
        translated[container_key] = {
            event: handlers
            for event, handlers in events.items()
            if event in installed_events
        }
    return translated


def extension_intents(
    platform: object,
    ctx: Context,
    plugins: Mapping[str, Path],
    *,
    staged: Mapping[str, Path],
) -> Iterator[Intent]:
    """One intent per extension, pointing at an already-staged directory.

    ``staged`` is built by :func:`stage_extension` before the walk, which keeps
    generation outside the traversal and keeps :class:`Intent` pure data.
    """
    harness = getattr(platform, "name", "")
    for plugin, source in staged.items():
        if plugin not in plugins:
            continue
        yield Intent(
            target=registration_source_dir(ctx, harness, plugin),
            source=source,
            plugin=plugin,
        )


def demo() -> None:
    """Self-check against the manifest shape a real install produced."""
    import tempfile

    manifest = Manifest(name="ar", version="1.0.0rc1", description="autorun")
    document = manifest.as_document()

    # The installed manifest on a real machine has exactly these values.
    assert document["hooks"] == "./hooks/hooks.json", document
    assert document["commands"] == "./commands/" and document["skills"] == "./skills/"
    assert document["contextFileName"] == "GEMINI.md"
    assert isinstance(document["hooks"], str), "hooks is a path string, not an object"

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        template = root / "gemini_template"
        (template / "hooks").mkdir(parents=True)
        (template / "hooks" / "hook_entry.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        (template / "hooks" / "hooks.json").write_text('{"PreToolUse": []}', encoding="utf-8")

        plugin = root / "plugins" / "ar"
        (plugin / "commands").mkdir(parents=True)
        (plugin / "commands" / "go.md").write_text("go\n", encoding="utf-8")
        (plugin / "skills" / "commit").mkdir(parents=True)
        (plugin / "skills" / "commit" / "SKILL.md").write_text("# commit\n", encoding="utf-8")

        # No skills passed: the harness reads the shared root, so the extension
        # must NOT receive a second copy. This is the defect that put all 17
        # skills into ~/.qwen/extensions/ar/skills as well as ~/.agents/skills.
        shared_route = stage_extension(template, plugin, root / "shared", manifest)
        assert not (shared_route / "skills").exists(), \
            "a shared-root harness gets no native skill copy"

        # A harness with a native route receives exactly the named skills.
        staging = stage_extension(
            template, plugin, root / "staged", manifest,
            skills={"commit": plugin / "skills" / "commit"},
        )

        # BUG #14449: hook code lands where the harness hardcodes its lookup.
        assert (staging / "hooks" / "hook_entry.py").is_file()
        assert (staging / "hooks" / "hooks.json").is_file()
        assert (staging / "commands" / "go.md").is_file()
        assert (staging / "skills" / "commit" / "SKILL.md").is_file()
        assert json.loads((staging / MANIFEST_NAME).read_text())["name"] == "ar"
        assert MANIFEST_NAME == "gemini-extension.json", "the name every family CLI reads"

        # Refresh eligibility: positive evidence only.
        installed = root / "installed"
        installed.mkdir()
        assert not refreshable(installed, template), "no marker and no receipt is the user's"

        (installed / ".qwen-extension-install.json").write_text(
            json.dumps({"source": str(template), "type": "local"}), encoding="utf-8"
        )
        assert refreshable(installed, template), "the harness receipt names our template"

        # A differently-rooted checkout must not claim someone else's extension.
        other = root / "other_template"
        other.mkdir()
        assert not refreshable(installed, other)

        # A receipt naming a path that merely shares a prefix is not a match.
        sibling = root / "gemini_template_backup"
        sibling.mkdir()
        assert not refreshable(installed, sibling)

        # An unreadable receipt is ignored rather than treated as ours.
        (installed / ".broken-extension-install.json").write_text("{not json", encoding="utf-8")
        assert refreshable(installed, template), "one bad receipt does not hide a good one"

    print("installer.extension: all self-checks passed")


if __name__ == "__main__":
    demo()
