"""Setuptools helpers for bundling canonical plugin assets into wheels."""

from __future__ import annotations

import shutil
from pathlib import Path


PLUGIN_ASSET_TREES = (
    (Path(".claude-plugin"), Path(".claude-plugin")),
    (Path(".codex-plugin"), Path(".codex-plugin")),
    (Path("agents"), Path("agents")),
    (Path("commands"), Path("commands")),
    (Path("hooks"), Path("hooks")),
    (Path("scripts"), Path("scripts")),
    (Path("skills"), Path("skills")),
    (Path("src/autorun/gemini_template"), Path("gemini_template")),
)


def copy_plugin_assets(plugin_root: Path, package_root: Path) -> None:
    """Copy tracked plugin trees into the staged autorun package directory."""
    ignore = shutil.ignore_patterns("__pycache__", "*.py[cod]", "*.tmp", ".DS_Store")
    for source_rel, destination_rel in PLUGIN_ASSET_TREES:
        shutil.copytree(
            plugin_root / source_rel,
            package_root / destination_rel,
            dirs_exist_ok=True,
            ignore=ignore,
        )
