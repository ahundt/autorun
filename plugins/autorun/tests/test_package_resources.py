"""Test package resource access for installed vs source autorun.

Test Coverage:
- get_plugin_root() returns valid directory with .claude-plugin/
- get_commands_dir() returns directory with known command files
- get_skills_dir(), get_agents_dir(), get_hooks_dir() return valid directories
- Resource access works from both source and installed package locations

TDD Methodology:
- RED: These tests define the expected API for resources.py
- GREEN: Implement resources.py to make tests pass
- REFACTOR: Clean up while keeping tests green
"""
from pathlib import Path


class TestGetPluginRoot:
    """Test get_plugin_root() locates plugin directory."""

    def test_get_plugin_root_returns_path(self):
        """Test: get_plugin_root() returns a Path object."""
        from autorun.resources import get_plugin_root

        root = get_plugin_root()
        assert isinstance(root, Path)

    def test_get_plugin_root_directory_exists(self):
        """Test: get_plugin_root() returns an existing directory."""
        from autorun.resources import get_plugin_root

        root = get_plugin_root()
        assert root.exists(), f"Plugin root does not exist: {root}"
        assert root.is_dir(), f"Plugin root is not a directory: {root}"

    def test_get_plugin_root_has_claude_plugin(self):
        """Test: get_plugin_root() returns directory containing .claude-plugin/."""
        from autorun.resources import get_plugin_root

        root = get_plugin_root()
        claude_plugin = root / ".claude-plugin"
        assert claude_plugin.exists(), f".claude-plugin not found in {root}"

    def test_get_plugin_root_has_marketplace_json(self):
        """Test: get_plugin_root() returns directory with marketplace.json."""
        from autorun.resources import get_plugin_root

        root = get_plugin_root()
        marketplace = root / ".claude-plugin" / "marketplace.json"
        assert marketplace.exists(), f"marketplace.json not found in {root}"

    def test_artifact_root_resolves_registered_plugin_name(self, tmp_path):
        """A wheel has no plugins/autorun subdirectory beneath its package root."""
        import json

        from autorun.installer.discovery import plugin_dir

        root = tmp_path / "autorun"
        metadata = root / ".claude-plugin"
        metadata.mkdir(parents=True)
        (metadata / "plugin.json").write_text(json.dumps({"name": "ar"}))
        (metadata / "marketplace.json").write_text(
            json.dumps(
                {
                    "plugins": [
                        {
                            "name": "ar",
                            "source": {
                                "source": "github",
                                "repo": "ahundt/autorun",
                                "subdirectory": "plugins/autorun",
                            },
                        },
                        {
                            "name": "pdf-extractor",
                            "source": {
                                "source": "github",
                                "repo": "ahundt/autorun",
                                "subdirectory": "plugins/pdf-extractor",
                            },
                        },
                    ]
                }
            )
        )

        assert plugin_dir(root, "ar") == root

    def test_wheel_asset_copy_excludes_untracked_files(
        self, tmp_path, monkeypatch
    ):
        import build_support

        source = tmp_path / "plugin"
        for tree, _destination in build_support.PLUGIN_ASSET_TREES:
            (source / tree).mkdir(parents=True, exist_ok=True)
        tracked = Path("skills/example/SKILL.md")
        leaked = Path("skills/example/private.pdf")
        (source / tracked).parent.mkdir(parents=True, exist_ok=True)
        (source / tracked).write_text("tracked")
        (source / leaked).write_bytes(b"private")
        monkeypatch.setattr(build_support, "_tracked_paths", lambda _root: {tracked})

        destination = tmp_path / "wheel" / "autorun"
        build_support.copy_plugin_assets(source, destination)

        assert (destination / tracked).read_text() == "tracked"
        assert not (destination / leaked).exists()

    def test_wheel_keeps_one_hook_entry_while_source_link_supports_gemini(
        self, tmp_path, monkeypatch
    ):
        import build_support

        source = tmp_path / "plugin"
        for tree, _destination in build_support.PLUGIN_ASSET_TREES:
            (source / tree).mkdir(parents=True, exist_ok=True)
        canonical = source / "hooks" / "hook_entry.py"
        template_hooks = source / "src" / "autorun" / "gemini_template" / "hooks"
        template_hooks.mkdir(parents=True, exist_ok=True)
        canonical.write_text("canonical\n")
        (template_hooks / "hook_entry.py").symlink_to(
            Path("../../../../hooks/hook_entry.py")
        )
        tracked = {
            Path("hooks/hook_entry.py"),
            Path("src/autorun/gemini_template/hooks/hook_entry.py"),
        }
        monkeypatch.setattr(build_support, "_tracked_paths", lambda _root: tracked)

        destination = tmp_path / "wheel" / "autorun"
        build_support.copy_plugin_assets(source, destination)

        assert (destination / "hooks" / "hook_entry.py").read_text() == "canonical\n"
        assert not (
            destination / "gemini_template" / "hooks" / "hook_entry.py"
        ).exists()

    def test_sdist_filter_keeps_tracked_and_generated_metadata_only(
        self, tmp_path, monkeypatch
    ):
        import subprocess

        import build_support

        source = tmp_path / "plugin"
        source.mkdir()
        tracked = Path("skills/example/SKILL.md")
        output = b"skills/example/SKILL.md\0"
        monkeypatch.setattr(
            build_support.subprocess,
            "run",
            lambda *_args, **_kwargs: subprocess.CompletedProcess((), 0, output, b""),
        )

        kept = build_support.tracked_sdist_files(
            source,
            [
                str(tracked),
                "skills/example/private.pdf",
                "src/autorun.egg-info/PKG-INFO",
                "LICENSE",
            ],
        )

        assert kept == [
            str(tracked),
            "src/autorun.egg-info/PKG-INFO",
            "LICENSE",
        ]

    def test_build_metadata_is_reproducible_and_marks_dirty_checkout(
        self, tmp_path, monkeypatch
    ):
        import subprocess

        import build_support

        source = tmp_path / "plugin" / "src" / "autorun"
        source.mkdir(parents=True)
        (source / "metadata.json").write_text(
            '{"version":"1.0.0rc1","commit":"unknown","build_time":"unknown"}'
        )
        responses = iter(
            (
                subprocess.CompletedProcess((), 0, "abc123\n", ""),
                subprocess.CompletedProcess((), 0, " M src/autorun/core.py\n", ""),
            )
        )
        monkeypatch.setattr(build_support.subprocess, "run", lambda *_a, **_k: next(responses))

        document = build_support.build_metadata(
            tmp_path / "plugin",
            {"SOURCE_DATE_EPOCH": "1700000000"},
        )

        assert document == {
            "version": "1.0.0rc1",
            "commit": "abc123+dirty",
            "build_time": "2023-11-14T22:13:20Z",
        }

    def test_explicit_build_commit_does_not_call_git(self, tmp_path, monkeypatch):
        import build_support

        source = tmp_path / "plugin" / "src" / "autorun"
        source.mkdir(parents=True)
        (source / "metadata.json").write_text('{"version":"1.0.0rc1"}')
        monkeypatch.setattr(
            build_support.subprocess,
            "run",
            lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("git called")),
        )

        document = build_support.build_metadata(
            tmp_path / "plugin",
            {"AUTORUN_BUILD_COMMIT": "release-sha", "SOURCE_DATE_EPOCH": "1700000000"},
        )

        assert document["commit"] == "release-sha"

    def test_build_metadata_reads_version_from_pyproject_when_source_is_absent(
        self, tmp_path
    ):
        import build_support

        plugin = tmp_path / "plugin"
        plugin.mkdir()
        (plugin / "pyproject.toml").write_text(
            '[project]\nname = "autorun"\nversion = "1.0.0rc1"\n'
        )

        document = build_support.build_metadata(
            plugin,
            {"AUTORUN_BUILD_COMMIT": "release-sha"},
        )

        assert document["version"] == "1.0.0rc1"

    def test_wheel_templates_have_one_package_local_layout(self, tmp_path, monkeypatch):
        import build_support

        source = tmp_path / "plugin"
        tracked = set()
        for name in ("claude", "codex", "forgecode", "gemini", "opencode"):
            relative = Path(f"src/autorun/{name}_template/marker.txt")
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name)
            tracked.add(relative)
        for tree, _destination in build_support.PLUGIN_ASSET_TREES:
            (source / tree).mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(build_support, "_tracked_paths", lambda _root: tracked)

        destination = tmp_path / "wheel" / "autorun"
        build_support.copy_plugin_assets(source, destination)

        for name in ("claude", "codex", "forgecode", "gemini", "opencode"):
            assert (destination / f"{name}_template" / "marker.txt").read_text() == name
            assert not (destination / "src" / "autorun" / f"{name}_template").exists()

    def test_wheel_claude_marketplace_and_hooks_use_the_installed_distribution(
        self, tmp_path, monkeypatch
    ):
        import json

        import build_support

        source = tmp_path / "plugin"
        marketplace = source / ".claude-plugin" / "marketplace.json"
        marketplace.parent.mkdir(parents=True)
        marketplace.write_text(
            json.dumps(
                {
                    "name": "autorun",
                    "plugins": [
                        {
                            "name": "ar",
                            "source": {
                                "source": "github",
                                "repo": "ahundt/autorun",
                                "subdirectory": "plugins/autorun",
                            },
                        }
                    ],
                }
            )
        )
        hook_manifest = source / "hooks" / "hooks.json"
        hook_manifest.parent.mkdir(parents=True)
        hook_manifest.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {
                                "hooks": [
                                    {"type": "command", "command": "uv run hook_entry.py"}
                                ]
                            }
                        ]
                    }
                }
            )
        )
        tracked = {
            Path(".claude-plugin/marketplace.json"),
            Path("hooks/hooks.json"),
        }
        for tree, _destination in build_support.PLUGIN_ASSET_TREES:
            (source / tree).mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(build_support, "_tracked_paths", lambda _root: tracked)

        destination = tmp_path / "wheel" / "autorun"
        build_support.copy_plugin_assets(source, destination)

        installed_marketplace = json.loads(
            (destination / ".claude-plugin" / "marketplace.json").read_text()
        )
        assert installed_marketplace["plugins"] == [
            {"name": "ar", "source": "."}
        ]
        installed_hooks = json.loads((destination / "hooks" / "hooks.json").read_text())
        assert (
            installed_hooks["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
            == "autorun --cli claude"
        )


class TestGetCommandsDir:
    """Test get_commands_dir() returns commands directory."""

    def test_get_commands_dir_returns_path(self):
        """Test: get_commands_dir() returns a Path object."""
        from autorun.resources import get_commands_dir

        commands_dir = get_commands_dir()
        assert isinstance(commands_dir, Path)

    def test_get_commands_dir_exists(self):
        """Test: get_commands_dir() returns existing directory."""
        from autorun.resources import get_commands_dir

        commands_dir = get_commands_dir()
        assert commands_dir.exists(), f"Commands directory not found: {commands_dir}"

    def test_get_commands_dir_has_known_commands(self):
        """Test: commands/ contains known command files."""
        from autorun.resources import get_commands_dir

        commands_dir = get_commands_dir()
        # These are core commands that should always exist
        expected_commands = ["go.md", "st.md", "sos.md"]
        for cmd in expected_commands:
            assert (commands_dir / cmd).exists(), f"Command file missing: {cmd}"


class TestGetSkillsDir:
    """Test get_skills_dir() returns skills directory."""

    def test_get_skills_dir_returns_path(self):
        """Test: get_skills_dir() returns a Path object."""
        from autorun.resources import get_skills_dir

        skills_dir = get_skills_dir()
        assert isinstance(skills_dir, Path)

    def test_get_skills_dir_exists(self):
        """Test: get_skills_dir() returns existing directory."""
        from autorun.resources import get_skills_dir

        skills_dir = get_skills_dir()
        assert skills_dir.exists(), f"Skills directory not found: {skills_dir}"


class TestGetAgentsDir:
    """Test get_agents_dir() returns agents directory."""

    def test_get_agents_dir_returns_path(self):
        """Test: get_agents_dir() returns a Path object."""
        from autorun.resources import get_agents_dir

        agents_dir = get_agents_dir()
        assert isinstance(agents_dir, Path)

    def test_get_agents_dir_exists(self):
        """Test: get_agents_dir() returns existing directory."""
        from autorun.resources import get_agents_dir

        agents_dir = get_agents_dir()
        assert agents_dir.exists(), f"Agents directory not found: {agents_dir}"


class TestGetHooksDir:
    """Test get_hooks_dir() returns hooks directory."""

    def test_get_hooks_dir_returns_path(self):
        """Test: get_hooks_dir() returns a Path object."""
        from autorun.resources import get_hooks_dir

        hooks_dir = get_hooks_dir()
        assert isinstance(hooks_dir, Path)

    def test_get_hooks_dir_exists(self):
        """Test: get_hooks_dir() returns existing directory."""
        from autorun.resources import get_hooks_dir

        hooks_dir = get_hooks_dir()
        assert hooks_dir.exists(), f"Hooks directory not found: {hooks_dir}"

    def test_get_hooks_dir_has_hook_files(self):
        """Test: hooks/ contains hook configuration files."""
        from autorun.resources import get_hooks_dir

        hooks_dir = get_hooks_dir()
        # hooks.json is the primary hook configuration for Claude Code (renamed from claude-hooks.json)
        assert (hooks_dir / "hooks.json").exists(), "hooks.json not found"
        assert (hooks_dir / "hook_entry.py").exists(), "hook_entry.py not found"


class TestAllResourcesAccessible:
    """Test that all plugin resources are accessible together."""

    def test_all_resource_dirs_under_same_root(self):
        """Test: All resource directories share the same parent plugin root."""
        from autorun.resources import (
            get_plugin_root,
            get_commands_dir,
            get_skills_dir,
            get_agents_dir,
            get_hooks_dir,
        )

        root = get_plugin_root()
        # All resource dirs should be subdirectories of the plugin root
        assert get_commands_dir().parent == root
        assert get_skills_dir().parent == root
        assert get_agents_dir().parent == root
        assert get_hooks_dir().parent == root
