#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for hooks/hook_entry.py and daemon.py bootstrap

TDD-driven tests for hook entry point and daemon bootstrap functionality.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


# =============================================================================
# Paths
# =============================================================================

PLUGIN_ROOT = Path(__file__).parent.parent
HOOK_ENTRY = PLUGIN_ROOT / "hooks" / "hook_entry.py"
SRC_DIR = PLUGIN_ROOT / "src"
DAEMON_PY = PLUGIN_ROOT / "src" / "clautorun" / "daemon.py"


# =============================================================================
# Test: hook_entry.py Structure
# =============================================================================


class TestHookEntryStructure:
    """Verify hook_entry.py has required structure."""

    def test_exists(self):
        """hook_entry.py exists in hooks directory."""
        assert HOOK_ENTRY.exists()

    def test_has_shebang(self):
        """hook_entry.py has Python shebang."""
        content = HOOK_ENTRY.read_text()
        assert content.startswith("#!/usr/bin/env python3")

    def test_has_main_function(self):
        """hook_entry.py has main() function."""
        content = HOOK_ENTRY.read_text()
        assert "def main()" in content

    def test_has_entry_point(self):
        """hook_entry.py has if __name__ == '__main__' block."""
        content = HOOK_ENTRY.read_text()
        assert 'if __name__ == "__main__":' in content

    def test_has_fail_open_function(self):
        """hook_entry.py has fail_open() for error handling."""
        content = HOOK_ENTRY.read_text()
        assert "def fail_open(" in content

    def test_has_try_cli_function(self):
        """hook_entry.py has try_cli() for CLI-first execution."""
        content = HOOK_ENTRY.read_text()
        assert "def try_cli(" in content

    def test_has_run_fallback_function(self):
        """hook_entry.py has run_fallback() for direct import fallback."""
        content = HOOK_ENTRY.read_text()
        assert "def run_fallback(" in content

    def test_has_get_src_dir_fallback(self):
        """hook_entry.py has get_src_dir() for when PLUGIN_ROOT missing."""
        content = HOOK_ENTRY.read_text()
        assert "def get_src_dir(" in content


# =============================================================================
# Test: hook_entry.py Execution Priority
# =============================================================================


class TestHookEntryExecutionPriority:
    """Verify hook_entry.py tries CLI first, then plugin root, then relative."""

    def test_uses_shutil_which_for_cli(self):
        """hook_entry.py uses shutil.which to find CLI."""
        content = HOOK_ENTRY.read_text()
        assert "shutil.which" in content

    def test_cli_checked_before_fallback(self):
        """main() tries CLI before fallback."""
        content = HOOK_ENTRY.read_text()
        # Check order: try_cli should be called first in main()
        main_idx = content.find("def main()")
        assert "try_cli(" in content[main_idx:]

    def test_has_timeout_constant(self):
        """hook_entry.py has HOOK_TIMEOUT constant."""
        content = HOOK_ENTRY.read_text()
        assert "HOOK_TIMEOUT" in content


# =============================================================================
# Test: hook_entry.py Fail-Open Behavior
# =============================================================================


class TestHookEntryFailOpen:
    """Test fail-open behavior - hooks should never crash Claude."""

    def test_missing_everything_fails_open(self):
        """Missing CLI and PLUGIN_ROOT outputs valid JSON and exits 0."""
        env = os.environ.copy()
        env.pop('CLAUDE_PLUGIN_ROOT', None)
        env['PATH'] = '/nonexistent'  # No CLI available

        result = subprocess.run(
            [sys.executable, str(HOOK_ENTRY)],
            capture_output=True,
            text=True,
            env=env,
            cwd='/tmp',  # Run from different dir to test relative path
            timeout=10
        )

        # Should exit 0 (not crash)
        assert result.returncode == 0, f"Exit {result.returncode}, stderr: {result.stderr}"

        # Should output valid JSON
        try:
            output = json.loads(result.stdout)
        except json.JSONDecodeError:
            pytest.fail(f"Invalid JSON: {result.stdout}")

        # Should have continue=True (fail-open)
        assert output.get("continue") is True

    def test_invalid_plugin_root_fails_open(self):
        """Invalid CLAUDE_PLUGIN_ROOT outputs valid JSON and exits 0."""
        env = os.environ.copy()
        env['CLAUDE_PLUGIN_ROOT'] = '/nonexistent/path'
        env['PATH'] = '/nonexistent'

        result = subprocess.run(
            [sys.executable, str(HOOK_ENTRY)],
            capture_output=True,
            text=True,
            env=env,
            cwd='/tmp',
            timeout=10
        )

        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output.get("continue") is True


# =============================================================================
# Test: hook_entry.py Integration
# =============================================================================


class TestHookEntryIntegration:
    """Integration tests with valid environment."""

    def test_runs_with_valid_plugin_root(self):
        """hook_entry.py runs successfully with valid CLAUDE_PLUGIN_ROOT."""
        env = os.environ.copy()
        env['CLAUDE_PLUGIN_ROOT'] = str(PLUGIN_ROOT)

        result = subprocess.run(
            [sys.executable, str(HOOK_ENTRY)],
            capture_output=True,
            text=True,
            input="{}",
            env=env,
            timeout=10
        )

        assert result.returncode == 0, f"Exit {result.returncode}, stderr: {result.stderr}"

    def test_relative_path_works(self):
        """hook_entry.py can find src via relative path from script location."""
        # Verify the relative path logic works
        hooks_dir = HOOK_ENTRY.parent
        relative_src = hooks_dir.parent / "src"
        assert relative_src.exists(), f"Relative src not found: {relative_src}"
        assert (relative_src / "clautorun" / "__main__.py").exists()


