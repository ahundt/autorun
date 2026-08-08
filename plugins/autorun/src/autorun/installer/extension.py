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
real ``hooks/`` directory with ``hook_entry.py`` beside the manifest, so
``${extensionPath}/hooks/hook_entry.py`` resolves. Verified on this machine: the
installed manifest says ``"hooks": "./hooks/hooks.json"`` and both files exist.
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
from typing import Iterator, Mapping

from .fs import read_marker
from .traversal import Context, Intent

__all__ = [
    "Manifest",
    "receipt_names_source",
    "extension_dir",
    "extension_intents",
    "refreshable",
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


def refreshable(installed: Path, source: Path, *, plugin: str = "") -> bool:
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
    return receipt_names_source(installed, source)


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
    if (entry := template / "hooks" / "hook_entry.py").is_file():
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


def translated_hooks(template: Path, platform: object) -> Mapping[str, object] | None:
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
            return value.replace("--cli gemini", f"--cli {target_name}")
        return value

    from ..platforms import GEMINI

    event_map = {
        source_event: getattr(platform, "autorun_to_harness_cli_events", {}).get(
            autorun_event
        )
        for source_event, autorun_event in GEMINI.harness_cli_to_autorun_events.items()
    }
    return getattr(platform, "hook_protocol").translate_manifest(
        rewrite(document), event_map
    )


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
    for plugin, source in staged.items():
        if plugin not in plugins:
            continue
        target = extension_dir(ctx, platform, plugin)
        if target is None:
            continue  # this harness has no extension directory; not an error
        yield Intent(target=target, source=source, plugin=plugin)


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
