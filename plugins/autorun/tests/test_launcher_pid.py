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

    from autorun.client import LAUNCHER_SHELLS
    from autorun.tmux_utils import is_named_process

    deadline = time.monotonic() + _WAIT_SECONDS
    while not is_named_process(psutil.Process(child.pid).name(), (), LAUNCHER_SHELLS):
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
