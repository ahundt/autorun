#!/usr/bin/env python3
"""
Integration tests for all installer pathways.

Tests verify:
1. autorun --install (consolidated installer)
2. autorun-install (legacy compatibility)
3. hook_entry.py bootstrap pathway
4. All combinations of flags (--tool, --force-install, selective install)

Each test verifies:
- Correct entry point routing
- All functions callable
- Expected behavior matches documentation
"""

import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest


def get_plugin_root() -> Path:
    """Get the autorun plugin root directory."""
    return Path(__file__).parent.parent


def get_install_module():
    """Import install.py module."""
    plugin_root = get_plugin_root()
    sys.path.insert(0, str(plugin_root / "src"))
    from autorun import install
    return install


class TestInstallModule:
    """Test that install.py module is properly structured."""

    def test_install_module_exists(self):
        """Verify install.py exists at expected location."""
        plugin_root = get_plugin_root()
        install_file = plugin_root / "src" / "autorun" / "install.py"
        assert install_file.exists(), f"install.py not found at {install_file}"

    def test_install_module_imports(self):
        """Verify install.py can be imported."""
        install = get_install_module()
        assert install is not None

    def test_public_api_exports(self):
        """Verify all expected functions are in __all__."""
        install = get_install_module()

        expected = [
            "install_plugins",
            "uninstall_plugins",
            "show_status",
            "install_main",
            "PluginName",
            "CmdResult",
            "MARKETPLACE",
        ]

        for name in expected:
            assert name in install.__all__, f"{name} missing from __all__"
            assert hasattr(install, name), f"{name} not defined in install.py"


class TestPluginNameEnum:
    """Test PluginName enum validation."""

    def test_plugin_name_enum_values(self):
        """Verify PluginName enum has correct plugins."""
        install = get_install_module()

        expected = ["autorun", "pdf-extractor"]
        actual = install.PluginName.all()

        assert actual == expected, f"Expected {expected}, got {actual}"

    def test_plugin_name_validation(self):
        """Verify PluginName.validate() works correctly."""
        install = get_install_module()

        assert install.PluginName.validate("autorun") is True
        assert install.PluginName.validate("pdf-extractor") is True
        assert install.PluginName.validate("plan-export") is False
        assert install.PluginName.validate("invalid") is False

    def test_plan_export_not_in_enum(self):
        """Verify plan-export was removed from PluginName enum."""
        install = get_install_module()

        # Check enum doesn't have PLAN_EXPORT attribute
        assert not hasattr(install.PluginName, "PLAN_EXPORT"), (
            "PluginName.PLAN_EXPORT should not exist (merged into autorun)"
        )


class TestParseSelection:
    """Test _parse_selection() function."""

    def test_parse_all(self):
        """Verify 'all' returns all plugins."""
        install = get_install_module()
        result = install._parse_selection("all")
        assert result == ["autorun", "pdf-extractor"]

    def test_parse_empty(self):
        """Verify empty string treated as 'all'."""
        install = get_install_module()
        result = install._parse_selection("")
        assert result == ["autorun", "pdf-extractor"]

    def test_parse_single(self):
        """Verify single plugin selection."""
        install = get_install_module()
        result = install._parse_selection("autorun")
        assert result == ["autorun"]

    def test_parse_multiple(self):
        """Verify comma-separated selection."""
        install = get_install_module()
        result = install._parse_selection("autorun,pdf-extractor")
        assert result == ["autorun", "pdf-extractor"]

    def test_parse_with_spaces(self):
        """Verify spaces are stripped."""
        install = get_install_module()
        result = install._parse_selection("autorun , pdf-extractor")
        assert result == ["autorun", "pdf-extractor"]

    def test_parse_deduplicates(self):
        """Verify duplicate plugins are removed."""
        install = get_install_module()
        result = install._parse_selection("autorun,autorun,pdf-extractor")
        assert result == ["autorun", "pdf-extractor"]

    def test_parse_invalid_plugins_skipped(self):
        """Verify invalid plugin names are skipped."""
        install = get_install_module()
        result = install._parse_selection("autorun,invalid,pdf-extractor")
        assert result == ["autorun", "pdf-extractor"]


