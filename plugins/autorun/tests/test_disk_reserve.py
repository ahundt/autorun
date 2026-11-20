#!/usr/bin/env python3
"""A full disk must not stop autorun from writing its own state.

2026-11-13 17:28: the volume under ~/.claude/sessions filled, SQLite could not
create its -wal/-shm files ("unable to open database file"), and every tool
gate failed while the agent that needed to free space was locked out.
2026-11-15 15:30: a full disk stopped the daemon from starting, because
creating daemon.flock failed with ENOSPC.

Property under test: when autorun's own write fails on a nearly full volume,
it deletes ``disk.reserve`` and retries once, and it never deletes the reserve
for a failure that is not about space. ``test_disk_reserve_on_a_full_volume``
exercises the same property against a real full volume on macOS.
"""

import errno
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from autorun import disk_reserve
from autorun.config import CONFIG
from autorun.session_manager import SessionBackendError, SQLiteStore

MIB = 1024 * 1024


@pytest.fixture
def reserve(tmp_path, monkeypatch):
    """A 1 MiB reserve in a private directory."""
    monkeypatch.setitem(CONFIG, "disk_reserve_bytes", MIB)
    path = tmp_path / "disk.reserve"
    monkeypatch.setattr(disk_reserve, "reserve_path", lambda: path)
    return path


def _free(monkeypatch, nbytes):
    monkeypatch.setattr(disk_reserve.shutil, "disk_usage",
                        lambda _p: shutil._ntuple_diskusage(10 * 1024 * MIB, 0, nbytes))


def test_arm_writes_the_configured_bytes_when_the_volume_has_room(reserve, monkeypatch):
    _free(monkeypatch, 4 * MIB)
    assert disk_reserve.arm() is True
    assert reserve.stat().st_size == MIB
    assert disk_reserve.arm() is False, "an existing reserve is left alone"
    assert not list(reserve.parent.glob("*.tmp")), "staging file removed"


def test_arm_leaves_a_volume_without_room_alone(reserve, monkeypatch):
    _free(monkeypatch, 4 * MIB - 1)
    assert disk_reserve.arm() is False
    assert not reserve.exists(), "holding the reserve must never fill the disk itself"


def test_a_zero_size_turns_the_reserve_off(reserve, monkeypatch):
    monkeypatch.setitem(CONFIG, "disk_reserve_bytes", 0)
    _free(monkeypatch, 1024 * MIB)
    assert disk_reserve.arm() is False
    reserve.write_bytes(b"x")
    _free(monkeypatch, 0)
    assert disk_reserve.release_if_exhausted(reserve.parent) is False
    assert reserve.exists()


def test_arm_never_raises_when_the_write_fails(reserve, monkeypatch):
    _free(monkeypatch, 1024 * MIB)
    monkeypatch.setattr(disk_reserve.os, "fsync", mock.Mock(side_effect=OSError(errno.ENOSPC, "full")))
    assert disk_reserve.arm() is False
    assert not reserve.exists() and not list(reserve.parent.glob("*.tmp"))


def test_release_deletes_the_reserve_only_when_the_volume_is_nearly_full(reserve, monkeypatch):
    reserve.write_bytes(bytes(MIB))
    _free(monkeypatch, MIB)  # roomy: the failure is about something else
    assert disk_reserve.release_if_exhausted(reserve.parent) is False
    assert reserve.exists()
    _free(monkeypatch, 0)
    assert disk_reserve.release_if_exhausted(reserve.parent) is True
    assert not reserve.exists()
    assert disk_reserve.release_if_exhausted(reserve.parent) is False, "nothing left to release"


@pytest.mark.parametrize("armed", [False, True], ids=["staging-only", "both-files"])
def test_release_removes_an_interrupted_staging_file(reserve, monkeypatch, armed):
    staging = reserve.with_suffix(".reserve.tmp")
    staging.write_bytes(bytes(MIB))
    if armed:
        reserve.write_bytes(bytes(MIB))
    _free(monkeypatch, MIB)
    assert disk_reserve.release_if_exhausted(reserve.parent) is False
    assert staging.exists()
    _free(monkeypatch, 0)
    assert disk_reserve.release_if_exhausted(reserve.parent) is True
    assert not staging.exists() and not reserve.exists()


