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

import json
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
        assert result == ["ar", "pdf-extractor"]

    def test_parse_empty(self):
        """Verify empty string treated as 'all'."""
        install = get_install_module()
        result = install._parse_selection("")
        assert result == ["ar", "pdf-extractor"]

    def test_parse_single(self):
        """Verify single plugin selection by canonical name 'ar'."""
        install = get_install_module()
        result = install._parse_selection("ar")
        assert result == ["ar"]

    def test_parse_multiple(self):
        """Verify comma-separated selection."""
        install = get_install_module()
        result = install._parse_selection("ar,pdf-extractor")
        assert result == ["ar", "pdf-extractor"]

    def test_parse_with_spaces(self):
        """Verify spaces are stripped."""
        install = get_install_module()
        result = install._parse_selection("ar , pdf-extractor")
        assert result == ["ar", "pdf-extractor"]

    def test_parse_deduplicates(self):
        """Verify duplicate plugins are removed."""
        install = get_install_module()
        result = install._parse_selection("ar,ar,pdf-extractor")
        assert result == ["ar", "pdf-extractor"]

    def test_parse_invalid_plugins_skipped(self):
        """Verify invalid plugin names are skipped."""
        install = get_install_module()
        result = install._parse_selection("ar,invalid,pdf-extractor")
        assert result == ["ar", "pdf-extractor"]


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

    def test_map_install_with_codex(self):
        """Verify 'install --codex' preserves the Codex-only flag."""
        install = get_install_module()
        result = install._map_legacy_flags(["install", "--codex"])
        assert result == ["--install", "--codex"]

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
        """Verify version read from autorun plugin.json returns a valid semver string."""
        install = get_install_module()
        plugin_root = get_plugin_root()

        version = install._read_plugin_version(plugin_root)
        # Version must be a non-empty string matching the pattern N.N.N[suffix]
        assert isinstance(version, str) and version, "Version must be a non-empty string"
        parts = version.split(".")
        assert len(parts) >= 2, f"Version must have at least major.minor: {version}"
        assert parts[0].isdigit(), f"Major version must be numeric: {version}"

    def test_read_plugin_version_fallback(self):
        """Verify fallback version when plugin.json missing returns a valid string."""
        install = get_install_module()
        nonexistent = Path("/tmp/nonexistent-plugin")

        version = install._read_plugin_version(nonexistent)
        # Fallback must be a non-empty semver-like string (tracks install.py default)
        assert isinstance(version, str) and version, "Fallback version must be non-empty"
        assert "." in version, f"Fallback version must be semver-like: {version}"


