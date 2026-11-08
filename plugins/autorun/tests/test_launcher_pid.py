"""A hook's reported launcher is the harness, not the shell that ran the hook.

hooks/hook_entry.py sends os.getppid(). Where a harness runs the hook command
through a shell that does not exec it (Gemini CLI's PowerShell on Windows,
Claude Code's Git Bash, sh -c for a Gemini-family POSIX command), that parent
is a shell that exits with the hook, and plan export then judged a live
session ended.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time

import pytest

from autorun.client import launcher_pid, resolve_launcher_pid

_WAIT_SECONDS = 30


def _shell_child() -> subprocess.Popen:
    """A real shell, child of this test process, that stays alive."""
    sh = shutil.which("sh")
    if sh:
        argv = [sh, "-c", f"sleep {_WAIT_SECONDS}; :"]  # the `:` keeps sh from exec'ing sleep
    elif os.name == "nt":
        argv = ["cmd", "/c", f"ping -n {_WAIT_SECONDS} 127.0.0.1 >nul"]
    else:  # pragma: no cover
        pytest.skip("no shell")
    child = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Popen returns once the child is forked, possibly before it has exec'd
    # the shell; wait until psutil sees the shell's name.
    import psutil

    from autorun.client import HOOK_LAUNCHERS
    from autorun.tmux_utils import is_named_process

    deadline = time.monotonic() + _WAIT_SECONDS
    while not is_named_process(psutil.Process(child.pid).name(), (), HOOK_LAUNCHERS):
        assert time.monotonic() < deadline, "the shell never started"
        time.sleep(0.01)
    return child


def test_a_launcher_shell_resolves_to_the_process_that_started_it():
    shell = _shell_child()
    try:
        assert launcher_pid(shell.pid) == os.getpid()
    finally:
        shell.kill()
        shell.wait()


def test_a_harness_process_is_kept():
    assert launcher_pid(os.getpid()) == os.getpid()


def test_uv_run_retains_the_live_harness_pid(tmp_path):
    import json
    from pathlib import Path
    from types import SimpleNamespace

    import psutil

    from autorun.installer.runtime import UvCommand
    from autorun.plan_export import _plan_session_still_running

    if shutil.which("uv") is None:
        pytest.skip("uv is required for the real launcher chain")
    script = tmp_path / "hook.py"
    script.write_text(
        'import os, json, sys\n'
        'print(json.dumps({"_pid": os.getppid()}), flush=True)\n'
        'sys.stdin.readline()\n', encoding="utf-8",
    )
    command = UvCommand(project=Path(__file__).parents[1], script=script)
    child = subprocess.Popen(command.argv(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        payload = json.loads(child.stdout.readline())
        reported_name = psutil.Process(payload["_pid"]).name()
        resolve_launcher_pid(payload)
    finally:
        child.communicate("\n", timeout=_WAIT_SECONDS)
    assert child.returncode == 0
    assert payload["_pid"] == os.getpid(), reported_name
    ctx = SimpleNamespace(source="startup", session_id="another-session", harness_pid=1)
    assert _plan_session_still_running({"harness_pid": payload["_pid"]}, ctx)


def test_a_vanished_pid_is_kept_as_reported():
    shell = _shell_child()
    shell.kill()
    shell.wait()
    assert launcher_pid(shell.pid) == shell.pid


def test_a_payload_with_birth_evidence_is_left_alone():
    """get_stable_process_identity already walked to the harness."""
    shell = _shell_child()
    try:
        payload = {"_pid": shell.pid, "_pid_started_at_units": 1}
        resolve_launcher_pid(payload)
        assert payload["_pid"] == shell.pid
        payload = {"_pid": shell.pid}
        resolve_launcher_pid(payload)
        assert payload["_pid"] == os.getpid()
    finally:
        shell.kill()
        shell.wait()


def test_the_daemon_and_the_direct_path_both_resolve_the_launcher():
    import inspect

    from autorun import __main__ as entry, core

    assert "resolve_launcher_pid(payload)" in inspect.getsource(core.AutorunDaemon.handle_client)
    assert "resolve_launcher_pid(payload)" in inspect.getsource(entry.run_direct)
