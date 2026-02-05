#!/usr/bin/env python3
"""
Tests for bootstrap configuration functionality.

Tests the --no-bootstrap and --enable-bootstrap CLI commands that modify hooks.json,
and the is_bootstrap_disabled() function in hook_entry.py.

v0.7.0: Bootstrap is controlled via:
1. --no-bootstrap flag in hooks.json commands (persistent)
2. CLAUTORUN_NO_BOOTSTRAP=1 environment variable (runtime)
"""

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest


def get_hook_entry_module():
    """Import hook_entry.py as a module from the hooks directory."""
    plugin_root = Path(__file__).parent.parent
    hook_entry_path = plugin_root / "hooks" / "hook_entry.py"

    spec = importlib.util.spec_from_file_location("hook_entry", hook_entry_path)
    hook_entry = importlib.util.module_from_spec(spec)
    sys.modules["hook_entry"] = hook_entry
    spec.loader.exec_module(hook_entry)
    return hook_entry


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_hooks_json():
    """Sample hooks.json content without --no-bootstrap flag."""
    return {
        "description": "clautorun v0.7 - unified daemon-based hook handler",
        "hooks": {
            "UserPromptSubmit": [
                {
                    "matcher": "/afs|/afa|/afj|/afst|/autorun|/autostop|/estop|/cr:",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/hook_entry.py",
                            "timeout": 10,
                        }
                    ],
                }
            ],
            "PreToolUse": [
                {
                    "matcher": "Write|Bash|ExitPlanMode",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/hook_entry.py",
                            "timeout": 10,
                        }
                    ],
                }
            ],
            "Stop": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/hook_entry.py",
                            "timeout": 10,
                        }
                    ]
                }
            ],
        },
    }


@pytest.fixture
def sample_hooks_json_disabled():
    """Sample hooks.json content WITH --no-bootstrap flag."""
    return {
        "description": "clautorun v0.7 - unified daemon-based hook handler",
        "hooks": {
            "UserPromptSubmit": [
                {
                    "matcher": "/afs|/afa|/afj|/afst|/autorun|/autostop|/estop|/cr:",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/hook_entry.py --no-bootstrap",
                            "timeout": 10,
                        }
                    ],
                }
            ],
            "PreToolUse": [
                {
                    "matcher": "Write|Bash|ExitPlanMode",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/hook_entry.py --no-bootstrap",
                            "timeout": 10,
                        }
                    ],
                }
            ],
            "Stop": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/hook_entry.py --no-bootstrap",
                            "timeout": 10,
                        }
                    ]
                }
            ],
        },
    }


