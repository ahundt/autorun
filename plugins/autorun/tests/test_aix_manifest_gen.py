#!/usr/bin/env python3
"""Test manifest generation from aix.toml."""
import json
import os
import shutil
import tempfile
from pathlib import Path
import pytest

from autorun.aix_manifest import generate_manifests

def test_generate_manifests_correctness():
    """Verify that generated manifests match expectations from aix.toml."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        
        # 1. Setup mock repository structure
        repo_root = tmp_path
        plugin_dir = repo_root / "plugins" / "autorun"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "skills").mkdir()
        (plugin_dir / "skills" / "test-skill").mkdir()
        (plugin_dir / "skills" / "test-skill" / "meaningful.md").write_text("# Test Skill")
        
        # 2. Create mock aix.toml
        aix_toml = repo_root / "aix.toml"
        aix_toml.write_text("""
[package]
name = "autorun"
version = "0.9.9"
description = "Test Description"
authors = ["Test Author"]
repository = "https://github.com/test/repo"

[[skills]]
name = "test_skill"
path = "plugins/autorun/skills/test-skill/SKILL.md"
""")

        # 3. Generate
        generate_manifests(plugin_dir)
        
        # 4. Verify Claude Manifest
        claude_manifest_path = plugin_dir / ".claude-plugin" / "plugin.json"
        assert claude_manifest_path.exists()
        with open(claude_manifest_path, encoding="utf-8") as f:
            data = json.load(f)
            assert data["version"] == "0.9.9"
            assert data["description"] == "Test Description"
            assert data["author"]["name"] == "Test Author"
            
        # 5. Verify Gemini Manifest
        gemini_manifest_path = plugin_dir / "gemini-extension.json"
        assert gemini_manifest_path.exists()
        with open(gemini_manifest_path, encoding="utf-8") as f:
            data = json.load(f)
            assert data["contextFileName"] == "GEMINI.md"
            assert data["version"] == "0.9.9"
            
        # 6. Verify Proxy Symlink
        skill_link = plugin_dir / "skills" / "test-skill" / "SKILL.md"
        assert skill_link.exists()
        assert skill_link.is_symlink()
        assert os.readlink(skill_link) == "meaningful.md"

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
