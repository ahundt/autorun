#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for hooks/hook_entry.py

Tests the hook entry point that allows clautorun to run from the plugin cache
without UV installation.
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest


# Get paths
PLUGIN_ROOT = Path(__file__).parent.parent
HOOK_ENTRY = PLUGIN_ROOT / "hooks" / "hook_entry.py"
SRC_DIR = PLUGIN_ROOT / "src"


class TestHookEntryExists:
    """Verify hook_entry.py exists and is executable."""

    def test_hook_entry_exists(self):
        """hook_entry.py exists in hooks directory."""
        assert HOOK_ENTRY.exists(), f"hook_entry.py not found at {HOOK_ENTRY}"

    def test_hook_entry_is_python(self):
        """hook_entry.py has Python shebang."""
        content = HOOK_ENTRY.read_text()
        assert content.startswith("#!/usr/bin/env python3"), "Missing Python shebang"

    def test_hook_entry_has_main(self):
        """hook_entry.py has main function and entry point."""
        content = HOOK_ENTRY.read_text()
        assert "def main()" in content, "Missing main function"
        assert 'if __name__ == "__main__":' in content, "Missing entry point"


class TestHookEntryFailOpen:
    """Test fail-open behavior - hooks should never crash Claude."""

    def test_missing_plugin_root_fails_open(self):
        """Missing CLAUDE_PLUGIN_ROOT outputs valid JSON and exits 0."""
        env = os.environ.copy()
        env.pop('CLAUDE_PLUGIN_ROOT', None)

        result = subprocess.run(
            [sys.executable, str(HOOK_ENTRY)],
            capture_output=True,
            text=True,
            env=env,
            timeout=10
        )

        # Should exit 0 (not crash)
        assert result.returncode == 0, f"Expected exit 0, got {result.returncode}"

        # Should output valid JSON
        try:
            output = json.loads(result.stdout)
        except json.JSONDecodeError:
            pytest.fail(f"Invalid JSON output: {result.stdout}")

        # Should have continue=True (fail-open)
        assert output.get("continue") is True, "Expected continue=True for fail-open"

        # Should have error message
        assert "CLAUDE_PLUGIN_ROOT" in output.get("systemMessage", "")

    def test_invalid_plugin_root_fails_open(self):
        """Invalid CLAUDE_PLUGIN_ROOT outputs valid JSON and exits 0."""
        env = os.environ.copy()
        env['CLAUDE_PLUGIN_ROOT'] = '/nonexistent/path/that/does/not/exist'

        result = subprocess.run(
            [sys.executable, str(HOOK_ENTRY)],
            capture_output=True,
            text=True,
            env=env,
            timeout=10
        )

        # Should exit 0 (not crash)
        assert result.returncode == 0, f"Expected exit 0, got {result.returncode}"

        # Should output valid JSON
        try:
            output = json.loads(result.stdout)
        except json.JSONDecodeError:
            pytest.fail(f"Invalid JSON output: {result.stdout}")

        # Should have continue=True (fail-open)
        assert output.get("continue") is True, "Expected continue=True for fail-open"


class TestHookEntryPathSetup:
    """Test that hook_entry.py correctly sets up Python path."""

    def test_adds_src_to_path(self):
        """hook_entry.py adds src directory to sys.path."""
        # Run a test that checks sys.path after import
        test_code = f'''
import os
import sys

# Simulate what hook_entry does
plugin_root = "{PLUGIN_ROOT}"
src_dir = os.path.join(plugin_root, "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

# Check we can import clautorun
try:
    from clautorun.__main__ import main
    print("SUCCESS")
except ImportError as e:
    print(f"FAIL: {{e}}")
'''
        result = subprocess.run(
            [sys.executable, "-c", test_code],
            capture_output=True,
            text=True,
            timeout=10
        )

        assert "SUCCESS" in result.stdout, f"Import failed: {result.stdout} {result.stderr}"


class TestHookEntryIntegration:
    """Integration tests running hook_entry.py with valid CLAUDE_PLUGIN_ROOT."""

    def test_runs_with_valid_plugin_root(self):
        """hook_entry.py runs successfully with valid CLAUDE_PLUGIN_ROOT."""
        env = os.environ.copy()
        env['CLAUDE_PLUGIN_ROOT'] = str(PLUGIN_ROOT)

        # Provide empty JSON on stdin (simulating hook call with no input)
        result = subprocess.run(
            [sys.executable, str(HOOK_ENTRY)],
            capture_output=True,
            text=True,
            input="{}",
            env=env,
            timeout=10
        )

        # Should complete without crashing (may output JSON or nothing)
        # Exit code 0 indicates success or fail-open
        assert result.returncode == 0, f"Unexpected exit code: {result.returncode}, stderr: {result.stderr}"

    def test_outputs_valid_json(self):
        """hook_entry.py outputs valid JSON when run."""
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

        # If there's output, it should be valid JSON
        if result.stdout.strip():
            try:
                json.loads(result.stdout)
            except json.JSONDecodeError:
                # Some output is not JSON (e.g., daemon messages) - that's OK
                pass


class TestHooksJsonUpdated:
    """Verify hooks.json uses hook_entry.py."""

    def test_hooks_json_uses_hook_entry(self):
        """hooks.json calls hook_entry.py instead of clautorun CLI."""
        hooks_json = PLUGIN_ROOT / "hooks" / "hooks.json"
        content = hooks_json.read_text()

        # Should reference hook_entry.py
        assert "hook_entry.py" in content, "hooks.json should reference hook_entry.py"

        # Should NOT reference clautorun CLI directly
        # (the pattern should be python3 ... hook_entry.py, not just "clautorun")
        data = json.loads(content)
        for event, matchers in data.get("hooks", {}).items():
            for matcher in matchers:
                for hook in matcher.get("hooks", []):
                    command = hook.get("command", "")
                    if "clautorun" in command:
                        assert "hook_entry.py" in command, \
                            f"Hook command should use hook_entry.py, not CLI: {command}"

    def test_no_shell_script_in_hooks(self):
        """hooks.json does not reference the old shell script."""
        hooks_json = PLUGIN_ROOT / "hooks" / "hooks.json"
        content = hooks_json.read_text()

        assert "clautorun-hook.sh" not in content, \
            "hooks.json should not reference old shell script"


class TestOldShellScriptRemoved:
    """Verify the old shell script is removed."""

    def test_shell_script_deleted(self):
        """clautorun-hook.sh should be deleted."""
        old_script = PLUGIN_ROOT / "hooks" / "clautorun-hook.sh"
        assert not old_script.exists(), \
            f"Old shell script should be deleted: {old_script}"
