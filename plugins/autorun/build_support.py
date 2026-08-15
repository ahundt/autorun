"""Setuptools helpers for bundling canonical plugin assets into wheels."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


PLUGIN_ASSET_TREES = (
    (Path(".claude-plugin"), Path(".claude-plugin")),
    (Path(".codex-plugin"), Path(".codex-plugin")),
    (Path("agents"), Path("agents")),
    (Path("commands"), Path("commands")),
    (Path("hooks"), Path("hooks")),
    (Path("scripts"), Path("scripts")),
    (Path("skills"), Path("skills")),
    *(
        (Path(f"src/autorun/{name}_template"), Path(f"{name}_template"))
        for name in (
            "bridge", "claude", "codex", "forgecode", "gemini", "opencode", "pi"
        )
    ),
)


#: Packages whose source lives in a sibling plugin directory and reaches this
#: distribution through a symlink under ``src/``. ``pdf_extraction`` belongs to
#: the ``pdf-extractor`` marketplace plugin, which keeps its own directory,
#: manifest, commands, skill, source and tests; it is not a second Python
#: distribution, so its code ships here behind the ``pdf`` extra.
#:
#: setuptools' package discovery follows the symlink, but its sdist does not,
#: and ``uv build`` builds the wheel *from the sdist*. Without this the sdist
#: ships a dangling link, the wheel silently loses the package, and the only
#: visible symptom is an ``extract-pdfs`` entry point that cannot import.
SIBLING_PACKAGE_LINKS = (Path("src") / "pdf_extraction",)


def materialize_sibling_packages(plugin_root: Path, base_dir: Path) -> list[Path]:
    """Replace symlinked package trees in a staged tree with their real files.

    Returns the destinations written, so a caller can assert the work happened
    rather than trusting a silent no-op.
    """
    written: list[Path] = []
    for relative in SIBLING_PACKAGE_LINKS:
        link = plugin_root / relative
        if not link.is_dir():
            continue
        destination = base_dir / relative
        if destination.is_symlink() or destination.is_file():
            destination.unlink()
        elif destination.is_dir():
            shutil.rmtree(destination)
        shutil.copytree(
            link.resolve(),
            destination,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(
                "__pycache__", "*.py[cod]", "*.tmp", ".DS_Store"
            ),
        )
        written.append(destination)
    return written


def _tracked_paths(plugin_root: Path) -> set[Path] | None:
    """Return Git-tracked asset paths, or None for an already-clean sdist."""
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(plugin_root),
                "ls-files",
                "-z",
                "--",
                *(str(source) for source, _destination in PLUGIN_ASSET_TREES),
            ],
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return {
        Path(raw.decode("utf-8", errors="strict"))
        for raw in result.stdout.split(b"\0")
        if raw
    }


def tracked_sdist_files(plugin_root: Path, files: list[str]) -> list[str]:
    """Keep an sdist at Git-tracked source plus required generated metadata."""
    try:
        result = subprocess.run(
            ["git", "-C", str(plugin_root), "ls-files", "-z"],
            capture_output=True,
            check=False,
        )
    except OSError:
        return files
    if result.returncode != 0:
        return files
    tracked = {
        Path(raw.decode("utf-8", errors="strict"))
        for raw in result.stdout.split(b"\0")
        if raw
    }
    return [
        filename
        for filename in files
        if Path(filename) in tracked
        or Path(filename).name == "LICENSE"
        or any(part.endswith(".egg-info") for part in Path(filename).parts)
    ]


def _copy_ignore(plugin_root: Path, tracked: set[Path] | None):
    generated = shutil.ignore_patterns("__pycache__", "*.py[cod]", "*.tmp", ".DS_Store")
    allowed = None if tracked is None else {
        parent
        for path in tracked
        for parent in (path, *path.parents)
        if parent != Path(".")
    }

    def ignore(directory: str, names: list[str]) -> set[str]:
        ignored = set(generated(directory, names))
        relative = Path(directory).resolve().relative_to(plugin_root.resolve())
        # The source checkout keeps this symlink so a legacy
        # ``gemini extensions install <repo>`` has the hard-coded hook path.
        # Wheels already carry the same canonical entry point in
        # ``autorun/hooks`` and stage it programmatically; copying the symlink
        # would turn one source into a second packaged file.
        if relative == Path("src/autorun/gemini_template/hooks"):
            ignored.add("hook_entry.py")
        if allowed is not None:
            ignored.update(
                name for name in names if relative / name not in allowed
            )
        return ignored

    return ignore


def copy_plugin_assets(plugin_root: Path, package_root: Path) -> None:
    """Copy tracked plugin trees into the staged autorun package directory."""
    ignore = _copy_ignore(plugin_root, _tracked_paths(plugin_root))
    for source_rel, destination_rel in PLUGIN_ASSET_TREES:
        shutil.copytree(
            plugin_root / source_rel,
            package_root / destination_rel,
            dirs_exist_ok=True,
            ignore=ignore,
        )
    # ``build_py`` may have copied this .py from sdist package-data before the
    # tracked asset pass runs. The installed extension always stages the
    # canonical ``autorun/hooks/hook_entry.py``; remove the source-only legacy
    # Gemini link even when it arrived before copytree's ignore callback.
    source_only_hook = (
        package_root / "gemini_template" / "hooks" / "hook_entry.py"
    )
    source_only_hook.unlink(missing_ok=True)
    _make_installed_claude_plugin_local(package_root)


def build_metadata(
    plugin_root: Path,
    env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return deterministic package identity without modifying the checkout."""
    environment = os.environ if env is None else env
    source = plugin_root / "src" / "autorun" / "metadata.json"
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        document = {}
    if not document.get("version") or document.get("version") == "unknown":
        try:
            project = (plugin_root / "pyproject.toml").read_text(encoding="utf-8")
        except OSError:
            project = ""
        match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', project, re.MULTILINE)
        document["version"] = match.group(1) if match else "unknown"

    commit = environment.get("AUTORUN_BUILD_COMMIT", "").strip()
    if not commit:
        try:
            resolved = subprocess.run(
                ["git", "-C", str(plugin_root), "rev-parse", "HEAD"],
                capture_output=True,
                check=False,
                text=True,
            )
        except OSError:
            resolved = subprocess.CompletedProcess((), 1, "", "")
        if resolved.returncode == 0:
            commit = resolved.stdout.strip()
            try:
                dirty = subprocess.run(
                    [
                        "git",
                        "-C",
                        str(plugin_root),
                        "status",
                        "--porcelain",
                        "--untracked-files=no",
                    ],
                    capture_output=True,
                    check=False,
                    text=True,
                )
            except OSError:
                dirty = subprocess.CompletedProcess((), 1, "", "")
            if dirty.returncode == 0 and dirty.stdout.strip():
                commit += "+dirty"
    document["commit"] = commit or str(document.get("commit") or "unknown")

    raw_epoch = environment.get("SOURCE_DATE_EPOCH", "").strip()
    if raw_epoch:
        try:
            document["build_time"] = datetime.fromtimestamp(
                int(raw_epoch), timezone.utc
            ).isoformat().replace("+00:00", "Z")
        except (ValueError, OverflowError, OSError):
            document["build_time"] = "unknown"
    else:
        document["build_time"] = str(document.get("build_time") or "unknown")
    return {key: str(value) for key, value in document.items()}


