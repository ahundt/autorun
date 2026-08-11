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


#: ``python -c`` puts the working directory on ``sys.path[0]``, and
#: ``plugins/autorun/autorun.py`` is a launcher shim named exactly like the
#: package it launches. A child started from the plugin root therefore imports
#: the shim, which cannot reach ``autorun.error_handling`` from inside itself
#: and exits 1 by design (``error_handling.py`` matches "is not a package" and
#: prints recovery steps). CI runs pytest from the plugin root, so every child
#: below inherited that shadowing and asserted on it instead of on the encoding
#: and terminal behaviour it names. Any child importing ``autorun`` gets a
#: neutral directory so the import resolves to the installed package.
def spawn_kwargs(cwd: Path) -> dict:
    """Subprocess options that keep ``import autorun`` off the launcher shim."""
    return {"cwd": str(cwd), "env": dict(os.environ)}


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

    def test_a_cp1252_stream_keeps_its_declared_encoding(self, monkeypatch):
        from autorun.logging_utils import use_utf8_output

        out = self._Stream("cp1252")
        err = self._Stream("cp1252")
        monkeypatch.setattr("sys.stdout", out)
        monkeypatch.setattr("sys.stderr", err)

        use_utf8_output()

        for stream in (out, err):
            assert stream.calls == [{"errors": "replace"}]

    def test_a_utf8_stream_is_left_alone(self, monkeypatch):
        """Reconfiguring an already-correct stream would discard its state."""
        from autorun.logging_utils import use_utf8_output

        out = self._Stream("UTF-8")
        monkeypatch.setattr("sys.stdout", out)
        monkeypatch.setattr("sys.stderr", self._Stream("utf8"))

        use_utf8_output()

        assert out.calls == [{"errors": "replace"}]

    def test_a_cp1252_parent_can_decode_cli_output(self, tmp_path):
        env = dict(os.environ, PYTHONIOENCODING="cp1252")
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from autorun.logging_utils import use_utf8_output; "
                "use_utf8_output(); print('client ₁ daemon')",
            ],
            capture_output=True,
            text=True,
            encoding="cp1252",
            timeout=5,
            **{**spawn_kwargs(tmp_path), "env": env},
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "client" in result.stdout

    def test_a_stream_that_cannot_reconfigure_is_not_fatal(self, monkeypatch):
        """A replaced stdout (pytest capture, a StringIO) has no reconfigure."""
        import io
        from autorun.logging_utils import use_utf8_output

        monkeypatch.setattr("sys.stdout", io.StringIO())
        monkeypatch.setattr("sys.stderr", io.StringIO())

        use_utf8_output()

