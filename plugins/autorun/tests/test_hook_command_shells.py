"""Hook commands written into a harness's manifest must run in the shell that
harness uses, checked by running them in that shell.

Gemini CLI runs hook commands through PowerShell on Windows
(https://github.com/akitaonrails/ai-memory/issues/1484); a POSIX-quoted
command that starts with a quoted path is a string expression there, printed
and not run, so every hook silently did nothing. Claude Code uses Git Bash
(https://code.claude.com/docs/en/hooks).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from autorun.installer.runtime import render_hook_command

_ECHO = (
    "import json, os, sys; "
    "print(json.dumps({'argv': sys.argv[1:], 'env': os.environ.get('AUTORUN_SHELL_PROBE')}))"
)


def _argv(tmp_path: Path) -> list[str]:
    script = tmp_path / "dir with space" / "it's.py"
    script.parent.mkdir()
    script.write_text(_ECHO, encoding="utf-8")
    return [sys.executable, str(script), "--cli", "gemini", "$HOME", "a;b", "C:\\x y\\z"]


def _check(result: subprocess.CompletedProcess, argv: list[str]) -> None:
    assert result.returncode == 0, result.stderr
    seen = json.loads(result.stdout.strip().splitlines()[-1])
    assert seen == {"argv": argv[2:], "env": "probe value"}


def test_a_posix_hook_command_runs_with_its_exact_arguments(tmp_path):
    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("no POSIX sh")
    argv = _argv(tmp_path)
    command = render_hook_command(argv, {"AUTORUN_SHELL_PROBE": "probe value"}, shell="posix")
    _check(subprocess.run([shell, "-c", command], capture_output=True, text=True, timeout=30), argv)


def test_a_powershell_hook_command_runs_instead_of_being_printed(tmp_path):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("no PowerShell")
    argv = _argv(tmp_path)
    command = render_hook_command(argv, {"AUTORUN_SHELL_PROBE": "probe value"}, shell="powershell")
    # How Gemini CLI invokes a hook on Windows.
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, timeout=60,
    )
    _check(result, argv)


def test_every_hook_harness_declares_its_windows_hook_shell():
    from autorun.platforms import hook_platforms

    for platform in hook_platforms():
        assert platform.windows_hook_shell in {"posix", "powershell", "none"}, platform.name


def test_the_installer_writes_each_harness_its_own_windows_shell_form(monkeypatch):
    """On Windows the Gemini-family extension commands are PowerShell; Claude
    keeps the POSIX form Git Bash runs; off Windows everything is POSIX."""
    import os

    from autorun.installer.runtime import hook_command_shell
    from autorun.platforms import PLATFORMS

    monkeypatch.setattr(os, "name", "nt")
    on_windows = {name: hook_command_shell(PLATFORMS[name]) for name in ("gemini", "qwen", "antigravity", "claude")}
    monkeypatch.undo()
    assert on_windows == {"gemini": "powershell", "qwen": "powershell",
                          "antigravity": "powershell", "claude": "posix"}
    if os.name != "nt":
        assert hook_command_shell(PLATFORMS["gemini"]) == "posix"


@pytest.mark.parametrize("windows", [True, False])
@pytest.mark.parametrize("name", ["qwen", "gemini", "antigravity"])
def test_hook_shell_override_matches_the_rendered_command(monkeypatch, windows, name):
    from dataclasses import replace
    import os

    import autorun
    from autorun.installer.runtime import hook_command_shell
    from autorun.installer.extension import translated_hooks
    from autorun.platforms import PLATFORMS

    template = Path(autorun.__file__).parent / "gemini_template"
    # Custom flavors must inherit the same contract without a name branch.
    platform = replace(PLATFORMS[name], name="custom-review-harness", install_flavor=name)
    with monkeypatch.context() as patch:
        patch.setattr(os, "name", "nt" if windows else "posix")
        shell = hook_command_shell(platform)
        command = render_hook_command([sys.executable, "hook_entry.py"], shell=shell)
        manifest = translated_hooks(template, platform, hook_command=command)
    entries = [
        hook for groups in manifest[platform.hook_protocol.hook_manifest_container_key].values()
        for group in groups for hook in group.get("hooks", [group])
    ]
    assert entries
    for hook in entries:
        assert hook["command"].startswith("& ") == windows
        if windows and name == "qwen":
            assert hook.get("shell") == "powershell", hook
        else:
            assert "shell" not in hook, hook


@pytest.mark.parametrize("layout", [".venv/bin/python", ".venv/Scripts/python.exe"])
def test_the_claude_hook_command_uses_the_venv_interpreter_on_either_layout(tmp_path, layout):
    """hooks/hooks.json checked only .venv/bin/python, so on Windows (where a
    venv's interpreter is .venv/Scripts/python.exe and Claude Code runs the
    command in Git Bash) every hook paid a `uv run` resolve instead. Each
    command in the file is run here against a plugin root with only that layout."""
    shell = shutil.which("bash")
    if shell is None or sys.platform == "win32":
        pytest.skip("runs the POSIX form with a POSIX bash")
    manifest = json.loads((Path(__file__).parents[1] / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    commands = {
        hook["command"]
        for groups in manifest["hooks"].values()
        for group in groups
        for hook in group["hooks"]
    }
    interpreter = tmp_path / layout
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text('#!/bin/sh\necho "VENV_INTERPRETER $@"\n', encoding="utf-8")
    interpreter.chmod(0o755)
    (tmp_path / "hooks").mkdir()
    for command in commands:
        result = subprocess.run(
            [shell, "-c", command], capture_output=True, text=True, timeout=30,
            env={"PATH": "/usr/bin:/bin", "CLAUDE_PLUGIN_ROOT": str(tmp_path)},
        )
        assert result.stdout.startswith("VENV_INTERPRETER "), (command, result.stdout, result.stderr)


def test_restart_daemon_finds_a_windows_layout_venv(tmp_path, monkeypatch):
    """restart_daemon looked only in .venv/bin for autorun and python3/python,
    so on Windows (.venv/Scripts/python.exe) it started the daemon under
    whatever interpreter ran it instead of the project's."""
    from autorun import restart_daemon

    plugin_root = tmp_path / "plugins" / "autorun"
    scripts = plugin_root / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "autorun.exe").write_text("", encoding="utf-8")
    if sys.platform == "win32":
        pytest.skip("the stub interpreter below is a POSIX script")
    # A stub that passes the daemon's dependency import check; a symlink to
    # the test interpreter would lose its venv (pyvenv.cfg is found beside it).
    interpreter = scripts / "python.exe"
    interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    interpreter.chmod(0o755)
    monkeypatch.setattr(restart_daemon.shutil, "which", lambda _name: None)

    assert restart_daemon._daemon_python_for_src(plugin_root / "src") == interpreter


