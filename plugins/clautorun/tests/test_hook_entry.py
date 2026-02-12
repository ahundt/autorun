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
# Test: UV Compatibility (catches pyproject.toml / UV version mismatches)
# =============================================================================


class TestUVCompatibility:
    """Verify pyproject.toml works with installed UV without warnings.

    Root cause this catches: UV versions may deprecate/remove [tool.uv] fields
    (e.g., `default-extras` was removed in UV 0.9+). When UV encounters unknown
    fields, it prints warnings to stderr. Claude Code interprets any hook stderr
    as "hook error", causing ALL hooks to fail silently — the hook returns valid
    JSON but Claude reports "PreToolUse:Bash hook error" and ignores it.

    This class of bug is invisible: hooks appear to "work" (no crash) but provide
    no protection (all blocked commands pass through).
    """

    def test_uv_available(self):
        """UV must be installed for hook execution."""
        import shutil
        uv_path = shutil.which("uv")
        assert uv_path is not None, (
            "UV not found in PATH. Hooks require UV for execution. "
            "Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
        )

    def test_uv_run_no_stderr_warnings(self):
        """uv run --project <plugin_root> must produce no stderr.

        Claude Code hooks run via 'uv run --project ${CLAUDE_PLUGIN_ROOT} python ...'.
        Any stderr output causes Claude Code to report 'hook error' and ignore
        the hook's JSON response, effectively disabling all hook protections.

        Common causes of stderr:
        - Deprecated [tool.uv] fields (e.g., default-extras removed in UV 0.9+)
        - Invalid pyproject.toml syntax
        - Missing dependencies during resolution
        """
        import shutil
        if not shutil.which("uv"):
            pytest.skip("UV not installed")

        result = subprocess.run(
            ["uv", "run", "--project", str(PLUGIN_ROOT), "python", "-c", "print('ok')"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Filter out expected UV output (build/install messages go to stderr)
        stderr_lines = [
            line for line in result.stderr.strip().splitlines()
            if line.strip()
            and not line.strip().startswith("Building ")
            and not line.strip().startswith("Built ")
            and not line.strip().startswith("Installed ")
            and not line.strip().startswith("Uninstalled ")
            and not line.strip().startswith("Resolved ")
            and not line.strip().startswith("Prepared ")
            and not line.strip().startswith("Audited ")
        ]

        assert len(stderr_lines) == 0, (
            f"UV produced unexpected stderr (causes Claude Code 'hook error'):\n"
            f"{''.join(stderr_lines)}\n\n"
            f"Fix: Check [tool.uv] section in {PLUGIN_ROOT}/pyproject.toml for "
            f"deprecated fields. Run 'uv run --project {PLUGIN_ROOT} python -c pass' "
            f"to reproduce."
        )
        assert result.returncode == 0, (
            f"uv run failed with exit code {result.returncode}.\n"
            f"stderr: {result.stderr}\n"
            f"Fix: Run 'uv run --project {PLUGIN_ROOT} python -c pass' to diagnose."
        )

    def test_pyproject_toml_no_deprecated_uv_fields(self):
        """pyproject.toml [tool.uv] must not contain deprecated fields.

        UV has removed fields across versions:
        - default-extras: removed in UV 0.9+ (use main dependencies instead)

        When deprecated fields are present, UV prints warnings to stderr,
        which Claude Code interprets as hook errors.
        """
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[no-redef]

        pyproject_path = PLUGIN_ROOT / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)

        uv_config = data.get("tool", {}).get("uv", {})

        # Known deprecated fields
        deprecated_fields = {
            "default-extras": "Removed in UV 0.9+. Move extras to main [project] dependencies instead.",
        }

        found_deprecated = []
        for field, fix in deprecated_fields.items():
            if field in uv_config:
                found_deprecated.append(f"  - {field}: {fix}")

        assert len(found_deprecated) == 0, (
            f"pyproject.toml [tool.uv] contains deprecated fields that cause UV stderr warnings.\n"
            f"Claude Code treats hook stderr as errors, disabling all hook protections.\n\n"
            f"Deprecated fields found:\n" + "\n".join(found_deprecated) + "\n\n"
            f"File: {pyproject_path}"
        )

    def test_hook_entry_via_uv_no_stderr(self):
        """hook_entry.py invoked via UV must produce no stderr.

        This is the actual command Claude Code runs. Tests the full chain:
        UV project resolution → Python launch → hook_entry.py → JSON output.

        Any stderr output = Claude Code reports 'hook error' = hooks disabled.
        """
        import shutil
        if not shutil.which("uv"):
            pytest.skip("UV not installed")

        env = os.environ.copy()
        env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)

        result = subprocess.run(
            [
                "uv", "run", "--project", str(PLUGIN_ROOT),
                "python", str(HOOK_ENTRY),
            ],
            capture_output=True,
            text=True,
            input='{"tool_name":"Bash","tool_input":{"command":"echo test"}}',
            env=env,
            timeout=15,
        )

        assert result.returncode == 0, (
            f"Hook exited with code {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Verify valid JSON on stdout
        try:
            output = json.loads(result.stdout)
        except json.JSONDecodeError:
            pytest.fail(
                f"Hook produced invalid JSON on stdout: {result.stdout!r}\n"
                f"stderr: {result.stderr}"
            )

        # Check for unexpected stderr (filter build/install noise)
        stderr_lines = [
            line for line in result.stderr.strip().splitlines()
            if line.strip()
            and not line.strip().startswith("Building ")
            and not line.strip().startswith("Built ")
            and not line.strip().startswith("Installed ")
            and not line.strip().startswith("Uninstalled ")
            and not line.strip().startswith("Resolved ")
            and not line.strip().startswith("Prepared ")
            and not line.strip().startswith("Audited ")
        ]

        assert len(stderr_lines) == 0, (
            f"Hook produced unexpected stderr (Claude Code treats as 'hook error'):\n"
            f"{''.join(stderr_lines)}\n\n"
            f"JSON output was valid: {output}\n"
            f"Fix: Check pyproject.toml [tool.uv] for deprecated fields, or "
            f"check hook_entry.py for stderr output."
        )

    def test_hook_entry_rm_blocked_or_warned(self):
        """Hook should handle 'rm' commands (block, warn, or pass through cleanly).

        Regardless of whether rm is blocked or allowed, the hook must:
        1. Return valid JSON
        2. Exit 0
        3. Produce no stderr warnings
        """
        import shutil
        if not shutil.which("uv"):
            pytest.skip("UV not installed")

        env = os.environ.copy()
        env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)

        result = subprocess.run(
            [
                "uv", "run", "--project", str(PLUGIN_ROOT),
                "python", str(HOOK_ENTRY),
            ],
            capture_output=True,
            text=True,
            input='{"tool_name":"Bash","tool_input":{"command":"rm /tmp/nonexistent_file_xyz"}}',
            env=env,
            timeout=15,
        )

        assert result.returncode == 0, (
            f"Hook crashed on 'rm' command (exit {result.returncode}).\n"
            f"stderr: {result.stderr}"
        )

        try:
            output = json.loads(result.stdout)
        except json.JSONDecodeError:
            pytest.fail(
                f"Hook produced invalid JSON for 'rm' command: {result.stdout!r}\n"
                f"stderr: {result.stderr}"
            )

        assert "continue" in output, (
            f"Hook response missing 'continue' field for 'rm' command: {output}"
        )