class TestCacheVersionSort:
    """Test semver-aware sorting of cache version directories."""

    def test_cache_version_sort_prefers_0_10_over_0_9(self, tmp_path):
        """Verify 0.10.1 sorts higher than 0.9.0 (not lexicographic)."""
        # Create fake cache dirs with version names
        for ver in ["0.8.0", "0.9.0", "0.10.0", "0.10.1"]:
            d = tmp_path / ver
            d.mkdir()
            (d / ".claude-plugin").mkdir()
            (d / ".claude-plugin" / "marketplace.json").write_text("{}")

        # Use the same sort key as install.py:_ver_key
        def _ver_key(p):
            try:
                return tuple(int(x) for x in p.name.split("."))
            except (ValueError, TypeError):
                return (0,)

        version_dirs = sorted(tmp_path.iterdir(), key=_ver_key, reverse=True)
        names = [d.name for d in version_dirs]
        assert names == ["0.10.1", "0.10.0", "0.9.0", "0.8.0"], f"Got {names}"

    def test_cache_version_sort_handles_non_version_dirs(self, tmp_path):
        """Verify non-version directories sort to the end."""
        for name in ["0.10.1", "latest", "0.9.0", "dev"]:
            (tmp_path / name).mkdir()

        def _ver_key(p):
            try:
                return tuple(int(x) for x in p.name.split("."))
            except (ValueError, TypeError):
                return (0,)

        version_dirs = sorted(tmp_path.iterdir(), key=_ver_key, reverse=True)
        names = [d.name for d in version_dirs]
        # 0.10.1 first, 0.9.0 second, non-version dirs last (both sort as (0,))
        assert names[0] == "0.10.1"
        assert names[1] == "0.9.0"


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
        content = main_file.read_text(encoding="utf-8")

        assert "from autorun.install import install_plugins" in content
        assert "install_plugins(" in content
        assert "args.install" in content

    def test_main_uninstall_flag_routes_to_uninstall_plugins(self):
        """Verify __main__.py --uninstall flag routes to install.uninstall_plugins()."""
        plugin_root = get_plugin_root()
        main_file = plugin_root / "src" / "autorun" / "__main__.py"
        content = main_file.read_text(encoding="utf-8")

        assert "from autorun.install import uninstall_plugins" in content
        assert "return uninstall_plugins()" in content

    def test_main_status_flag_routes_to_show_status(self):
        """Verify __main__.py --status flag routes to install.show_status()."""
        plugin_root = get_plugin_root()
        main_file = plugin_root / "src" / "autorun" / "__main__.py"
        content = main_file.read_text(encoding="utf-8")

        assert "from autorun.install import show_status" in content
        assert "return show_status(custom_harnesses=args.custom_harness)" in content

    def test_sync_removed(self):
        """Verify --sync flag has been removed (was broken, replaced by --install --force)."""
        plugin_root = get_plugin_root()
        main_file = plugin_root / "src" / "autorun" / "__main__.py"
        content = main_file.read_text(encoding="utf-8")

        assert "sync_to_cache" not in content, \
            "--sync was removed because it never worked (path construction bug). Use --install --force."
        assert '"--sync"' not in content, \
            "--sync argument should be removed from argparse"

    def test_pyproject_entry_point_correct(self):
        """Verify pyproject.toml autorun-install entry point."""
        plugin_root = get_plugin_root()
        pyproject = plugin_root / "pyproject.toml"
        content = pyproject.read_text(encoding="utf-8")

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

    def test_install_module_main_codex_force_routes_to_install_plugins(self):
        """Verify direct module install honors --codex and --force."""
        install = get_install_module()

        with mock.patch.object(install, "install_plugins", return_value=0) as mock_install:
            result = install._install_module_main(["--install", "--codex", "--force"])

        assert result == 0
        mock_install.assert_called_once_with(
            "all",
            tool=False,
            force=True,
            claude_only=False,
            gemini_only=False,
            codex_only=True,
            antigravity_only=False,
            qwen_only=False,
            conductor=True,
            codex_hook_source="user", codex_plugin_marketplace="personal",
        )

    def test_install_module_main_codex_hook_source_routes_to_install_plugins(self):
        """Verify direct module install honors --codex-hook-source."""
        install = get_install_module()

        with mock.patch.object(install, "install_plugins", return_value=0) as mock_install:
            result = install._install_module_main(
                ["--install", "--codex", "--codex-hook-source", "plugin"]
            )

        assert result == 0
        mock_install.assert_called_once_with(
            "all",
            tool=False,
            force=False,
            claude_only=False,
            gemini_only=False,
            codex_only=True,
            antigravity_only=False,
            qwen_only=False,
            conductor=True,
            codex_hook_source="plugin", codex_plugin_marketplace="personal",
        )

    def test_install_module_main_custom_harness_routes_to_install_plugins(self):
        """Direct module install forwards custom Gemini-family harness specs."""
        install = get_install_module()
        spec = "lab=gemini:agy-lab:/tmp/agy-lab"

        with mock.patch.object(install, "install_plugins", return_value=0) as mock_install:
            result = install._install_module_main(["--install", "--custom-harness", spec])

        assert result == 0
        mock_install.assert_called_once_with(
            "all",
            tool=False,
            force=False,
            claude_only=False,
            gemini_only=False,
            codex_only=False,
            antigravity_only=False,
            qwen_only=False,
            conductor=True,
            codex_hook_source="user", codex_plugin_marketplace="personal",
            custom_harnesses=[spec],
        )

    def test_install_module_main_install_dry_run_routes_to_install_plugins(self):
        """Direct module install forwards install dry-run mode explicitly."""
        install = get_install_module()

        with mock.patch.object(install, "install_plugins", return_value=0) as mock_install:
            result = install._install_module_main(["--install", "--install-dry-run"])

        assert result == 0
        mock_install.assert_called_once_with(
            "all",
            tool=False,
            force=False,
            claude_only=False,
            gemini_only=False,
            codex_only=False,
            antigravity_only=False,
            qwen_only=False,
            conductor=True,
            codex_hook_source="user", codex_plugin_marketplace="personal",
            dry_run=True,
        )

    def test_install_module_custom_harness_help_lists_values_and_usage(self):
        """Direct module help documents custom harness values and status usage."""
        install = get_install_module()

        help_text = install._create_install_module_parser().format_help()

        assert "--custom-harness SPEC" in help_text
        assert "--install --custom-harness" in help_text
        assert "--status --custom-harness" in help_text
        assert "flavor: gemini|qwen|antigravity|agy|codex" in help_text
        assert "agy is an alias for antigravity" in help_text
        assert "--custom-harness-status" not in help_text

    def test_install_module_main_status_with_custom_harness_routes_to_show_status(self):
        """Direct module status reuses --status and forwards custom harness specs."""
        install = get_install_module()
        spec = "lab=agy:agy-lab:/tmp/agy-home"

        with mock.patch.object(install, "show_status", return_value=0) as mock_status:
            result = install._install_module_main(["--status", "--custom-harness", spec])

        assert result == 0
        mock_status.assert_called_once_with(custom_harnesses=[spec])

    def test_install_module_main_codex_plugin_marketplace_routes_to_install_plugins(self):
        """Verify direct module install honors --codex-plugin-marketplace."""
        install = get_install_module()

        with mock.patch.object(install, "install_plugins", return_value=0) as mock_install:
            result = install._install_module_main(
                ["--install", "--codex", "--codex-plugin-marketplace", "github"]
            )

        assert result == 0
        mock_install.assert_called_once_with(
            "all",
            tool=False,
            force=False,
            claude_only=False,
            gemini_only=False,
            codex_only=True,
            antigravity_only=False,
            qwen_only=False,
            conductor=True,
            codex_hook_source="user",
            codex_plugin_marketplace="github",
        )

    def test_install_module_main_antigravity_force_routes_to_install_plugins(self):
        """Verify direct module install honors --antigravity and --force."""
        install = get_install_module()

        with mock.patch.object(install, "install_plugins", return_value=0) as mock_install:
            result = install._install_module_main(["--install", "--antigravity", "--force"])

        assert result == 0
        mock_install.assert_called_once_with(
            "all",
            tool=False,
            force=True,
            claude_only=False,
            gemini_only=False,
            codex_only=False,
            antigravity_only=True,
            qwen_only=False,
            conductor=True,
            codex_hook_source="user", codex_plugin_marketplace="personal",
        )

    def test_install_module_main_qwen_force_routes_to_install_plugins(self):
        """Verify direct module install honors --qwen and --force."""
        install = get_install_module()

        with mock.patch.object(install, "install_plugins", return_value=0) as mock_install:
            result = install._install_module_main(["--install", "--qwen", "--force"])

        assert result == 0
        mock_install.assert_called_once_with(
            "all",
            tool=False,
            force=True,
            claude_only=False,
            gemini_only=False,
            codex_only=False,
            antigravity_only=False,
            qwen_only=True,
            conductor=True,
            codex_hook_source="user", codex_plugin_marketplace="personal",
        )

    def test_install_main_legacy_codex_force_routes_to_install_plugins(self):
        """Verify autorun-install install --force --codex honors both flags."""
        install = get_install_module()

        with (
            mock.patch.object(
                sys,
                "argv",
                ["autorun-install", "install", "--force", "--codex"],
            ),
            mock.patch.object(install, "install_plugins", return_value=0) as mock_install,
            pytest.raises(SystemExit) as exc,
        ):
            install.install_main()

        assert exc.value.code == 0
        mock_install.assert_called_once_with(
            "all",
            tool=False,
            force=True,
            claude_only=False,
            gemini_only=False,
            codex_only=True,
            antigravity_only=False,
            qwen_only=False,
            conductor=True,
            codex_hook_source="user", codex_plugin_marketplace="personal",
        )


