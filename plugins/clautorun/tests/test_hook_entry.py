#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for hooks/hook_entry.py and daemon.py bootstrap

TDD-driven tests for hook entry point and daemon bootstrap functionality.
"""
import json
import os
import shutil
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
# Test: try_cli Return Code and Stdin Preservation
# =============================================================================


class TestTryCliRobustness:
    """Test that try_cli checks return codes and stdin is preserved for fallback.

    Bug history:
    - try_cli() returned True even when the subprocess failed (non-zero exit
      code, argparse errors from stale UV tool installs, empty stdout). This
      caused hook_entry.py to exit without printing any JSON → Claude Code
      fail-open → rm/git-reset-hard executed unblocked.
    - try_cli() consumed stdin, so if it failed the fallback path
      (run_fallback → run_client → json.load(sys.stdin)) got empty input.
    """

    def test_try_cli_checks_return_code(self):
        """try_cli must check returncode — must not return True on failure."""
        content = HOOK_ENTRY.read_text()
        assert "result.returncode" in content, \
            "try_cli must check subprocess return code. Without this, " \
            "a stale/broken CLI binary silently causes fail-open."

    def test_try_cli_requires_stdout(self):
        """try_cli must return False when stdout is empty."""
        content = HOOK_ENTRY.read_text()
        # After the returncode check, there should be a check for empty stdout
        try_cli_idx = content.find("def try_cli(")
        try_cli_end = content.find("\ndef ", try_cli_idx + 1)
        try_cli_body = content[try_cli_idx:try_cli_end]
        assert "return False" in try_cli_body, \
            "try_cli must return False on empty stdout (no valid hook response)"

    def test_stdin_read_once_in_main(self):
        """stdin must be read once in main(), not inside try_cli."""
        content = HOOK_ENTRY.read_text()
        main_idx = content.find("def main()")
        main_body = content[main_idx:]
        # stdin should be read in main, not try_cli
        assert "sys.stdin.read()" in main_body or "stdin.read()" in main_body, \
            "main() must read stdin once to preserve it for fallback path"

    def test_stdin_restored_for_fallback(self):
        """After try_cli fails, stdin must be restored for run_fallback."""
        content = HOOK_ENTRY.read_text()
        assert "io.StringIO" in content, \
            "main() must use io.StringIO to restore stdin for fallback path. " \
            "Without this, run_client() gets empty stdin after try_cli consumes it."

    def test_try_cli_accepts_stdin_data_param(self):
        """try_cli must accept stdin_data parameter (not read stdin itself)."""
        content = HOOK_ENTRY.read_text()
        assert "def try_cli(bin_path" in content
        try_cli_idx = content.find("def try_cli(")
        sig_end = content.find(")", try_cli_idx) + 1
        signature = content[try_cli_idx:sig_end]
        assert "stdin_data" in signature, \
            "try_cli must accept stdin_data as parameter, not read stdin itself"

    def test_hook_rm_blocked_no_stderr(self):
        """Full e2e: hook_entry.py blocks rm with deny on stdout, no stderr."""
        env = os.environ.copy()
        env['CLAUDE_PLUGIN_ROOT'] = str(PLUGIN_ROOT)
        payload = json.dumps({
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "rm /tmp/test"}
        })

        result = subprocess.run(
            [sys.executable, str(HOOK_ENTRY)],
            input=payload,
            capture_output=True,
            text=True,
            env=env,
            timeout=10
        )

        assert result.returncode == 0, f"Exit {result.returncode}"

        # Must have valid JSON on stdout
        try:
            output = json.loads(result.stdout)
        except json.JSONDecodeError:
            pytest.fail(
                f"No valid JSON on stdout (this causes Claude Code fail-open).\n"
                f"stdout: {result.stdout[:200]}\n"
                f"stderr: {result.stderr[:200]}"
            )

        # Must deny rm
        assert output.get("continue") is False, \
            f"rm should be blocked (continue=false) but got: {output}"
        assert output.get("decision") == "deny", \
            f"rm should get decision=deny but got: {output.get('decision')}"

        # stderr MUST be empty — any stderr causes Claude Code "hook error"
        # Filter out known-benign UV build lines
        stderr_lines = [
            line for line in result.stderr.strip().splitlines()
            if line.strip() and not any(
                line.strip().startswith(p) for p in
                ("Building", "Built", "Installed", "Resolved", "Prepared", "Downloading")
            )
        ]
        assert len(stderr_lines) == 0, (
            f"stderr must be empty (Claude Code treats any stderr as 'hook error' → fail-open).\n"
            f"stderr content: {result.stderr[:500]}"
        )


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


class TestCommandBlockingE2E:
    """End-to-end tests for command blocking through the hook system.

    These tests exercise the real should_block_command() and
    build_pretooluse_response() functions to verify that dangerous commands
    are blocked with decision="deny" + continue=false, and safe commands
    are allowed.

    Bug history:
    - UV stderr from deprecated pyproject.toml fields caused Claude Code to
      treat hook output as "hook error" → fail-open → ALL commands passed
    - Substring matching caused "/cr:plannew" to match "rm" pattern (substring
      of "plannew") → false positive blocking of slash commands
    """

    @pytest.fixture(autouse=True)
    def _import_main(self):
        """Import main module functions for testing."""
        # Add source to path so we can import clautorun
        src_dir = PLUGIN_ROOT / "src"
        if str(src_dir) not in sys.path:
            sys.path.insert(0, str(src_dir))
        from clautorun.main import (
            should_block_command,
            build_pretooluse_response,
            command_matches_pattern,
        )
        self.should_block_command = should_block_command
        self.build_pretooluse_response = build_pretooluse_response
        self.command_matches_pattern = command_matches_pattern

    # ─── Dangerous commands MUST be blocked ───────────────────────────

    def test_rm_is_blocked(self):
        """rm /tmp/file MUST be blocked by default integrations."""
        result = self.should_block_command("test-session", "rm /tmp/file")
        assert result is not None, \
            "rm should be blocked but was allowed. Check DEFAULT_INTEGRATIONS in config.py"
        assert result["pattern"] == "rm"
        assert "trash" in result["suggestion"].lower()

    def test_rm_rf_is_blocked(self):
        """rm -rf /tmp/dir MUST be blocked."""
        result = self.should_block_command("test-session", "rm -rf /tmp/dir")
        assert result is not None, \
            "rm -rf should be blocked. Check DEFAULT_INTEGRATIONS in config.py"

    def test_sudo_rm_is_blocked(self):
        """sudo rm /tmp/file MUST be blocked (prefix detection)."""
        result = self.should_block_command("test-session", "sudo rm /tmp/file")
        assert result is not None, \
            "sudo rm should be blocked via command prefix detection"

    def test_git_reset_hard_matches_pattern(self):
        """'git reset --hard' pattern MUST match the command."""
        assert self.command_matches_pattern(
            "git reset --hard HEAD~1", "git reset --hard"
        ), "git reset --hard should match pattern"

    # ─── Safe commands MUST be allowed ────────────────────────────────

    def test_echo_is_allowed(self):
        """echo is a safe command, must not be blocked."""
        result = self.should_block_command("test-session", "echo hello")
        assert result is None, \
            f"echo should be allowed but was blocked: {result}"

    def test_ls_is_allowed(self):
        """ls is a safe command, must not be blocked."""
        result = self.should_block_command("test-session", "ls -la")
        assert result is None, \
            f"ls should be allowed but was blocked: {result}"

    def test_pwd_is_allowed(self):
        """pwd is a safe command, must not be blocked."""
        result = self.should_block_command("test-session", "pwd")
        assert result is None, \
            f"pwd should be allowed but was blocked: {result}"

    def test_git_status_is_allowed(self):
        """git status is safe, must not be blocked."""
        result = self.should_block_command("test-session", "git status")
        assert result is None, \
            f"git status should be allowed but was blocked: {result}"

    def test_git_log_is_allowed(self):
        """git log is safe, must not be blocked."""
        result = self.should_block_command("test-session", "git log --oneline -5")
        assert result is None, \
            f"git log should be allowed but was blocked: {result}"

    def test_uv_run_is_allowed(self):
        """uv run is safe, must not be blocked."""
        result = self.should_block_command("test-session", "uv run pytest -v")
        assert result is None, \
            f"uv run should be allowed but was blocked: {result}"

    # ─── Response format validation ───────────────────────────────────

    def test_deny_response_has_continue_false(self):
        """deny response MUST have continue=false to actually block the tool."""
        response = self.build_pretooluse_response(decision="deny", reason="test")
        assert response["continue"] is False, \
            "deny response must set continue=false to block tool execution"
        assert response["decision"] == "deny"
        assert response["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_allow_response_has_continue_true(self):
        """allow response MUST have continue=true to let tool proceed."""
        response = self.build_pretooluse_response(decision="allow", reason="ok")
        assert response["continue"] is True, \
            "allow response must set continue=true"
        assert response["decision"] == "allow"

    def test_deny_response_has_stop_reason(self):
        """deny response MUST include stop reason for user feedback."""
        response = self.build_pretooluse_response(decision="deny", reason="blocked rm")
        assert response["stopReason"] != "", \
            "deny response must include non-empty stopReason"
        assert "blocked rm" in response["stopReason"]


class TestSlashCommandFalsePositives:
    """Tests that slash commands are NOT falsely blocked by hook patterns.

    Bug history: Substring matching caused "/cr:plannew" to match "rm"
    pattern because "rm" is a substring of "plannew". The AST-based
    command detection (command_detection.py) fixes this by parsing the
    command as a shell AST and only matching commands in command position,
    not as substrings of arguments or other tokens.

    These tests prevent regression of the fix by verifying that all
    clautorun slash commands are not falsely blocked.
    """

    @pytest.fixture(autouse=True)
    def _import_main(self):
        """Import main module functions for testing."""
        src_dir = PLUGIN_ROOT / "src"
        if str(src_dir) not in sys.path:
            sys.path.insert(0, str(src_dir))
        from clautorun.main import (
            should_block_command,
            command_matches_pattern,
        )
        self.should_block_command = should_block_command
        self.command_matches_pattern = command_matches_pattern

    # ─── Pattern matching must not match substrings ───────────────────

    def test_rm_pattern_does_not_match_plannew(self):
        """'rm' pattern must NOT match '/cr:plannew' (substring bug)."""
        assert not self.command_matches_pattern("/cr:plannew", "rm"), \
            "'rm' falsely matched '/cr:plannew' — substring matching bug. " \
            "command_matches_pattern must use AST-based detection."

    def test_rm_pattern_does_not_match_autorun(self):
        """'rm' pattern must NOT match '/cr:autorun' (substring 'run')."""
        assert not self.command_matches_pattern("/cr:autorun task", "rm"), \
            "'rm' falsely matched '/cr:autorun' — substring matching bug"

    def test_rm_pattern_does_not_match_planrefine(self):
        """'rm' pattern must NOT match '/cr:planrefine'."""
        assert not self.command_matches_pattern("/cr:planrefine", "rm")

    def test_rm_pattern_does_not_match_commit(self):
        """'rm' pattern must NOT match '/cr:commit'."""
        assert not self.command_matches_pattern("/cr:commit", "rm")

    def test_rm_pattern_does_not_match_echo_rm(self):
        """'rm' pattern must NOT match 'echo rm' (argument position)."""
        assert not self.command_matches_pattern("echo rm", "rm"), \
            "'rm' falsely matched 'echo rm' — rm is an argument, not a command"

    # ─── Slash commands must not be blocked ───────────────────────────

    def test_slash_plannew_not_blocked(self):
        """'/cr:plannew' must not be blocked by any pattern."""
        result = self.should_block_command("test-session", "/cr:plannew")
        assert result is None, \
            f"/cr:plannew was falsely blocked: {result}"

    def test_slash_go_not_blocked(self):
        """'/cr:go task' must not be blocked."""
        result = self.should_block_command("test-session", "/cr:go implement feature")
        assert result is None, \
            f"/cr:go was falsely blocked: {result}"

    def test_slash_status_not_blocked(self):
        """'/cr:st' must not be blocked."""
        result = self.should_block_command("test-session", "/cr:st")
        assert result is None, \
            f"/cr:st was falsely blocked: {result}"

    def test_slash_commit_not_blocked(self):
        """'/cr:commit' must not be blocked."""
        result = self.should_block_command("test-session", "/cr:commit")
        assert result is None, \
            f"/cr:commit was falsely blocked: {result}"

    def test_slash_philosophy_not_blocked(self):
        """'/cr:philosophy' must not be blocked."""
        result = self.should_block_command("test-session", "/cr:philosophy")
        assert result is None, \
            f"/cr:philosophy was falsely blocked: {result}"

    def test_slash_estop_not_blocked(self):
        """'/cr:sos' (emergency stop) must not be blocked."""
        result = self.should_block_command("test-session", "/cr:sos")
        assert result is None, \
            f"/cr:sos was falsely blocked: {result}"

    # ─── Edge cases: rm in various positions ──────────────────────────

    def test_rm_actual_command_IS_blocked(self):
        """Actual 'rm file' command must still be blocked."""
        result = self.should_block_command("test-session", "rm /tmp/test.txt")
        assert result is not None, \
            "Actual 'rm' command should be blocked"

    def test_trash_is_allowed(self):
        """'trash' (the safe alternative) must be allowed."""
        result = self.should_block_command("test-session", "trash /tmp/test.txt")
        assert result is None, \
            f"'trash' should be allowed (it's the safe alternative to rm): {result}"

    def test_echo_rm_is_allowed(self):
        """'echo rm' — rm as argument to echo must be allowed (not blocked as rm)."""
        result = self.should_block_command("test-session", "echo rm")
        assert result is None, \
            f"'echo rm' should be allowed (rm is an echo argument, not a command): {result}"


class TestAllLocationsSync:
    """Verify all code locations stay synchronized across platforms.

    This prevents recurring desync bugs where:
    - Source is fixed but cache/UV tool/Gemini extension have old code
    - UV tool install --force doesn't invalidate cache (uv#9492)
    - Gemini extensions install creates separate copies
    - Build/ artifacts lag behind source

    9 code locations that can desync:
    1. Source: plugins/clautorun/src/clautorun/
    2. Dev venv: plugins/clautorun/.venv/.../clautorun/
    3. Build: plugins/clautorun/build/lib/clautorun/
    4. Claude cache: ~/.claude/plugins/cache/clautorun/clautorun/0.8.0/
    5. UV tool: ~/.local/share/uv/tools/clautorun/.../clautorun/
    6. Gemini source: ~/.gemini/extensions/clautorun-workspace/plugins/clautorun/src/
    7. Gemini plugin venv: ~/.gemini/extensions/clautorun-workspace/plugins/clautorun/.venv/
    8. Gemini workspace venv: ~/.gemini/extensions/clautorun-workspace/.venv/
    9. Gemini build: ~/.gemini/extensions/clautorun-workspace/plugins/clautorun/build/

    Target state (after symlink migration):
    - Source: 1 location (authoritative)
    - Symlinks: UV tool (editable), Gemini extension (link)
    - Caches: Claude Code cache (synced via --install --force)
    - Deleted: Build artifacts (not needed)
    """

    def test_source_hooks_json_is_claude_format(self):
        """Source hooks.json must have Claude Code format, not Gemini format."""
        hooks_json = PLUGIN_ROOT / "hooks" / "hooks.json"
        content = hooks_json.read_text()

        assert "unified daemon-based hook handler" in content, \
            "Source hooks.json has wrong format. Should be Claude Code, not Gemini. " \
            "Restore from: ~/.claude/plugins/cache/clautorun/clautorun/0.8.0/hooks/hooks.json"

        assert "PreToolUse" in content, \
            "Must have Claude Code event names (not Gemini's BeforeTool)"
        assert "${CLAUDE_PLUGIN_ROOT}" in content, \
            "Must use Claude Code variables (not Gemini's ${extensionPath})"
        assert "Bash" in content, \
            "Must use Claude Code tool names (not Gemini's run_shell_command)"

    def test_cache_matches_source_hook_entry(self):
        """Location 4: Claude Code cache hook_entry.py must match source."""
        cache_versions = list(Path.home().glob(
            ".claude/plugins/cache/clautorun/clautorun/*/hooks/hook_entry.py"
        ))

        if not cache_versions:
            pytest.skip("Claude Code cache not installed")

        source_content = (PLUGIN_ROOT / "hooks" / "hook_entry.py").read_text()

        for cache_file in cache_versions:
            cache_content = cache_file.read_text()
            assert cache_content == source_content, \
                f"Cache {cache_file} doesn't match source. " \
                f"Run: uv run --project plugins/clautorun python -m clautorun --install --force"

    def test_uv_tool_is_editable_not_copy(self):
        """Location 5: UV tool should be editable install (symlink), not copy."""
        if not shutil.which("clautorun"):
            pytest.skip("UV tool not installed")

        # Check for direct_url.json (indicates editable)
        tool_paths = list(Path.home().glob(
            ".local/share/uv/tools/clautorun/lib/python*/site-packages/clautorun*.dist-info/direct_url.json"
        ))

        if not tool_paths:
            pytest.fail(
                "UV tool is not editable (no direct_url.json found). "
                "This is a COPY which will desync from source. "
                "Run: uv tool uninstall clautorun && "
                "cd plugins/clautorun && uv tool install --editable ."
            )

        # Verify it's actually editable
        import json
        direct_url = json.loads(tool_paths[0].read_text())
        assert direct_url.get("dir_info", {}).get("editable") is True, \
            f"UV tool has direct_url.json but editable=false. Reinstall with --editable."

    def test_gemini_extension_is_symlink_not_copy(self):
        """Locations 6-9: Gemini extension should be symlink, not copy."""
        gemini_ext = Path.home() / ".gemini/extensions/clautorun-workspace"

        if not gemini_ext.exists():
            pytest.skip("Gemini extension not installed")

        assert gemini_ext.is_symlink(), \
            "Gemini extension is a COPY which will desync from source. " \
            "This creates 4 separate code locations that must be manually synced. " \
            "Run: gemini extensions uninstall clautorun-workspace && " \
            "gemini extensions link /Users/athundt/.claude/clautorun"

    def test_build_artifacts_do_not_exist(self):
        """Locations 3, 9: Build artifacts should be deleted."""
        build_dirs = [
            PLUGIN_ROOT / "build",
            Path.home() / ".gemini/extensions/clautorun-workspace/plugins/clautorun/build"
        ]

        for build_dir in build_dirs:
            if build_dir.exists() and not build_dir.is_symlink():
                pytest.fail(
                    f"Build artifacts at {build_dir} should not exist (gitignored). "
                    f"These are setuptools artifacts that lag behind source. "
                    f"Run: rm -rf {build_dir}"
                )

    def test_only_one_daemon_process(self):
        """Verify only one daemon running (not multiple from different locations)."""
        result = subprocess.run(
            ["pgrep", "-f", "clautorun.daemon"],
            capture_output=True,
            text=True
        )

        if result.returncode == 0:
            pids = result.stdout.strip().splitlines()
            if len(pids) > 1:
                pytest.fail(
                    f"Multiple daemons running from different code locations: {pids}. "
                    f"This causes inconsistent hook behavior. "
                    f"Run: pkill -f 'clautorun.daemon'"
                )


class TestCleanup:
    """Verify old/unused files are removed."""

    def test_shell_script_deleted(self):
        """clautorun-hook.sh should not exist."""
        old_script = PLUGIN_ROOT / "hooks" / "clautorun-hook.sh"
        assert not old_script.exists()
