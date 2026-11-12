"""AUTORUN_HOME is owner-only (0700) whichever entry point creates it first.

The daemon socket, its port token, session state and the hook debug log live
there. `import autorun` used to create it 0700 as a side effect; once that
chain was gone the installer, the restart lock and the hook's debug log each
created it with the umask's mode (0755 here), and nothing tightened an
existing one. `ipc.ensure_config_dir` is the owner; `hooks/hook_entry.py`,
which cannot import autorun, mirrors it.

Modes do not apply on Windows, so the mode assertions are conditional there.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from autorun import ipc

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
HOOK_ENTRY = PLUGIN_ROOT / "hooks" / "hook_entry.py"
MODES_APPLY = os.name != "nt"


def _hook_entry_ensure():
    spec = importlib.util.spec_from_file_location("hook_entry_mode_test", HOOK_ENTRY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._ensure_private_dir


#: The owner and the hook's stdlib mirror of it, held to the same result.
ENSURERS = {"ipc": lambda: ipc.ensure_config_dir, "hook_entry": _hook_entry_ensure}


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _assert_private(path: Path) -> None:
    assert path.is_dir(), path
    if MODES_APPLY:
        assert _mode(path) == 0o700, oct(_mode(path))


@pytest.mark.parametrize("existing", [None, 0o755, 0o750, 0o777])
@pytest.mark.parametrize("owner", sorted(ENSURERS))
def test_ensure_config_dir_creates_or_tightens_to_0700(tmp_path, owner, existing):
    home = tmp_path / "ar"
    if existing is not None:
        home.mkdir()
        os.chmod(home, existing)
    assert ENSURERS[owner]()(home) == home
    _assert_private(home)


@pytest.mark.parametrize("owner", sorted(ENSURERS))
def test_ensure_config_dir_never_loosens(tmp_path, owner):
    home = tmp_path / "ar"
    home.mkdir()
    os.chmod(home, 0o500)
    try:
        ENSURERS[owner]()(home)
        if MODES_APPLY:
            assert _mode(home) == 0o500, oct(_mode(home))
    finally:
        os.chmod(home, 0o700)


@pytest.mark.parametrize("owner", sorted(ENSURERS))
def test_ensure_config_dir_leaves_another_users_directory_alone(tmp_path, monkeypatch, owner):
    home = tmp_path / "ar"
    home.mkdir()
    os.chmod(home, 0o755)
    ensure = ENSURERS[owner]()
    if MODES_APPLY:
        monkeypatch.setattr(os, "getuid", lambda: home.stat().st_uid + 1)
    ensure(home)
    if MODES_APPLY:
        assert _mode(home) == 0o755, oct(_mode(home))


def test_install_creates_autorun_home_0700(tmp_path, monkeypatch):
    from autorun.installer import discovery, orchestrate

    home = tmp_path / "home"
    home.mkdir()
    autorun_home = tmp_path / "ar"
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("AUTORUN_HOME", str(autorun_home))

    def record(argv):
        return subprocess.CompletedProcess(argv, 0, "", "")

    root = discovery.marketplace_root(PLUGIN_ROOT / "src" / "autorun" / "installer" / "orchestrate.py")
    with discovery.redirected_home(home):
        orchestrate.install(
            marketplace_root=root, plugins=["ar"], home=home, run_command=record,
            available=(), settings={"skill_placement": {"": "auto"}},
        )
    _assert_private(autorun_home)


ENTRY_POINTS = {
    "status": [sys.executable, "-m", "autorun", "--status"],
    "restart-daemon": [sys.executable, "-m", "autorun", "--restart-daemon"],
    "task": [sys.executable, "-m", "autorun", "task", "status"],
    "hook": [sys.executable, str(HOOK_ENTRY)],
}


@pytest.mark.parametrize("existing", [None, 0o755])
@pytest.mark.parametrize("entry", sorted(ENTRY_POINTS))
def test_each_entry_point_leaves_autorun_home_0700(entry, existing):
    # Under the worker's runtime root: short enough for the daemon socket, and
    # pytest_sessionfinish stops any daemon whose AUTORUN_HOME is inside it.
    base = Path(tempfile.mkdtemp(prefix="mode_", dir=os.environ.get("AUTORUN_TEST_RUNTIME_DIR")))
    autorun_home = base / "ar"
    if existing is not None:
        autorun_home.mkdir()
        os.chmod(autorun_home, existing)
    user_home = base / "home"
    user_home.mkdir()
    env = dict(os.environ, AUTORUN_HOME=str(autorun_home), HOME=str(user_home), USERPROFILE=str(user_home))
    env.pop("XDG_CONFIG_HOME", None)
    payload = json.dumps({"hook_event_name": "SessionStart", "session_id": f"mode-{entry}"})
    subprocess.run(
        ENTRY_POINTS[entry], input=payload if entry == "hook" else "", env=env,
        cwd=str(base), capture_output=True, text=True, timeout=120,
    )
    _assert_private(autorun_home)