class TestClaudeCommandIntegration:
    """Test that claude CLI commands are properly integrated."""

    def test_install_uses_claude_plugin_marketplace_add(self):
        """Verify install_plugins() calls 'claude plugin marketplace add'."""
        plugin_root = get_plugin_root()
        install_file = plugin_root / "src" / "autorun" / "install.py"
        content = install_file.read_text(encoding="utf-8")

        assert 'claude", "plugin", "marketplace", "add"' in content

    def test_install_uses_claude_plugin_install(self):
        """Verify install_plugins() calls 'claude plugin install'."""
        plugin_root = get_plugin_root()
        install_file = plugin_root / "src" / "autorun" / "install.py"
        content = install_file.read_text(encoding="utf-8")

        assert 'claude", "plugin", "install"' in content

    def test_install_uses_claude_plugin_enable(self):
        """Verify install_plugins() calls 'claude plugin enable'."""
        plugin_root = get_plugin_root()
        install_file = plugin_root / "src" / "autorun" / "install.py"
        content = install_file.read_text(encoding="utf-8")

        assert 'claude", "plugin", "enable"' in content

    def test_install_uses_claude_plugin_update(self):
        """Verify install_plugins() tries 'claude plugin update' first."""
        plugin_root = get_plugin_root()
        install_file = plugin_root / "src" / "autorun" / "install.py"
        content = install_file.read_text(encoding="utf-8")

        assert 'claude", "plugin", "update"' in content
        # Should try update before install
        update_pos = content.find('claude", "plugin", "update"')
        install_pos = content.find('claude", "plugin", "install"')
        assert update_pos < install_pos, "update should be tried before install"

    def test_uninstall_uses_claude_plugin_uninstall(self):
        """Verify uninstall_plugins() calls 'claude plugin uninstall'."""
        plugin_root = get_plugin_root()
        install_file = plugin_root / "src" / "autorun" / "install.py"
        content = install_file.read_text(encoding="utf-8")

        assert 'claude", "plugin", "uninstall"' in content

    def test_status_uses_claude_plugin_list(self):
        """Verify show_status() calls 'claude plugin list'."""
        plugin_root = get_plugin_root()
        install_file = plugin_root / "src" / "autorun" / "install.py"
        content = install_file.read_text(encoding="utf-8")

        assert 'claude", "plugin", "list"' in content