def test_release_ignores_a_failure_on_another_volume(reserve, monkeypatch):
    reserve.write_bytes(bytes(MIB))
    _free(monkeypatch, 0)
    monkeypatch.setattr(disk_reserve, "_same_volume", lambda *_a: False)
    assert disk_reserve.release_if_exhausted(reserve.parent) is False
    assert reserve.exists(), "freeing this volume cannot help the other one"


def test_json_save_releases_the_reserve_and_retries_once(reserve, tmp_path, monkeypatch):
    from autorun import session_manager

    reserve.write_bytes(bytes(MIB))
    _free(monkeypatch, 0)
    store = session_manager._JSONStore(str(tmp_path / "state.json"), str(tmp_path / "state.lock"))
    real_write = session_manager.atomic_write_json
    attempts = []

    def write_needing_space(path, data):
        attempts.append(reserve.exists())
        if reserve.exists():
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_write(path, data)

    monkeypatch.setattr(session_manager, "atomic_write_json", write_needing_space)
    with store.session("s") as state:
        state["saved"] = True
    assert attempts == [True, False]
    assert not reserve.exists()
    with store.session("s") as state:
        assert state["saved"] is True


def _store_needing_space(tmp_path, reserve):
    """A store whose connection opens only once the reserve is gone."""
    store = SQLiteStore(tmp_path / "state.sqlite3")
    calls = []

    def connect_once(_timeout):
        calls.append(reserve.exists())
        if reserve.exists():
            raise SessionBackendError("unable to open database file")
        return "connection"

    store._connect_once = connect_once
    return store, calls


def test_state_connect_releases_the_reserve_and_retries_once(reserve, tmp_path, monkeypatch):
    reserve.write_bytes(bytes(MIB))
    _free(monkeypatch, 0)
    store, calls = _store_needing_space(tmp_path, reserve)
    assert store._connect(1.0) == "connection"
    assert calls == [True, False]
    assert not reserve.exists()


def test_state_connect_without_a_reserve_fails_as_before(reserve, tmp_path, monkeypatch):
    _free(monkeypatch, 0)
    store = SQLiteStore(tmp_path / "state.sqlite3")
    store._connect_once = mock.Mock(side_effect=SessionBackendError("unable to open database file"))
    with pytest.raises(SessionBackendError):
        store._connect(1.0)
    assert store._connect_once.call_count == 1, "no reserve, so no retry"


def test_state_connect_keeps_the_reserve_when_the_failure_is_not_about_space(reserve, tmp_path, monkeypatch):
    reserve.write_bytes(bytes(MIB))
    _free(monkeypatch, 1024 * MIB)
    store = SQLiteStore(tmp_path / "state.sqlite3")
    store._connect_once = mock.Mock(side_effect=SessionBackendError("unable to open database file"))
    with pytest.raises(SessionBackendError):
        store._connect(1.0)
    assert reserve.exists()
    assert store._connect_once.call_count == 1


def test_a_second_failure_after_the_retry_is_raised(reserve, tmp_path, monkeypatch):
    reserve.write_bytes(bytes(MIB))
    _free(monkeypatch, 0)
    store = SQLiteStore(tmp_path / "state.sqlite3")
    store._connect_once = mock.Mock(side_effect=SessionBackendError("still failing"))
    with pytest.raises(SessionBackendError, match="still failing"):
        store._connect(1.0)
    assert store._connect_once.call_count == 2, "exactly one retry"