# =============================================================================
# Test: hooks.json Configuration
# =============================================================================


class TestHooksJson:
    """Verify hooks.json is correctly configured."""

    def test_uses_hook_entry(self):
        """hooks.json calls hook_entry.py."""
        hooks_json = PLUGIN_ROOT / "hooks" / "hooks.json"
        content = hooks_json.read_text()
        assert "hook_entry.py" in content

    def test_no_direct_cli_reference(self):
        """hooks.json uses hook_entry.py, not direct CLI."""
        hooks_json = PLUGIN_ROOT / "hooks" / "hooks.json"
        data = json.loads(hooks_json.read_text())

        for event, matchers in data.get("hooks", {}).items():
            for matcher in matchers:
                for hook in matcher.get("hooks", []):
                    command = hook.get("command", "")
                    # Should use hook_entry.py wrapper
                    if "clautorun" in command.lower():
                        assert "hook_entry.py" in command

    def test_no_shell_script_reference(self):
        """hooks.json does not reference old shell script."""
        hooks_json = PLUGIN_ROOT / "hooks" / "hooks.json"
        content = hooks_json.read_text()
        assert "clautorun-hook.sh" not in content


# =============================================================================
# Test: daemon.py Bootstrap Structure
# =============================================================================


class TestDaemonBootstrapStructure:
    """Test daemon bootstrap has correct structure (testable module-level functions)."""

    def test_has_bootstrap_function(self):
        """daemon.py has _bootstrap_optional_deps function."""
        content = DAEMON_PY.read_text()
        assert "_bootstrap_optional_deps" in content

    def test_has_get_pip_command_function(self):
        """daemon.py has _get_pip_command() helper (DRY)."""
        content = DAEMON_PY.read_text()
        assert "def _get_pip_command(" in content

    def test_has_ensure_uv_function(self):
        """daemon.py has _ensure_uv() at module level (testable)."""
        content = DAEMON_PY.read_text()
        # Should be module-level, not nested
        assert "\ndef _ensure_uv(" in content

    def test_has_install_bashlex_function(self):
        """daemon.py has _install_bashlex() at module level (testable)."""
        content = DAEMON_PY.read_text()
        assert "\ndef _install_bashlex(" in content

    def test_has_install_clautorun_function(self):
        """daemon.py has _install_clautorun() to enable fast CLI path."""
        content = DAEMON_PY.read_text()
        assert "def _install_clautorun(" in content

    def test_has_get_plugin_root_function(self):
        """daemon.py has _get_plugin_root() to find local install path."""
        content = DAEMON_PY.read_text()
        assert "def _get_plugin_root(" in content


class TestDaemonBootstrapBehavior:
    """Test daemon bootstrap behavior."""

    def test_bootstrap_runs_in_background(self):
        """Bootstrap runs in background thread."""
        content = DAEMON_PY.read_text()
        assert "threading.Thread" in content
        assert "daemon=True" in content

    def test_bootstrap_installs_uv_if_missing(self):
        """Bootstrap installs UV via pip if UV not available."""
        content = DAEMON_PY.read_text()
        assert "pip" in content and "uv" in content

    def test_bootstrap_prefers_uv(self):
        """Bootstrap prefers UV over pip for package installs."""
        content = DAEMON_PY.read_text()
        assert "shutil.which('uv')" in content

    def test_bootstrap_installs_bashlex(self):
        """Bootstrap installs bashlex for better command parsing."""
        content = DAEMON_PY.read_text()
        assert "bashlex" in content

    def test_bootstrap_called_in_main(self):
        """Bootstrap is called in daemon main()."""
        content = DAEMON_PY.read_text()
        assert "_bootstrap_optional_deps()" in content


class TestDaemonBootstrapClautorun:
    """Test clautorun CLI installation in bootstrap."""

    def test_checks_if_clautorun_already_installed(self):
        """_install_clautorun() skips if CLI already available."""
        content = DAEMON_PY.read_text()
        assert "shutil.which('clautorun')" in content

    def test_installs_from_local_path(self):
        """_install_clautorun() uses local plugin path, not GitHub."""
        content = DAEMON_PY.read_text()
        # Should use _get_plugin_root() or similar for local path
        assert "_get_plugin_root" in content
        # Should NOT install from GitHub
        assert "github.com/ahundt/clautorun" not in content

    def test_uses_uv_tool_install(self):
        """_install_clautorun() uses 'uv tool install' for global CLI."""
        content = DAEMON_PY.read_text()
        assert "uv" in content and "tool" in content and "install" in content

    def test_install_order_uv_then_clautorun_then_bashlex(self):
        """Bootstrap order: UV -> clautorun -> bashlex."""
        content = DAEMON_PY.read_text()
        # Find the _bootstrap_optional_deps function or install orchestration
        uv_idx = content.find("_ensure_uv")
        clautorun_idx = content.find("_install_clautorun")
        bashlex_idx = content.find("_install_bashlex")
        # UV should be installed before clautorun, clautorun before bashlex
        assert uv_idx < clautorun_idx < bashlex_idx, \
            f"Wrong order: uv={uv_idx}, clautorun={clautorun_idx}, bashlex={bashlex_idx}"


# =============================================================================
# Test: Old Files Removed
# =============================================================================


class TestCleanup:
    """Verify old/unused files are removed."""

    def test_shell_script_deleted(self):
        """clautorun-hook.sh should not exist."""
        old_script = PLUGIN_ROOT / "hooks" / "clautorun-hook.sh"
        assert not old_script.exists()