class TestBootstrapIntegration:
    """Test bootstrap pathway integration."""

    def test_hook_entry_calls_autorun_install(self):
        """Verify hook_entry.py bootstrap calls 'autorun --install'."""
        plugin_root = get_plugin_root()
        hook_entry = plugin_root / "hooks" / "hook_entry.py"
        content = hook_entry.read_text(encoding="utf-8")

        assert "autorun --install" in content

    def test_daemon_calls_install_pdf_deps(self):
        """Verify daemon.py bootstrap includes _install_pdf_deps()."""
        plugin_root = get_plugin_root()
        daemon_file = plugin_root / "src" / "autorun" / "daemon.py"
        content = daemon_file.read_text(encoding="utf-8")

        assert "_install_pdf_deps()" in content

    def test_daemon_uses_python_sys_executable(self):
        """Verify daemon bootstrap uses --python sys.executable."""
        plugin_root = get_plugin_root()
        daemon_file = plugin_root / "src" / "autorun" / "daemon.py"
        content = daemon_file.read_text(encoding="utf-8")

        assert "--python" in content and "sys.executable" in content


class TestDependencyInstallation:
    """Test that dependency installation is integrated."""

    def test_install_syncs_autorun_deps(self):
        """Verify install_plugins() calls _sync_dependencies()."""
        plugin_root = get_plugin_root()
        install_file = plugin_root / "src" / "autorun" / "install.py"
        content = install_file.read_text(encoding="utf-8")

        assert "_sync_dependencies()" in content
        # Verify the sync command includes required extras (as list elements)
        assert '"uv"' in content and '"sync"' in content
        assert '"claude-code"' in content and '"bashlex"' in content

    def test_install_syncs_pdf_deps_when_selected(self):
        """Verify install_plugins() calls _install_pdf_deps() when pdf-extractor selected."""
        plugin_root = get_plugin_root()
        install_file = plugin_root / "src" / "autorun" / "install.py"
        content = install_file.read_text(encoding="utf-8")

        assert "_install_pdf_deps()" in content
        assert '"pdf-extractor" in plugins' in content

    def test_pdf_deps_use_correct_packages(self):
        """Verify _install_pdf_deps() installs correct packages."""
        plugin_root = get_plugin_root()
        install_file = plugin_root / "src" / "autorun" / "install.py"
        content = install_file.read_text(encoding="utf-8")

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
        content = install_file.read_text(encoding="utf-8")

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
        content = main_file.read_text(encoding="utf-8")

        # Legacy commands should still be mentioned in help
        assert "autorun install" in content.lower() or "legacy" in content.lower()


class TestGenerateGeminiTomlCommands:
    """Test _generate_gemini_toml_commands() for Gemini CLI command conversion.

    Gemini CLI reads commands as TOML files (commands/ar/status.toml -> /ar:status),
    while Claude Code reads .md files (commands/status.md -> /ar:status).

    References:
        - Extension commands: https://geminicli.com/docs/extensions/reference/
        - Writing extensions: https://geminicli.com/docs/extensions/writing-extensions/
    """

    def test_converts_md_to_toml(self, tmp_path):
        """Verify .md file with frontmatter is converted to valid TOML."""
        install = get_install_module()
        commands_dir = tmp_path / "commands"
        commands_dir.mkdir()
        (commands_dir / "status.md").write_text(
            '---\ndescription: Show current status\n---\n\n# Status\n\nShows status.'
        )

        count = install._generate_gemini_toml_commands(tmp_path, "ar")
        assert count == 1

        toml_path = commands_dir / "ar" / "status.toml"
        assert toml_path.exists()
        content = toml_path.read_text()
        assert 'description = "Show current status"' in content
        assert "# Status" in content
        assert "Shows status." in content

    def test_converts_arguments_placeholder(self, tmp_path):
        """Verify $ARGUMENTS is converted to {{args}} (Gemini convention)."""
        install = get_install_module()
        commands_dir = tmp_path / "commands"
        commands_dir.mkdir()
        (commands_dir / "run.md").write_text(
            '---\ndescription: Start run\n---\n\n$ARGUMENTS\n\nRun task.'
        )

        install._generate_gemini_toml_commands(tmp_path, "ar")
        content = (commands_dir / "ar" / "run.toml").read_text()
        assert "{{args}}" in content
        assert "$ARGUMENTS" not in content

    def test_creates_namespaced_directory(self, tmp_path):
        """Verify TOML files are in commands/<ext_name>/ directory."""
        install = get_install_module()
        commands_dir = tmp_path / "commands"
        commands_dir.mkdir()
        (commands_dir / "test.md").write_text(
            '---\ndescription: Test cmd\n---\n\nContent.'
        )

        install._generate_gemini_toml_commands(tmp_path, "myext")
        assert (commands_dir / "myext" / "test.toml").exists()

    def test_handles_missing_frontmatter(self, tmp_path):
        """Verify .md files without frontmatter get empty description."""
        install = get_install_module()
        commands_dir = tmp_path / "commands"
        commands_dir.mkdir()
        (commands_dir / "bare.md").write_text("# Just Content\n\nNo frontmatter.")

        count = install._generate_gemini_toml_commands(tmp_path, "ar")
        assert count == 1
        content = (commands_dir / "ar" / "bare.toml").read_text()
        assert 'description = ""' in content
        assert "# Just Content" in content

    def test_returns_correct_count(self, tmp_path):
        """Verify returned count matches number of .md files."""
        install = get_install_module()
        commands_dir = tmp_path / "commands"
        commands_dir.mkdir()
        for i in range(5):
            (commands_dir / f"cmd{i}.md").write_text(
                f'---\ndescription: Cmd {i}\n---\n\nContent {i}.'
            )

        count = install._generate_gemini_toml_commands(tmp_path, "ar")
        assert count == 5
        assert len(list((commands_dir / "ar").glob("*.toml"))) == 5

    def test_no_commands_dir_returns_zero(self, tmp_path):
        """Verify returns 0 when no commands directory exists."""
        install = get_install_module()
        count = install._generate_gemini_toml_commands(tmp_path, "ar")
        assert count == 0

    def test_ignores_non_md_files(self, tmp_path):
        """Verify only .md files are converted, not .toml or other files."""
        install = get_install_module()
        commands_dir = tmp_path / "commands"
        commands_dir.mkdir()
        (commands_dir / "cmd.md").write_text('---\ndescription: Cmd\n---\n\nContent.')
        (commands_dir / "existing.toml").write_text('description = "existing"')
        (commands_dir / "script.py").write_text("print('hello')")

        count = install._generate_gemini_toml_commands(tmp_path, "ar")
        assert count == 1  # Only .md file converted