@pytest.fixture
def temp_hooks_dir(sample_hooks_json):
    """Create a temporary directory with hooks.json for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        hooks_dir = Path(tmpdir) / "hooks"
        hooks_dir.mkdir()
        hooks_file = hooks_dir / "hooks.json"
        hooks_file.write_text(json.dumps(sample_hooks_json, indent=2))
        yield tmpdir


@pytest.fixture
def temp_hooks_dir_disabled(sample_hooks_json_disabled):
    """Create a temporary directory with hooks.json that has --no-bootstrap."""
    with tempfile.TemporaryDirectory() as tmpdir:
        hooks_dir = Path(tmpdir) / "hooks"
        hooks_dir.mkdir()
        hooks_file = hooks_dir / "hooks.json"
        hooks_file.write_text(json.dumps(sample_hooks_json_disabled, indent=2))
        yield tmpdir


# =============================================================================
# Tests for set_bootstrap_config() in __main__.py
# =============================================================================


class TestSetBootstrapConfig:
    """Tests for the set_bootstrap_config function."""

    def test_disable_bootstrap_adds_flag(self, temp_hooks_dir):
        """Test that --no-bootstrap disables bootstrap by adding flag to hooks.json."""
        from clautorun.__main__ import set_bootstrap_config

        with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_ROOT": temp_hooks_dir}):
            result = set_bootstrap_config(enabled=False)

        assert result == 0

        # Verify hooks.json was modified
        hooks_file = Path(temp_hooks_dir) / "hooks" / "hooks.json"
        content = hooks_file.read_text()

        # All commands should now have --no-bootstrap
        assert "--no-bootstrap" in content
        assert content.count("--no-bootstrap") >= 3  # At least 3 hooks

    def test_enable_bootstrap_removes_flag(self, temp_hooks_dir_disabled):
        """Test that --enable-bootstrap removes flag from hooks.json."""
        from clautorun.__main__ import set_bootstrap_config

        with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_ROOT": temp_hooks_dir_disabled}):
            result = set_bootstrap_config(enabled=True)

        assert result == 0

        # Verify hooks.json was modified
        hooks_file = Path(temp_hooks_dir_disabled) / "hooks" / "hooks.json"
        content = hooks_file.read_text()

        # No commands should have --no-bootstrap
        assert "--no-bootstrap" not in content

    def test_disable_bootstrap_idempotent(self, temp_hooks_dir_disabled, capsys):
        """Test that disabling already-disabled bootstrap is idempotent."""
        from clautorun.__main__ import set_bootstrap_config

        with mock.patch.dict(
            os.environ, {"CLAUDE_PLUGIN_ROOT": temp_hooks_dir_disabled}
        ):
            result = set_bootstrap_config(enabled=False)

        assert result == 0
        captured = capsys.readouterr()
        assert "already disabled" in captured.out.lower()

    def test_enable_bootstrap_idempotent(self, temp_hooks_dir, capsys):
        """Test that enabling already-enabled bootstrap is idempotent."""
        from clautorun.__main__ import set_bootstrap_config

        with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_ROOT": temp_hooks_dir}):
            result = set_bootstrap_config(enabled=True)

        assert result == 0
        captured = capsys.readouterr()
        assert "already enabled" in captured.out.lower()

    def test_missing_hooks_json_returns_error(self, capsys):
        """Test that missing hooks.json returns error code 1."""
        from clautorun.__main__ import set_bootstrap_config

        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_ROOT": tmpdir}):
                result = set_bootstrap_config(enabled=False)

        assert result == 1
        captured = capsys.readouterr()
        assert "not found" in captured.out.lower()

    def test_disable_preserves_json_structure(self, temp_hooks_dir):
        """Test that disabling bootstrap preserves overall JSON structure."""
        from clautorun.__main__ import set_bootstrap_config

        hooks_file = Path(temp_hooks_dir) / "hooks" / "hooks.json"
        original_data = json.loads(hooks_file.read_text())

        with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_ROOT": temp_hooks_dir}):
            set_bootstrap_config(enabled=False)

        # Verify JSON is still valid
        modified_content = hooks_file.read_text()
        modified_data = json.loads(modified_content)

        # Structure should be preserved
        assert "description" in modified_data
        assert "hooks" in modified_data
        assert set(modified_data["hooks"].keys()) == set(original_data["hooks"].keys())


# =============================================================================
# Tests for is_bootstrap_disabled() in hook_entry.py
# =============================================================================


class TestIsBootstrapDisabled:
    """Tests for the is_bootstrap_disabled function in hook_entry.py."""

    def test_disabled_by_flag(self):
        """Test that --no-bootstrap flag disables bootstrap."""
        hook_entry = get_hook_entry_module()

        # Mock sys.argv to include --no-bootstrap
        with mock.patch.object(sys, "argv", ["hook_entry.py", "--no-bootstrap"]):
            result = hook_entry.is_bootstrap_disabled()

        assert result is True

    def test_disabled_by_env_var(self):
        """Test that CLAUTORUN_NO_BOOTSTRAP=1 disables bootstrap."""
        hook_entry = get_hook_entry_module()

        with mock.patch.object(sys, "argv", ["hook_entry.py"]):
            with mock.patch.dict(os.environ, {"CLAUTORUN_NO_BOOTSTRAP": "1"}):
                result = hook_entry.is_bootstrap_disabled()

        assert result is True

    def test_enabled_by_default(self):
        """Test that bootstrap is enabled by default (no flag, no env var)."""
        hook_entry = get_hook_entry_module()

        with mock.patch.object(sys, "argv", ["hook_entry.py"]):
            with mock.patch.dict(
                os.environ, {"CLAUTORUN_NO_BOOTSTRAP": "0"}, clear=False
            ):
                # Remove the env var if it exists
                env_copy = os.environ.copy()
                env_copy.pop("CLAUTORUN_NO_BOOTSTRAP", None)
                with mock.patch.dict(os.environ, env_copy, clear=True):
                    result = hook_entry.is_bootstrap_disabled()

        assert result is False

    def test_env_var_zero_means_enabled(self):
        """Test that CLAUTORUN_NO_BOOTSTRAP=0 means bootstrap is enabled."""
        hook_entry = get_hook_entry_module()

        with mock.patch.object(sys, "argv", ["hook_entry.py"]):
            with mock.patch.dict(os.environ, {"CLAUTORUN_NO_BOOTSTRAP": "0"}):
                result = hook_entry.is_bootstrap_disabled()

        assert result is False

    def test_flag_takes_precedence(self):
        """Test that flag works even with env var set to 0."""
        hook_entry = get_hook_entry_module()

        with mock.patch.object(sys, "argv", ["hook_entry.py", "--no-bootstrap"]):
            with mock.patch.dict(os.environ, {"CLAUTORUN_NO_BOOTSTRAP": "0"}):
                result = hook_entry.is_bootstrap_disabled()

        assert result is True


# =============================================================================
# Tests for CLI argument parsing
# =============================================================================


class TestCLIArgumentParsing:
    """Tests for CLI argument parsing in __main__.py."""

    def test_force_install_flag_parsed(self):
        """Test that --force-install flag is parsed correctly."""
        from clautorun.__main__ import create_parser

        parser = create_parser()
        args = parser.parse_args(["--install", "--force-install"])

        assert args.install == "all"
        assert args.force_install is True

    def test_force_install_short_flag_parsed(self):
        """Test that -f short flag works for --force-install."""
        from clautorun.__main__ import create_parser

        parser = create_parser()
        args = parser.parse_args(["--install", "-f"])

        assert args.force_install is True

    def test_no_bootstrap_flag_parsed(self):
        """Test that --no-bootstrap flag is parsed correctly."""
        from clautorun.__main__ import create_parser

        parser = create_parser()
        args = parser.parse_args(["--no-bootstrap"])

        assert args.no_bootstrap is True
        assert args.enable_bootstrap is False

    def test_enable_bootstrap_flag_parsed(self):
        """Test that --enable-bootstrap flag is parsed correctly."""
        from clautorun.__main__ import create_parser

        parser = create_parser()
        args = parser.parse_args(["--enable-bootstrap"])

        assert args.enable_bootstrap is True
        assert args.no_bootstrap is False

    def test_install_with_plugins(self):
        """Test that --install accepts plugin names."""
        from clautorun.__main__ import create_parser

        parser = create_parser()
        args = parser.parse_args(["--install", "clautorun,plan-export"])

        assert args.install == "clautorun,plan-export"

    def test_install_without_plugins_defaults_to_all(self):
        """Test that --install without plugins defaults to 'all'."""
        from clautorun.__main__ import create_parser

        parser = create_parser()
        args = parser.parse_args(["--install"])

        assert args.install == "all"

    def test_status_flag_parsed(self):
        """Test that --status flag is parsed correctly."""
        from clautorun.__main__ import create_parser

        parser = create_parser()
        args = parser.parse_args(["--status"])

        assert args.status is True

    def test_version_flag_parsed(self):
        """Test that --version flag is parsed correctly."""
        from clautorun.__main__ import create_parser

        parser = create_parser()
        args = parser.parse_args(["--version"])

        assert args.version is True


# =============================================================================
# Tests for main() function routing
# =============================================================================


class TestMainFunctionRouting:
    """Tests for main() function routing based on arguments."""

    def test_no_bootstrap_calls_set_bootstrap_config_false(self, temp_hooks_dir):
        """Test that --no-bootstrap calls set_bootstrap_config(enabled=False)."""
        from clautorun.__main__ import main

        with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_ROOT": temp_hooks_dir}):
            result = main(["--no-bootstrap"])

        assert result == 0

        # Verify flag was added
        hooks_file = Path(temp_hooks_dir) / "hooks" / "hooks.json"
        assert "--no-bootstrap" in hooks_file.read_text()

    def test_enable_bootstrap_calls_set_bootstrap_config_true(
        self, temp_hooks_dir_disabled
    ):
        """Test that --enable-bootstrap calls set_bootstrap_config(enabled=True)."""
        from clautorun.__main__ import main

        with mock.patch.dict(
            os.environ, {"CLAUDE_PLUGIN_ROOT": temp_hooks_dir_disabled}
        ):
            result = main(["--enable-bootstrap"])

        assert result == 0

        # Verify flag was removed
        hooks_file = Path(temp_hooks_dir_disabled) / "hooks" / "hooks.json"
        assert "--no-bootstrap" not in hooks_file.read_text()

    def test_version_returns_zero(self, capsys):
        """Test that --version returns exit code 0."""
        from clautorun.__main__ import main

        result = main(["--version"])

        assert result == 0
        captured = capsys.readouterr()
        assert "clautorun" in captured.out
        assert "0.7.0" in captured.out

    def test_install_calls_install_plugins(self):
        """Test that --install calls install_plugins function."""
        from clautorun.__main__ import main

        with mock.patch(
            "clautorun.install_plugins.install_plugins", return_value=0
        ) as mock_install:
            result = main(["--install"])

        mock_install.assert_called_once_with("all", tool=False, force=False)
        assert result == 0

    def test_install_with_force_passes_force_flag(self):
        """Test that --install --force-install passes force=True."""
        from clautorun.__main__ import main

        with mock.patch(
            "clautorun.install_plugins.install_plugins", return_value=0
        ) as mock_install:
            result = main(["--install", "--force-install"])

        mock_install.assert_called_once_with("all", tool=False, force=True)
        assert result == 0

    def test_install_with_plugins_passes_selection(self):
        """Test that --install clautorun,plan-export passes selection."""
        from clautorun.__main__ import main

        with mock.patch(
            "clautorun.install_plugins.install_plugins", return_value=0
        ) as mock_install:
            result = main(["--install", "clautorun,plan-export"])

        mock_install.assert_called_once_with(
            "clautorun,plan-export", tool=False, force=False
        )
        assert result == 0

    def test_status_calls_show_status(self):
        """Test that --status calls show_status function."""
        from clautorun.__main__ import main

        with mock.patch(
            "clautorun.install_plugins.show_status", return_value=0
        ) as mock_status:
            result = main(["--status"])

        mock_status.assert_called_once()
        assert result == 0


# =============================================================================
# Tests for hook_entry.py bootstrap functions
# =============================================================================


class TestCanBootstrap:
    """Tests for can_bootstrap() function."""

    def test_returns_true_with_uv(self):
        """Test that can_bootstrap returns True when uv is available."""
        hook_entry = get_hook_entry_module()

        with mock.patch.object(hook_entry.shutil, "which") as mock_which:
            mock_which.side_effect = lambda cmd: "/usr/bin/uv" if cmd == "uv" else None
            with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_ROOT": "/tmp/test"}):
                can_run, tool = hook_entry.can_bootstrap()

        assert can_run is True
        assert tool == "uv"

    def test_returns_true_with_pip3(self):
        """Test that can_bootstrap returns True when pip3 is available."""
        hook_entry = get_hook_entry_module()

        with mock.patch.object(hook_entry.shutil, "which") as mock_which:

            def which_side_effect(cmd):
                if cmd == "pip3":
                    return "/usr/bin/pip3"
                return None

            mock_which.side_effect = which_side_effect
            with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_ROOT": "/tmp/test"}):
                can_run, tool = hook_entry.can_bootstrap()

        assert can_run is True
        assert tool == "pip"

    def test_returns_false_without_pip_or_uv(self):
        """Test that can_bootstrap returns False without pip or uv."""
        hook_entry = get_hook_entry_module()

        with mock.patch.object(hook_entry.shutil, "which", return_value=None):
            with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_ROOT": "/tmp/test"}):
                can_run, reason = hook_entry.can_bootstrap()

        assert can_run is False
        assert "pip" in reason.lower()

    def test_returns_false_without_plugin_root(self):
        """Test that can_bootstrap returns False without CLAUDE_PLUGIN_ROOT."""
        hook_entry = get_hook_entry_module()

        with mock.patch.object(hook_entry.shutil, "which", return_value="/usr/bin/uv"):
            with mock.patch.dict(os.environ, {}, clear=True):
                can_run, reason = hook_entry.can_bootstrap()

        assert can_run is False
        assert "CLAUDE_PLUGIN_ROOT" in reason

    def test_returns_false_with_old_python(self):
        """Test that can_bootstrap returns False with Python < 3.10."""
        hook_entry = get_hook_entry_module()

        # Create a proper version_info mock with major/minor attributes
        class MockVersionInfo:
            def __init__(self, major, minor, micro):
                self.major = major
                self.minor = minor
                self.micro = micro

            def __lt__(self, other):
                return (self.major, self.minor) < other[:2]

            def __ge__(self, other):
                return (self.major, self.minor) >= other[:2]

        mock_version = MockVersionInfo(3, 9, 0)

        with mock.patch.object(hook_entry.sys, "version_info", mock_version):
            with mock.patch.object(
                hook_entry.shutil, "which", return_value="/usr/bin/uv"
            ):
                with mock.patch.dict(os.environ, {"CLAUDE_PLUGIN_ROOT": "/tmp/test"}):
                    can_run, reason = hook_entry.can_bootstrap()

        assert can_run is False
        assert "3.10" in reason


class TestIsBootstrapRunning:
    """Tests for is_bootstrap_running() function."""

    def test_returns_false_without_lockfile(self):
        """Test that is_bootstrap_running returns False without lockfile."""
        hook_entry = get_hook_entry_module()

        # Use a non-existent lockfile path
        with mock.patch.object(
            hook_entry, "BOOTSTRAP_LOCKFILE", "/tmp/nonexistent_lockfile_12345"
        ):
            result = hook_entry.is_bootstrap_running()

        assert result is False

    def test_returns_true_with_recent_lockfile(self):
        """Test that is_bootstrap_running returns True with recent lockfile."""
        hook_entry = get_hook_entry_module()

        with tempfile.NamedTemporaryFile(delete=False) as f:
            lockfile_path = f.name

        try:
            # Patch the BOOTSTRAP_LOCKFILE constant
            with mock.patch.object(hook_entry, "BOOTSTRAP_LOCKFILE", lockfile_path):
                result = hook_entry.is_bootstrap_running()
            assert result is True
        finally:
            os.unlink(lockfile_path)

    def test_removes_stale_lockfile(self):
        """Test that is_bootstrap_running removes stale (>60s) lockfile."""
        import time

        hook_entry = get_hook_entry_module()

        with tempfile.NamedTemporaryFile(delete=False) as f:
            lockfile_path = f.name

        try:
            # Make the file appear old
            old_time = time.time() - 120  # 2 minutes ago
            os.utime(lockfile_path, (old_time, old_time))

            with mock.patch.object(hook_entry, "BOOTSTRAP_LOCKFILE", lockfile_path):
                result = hook_entry.is_bootstrap_running()

            assert result is False
            assert not os.path.exists(lockfile_path)
        finally:
            if os.path.exists(lockfile_path):
                os.unlink(lockfile_path)


class TestFailOpen:
    """Tests for fail_open() function."""

    def test_fail_open_prints_valid_json(self, capsys):
        """Test that fail_open prints valid JSON."""
        hook_entry = get_hook_entry_module()

        with pytest.raises(SystemExit) as exc_info:
            hook_entry.fail_open("Test message")

        assert exc_info.value.code == 0

        captured = capsys.readouterr()
        data = json.loads(captured.out)

        assert data["continue"] is True
        assert "Test message" in data["systemMessage"]

    def test_fail_open_exits_with_zero(self):
        """Test that fail_open exits with code 0 (success for hook)."""
        hook_entry = get_hook_entry_module()

        with pytest.raises(SystemExit) as exc_info:
            hook_entry.fail_open()

        assert exc_info.value.code == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