def test_daemon_lock_releases_the_reserve_when_creating_the_lock_file_fails(reserve, tmp_path, monkeypatch):
    from autorun.core import AutorunDaemon

    reserve.write_bytes(bytes(MIB))
    _free(monkeypatch, 0)
    lock_path = tmp_path / "daemon.lock"
    attempts = []

    class SpaceDependentLock:
        def __init__(self, *_a, **_k):
            pass

        def acquire(self):
            attempts.append(reserve.exists())
            if reserve.exists():
                raise OSError(errno.ENOSPC, "No space left on device")

        def release(self):
            pass

    daemon = AutorunDaemon.__new__(AutorunDaemon)
    daemon._daemon_lock = None
    with mock.patch("autorun.core.LOCK_PATH", lock_path), \
            mock.patch("filelock.FileLock", SpaceDependentLock):
        assert daemon._acquire_daemon_lock() is True
    assert attempts == [True, False]
    assert not reserve.exists()


def test_lock_failure_warning_names_the_errno_and_does_not_blame_nfs(reserve, tmp_path, monkeypatch, caplog):
    import logging

    from autorun.core import AutorunDaemon

    _free(monkeypatch, 10 * MIB)  # not a space failure: the reserve is kept

    class FullLock:
        def __init__(self, *_a, **_k):
            pass

        def acquire(self):
            raise OSError(errno.ENOSPC, "No space left on device")

    daemon = AutorunDaemon.__new__(AutorunDaemon)
    daemon._daemon_lock = None
    monkeypatch.setattr(daemon, "_socket_connect_test", lambda: False)
    with mock.patch("autorun.core.LOCK_PATH", tmp_path / "daemon.lock"), \
            mock.patch("filelock.FileLock", FullLock), caplog.at_level(logging.WARNING):
        daemon._acquire_daemon_lock()
    text = caplog.text
    assert "No space left on device" in text and str(errno.ENOSPC) in text
    assert "(NFS?)" not in text


@pytest.mark.parametrize("error", [OSError(errno.EIO, "I/O error"), RuntimeError("config dir unresolved")])
def test_arm_and_release_never_raise_when_the_reserve_path_cannot_be_resolved(error, monkeypatch):
    """The reserve exists to recover from failure, so it must never be the failure."""
    monkeypatch.setitem(CONFIG, "disk_reserve_bytes", MIB)

    def broken():
        raise error

    monkeypatch.setattr(disk_reserve, "reserve_path", broken)
    assert disk_reserve.arm() is False
    assert disk_reserve.release_if_exhausted(Path("/")) is False


def test_arm_and_release_never_raise_when_the_size_lookup_fails(reserve, monkeypatch):
    def broken():
        raise RuntimeError("config unavailable")

    monkeypatch.setattr(disk_reserve, "reserve_bytes", broken)
    assert disk_reserve.arm() is False
    assert disk_reserve.release_if_exhausted(reserve.parent) is False


def test_the_watchdog_tick_arms_the_reserve(monkeypatch):
    """The entry point must call arm: a correct arm() nobody calls protects nothing."""
    import asyncio

    from autorun import core

    daemon = core.AutorunDaemon.__new__(core.AutorunDaemon)
    daemon.running = True
    daemon._shutdown_event = None
    daemon.active_pids = {os.getpid()}
    daemon.last_activity = 0

    async def tick_now(_awaitable, timeout):
        _awaitable.close()  # a coroutine that was never awaited warns
        raise asyncio.TimeoutError

    calls = []

    def arm_once():
        calls.append(1)
        daemon.running = False

    monkeypatch.setattr(core, "disk_reserve_arm", arm_once)
    monkeypatch.setattr(core.asyncio, "wait_for", tick_now)
    asyncio.run(daemon.watchdog())
    assert calls == [1]