class TestAntigravityImportSync:
    """Antigravity imports Gemini plugins, then autorun must stamp AGY identity."""

    def test_gemini_family_hook_cli_rewrites_nested_and_root_hooks(self, tmp_path):
        """Antigravity imports can contain both hooks/hooks.json and root hooks.json."""
        install = get_install_module()
        ext_dir = tmp_path / "ar"
        nested_hooks = ext_dir / "hooks"
        nested_hooks.mkdir(parents=True)
        hook_data = {
            "hooks": {
                "BeforeTool": [
                    {
                        "hooks": [
                            {
                                "command": (
                                    "uv run --quiet --project ${extensionPath} "
                                    "python ${extensionPath}/hooks/hook_entry.py --cli gemini"
                                )
                            }
                        ]
                    },
                    {
                        "hooks": [
                            {
                                "command": (
                                    "uv run custom-wrapper --cli gemini "
                                    "python custom_hook.py"
                                )
                            }
                        ]
                    }
                ]
            }
        }
        (nested_hooks / "hooks.json").write_text(json.dumps(hook_data), encoding="utf-8")
        (ext_dir / "hooks.json").write_text(json.dumps(hook_data), encoding="utf-8")

        install._set_gemini_family_hook_cli(ext_dir, "antigravity")

        for hooks_file in (nested_hooks / "hooks.json", ext_dir / "hooks.json"):
            text = hooks_file.read_text(encoding="utf-8")
            assert "--cli antigravity" in text
            assert "hook_entry.py --cli gemini" not in text
            assert "uv run custom-wrapper --cli gemini python custom_hook.py" in text

    def test_gemini_family_hook_cli_preserves_unrelated_text_on_malformed_hooks(self, tmp_path):
        """Fallback text repair should touch only autorun hook_entry.py lines."""
        install = get_install_module()
        ext_dir = tmp_path / "ar"
        hooks_dir = ext_dir / "hooks"
        hooks_dir.mkdir(parents=True)
        hooks_file = hooks_dir / "hooks.json"
        hooks_file.write_text(
            "\n".join([
                "{",
                "command = 'python ${extensionPath}/hooks/hook_entry.py --cli gemini'",
                "custom = 'uv run custom-wrapper --cli gemini python custom_hook.py'",
                "",
            ]),
            encoding="utf-8",
        )

        install._set_gemini_family_hook_cli(ext_dir, "antigravity")

        text = hooks_file.read_text(encoding="utf-8")
        assert "hook_entry.py --cli antigravity" in text
        assert "hook_entry.py --cli gemini" not in text
        assert "uv run custom-wrapper --cli gemini python custom_hook.py" in text

    def test_antigravity_import_syncs_autorun_resources_with_antigravity_cli(self, tmp_path, monkeypatch):
        """After `agy plugin import gemini`, installer stamps imported ar hooks."""
        install = get_install_module()
        marketplace = tmp_path / "marketplace"
        plugin_dir = marketplace / "plugins" / "autorun"
        template = plugin_dir / "src" / "autorun" / "gemini_template"
        template.mkdir(parents=True)
        (template / "gemini-extension.json").write_text('{"name": "ar"}', encoding="utf-8")
        marketplace_meta = marketplace / ".claude-plugin"
        marketplace_meta.mkdir()
        (marketplace_meta / "marketplace.json").write_text(
            json.dumps({"plugins": [{"name": "ar", "source": "./plugins/autorun"}]}),
            encoding="utf-8",
        )

        home = tmp_path / "home"
        imported_ar = home / ".gemini" / "antigravity-cli" / "plugins" / "ar"
        imported_ar.mkdir(parents=True)
        calls = []

        monkeypatch.setattr(install.shutil, "which", lambda name: "/opt/homebrew/bin/agy" if name == "agy" else None)
        monkeypatch.setattr(install.Path, "home", lambda: home)

        def fake_run_cmd(args, timeout=30, **kwargs):
            calls.append((args, timeout))
            if args == ["agy", "plugin", "list"]:
                return install.CmdResult(True, '"name": "ar"')
            if args == ["agy", "plugin", "import", "gemini"]:
                return install.CmdResult(True, "imported")
            return install.CmdResult(False, f"unexpected {args!r}")

        synced = []

        def fake_sync(plugin, ext, ext_name, cli_name="gemini"):
            synced.append((plugin, ext, ext_name, cli_name))
            return (0, 0)

        monkeypatch.setattr(install, "run_cmd", fake_run_cmd)
        monkeypatch.setattr(install, "_sync_gemini_extension_resources", fake_sync)

        ok, message = install._install_for_antigravity(marketplace, ["autorun"], force=True)

        assert ok is True
        assert message == "success"
        assert (["agy", "plugin", "import", "gemini"], 120) in calls
        assert synced == [(plugin_dir.resolve(), imported_ar, "ar", "antigravity")]


