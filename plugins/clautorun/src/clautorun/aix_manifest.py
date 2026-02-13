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
    
    # 1. Base Manifest Data
    manifest = {
        "name": "cr",
        "version": pkg.get("version", "0.8.0"),
        "description": pkg.get("description", ""),
        "author": {
            "name": pkg.get("authors", ["clautorun contributors"])[0],
            "url": pkg.get("repository", "https://github.com/ahundt/clautorun")
        },
        "homepage": f"{pkg.get('repository', '')}#readme",
        "repository": pkg.get("repository", ""),
        "license": pkg.get("license", "Apache-2.0"),
        "keywords": [
            "claude-code", "gemini-cli", "agent-sdk", "file-policy", 
            "autonomous-sessions", "safety-guards"
        ],
        "commands": "./commands/",
        "skills": "./skills/"
    }

    # 2. Write Claude Manifest
    claude_dir = plugin_dir / ".claude-plugin"
    claude_dir.mkdir(exist_ok=True)
    with open(claude_dir / "plugin.json", "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"   ✓ Generated .claude-plugin/plugin.json")

    # 3. Write Gemini Manifest
    with open(plugin_dir / "gemini-extension.json", "w") as f:
        gemini_manifest = manifest.copy()
        gemini_manifest["contextFileName"] = "GEMINI.md"
        json.dump(gemini_manifest, f, indent=2)
    print(f"   ✓ Generated gemini-extension.json")

    # 4. Generate Skill Proxies (Gemini Compliance)
    skills_dir = plugin_dir / "skills"
    if skills_dir.is_dir():
        for skill in aix_data.get("skills", []):
            skill_path = Path(skill.get("path", ""))
            # We expect path like: plugins/clautorun/skills/name/SKILL.md
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
    # If run directly, assume we are in src/clautorun
    plugin_root = Path(__file__).resolve().parent.parent.parent
    generate_manifests(plugin_root)