def test_restart_daemon_and_the_hook_wrapper_share_the_venv_layout_tables():
    import importlib.util

    from autorun import restart_daemon

    path = Path(__file__).parents[1] / "hooks" / "hook_entry.py"
    spec = importlib.util.spec_from_file_location("hook_entry_layout_tables", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert restart_daemon.VENV_BIN_DIRS == module._VENV_BIN_DIRS
    assert restart_daemon.AUTORUN_EXE_NAMES == module._AUTORUN_EXE_NAMES


@pytest.mark.parametrize("layout", [("bin", "autorun", "python"), ("Scripts", "autorun.exe", "python.exe")])
def test_try_cli_runs_the_venv_python_directly_on_either_layout(tmp_path, monkeypatch, layout):
    import importlib.util

    path = Path(__file__).parents[1] / "hooks" / "hook_entry.py"
    spec = importlib.util.spec_from_file_location("hook_entry_try_cli_layout", path)
    hook_entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook_entry)
    bin_dir, exe, python = layout
    scripts = tmp_path / ".venv" / bin_dir
    scripts.mkdir(parents=True)
    (scripts / exe).write_text("", encoding="utf-8")
    (scripts / python).write_text("", encoding="utf-8")
    seen = []

    def record(cmd, **_kwargs):
        seen.append(cmd)
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(hook_entry.subprocess, "run", record)
    with pytest.raises(SystemExit):
        hook_entry.try_cli(scripts / exe, "{}", "codex", "SessionStart")
    assert seen[0] == [str(scripts / python), "-m", "autorun"]
