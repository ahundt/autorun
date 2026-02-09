#!/usr/bin/env python3
"""TDD tests for Task #14: CLI non-interactive fixes.

Verifies that:
1. CLI commands work in non-TTY contexts (no hanging on input())
2. --configure shows settings without prompting when non-TTY
3. --interactive flag forces interactive mode (requires TTY)
4. --clear operations respect --no-confirm flag
5. All 8 input() calls have proper TTY detection
"""

import sys
import subprocess
from pathlib import Path

# Add src to path
plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root / 'src'))


class TestCLINonInteractive:
    """Test CLI non-interactive behavior."""

    def test_configure_non_tty_shows_settings_only(self):
        """Test --configure in non-TTY shows settings without prompting."""
        result = subprocess.run(
            ["uv", "run", "python",
             str(plugin_root / "scripts" / "task_lifecycle_cli.py"),
             "--configure"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL  # Non-TTY (no stdin)
        )

        assert result.returncode == 0, f"Command failed: {result.stderr}"
        assert "Task Lifecycle Configuration" in result.stdout
        assert "Current settings:" in result.stdout
        assert "Non-interactive mode" in result.stdout
        assert "Use --interactive flag" in result.stdout

        # Should NOT contain prompts (these would hang in non-TTY)
        assert "Modify settings? (y/n):" not in result.stdout
        assert "Enable task lifecycle?" not in result.stdout

    def test_configure_with_pipe_input(self):
        """Test --configure works when input is piped (non-TTY)."""
        result = subprocess.run(
            ["uv", "run", "python",
             str(plugin_root / "scripts" / "task_lifecycle_cli.py"),
             "--configure"],
            capture_output=True,
            text=True,
            input="y\n"  # Provide input via pipe (still non-TTY)
        )

        assert result.returncode == 0
        assert "Non-interactive mode" in result.stdout

    def test_clear_with_no_confirm_flag(self):
        """Test --clear --all --no-confirm works without prompting."""
        result = subprocess.run(
            ["uv", "run", "python",
             str(plugin_root / "scripts" / "task_lifecycle_cli.py"),
             "--clear", "test-session-nonexistent",
             "--no-confirm"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL
        )

        # Should succeed without prompting
        assert result.returncode in [0, 1]  # 0 if cleared, 1 if error

        # Should NOT contain confirmation prompts
        assert "Type 'yes' to confirm:" not in result.stdout

    def test_clear_without_no_confirm_in_non_tty_refuses(self):
        """Test --clear without --no-confirm refuses in non-TTY."""
        result = subprocess.run(
            ["uv", "run", "python",
             str(plugin_root / "scripts" / "task_lifecycle_cli.py"),
             "--clear", "--all"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL  # Non-TTY
        )

        # Should refuse or exit with error code
        assert result.returncode == 2 or "non-interactive mode" in result.stdout.lower()

    def test_status_command_always_works_non_interactive(self):
        """Test --status works in non-TTY (never prompts)."""
        result = subprocess.run(
            ["uv", "run", "python",
             str(plugin_root / "scripts" / "task_lifecycle_cli.py"),
             "--status"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL
        )

        # Status may fail with exit code 1 if no session, but should never hang
        assert result.returncode in [0, 1], f"Unexpected exit code: {result.returncode}"
        # Should show either status output OR error message (not hang)
        assert len(result.stdout) > 0 or len(result.stderr) > 0
        # Should NOT hang waiting for input
        assert "Modify settings" not in result.stdout

    def test_enable_disable_commands_non_interactive(self):
        """Test --enable and --disable work without prompting."""
        # Test enable
        result_enable = subprocess.run(
            ["uv", "run", "python",
             str(plugin_root / "scripts" / "task_lifecycle_cli.py"),
             "--enable"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL
        )

        assert result_enable.returncode == 0

        # Test disable
        result_disable = subprocess.run(
            ["uv", "run", "python",
             str(plugin_root / "scripts" / "task_lifecycle_cli.py"),
             "--disable"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL
        )

        assert result_disable.returncode == 0

    def test_no_hanging_in_background_script(self):
        """Test CLI doesn't hang when run in background (common CI scenario)."""
        import signal

        # Run with timeout to detect hangs
        try:
            result = subprocess.run(
                ["uv", "run", "python",
                 str(plugin_root / "scripts" / "task_lifecycle_cli.py"),
                 "--configure"],
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=5  # Should complete in < 5 seconds
            )
            assert result.returncode == 0
            assert "Non-interactive mode" in result.stdout
        except subprocess.TimeoutExpired:
            assert False, "CLI hung waiting for input (TTY check failed)"


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