class TestCustomHarnessInstall:
    """Custom Gemini-family targets reuse the shared installer safely."""

    def test_parse_custom_harness_spec_requires_known_gemini_family_flavor(self, tmp_path):
        """Custom harness specs separate the binary from the known hook identity."""
        install = get_install_module()
        config_dir = tmp_path / "custom-home"

        spec = install.parse_custom_harness_spec(
            f"lab=gemini:agy-lab:{config_dir}:Antigravity Lab"
        )

        assert spec.name == "lab"
        assert spec.flavor == "gemini"
        assert spec.binary == "agy-lab"
        assert spec.config_dir == config_dir
        assert spec.display_name == "Antigravity Lab"

    def test_parse_custom_harness_spec_rejects_arbitrary_hook_identity(self, tmp_path):
        """Custom harnesses must not create unvalidated hook_entry.py --cli values."""
        install = get_install_module()

        with pytest.raises(ValueError, match="supported flavors"):
            install.parse_custom_harness_spec(f"lab=unknown:agy-lab:{tmp_path}")

    def test_parse_custom_harness_spec_accepts_codex_flavor(self, tmp_path):
        """Codex-flavored custom harnesses install strict user hooks by config dir."""
        install = get_install_module()
        spec = install.parse_custom_harness_spec(f"codexlab=codex:codex-lab:{tmp_path}")

        assert spec.flavor == "codex"
        assert spec.binary == "codex-lab"
        assert spec.config_dir == tmp_path

    def test_parse_custom_harness_spec_accepts_agy_alias(self, tmp_path):
        """The agy CLI spelling maps to the validated antigravity hook identity."""
        install = get_install_module()

        spec = install.parse_custom_harness_spec(f"lab=agy:agy-lab:{tmp_path}")

        assert spec.flavor == "antigravity"
        assert spec.binary == "agy-lab"

    def test_parse_custom_harness_spec_accepts_antigravity_flavor(self, tmp_path):
        """Antigravity can be named by product flavor as well as binary alias."""
        install = get_install_module()

        spec = install.parse_custom_harness_spec(
            f"lab=antigravity:agy-lab:{tmp_path}:Antigravity Lab"
        )

        assert spec.flavor == "antigravity"
        assert spec.display_name == "Antigravity Lab"

    def test_custom_antigravity_binary_stamps_antigravity_hook_flavor(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Custom agy-like binary runs commands, but hooks see Antigravity."""
        install = get_install_module()
        marketplace = tmp_path / "marketplace"
        plugin_dir = marketplace / "plugins" / "autorun"
        template = plugin_dir / "src" / "autorun" / "gemini_template"
        template.mkdir(parents=True)
        (template / "gemini-extension.json").write_text('{"name": "ar"}', encoding="utf-8")
        marketplace_meta = marketplace / ".claude-plugin"
        marketplace_meta.mkdir(parents=True)
        (marketplace_meta / "marketplace.json").write_text(
            '{"plugins": [{"name": "autorun", "source": "./plugins/autorun"}]}',
            encoding="utf-8",
        )

        custom_home = tmp_path / "custom-gemini"
        installed_ar = custom_home / "extensions" / "ar"
        installed_ar.mkdir(parents=True)
        calls = []

        def fake_which(name):
            return f"/usr/local/bin/{name}" if name == "agy-lab" else None

        def fake_run_cmd(args, timeout=30):
            calls.append((args, timeout))
            return install.CmdResult(True, "")

        synced = []

        def fake_sync(plugin, ext, ext_name, cli_name="gemini"):
            synced.append((plugin.resolve(), ext, ext_name, cli_name))
            return (0, 0)

        monkeypatch.setattr(install.shutil, "which", fake_which)
        monkeypatch.setattr(install, "run_cmd", fake_run_cmd)
        monkeypatch.setattr(install, "_sync_gemini_extension_resources", fake_sync)

        ok, message = install._install_gemini_family_extensions(
            marketplace_root=marketplace,
            plugins=["autorun"],
            force=False,
            cli_name="agy-lab",
            display_name="Antigravity Lab",
            config_dir=custom_home,
            install_hint="install agy-lab",
            hook_cli_name="antigravity",
        )

        assert ok is True, message
        assert any(args[:3] == ["agy-lab", "extensions", "install"] for args, _ in calls)
        assert synced == [(plugin_dir.resolve(), installed_ar, "ar", "antigravity")]

    def test_custom_codex_config_dir_installs_only_user_surface(self, tmp_path, monkeypatch):
        """Custom Codex-like config dirs get hooks and AGENTS.md, not global assets."""
        install = get_install_module()
        marketplace = tmp_path / "marketplace"
        plugin_dir = marketplace / "plugins" / "autorun"
        hooks_dir = plugin_dir / "hooks"
        template = plugin_dir / "src" / "autorun" / "codex_template"
        hooks_dir.mkdir(parents=True)
        template.mkdir(parents=True)
        (hooks_dir / "hook_entry.py").write_text("# hook\n", encoding="utf-8")
        (template / "AGENTS.md").write_text("# autorun\nar:sos\n", encoding="utf-8")
        marketplace_meta = marketplace / ".claude-plugin"
        marketplace_meta.mkdir(parents=True)
        (marketplace_meta / "marketplace.json").write_text(
            '{"plugins": [{"name": "autorun", "source": "./plugins/autorun"}]}',
            encoding="utf-8",
        )
        custom_codex = tmp_path / "custom-codex"
        custom_codex.mkdir()
        existing = {
            "hooks": {
                "PreToolUse": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "python /user/pre_tool.py",
                            }
                        ]
                    }
                ]
            }
        }
        (custom_codex / "hooks.json").write_text(json.dumps(existing), encoding="utf-8")

        def global_asset_called(*_args, **_kwargs):
            raise AssertionError("custom Codex install must not touch global assets")

        monkeypatch.setattr(install, "_install_codex_skills", global_asset_called)
        monkeypatch.setattr(install, "_install_codex_plugin_marketplace", global_asset_called)

        ok, message = install._install_for_codex(
            marketplace,
            ["autorun"],
            force=False,
            codex_dir=custom_codex,
            install_global_assets=False,
        )

        assert ok is True, message
        hooks = json.loads((custom_codex / "hooks.json").read_text(encoding="utf-8"))
        commands = [
            hook["command"]
            for entries in hooks.get("hooks", {}).values()
            for entry in entries
            for hook in entry.get("hooks", [])
            if isinstance(hook, dict) and "command" in hook
        ]
        assert "python /user/pre_tool.py" in commands
        assert any("--cli codex" in command for command in commands)
        assert (custom_codex / "AGENTS.md").is_file()

    def test_custom_codex_config_dir_reinstall_is_idempotent(self, tmp_path, monkeypatch):
        """Repeated custom Codex installs must not duplicate hooks or AGENTS blocks."""
        install = get_install_module()
        marketplace = tmp_path / "marketplace"
        plugin_dir = marketplace / "plugins" / "autorun"
        hooks_dir = plugin_dir / "hooks"
        template = plugin_dir / "src" / "autorun" / "codex_template"
        hooks_dir.mkdir(parents=True)
        template.mkdir(parents=True)
        (hooks_dir / "hook_entry.py").write_text("# hook\n", encoding="utf-8")
        (template / "AGENTS.md").write_text("# autorun\nar:sos\n", encoding="utf-8")
        marketplace_meta = marketplace / ".claude-plugin"
        marketplace_meta.mkdir(parents=True)
        (marketplace_meta / "marketplace.json").write_text(
            '{"plugins": [{"name": "autorun", "source": "./plugins/autorun"}]}',
            encoding="utf-8",
        )
        custom_codex = tmp_path / "custom-codex"

        monkeypatch.setattr(install, "_install_codex_skills", lambda *_args, **_kwargs: (0, 0))
        monkeypatch.setattr(
            install,
            "_install_codex_plugin_marketplace",
            lambda *_args, **_kwargs: install.CodexPluginMarketplaceInstall(False, False),
        )

        for _ in range(2):
            ok, message = install._install_for_codex(
                marketplace,
                ["autorun"],
                force=False,
                codex_dir=custom_codex,
                install_global_assets=False,
            )
            assert ok is True, message

        hooks = json.loads((custom_codex / "hooks.json").read_text(encoding="utf-8"))
        for entries in hooks.get("hooks", {}).values():
            event_commands = [
                hook["command"]
                for entry in entries
                for hook in entry.get("hooks", [])
                if isinstance(hook, dict) and "command" in hook
            ]
            assert len(event_commands) == len(set(event_commands))
        agents = (custom_codex / "AGENTS.md").read_text(encoding="utf-8")
        assert agents.count("<!-- autorun:codex-agents-md:start -->") == 1
        assert agents.count("<!-- autorun:codex-agents-md:end -->") == 1

    def test_install_plugins_routes_custom_codex_without_global_assets(self, tmp_path, monkeypatch):
        """Top-level custom Codex install stays scoped to the supplied config dir."""
        install = get_install_module()
        custom_codex = tmp_path / "custom-codex"
        calls = []

        monkeypatch.setattr(install, "find_marketplace_root", lambda: tmp_path / "marketplace")
        monkeypatch.setattr(install, "_update_package_metadata", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(install, "detect_available_clis", lambda: {name: False for name in install.PLATFORMS})
        monkeypatch.setattr(install, "_check_uv_env", lambda *_args, **_kwargs: install.CmdResult(True, ""))
        monkeypatch.setattr(install.shutil, "which", lambda name: None)
        monkeypatch.setattr(install, "_check_hook_conflicts", lambda: None)
        monkeypatch.setattr(install, "_restart_daemon_if_running", lambda: None)

        def fake_install_for_codex(*args, **kwargs):
            calls.append((args, kwargs))
            return (True, "success")

        monkeypatch.setattr(install, "_install_for_codex", fake_install_for_codex)

        rc = install.install_plugins(
            "autorun",
            custom_harnesses=[f"lab=codex:codex-lab:{custom_codex}:Codex Lab"],
            conductor=False,
        )

        assert rc == 0
        assert len(calls) == 1
        _args, kwargs = calls[0]
        assert kwargs["codex_dir"] == custom_codex
        assert kwargs["install_global_assets"] is False
        assert kwargs["codex_hook_source"] == "user"

    def test_install_plugins_dry_run_does_not_write_or_install(self, tmp_path, monkeypatch, capsys):
        """Install dry-run previews custom targets without touching hook state."""
        install = get_install_module()

        marketplace = tmp_path / "marketplace"
        plugin_dir = marketplace / "plugins" / "autorun"
        (plugin_dir / ".claude-plugin").mkdir(parents=True)
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text("{}", encoding="utf-8")

        def forbidden(*_args, **_kwargs):
            raise AssertionError("dry-run must not mutate install state")

        monkeypatch.setattr(install, "find_marketplace_root", lambda: marketplace)
        monkeypatch.setattr(install, "_update_package_metadata", forbidden)
        monkeypatch.setattr(install, "_sync_dependencies", forbidden)
        monkeypatch.setattr(install, "_install_for_codex", forbidden)
        monkeypatch.setattr(install, "_install_gemini_family_extensions", forbidden)
        monkeypatch.setattr(install, "_check_hook_conflicts", forbidden)
        monkeypatch.setattr(install, "_restart_daemon_if_running", forbidden)
        monkeypatch.setattr(install, "detect_available_clis", lambda: {name: False for name in install.PLATFORMS})

        rc = install.install_plugins(
            "autorun",
            custom_harnesses=[f"lab=agy:agy-lab:{tmp_path / 'agy-home'}"],
            conductor=False,
            dry_run=True,
        )

        out = capsys.readouterr().out
        assert rc == 0
        assert "DRY RUN" in out
        assert "lab" in out
        assert "antigravity" in out
        assert "No files, hooks, plugin state, dependencies, or daemons were changed." in out

    def test_custom_harness_status_reports_codex_config_dir(self, tmp_path, capsys):
        """Custom Codex status inspects only the supplied config directory."""
        install = get_install_module()
        codex_dir = tmp_path / "custom-codex"
        codex_dir.mkdir()
        (codex_dir / "hooks.json").write_text(
            json.dumps({
                "hooks": {
                    "PreToolUse": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "uv run python /tmp/hook_entry.py --cli codex",
                                }
                            ]
                        }
                    ]
                }
            }),
            encoding="utf-8",
        )
        (codex_dir / "AGENTS.md").write_text("autorun guidance", encoding="utf-8")

        rc = install.show_custom_harness_status(f"lab=codex:codex-lab:{codex_dir}:Codex Lab")

        out = capsys.readouterr().out
        assert rc == 0
        assert "Codex Lab" in out
        assert "flavor: codex" in out
        assert "hooks.json: ✓ installed" in out
        assert "AGENTS.md: ✓ installed" in out

    def test_custom_harness_status_reports_gemini_family_config_dir(self, tmp_path, capsys):
        """Custom Antigravity/Gemini-family status checks extension hook identity."""
        install = get_install_module()
        config_dir = tmp_path / "agy-home"
        hooks_dir = config_dir / "extensions" / "ar" / "hooks"
        hooks_dir.mkdir(parents=True)
        (hooks_dir / "hooks.json").write_text(
            json.dumps({
                "hooks": {
                    "BeforeTool": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": (
                                        "uv run python ${extensionPath}/hooks/"
                                        "hook_entry.py --cli antigravity"
                                    ),
                                }
                            ]
                        }
                    ]
                }
            }),
            encoding="utf-8",
        )

        rc = install.show_custom_harness_status(f"lab=agy:agy-lab:{config_dir}:Antigravity Lab")

        out = capsys.readouterr().out
        assert rc == 0
        assert "Antigravity Lab" in out
        assert "flavor: antigravity" in out
        assert "ar extension: ✓ installed" in out
        assert "hooks identity: ✓ antigravity" in out


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