class TestMapLegacyFlags:
    """Test _map_legacy_flags() function for autorun-install compatibility."""

    def test_map_install_default(self):
        """Verify 'install' maps to '--install'."""
        install = get_install_module()
        result = install._map_legacy_flags(["install"])
        assert result == ["--install"]

    def test_map_install_with_force(self):
        """Verify 'install --force' maps to '--install --force-install'."""
        install = get_install_module()
        result = install._map_legacy_flags(["install", "--force"])
        assert result == ["--install", "--force-install"]

    def test_map_install_with_tool(self):
        """Verify 'install --tool' maps to '--install --tool'."""
        install = get_install_module()
        result = install._map_legacy_flags(["install", "--tool"])
        assert result == ["--install", "--tool"]

    def test_map_uninstall(self):
        """Verify 'uninstall' maps to '--uninstall'."""
        install = get_install_module()
        result = install._map_legacy_flags(["uninstall"])
        assert result == ["--uninstall"]

    def test_map_check(self):
        """Verify 'check' maps to '--status'."""
        install = get_install_module()
        result = install._map_legacy_flags(["check"])
        assert result == ["--status"]

    def test_map_status(self):
        """Verify 'status' maps to '--status'."""
        install = get_install_module()
        result = install._map_legacy_flags(["status"])
        assert result == ["--status"]

    def test_map_empty(self):
        """Verify empty args maps to '--install'."""
        install = get_install_module()
        result = install._map_legacy_flags([])
        assert result == ["--install"]

    def test_map_unknown(self):
        """Verify unknown subcommand defaults to '--install'."""
        install = get_install_module()
        result = install._map_legacy_flags(["unknown"])
        assert result == ["--install"]


class TestCmdResult:
    """Test CmdResult dataclass."""

    def test_cmd_result_immutable(self):
        """Verify CmdResult is frozen (immutable)."""
        install = get_install_module()
        result = install.CmdResult(True, "output")

        with pytest.raises(Exception):  # FrozenInstanceError
            result.ok = False

    def test_cmd_result_has_text_case_insensitive(self):
        """Verify has_text() is case-insensitive."""
        install = get_install_module()
        result = install.CmdResult(True, "This is OUTPUT text")

        assert result.has_text("output") is True
        assert result.has_text("OUTPUT") is True
        assert result.has_text("Output") is True
        assert result.has_text("missing") is False


class TestFindMarketplaceRoot:
    """Test find_marketplace_root() function."""

    def test_find_marketplace_root_from_test_file(self):
        """Verify find_marketplace_root() finds root from test location."""
        install = get_install_module()

        root = install.find_marketplace_root()
        marker = root / ".claude-plugin" / "marketplace.json"

        assert marker.exists(), f"marketplace.json not found at {marker}"
        assert root.name == "autorun", f"Expected root to be 'autorun', got {root.name}"

    def test_find_marketplace_root_cached(self):
        """Verify find_marketplace_root() result is cached."""
        install = get_install_module()

        root1 = install.find_marketplace_root()
        root2 = install.find_marketplace_root()

        # Same object (cached)
        assert root1 is root2


class TestInstallToCachePathResolution:
    """Test _install_to_cache() resolves plugin paths correctly.

    find_marketplace_root() returns the workspace root (e.g., autorun/)
    which contains plugins/ subdirectory with individual plugin directories.
    """

    def test_install_to_cache_finds_own_plugin(self):
        """Verify _install_to_cache resolves autorun plugin directory correctly."""
        install = get_install_module()
        root = install.find_marketplace_root()

        # root is the workspace root (named "autorun")
        assert root.name == "autorun"
        assert (root / ".claude-plugin").exists()

        # The workspace root contains plugins/ with individual plugin dirs
        plugin_dir = root / "plugins" / "autorun"
        assert plugin_dir.exists(), \
            f"Workspace should contain plugins/autorun: {plugin_dir}"
        assert (plugin_dir / "src" / "autorun").exists(), \
            f"Plugin dir should contain src/autorun: {plugin_dir}"

    def test_install_to_cache_finds_sibling_plugin(self):
        """Verify _install_to_cache resolves sibling plugins."""
        install = get_install_module()
        root = install.find_marketplace_root()

        # Sibling plugin (pdf-extractor) is at root / "plugins" / "pdf-extractor"
        sibling = root / "plugins" / "pdf-extractor"
        if sibling.exists():
            assert (sibling / ".claude-plugin").exists(), \
                f"Sibling plugin at {sibling} should have .claude-plugin/"


class TestReadPluginVersion:
    """Test _read_plugin_version() function."""

    def test_read_plugin_version_autorun(self):
        """Verify version read from autorun plugin.json."""
        install = get_install_module()
        plugin_root = get_plugin_root()

        version = install._read_plugin_version(plugin_root)
        assert version == "0.9.0"

    def test_read_plugin_version_fallback(self):
        """Verify fallback version when plugin.json missing."""
        install = get_install_module()
        nonexistent = Path("/tmp/nonexistent-plugin")

        version = install._read_plugin_version(nonexistent)
        assert version == "0.9.0"  # Default fallback


