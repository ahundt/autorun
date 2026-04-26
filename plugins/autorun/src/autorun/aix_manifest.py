#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIX Manifest Generator - Single source of truth for Claude and Gemini manifests.

Parses aix.toml and generates:
- .claude-plugin/plugin.json
- gemini-extension.json
- Proxy symlinks for Gemini skills (SKILL.md -> meaningful_name.md)
"""
import json
import os
import shutil
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib

def generate_manifests(plugin_dir: Path):
    """Generate platform-specific manifests from aix.toml."""
    aix_path = plugin_dir.parent.parent / "aix.toml"
    if not aix_path.exists():
        # Fallback to current dir if nested structure differs
        aix_path = plugin_dir / "aix.toml"
        if not aix_path.exists():
            print(f"⚠️ aix.toml not found at {aix_path}, skipping manifest generation")
            return

    with open(aix_path, "rb") as f:
        aix_data = tomllib.load(f)

    pkg = aix_data.get("package", {})

    # 1. Base Manifest Data (shared fields). Claude uses the default hooks path
    # (hooks/hooks.json) and therefore declares no explicit "hooks" field.
    # Claude bug #24115 scans the hooks/ subdirectory of the plugin source,
    # so the Gemini manifest cannot live at plugin root (would not leak Gemini
    # event names into Claude's scan, but would leak a manifest that bug #24115
    # does not check — still, keep it out of the plugin root for consistency).
    manifest = {
        "name": "ar",
        "version": pkg.get("version", "0.11.0"),
        "description": pkg.get("description", ""),
        "author": {
            "name": pkg.get("authors", ["autorun contributors"])[0],
            "url": pkg.get("repository", "https://github.com/ahundt/autorun")
        },
        "homepage": f"{pkg.get('repository', '')}#readme",
        "repository": pkg.get("repository", ""),
        "license": pkg.get("license", "Apache-2.0"),
        "keywords": [
            "claude-code", "gemini-cli", "agent-sdk", "file-policy",
            "autonomous-sessions", "safety-guards"
        ],
        "commands": "./commands/",
        "skills": "./skills/",
    }

    # 2. Write Claude Manifest (no explicit hooks field — uses default path).
    claude_dir = plugin_dir / ".claude-plugin"
    claude_dir.mkdir(exist_ok=True)
    with open(claude_dir / "plugin.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"   ✓ Generated .claude-plugin/plugin.json")

    # 3. Write Gemini Manifest into the template dir under src/autorun/. This
    # keeps Gemini-only assets out of Claude's marketplace-source scan path
    # (bug #24115). The installer materializes ~/.gemini/extensions/ar/ from
    # this template; Gemini's hardcoded "hooks/hooks.json" path lives next to
    # this manifest inside the template directory.
    gemini_template = plugin_dir / "src" / "autorun" / "gemini_template"
    gemini_template.mkdir(parents=True, exist_ok=True)
    gemini_manifest = manifest.copy()
    gemini_manifest["contextFileName"] = "GEMINI.md"
    # Gemini CLI ignores this field today (bug #14449) but include it for
    # forward compatibility with PR #14460 (gemini-cli 0.28+).
    gemini_manifest["hooks"] = "./hooks/hooks.json"
    with open(gemini_template / "gemini-extension.json", "w", encoding="utf-8") as f:
        json.dump(gemini_manifest, f, indent=2)
    print(f"   ✓ Generated src/autorun/gemini_template/gemini-extension.json")

    # 3b. Sync hook_entry.py into the template so Pathway 2/6
    # (`gemini extensions install <github-url>` or `.` from repo root) finds a
    # usable hook_entry.py at `<template>/hooks/hook_entry.py` without needing
    # the autorun installer to run first. The canonical source is
    # `plugins/autorun/hooks/hook_entry.py` — this copy is kept byte-identical.
    # test_dual_cli_pathways.test_template_hook_entry_matches_canonical pins
    # the sync so drift fails loudly at pytest time.
    canonical_entry = plugin_dir / "hooks" / "hook_entry.py"
    template_hooks_dir = gemini_template / "hooks"
    if canonical_entry.is_file():
        template_hooks_dir.mkdir(parents=True, exist_ok=True)
        template_entry = template_hooks_dir / "hook_entry.py"
        shutil.copy2(canonical_entry, template_entry)
        print(f"   ✓ Synced hook_entry.py → gemini_template/hooks/")

    # 4. Cleanup legacy manifest locations (runs after every install to keep
    # pre-refactor working trees healthy).
    for legacy in [plugin_dir / "gemini-extension.json",
                   plugin_dir / "hooks" / "claude-hooks.json",
                   plugin_dir / "hooks" / "claude-hooks.json.bak"]:
        if legacy.exists() and not legacy.is_symlink():
            try:
                legacy.unlink()
                print(f"   ✓ Removed legacy file: {legacy.relative_to(plugin_dir)}")
            except OSError:
                pass

    # 4. Generate Skill Proxies (Gemini Compliance)
    skills_dir = plugin_dir / "skills"
    if skills_dir.is_dir():
        for skill in aix_data.get("skills", []):
            skill_path = Path(skill.get("path", ""))
            # We expect path like: plugins/autorun/skills/name/SKILL.md
            if "SKILL.md" in skill_path.name:
                skill_subdir = plugin_dir / "skills" / skill_path.parent.name
                skill_subdir.mkdir(exist_ok=True)
                
                # Check for meaningful file to link to
                # If the subdirectory only has SKILL.md, it might be a migration case
                # or it might already be correct. 
                # We want to ensure that if a meaningfully named file exists, SKILL.md links to it.
                target_file = None
                for f in skill_subdir.iterdir():
                    if f.is_file() and f.suffix == ".md" and f.name != "SKILL.md":
                        target_file = f
                        break
                
                if target_file:
                    skill_link = skill_subdir / "SKILL.md"
                    if not skill_link.exists() or skill_link.is_symlink():
                        # Force relative symlink
                        if skill_link.exists():
                            skill_link.unlink()
                        os.symlink(target_file.name, skill_link)
                        print(f"   ✓ Ensured proxy: {skill_subdir.name}/SKILL.md -> {target_file.name}")

if __name__ == "__main__":
    # If run directly, assume we are in src/autorun
    plugin_root = Path(__file__).resolve().parent.parent.parent
    generate_manifests(plugin_root)