def write_build_metadata(
    plugin_root: Path,
    target: Path,
    env: Mapping[str, str] | None = None,
) -> None:
    """Write package identity into a build tree, never into source."""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(build_metadata(plugin_root, env), indent=2) + "\n",
        encoding="utf-8",
    )


def _make_installed_claude_plugin_local(package_root: Path) -> None:
    """Bind the wheel's Claude plugin to the installed distribution.

    The source-tree marketplace intentionally points at GitHub.  Inside a wheel
    that would clone a second copy (and Claude currently chooses SSH for that
    source), even though the complete plugin is already present.  The wheel is
    its own durable local marketplace, and its hooks use the installed console
    entry point whose dependencies were resolved by the package installer.
    """
    marketplace_path = package_root / ".claude-plugin" / "marketplace.json"
    hooks_path = package_root / "hooks" / "hooks.json"
    if not marketplace_path.is_file() or not hooks_path.is_file():
        return
    marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
    plugins = [
        plugin
        for plugin in marketplace.get("plugins", ())
        if isinstance(plugin, dict) and plugin.get("name") == "ar"
    ]
    marketplace["plugins"] = plugins
    for plugin in plugins:
        if isinstance(plugin, dict) and plugin.get("name") == "ar":
            plugin["source"] = "."
    marketplace_path.write_text(
        json.dumps(marketplace, indent=2) + "\n",
        encoding="utf-8",
    )

    hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
    for matchers in hooks.get("hooks", {}).values():
        if not isinstance(matchers, list):
            continue
        for matcher in matchers:
            if not isinstance(matcher, dict):
                continue
            for hook in matcher.get("hooks", ()):
                if isinstance(hook, dict) and hook.get("type") == "command":
                    hook["command"] = "autorun --cli claude"
    hooks_path.write_text(json.dumps(hooks, indent=2) + "\n", encoding="utf-8")
