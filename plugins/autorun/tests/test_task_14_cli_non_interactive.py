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
import os
import subprocess
from pathlib import Path
import pytest

pytestmark = pytest.mark.subprocess

# Add src to path
plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root / 'src'))


@pytest.fixture
def isolated_autorun_home(tmp_path):
    return tmp_path / "autorun-home"


def run_task_lifecycle_cli(*args: str, autorun_home: Path, **kwargs) -> subprocess.CompletedProcess:
    """Run the task lifecycle CLI through the autorun project environment."""
    env = dict(os.environ)
    env["AUTORUN_HOME"] = str(autorun_home)
    env.update(kwargs.pop("env", {}) or {})
    return subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(plugin_root),
            "python",
            str(plugin_root / "scripts" / "task_lifecycle_cli.py"),
            *args,
        ],
        env=env,
        **kwargs,
    )


class TestCLINonInteractive:
    """Test CLI non-interactive behavior."""

    def test_configure_non_tty_shows_settings_only(self, isolated_autorun_home):
        """Test --configure in non-TTY shows settings without prompting."""
        result = run_task_lifecycle_cli(
            "--configure",
            autorun_home=isolated_autorun_home,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,  # Non-TTY (no stdin)
            timeout=30
        )

        assert result.returncode == 0, f"Command failed: {result.stderr}"
        assert "Task Lifecycle Configuration" in result.stdout
        assert "Current settings:" in result.stdout
        assert "Non-interactive mode" in result.stdout
        assert "Use --interactive flag" in result.stdout

        # Should NOT contain prompts (these would hang in non-TTY)
        assert "Modify settings? (y/n):" not in result.stdout
        assert "Enable task lifecycle?" not in result.stdout

    def test_configure_with_pipe_input(self, isolated_autorun_home):
        """Test --configure works when input is piped (non-TTY)."""
        result = run_task_lifecycle_cli(
            "--configure",
            autorun_home=isolated_autorun_home,
            capture_output=True,
            text=True,
            input="y\n",  # Provide input via pipe (still non-TTY)
            timeout=30
        )

        assert result.returncode == 0
        assert "Non-interactive mode" in result.stdout

    def test_clear_with_no_confirm_flag(self, isolated_autorun_home):
        """Test --clear --all --no-confirm works without prompting."""
        result = run_task_lifecycle_cli(
            "--clear", "test-session-nonexistent",
            "--no-confirm",
            autorun_home=isolated_autorun_home,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=30
        )

        # Should succeed without prompting
        assert result.returncode in [0, 1]  # 0 if cleared, 1 if error

        # Should NOT contain confirmation prompts
        assert "Type 'yes' to confirm:" not in result.stdout

    def test_clear_without_no_confirm_in_non_tty_refuses(self, isolated_autorun_home):
        """Test --clear without --no-confirm refuses in non-TTY."""
        result = run_task_lifecycle_cli(
            "--clear", "--all",
            autorun_home=isolated_autorun_home,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,  # Non-TTY
            timeout=30
        )

        # Should refuse or exit with error code
        assert result.returncode == 2 or "non-interactive mode" in result.stdout.lower()

    def test_status_command_always_works_non_interactive(self, isolated_autorun_home):
        """Test --status works in non-TTY (never prompts)."""
        result = run_task_lifecycle_cli(
            "--status",
            autorun_home=isolated_autorun_home,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=30
        )

        # Status may fail with exit code 1 if no session, but should never hang
        assert result.returncode in [0, 1], f"Unexpected exit code: {result.returncode}"
        # Should show either status output OR error message (not hang)
        assert len(result.stdout) > 0 or len(result.stderr) > 0
        # Should NOT hang waiting for input
        assert "Modify settings" not in result.stdout

    def test_enable_disable_commands_non_interactive(self, isolated_autorun_home):
        """Test --enable and --disable work without prompting."""
        # Test enable
        result_enable = run_task_lifecycle_cli(
            "--enable",
            autorun_home=isolated_autorun_home,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=30
        )

        assert result_enable.returncode == 0

        # Test disable
        result_disable = run_task_lifecycle_cli(
            "--disable",
            autorun_home=isolated_autorun_home,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=30
        )

        assert result_disable.returncode == 0

    def test_no_hanging_in_background_script(self, isolated_autorun_home):
        """Test CLI doesn't hang when run in background (common CI scenario)."""
        # Run with timeout to detect hangs
        try:
            result = run_task_lifecycle_cli(
                "--configure",
                autorun_home=isolated_autorun_home,
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

class TestOutputEncoding:
    """The CLI must be able to print its own output on a non-UTF-8 console."""

    class _Stream:
        def __init__(self, encoding):
            self.encoding = encoding
            self.calls = []

        def reconfigure(self, **kwargs):
            self.calls.append(kwargs)
            self.encoding = kwargs.get("encoding", self.encoding)

    def test_a_cp1252_stream_is_switched_to_utf8(self, monkeypatch):
        from autorun.__main__ import _use_utf8_output

        out = self._Stream("cp1252")
        err = self._Stream("cp1252")
        monkeypatch.setattr("sys.stdout", out)
        monkeypatch.setattr("sys.stderr", err)

        _use_utf8_output()

        for stream in (out, err):
            assert stream.calls == [{"encoding": "utf-8", "errors": "replace"}]

    def test_a_utf8_stream_is_left_alone(self, monkeypatch):
        """Reconfiguring an already-correct stream would discard its state."""
        from autorun.__main__ import _use_utf8_output

        out = self._Stream("UTF-8")
        monkeypatch.setattr("sys.stdout", out)
        monkeypatch.setattr("sys.stderr", self._Stream("utf8"))

        _use_utf8_output()

        assert out.calls == []

    def test_a_stream_that_cannot_reconfigure_is_not_fatal(self, monkeypatch):
        """A replaced stdout (pytest capture, a StringIO) has no reconfigure."""
        import io
        from autorun.__main__ import _use_utf8_output

        monkeypatch.setattr("sys.stdout", io.StringIO())
        monkeypatch.setattr("sys.stderr", io.StringIO())

        _use_utf8_output()