@pytest.mark.parametrize("transport", [None, "unix", "tcp"], ids=["no-error", "unix-enospc", "tcp-enospc"])
def test_daemon_start_arms_the_reserve(reserve, monkeypatch, transport):
    import asyncio
    import threading

    from autorun import command_detection, core

    daemon = core.AutorunDaemon.__new__(core.AutorunDaemon)
    daemon._loop = daemon._shutdown_event = daemon._server = daemon._watchdog_task = None
    armed = threading.Event()
    attempts = []
    if transport:
        reserve.write_bytes(bytes(MIB))
        _free(monkeypatch, 0)
        monkeypatch.setattr(core.ipc, "HAS_UNIX_SOCKETS", transport == "unix")
        for attr in ("SOCKET_PATH", "PORT_FILE", "TOKEN_FILE"):
            monkeypatch.setattr(core.ipc, attr, reserve.parent / attr.lower())

    class Server:
        async def __aenter__(self):
            for _ in range(500):  # the arm call runs on an executor thread
                if armed.is_set():
                    break
                await asyncio.sleep(0.01)
            daemon._shutdown_event.set()

        async def __aexit__(self, *_exc):
            return False

    async def start_server(*_a, **_k):
        attempts.append(reserve.exists())
        if reserve.exists():
            raise OSError(errno.ENOSPC, "No space left on device")
        return Server()

    async def no_watchdog():
        return None

    async def no_stop():
        return None

    monkeypatch.setattr(core, "disk_reserve_arm", armed.set)
    monkeypatch.setattr(core.ipc, "start_server", start_server)
    monkeypatch.setattr(command_detection, "warm_parser", lambda: None)
    monkeypatch.setattr(daemon, "_cleanup_stale_socket", lambda: None)
    monkeypatch.setattr(daemon, "_register_atexit_cleanup", lambda: None)
    monkeypatch.setattr(daemon, "_setup_signal_handlers", lambda: None)
    monkeypatch.setattr(daemon, "watchdog", no_watchdog)
    monkeypatch.setattr(daemon, "async_stop", no_stop)
    asyncio.run(daemon.start())
    assert armed.is_set(), "AutorunDaemon.start() never called disk_reserve arm"
    assert attempts == ([True, False] if transport else [False])
    assert not reserve.exists()


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("hdiutil"),
                    reason="needs a real full volume; macOS can make one without privileges")
def test_disk_reserve_on_a_full_volume(tmp_path):
    """A real ENOSPC: with the reserve a SQLite store opens, without it it does not."""
    root = Path(os.environ.get("TMPDIR", "/tmp")) / f"arfull{os.getpid()}"
    root.mkdir()
    image, mount = root / "v.dmg", root / "mnt"
    try:
        subprocess.run(["hdiutil", "create", "-size", "6m", "-fs", "HFS+", "-volname", "arfull", str(image)],
                       check=True, capture_output=True)
        subprocess.run(["hdiutil", "attach", str(image), "-mountpoint", str(mount), "-nobrowse"],
                       check=True, capture_output=True)
        results = {}
        for label, held in (("with", True), ("without", False)):
            volume = mount / label
            volume.mkdir()
            store = SQLiteStore(volume / "state.sqlite3")
            store.initialize()
            held_path = volume / "disk.reserve"
            if held:
                held_path.write_bytes(bytes(256 * 1024))
            filler = volume / "fill"
            try:
                with open(filler, "wb") as handle:
                    while True:
                        handle.write(bytes(4096))
                        handle.flush()
            except OSError as exc:
                assert exc.errno == errno.ENOSPC
            code = (
                "import sys; from pathlib import Path\n"
                "from unittest import mock\n"
                "from autorun import disk_reserve\n"
                "from autorun.config import CONFIG\n"
                "from autorun.session_manager import SQLiteStore\n"
                "CONFIG['disk_reserve_bytes'] = 256 * 1024\n"
                f"disk_reserve.reserve_path = lambda: Path({str(held_path)!r})\n"
                f"store = SQLiteStore({str(volume / 'state.sqlite3')!r})\n"
                "try:\n"
                "    with store.operation_scope(1.0): pass\n"
                "    print('opened')\n"
                "except Exception as exc:\n"
                "    print('failed', type(exc).__name__)\n")
            ran = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                                 env={**os.environ, "PYTHONPATH": str(Path(disk_reserve.__file__).parents[1])})
            results[label] = ran.stdout.strip()
            filler.unlink()
        assert results == {"with": "opened", "without": "failed SessionBackendError"}, results
    finally:
        subprocess.run(["hdiutil", "detach", str(mount), "-force"], capture_output=True)
        shutil.rmtree(root, ignore_errors=True)