class TestCheckUvEnv:
    """Test _check_uv_env() validation."""

    def test_check_uv_env_detects_missing_uv(self):
        """Verify error when UV not in PATH."""
        install = get_install_module()

        with mock.patch("shutil.which", return_value=None):
            result = install._check_uv_env(Path("/tmp"))
            assert result.ok is False
            assert "uv not found" in result.output.lower()

    def test_check_uv_env_detects_missing_pyproject(self):
        """Verify error when pyproject.toml missing."""
        install = get_install_module()

        with mock.patch("shutil.which", return_value="/usr/bin/uv"):
            result = install._check_uv_env(Path("/tmp"))
            assert result.ok is False
            assert "pyproject.toml not found" in result.output


class TestInstallPathwayRouting:
    """Test that all entry points route correctly."""

    def test_main_install_flag_routes_to_install_plugins(self):
        """Verify __main__.py --install flag routes to install.install_plugins()."""
        # This is verified by imports in __main__.py
        plugin_root = get_plugin_root()
        main_file = plugin_root / "src" / "autorun" / "__main__.py"
        content = main_file.read_text()

        assert "from autorun.install import install_plugins" in content
        assert "install_plugins(" in content
        assert "args.install" in content

    def test_main_uninstall_flag_routes_to_uninstall_plugins(self):
        """Verify __main__.py --uninstall flag routes to install.uninstall_plugins()."""
        plugin_root = get_plugin_root()
        main_file = plugin_root / "src" / "autorun" / "__main__.py"
        content = main_file.read_text()

        assert "from autorun.install import uninstall_plugins" in content
        assert "return uninstall_plugins()" in content

    def test_main_status_flag_routes_to_show_status(self):
        """Verify __main__.py --status flag routes to install.show_status()."""
        plugin_root = get_plugin_root()
        main_file = plugin_root / "src" / "autorun" / "__main__.py"
        content = main_file.read_text()

        assert "from autorun.install import show_status" in content
        assert "return show_status()" in content

    def test_sync_removed(self):
        """Verify --sync flag has been removed (was broken, replaced by --install --force)."""
        plugin_root = get_plugin_root()
        main_file = plugin_root / "src" / "autorun" / "__main__.py"
        content = main_file.read_text()

        assert "sync_to_cache" not in content, \
            "--sync was removed because it never worked (path construction bug). Use --install --force."
        assert '"--sync"' not in content, \
            "--sync argument should be removed from argparse"

    def test_pyproject_entry_point_correct(self):
        """Verify pyproject.toml autorun-install entry point."""
        plugin_root = get_plugin_root()
        pyproject = plugin_root / "pyproject.toml"
        content = pyproject.read_text()

        assert 'autorun-install = "autorun.install:install_main"' in content


class TestInstallMainAdapter:
    """Test install_main() adapter function."""

    def test_install_main_exists(self):
        """Verify install_main() is defined."""
        install = get_install_module()
        assert hasattr(install, "install_main")
        assert callable(install.install_main)

    def test_install_main_in_all(self):
        """Verify install_main is exported in __all__."""
        install = get_install_module()
        assert "install_main" in install.__all__


class TestClaudeCommandIntegration:
    """Test that claude CLI commands are properly integrated."""

    def test_install_uses_claude_plugin_marketplace_add(self):
        """Verify install_plugins() calls 'claude plugin marketplace add'."""
        plugin_root = get_plugin_root()
        install_file = plugin_root / "src" / "autorun" / "install.py"
        content = install_file.read_text()

        assert 'claude", "plugin", "marketplace", "add"' in content

    def test_install_uses_claude_plugin_install(self):
        """Verify install_plugins() calls 'claude plugin install'."""
        plugin_root = get_plugin_root()
        install_file = plugin_root / "src" / "autorun" / "install.py"
        content = install_file.read_text()

        assert 'claude", "plugin", "install"' in content

    def test_install_uses_claude_plugin_enable(self):
        """Verify install_plugins() calls 'claude plugin enable'."""
        plugin_root = get_plugin_root()
        install_file = plugin_root / "src" / "autorun" / "install.py"
        content = install_file.read_text()

        assert 'claude", "plugin", "enable"' in content

    def test_install_uses_claude_plugin_update(self):
        """Verify install_plugins() tries 'claude plugin update' first."""
        plugin_root = get_plugin_root()
        install_file = plugin_root / "src" / "autorun" / "install.py"
        content = install_file.read_text()

        assert 'claude", "plugin", "update"' in content
        # Should try update before install
        update_pos = content.find('claude", "plugin", "update"')
        install_pos = content.find('claude", "plugin", "install"')
        assert update_pos < install_pos, "update should be tried before install"

    def test_uninstall_uses_claude_plugin_uninstall(self):
        """Verify uninstall_plugins() calls 'claude plugin uninstall'."""
        plugin_root = get_plugin_root()
        install_file = plugin_root / "src" / "autorun" / "install.py"
        content = install_file.read_text()

        assert 'claude", "plugin", "uninstall"' in content

    def test_status_uses_claude_plugin_list(self):
        """Verify show_status() calls 'claude plugin list'."""
        plugin_root = get_plugin_root()
        install_file = plugin_root / "src" / "autorun" / "install.py"
        content = install_file.read_text()

        assert 'claude", "plugin", "list"' in content