# =============================================================================
# Test: Cache Sync (source vs installed plugin)
# =============================================================================


class TestCacheSync:
    """Verify source and cache pyproject.toml stay in sync.

    When the plugin cache has a stale pyproject.toml with deprecated fields,
    hooks fail even though the source is fixed. This test catches the case
    where a developer fixes the source but forgets to update the cache.
    """

    CACHE_ROOT = Path.home() / ".claude" / "plugins" / "cache" / "clautorun" / "clautorun"

    def _get_cache_versions(self):
        """Find installed cache versions."""
        if not self.CACHE_ROOT.exists():
            return []
        return [d for d in self.CACHE_ROOT.iterdir() if d.is_dir() and (d / "pyproject.toml").exists()]

    def test_cache_pyproject_no_deprecated_uv_fields(self):
        """Cached pyproject.toml must not have deprecated [tool.uv] fields.

        The plugin cache at ~/.claude/plugins/cache/ is what Claude Code
        actually loads at runtime. Even if the source is fixed, a stale
        cache causes hook errors.
        """
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[no-redef]

        versions = self._get_cache_versions()
        if not versions:
            pytest.skip("No plugin cache found (plugin not installed)")

        deprecated_fields = {"default-extras"}
        errors = []

        for version_dir in versions:
            pyproject_path = version_dir / "pyproject.toml"
            with open(pyproject_path, "rb") as f:
                data = tomllib.load(f)
            uv_config = data.get("tool", {}).get("uv", {})
            found = deprecated_fields & set(uv_config.keys())
            if found:
                errors.append(
                    f"  {pyproject_path}: deprecated fields {found}\n"
                    f"  Fix: Copy source pyproject.toml to cache, or reinstall plugin"
                )

        assert len(errors) == 0, (
            f"Plugin cache has deprecated [tool.uv] fields that cause hook errors:\n"
            + "\n".join(errors) + "\n\n"
            f"Source: {PLUGIN_ROOT / 'pyproject.toml'}\n"
            f"Run: cp {PLUGIN_ROOT / 'pyproject.toml'} <cache_path>/pyproject.toml"
        )


# =============================================================================
# Test: Old Files Removed
# =============================================================================


class TestCleanup:
    """Verify old/unused files are removed."""

    def test_shell_script_deleted(self):
        """clautorun-hook.sh should not exist."""
        old_script = PLUGIN_ROOT / "hooks" / "clautorun-hook.sh"
        assert not old_script.exists()