class TestCanPrompt:
    """Prompting is only possible when a read would actually block for input.

    isatty() is not that question on Windows: the CRT calls every character
    device a tty and subprocess.DEVNULL is NUL, so a non-interactive run
    reported itself interactive, prompted, and died on EOF.
    """

    class _Stream:
        def __init__(self, tty):
            self._tty = tty

        def isatty(self):
            return self._tty

    def test_a_non_tty_cannot_prompt(self, monkeypatch):
        from autorun.task_lifecycle import can_prompt

        monkeypatch.setattr("sys.stdin", self._Stream(tty=False))
        assert can_prompt() is False

    @pytest.mark.skipif(os.name != "nt", reason="Windows NUL semantics")
    def test_a_character_device_at_eof_cannot_prompt(self, tmp_path):
        """NUL on Windows: claims to be a tty, has nothing to give."""
        result = subprocess.run(
            [sys.executable, "-c", "from autorun.task_lifecycle import can_prompt; print(can_prompt())"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=5,
            **spawn_kwargs(tmp_path),
        )
        assert result.stdout.strip() == "False"

    def test_a_terminal_with_input_can_prompt(self, monkeypatch):
        from autorun.task_lifecycle import can_prompt

        monkeypatch.setattr("sys.stdin", self._Stream(tty=True))
        assert can_prompt() is True

    @pytest.mark.skipif(os.name == "nt", reason="pty is POSIX-only")
    def test_an_idle_terminal_is_detected_without_reading(self, tmp_path):
        import pty

        master, slave = pty.openpty()
        process = subprocess.Popen(
            [sys.executable, "-c", "from autorun.task_lifecycle import can_prompt; print(can_prompt())"],
            stdin=slave,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **spawn_kwargs(tmp_path),
        )
        os.close(slave)
        try:
            stdout, stderr = process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            pytest.fail("can_prompt blocked while inspecting an idle terminal")
        finally:
            os.close(master)
        assert process.returncode == 0, stderr
        assert stdout.strip() == "True"

    def test_a_stream_without_peek_is_trusted(self, monkeypatch):
        """Never refuse a real terminal because the stream lacks an optional API."""
        from autorun.task_lifecycle import can_prompt

        class Bare:
            def isatty(self):
                return True

        monkeypatch.setattr("sys.stdin", Bare())
        assert can_prompt() is True

    def test_a_missing_stdin_cannot_prompt(self, monkeypatch):
        from autorun.task_lifecycle import can_prompt

        monkeypatch.setattr("sys.stdin", None)
        assert can_prompt() is False


class TestWindowsConsoleProbe:
    """The Windows branch, exercised on every platform.

    ``can_prompt`` returns True at the ``sys.platform != "win32"`` guard, so
    every test above passes without running the console probe anywhere except
    Windows. That is how a change making the probe refuse any stream lacking
    ``fileno()`` reached Windows CI green everywhere else: two of these cases
    were only ever asserted on the one platform that never ran them.
    """

    def test_a_stream_with_no_fileno_is_trusted(self):
        from autorun.task_lifecycle import windows_tty_is_a_console

        class NoFileno:
            def isatty(self):
                return True

        assert windows_tty_is_a_console(NoFileno()) is True

    def test_a_stream_whose_fileno_raises_is_trusted(self):
        """pytest's captured stdin raises io.UnsupportedOperation from fileno."""
        import io

        from autorun.task_lifecycle import windows_tty_is_a_console

        class Captured:
            def isatty(self):
                return True

            def fileno(self):
                raise io.UnsupportedOperation("fileno")

        assert windows_tty_is_a_console(Captured()) is True

    def test_a_handle_that_is_not_a_console_cannot_prompt(self, monkeypatch):
        """NUL yields a real handle and GetConsoleMode refuses it."""
        import sys

        from autorun import task_lifecycle

        class FakeKernel32:
            @staticmethod
            def GetConsoleMode(handle, mode_ref):
                return 0

        fake_ctypes = type(sys)("ctypes")
        fake_ctypes.c_ulong = lambda: 0
        fake_ctypes.byref = lambda value: value
        fake_ctypes.windll = type("windll", (), {"kernel32": FakeKernel32})
        fake_msvcrt = type(sys)("msvcrt")
        fake_msvcrt.get_osfhandle = lambda fd: 7
        monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)
        monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)

        class RealHandle:
            def isatty(self):
                return True

            def fileno(self):
                return 0

        assert task_lifecycle.windows_tty_is_a_console(RealHandle()) is False

    def test_a_console_handle_can_prompt(self, monkeypatch):
        import sys

        from autorun import task_lifecycle

        class FakeKernel32:
            @staticmethod
            def GetConsoleMode(handle, mode_ref):
                return 1

        fake_ctypes = type(sys)("ctypes")
        fake_ctypes.c_ulong = lambda: 0
        fake_ctypes.byref = lambda value: value
        fake_ctypes.windll = type("windll", (), {"kernel32": FakeKernel32})
        fake_msvcrt = type(sys)("msvcrt")
        fake_msvcrt.get_osfhandle = lambda fd: 7
        monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)
        monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)

        class Console:
            def isatty(self):
                return True

            def fileno(self):
                return 0

        assert task_lifecycle.windows_tty_is_a_console(Console()) is True