class TestBootstrapIntegration:
    """Test bootstrap pathway integration."""

    def test_hook_entry_calls_autorun_install(self):
        """Verify hook_entry.py bootstrap calls 'autorun --install'."""
        plugin_root = get_plugin_root()
        hook_entry = plugin_root / "hooks" / "hook_entry.py"
        content = hook_entry.read_text()

        assert "autorun --install" in content

    def test_daemon_calls_install_pdf_deps(self):
        """Verify daemon.py bootstrap includes _install_pdf_deps()."""
        plugin_root = get_plugin_root()
        daemon_file = plugin_root / "src" / "autorun" / "daemon.py"
        content = daemon_file.read_text()

        assert "_install_pdf_deps()" in content

    def test_daemon_uses_python_sys_executable(self):
        """Verify daemon bootstrap uses --python sys.executable."""
        plugin_root = get_plugin_root()
        daemon_file = plugin_root / "src" / "autorun" / "daemon.py"
        content = daemon_file.read_text()

        assert "--python" in content and "sys.executable" in content


class TestDependencyInstallation:
    """Test that dependency installation is integrated."""

    def test_install_syncs_autorun_deps(self):
        """Verify install_plugins() calls _sync_dependencies()."""
        plugin_root = get_plugin_root()
        install_file = plugin_root / "src" / "autorun" / "install.py"
        content = install_file.read_text()

        assert "_sync_dependencies()" in content
        # Verify the sync command includes required extras (as list elements)
        assert '"uv"' in content and '"sync"' in content
        assert '"claude-code"' in content and '"bashlex"' in content

    def test_install_syncs_pdf_deps_when_selected(self):
        """Verify install_plugins() calls _install_pdf_deps() when pdf-extractor selected."""
        plugin_root = get_plugin_root()
        install_file = plugin_root / "src" / "autorun" / "install.py"
        content = install_file.read_text()

        assert "_install_pdf_deps()" in content
        assert '"pdf-extractor" in plugins' in content

    def test_pdf_deps_use_correct_packages(self):
        """Verify _install_pdf_deps() installs correct packages."""
        plugin_root = get_plugin_root()
        install_file = plugin_root / "src" / "autorun" / "install.py"
        content = install_file.read_text()

        # Should install all 5 core pdf deps
        required_deps = ["pdfplumber", "pdfminer.six", "PyPDF2", "markitdown", "tqdm"]
        for dep in required_deps:
            assert dep in content, f"{dep} should be in _install_pdf_deps()"


class TestCacheFallback:
    """Test cache fallback functionality."""

    def test_cache_fallback_exists(self):
        """Verify _install_to_cache() function exists."""
        install = get_install_module()
        assert hasattr(install, "_install_to_cache")

    def test_cache_fallback_integrated_in_install_loop(self):
        """Verify cache fallback is called when marketplace install fails."""
        plugin_root = get_plugin_root()
        install_file = plugin_root / "src" / "autorun" / "install.py"
        content = install_file.read_text()

        # Should call _install_to_cache when marketplace fails
        assert "if _install_to_cache(name):" in content
        assert "cache fallback" in content.lower()

    def test_register_in_json_exists(self):
        """Verify _register_in_json() function exists."""
        install = get_install_module()
        assert hasattr(install, "_register_in_json")

    def test_substitute_paths_exists(self):
        """Verify _substitute_paths() function exists."""
        install = get_install_module()
        assert hasattr(install, "_substitute_paths")


class TestEntryPointCompatibility:
    """Test backward compatibility for all entry points."""

    def test_legacy_subcommands_documented(self):
        """Verify legacy subcommands are documented in __main__.py."""
        plugin_root = get_plugin_root()
        main_file = plugin_root / "src" / "autorun" / "__main__.py"
        content = main_file.read_text()

        # Legacy commands should still be mentioned in help
        assert "autorun install" in content.lower() or "legacy" in content.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
