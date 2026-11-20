"""A file autorun holds so it can still write its own state when the disk fills.

On 2026-11-13 the volume holding the session state directory ran out of space.
SQLite reported ``unable to open database file`` (it could not create the
``-wal``/``-shm`` files), every tool-gate hook failed, and the agent that had
filled the disk could not run the commands that would free it. On 2026-11-15 a
second full disk stopped the daemon from starting at all, because creating
``daemon.flock`` failed with ENOSPC.

Both failures are autorun being unable to write a few kilobytes. This module
removes the cause: while the disk has room, autorun keeps ``disk.reserve``
(``disk_reserve_bytes`` of real data, 8 MiB by default). When opening SQLite,
saving JSON state, taking the daemon lock, or starting the IPC server fails
on a nearly full volume, the caller deletes the reserve and retries once.
The daemon's watchdog recreates it after space returns.

``arm`` and ``release_if_exhausted`` never raise and return a bool: the reserve
exists to recover from failure, so it must never be one. Filesystem calls catch
``OSError``; resolving the path or size (config, environment) can fail in other
ways, so each public function also catches ``Exception`` around its whole body.

Limits. The reserve does not cover a write that fails inside an open SQLite
transaction. It frees bytes, not inodes, so a volume out of inodes is not
helped. On a volume with snapshots or transparent compression, deleting it may
free less than its size.
"""

from __future__ import annotations

import errno
import os
import shutil
from pathlib import Path

from . import ipc
from .config import CONFIG
from .logging_utils import get_logger

logger = get_logger(__name__)

RESERVE_NAME = "disk.reserve"

#: The reserve is only created while the volume has this many times its size
#: free, so holding it never contributes to filling the disk.
ARM_FREE_FACTOR = 4

_reported: set[str] = set()


def _report_once(what: str, exc: Exception) -> None:
    """Warn once per distinct failure so a 60-second watchdog cannot flood the log."""
    key = f"{what}:{type(exc).__name__}:{exc}"
    if key not in _reported:
        _reported.add(key)
        logger.warning("Disk reserve %s failed: %s: %s", what, type(exc).__name__, exc)


def reserve_bytes() -> int:
    """Configured size in bytes; 0 turns the reserve off."""
    try:
        return max(0, int(CONFIG.get("disk_reserve_bytes", 0)))
    except (TypeError, ValueError):
        return 0


def reserve_path() -> Path:
    return ipc._get_autorun_config_dir() / RESERVE_NAME


def _free_bytes(directory: Path) -> int | None:
    try:
        return shutil.disk_usage(directory).free
    except OSError:
        return None


def _same_volume(first: Path, second: Path) -> bool:
    try:
        return os.stat(first).st_dev == os.stat(second).st_dev
    except OSError:
        return False


def arm(path: Path | None = None) -> bool:
    """Create the reserve when it is missing and the volume has room to spare.

    Returns True only when this call created it. Never raises.
    """
    try:
        return _arm(path)
    except Exception as exc:  # the reserve must never be the failure
        _report_once("arm", exc)
        return False


def _arm(path: Path | None) -> bool:
    size = reserve_bytes()
    path = path or reserve_path()
    if size <= 0:
        return False
    staging = path.with_name(path.name + ".tmp")
    try:
        if path.exists():
            return False
        free = _free_bytes(path.parent)
        if free is None or free < ARM_FREE_FACTOR * size:
            return False
        with open(staging, "wb") as handle:
            handle.write(bytes(size))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staging, path)
        return True
    except OSError as exc:
        logger.warning("Disk reserve was not created: %s", exc)
        try:
            staging.unlink()
        except OSError:
            pass
        return False


def is_out_of_space(error: BaseException | None) -> bool:
    """Identify native space errors before checking whether the reserve can help.

    ``durable_io`` re-raises a failed directory fsync as an ``OSError`` with a
    message and no errno, keeping the native error as its cause, so the causes
    are examined too.
    """
    # CPython maps ERROR_DISK_FULL (112) to ENOSPC, but
    # ERROR_HANDLE_DISK_FULL (39) falls back to EINVAL.
    for _ in range(8):  # a cause chain is short; the bound stops a cycle
        if error is None:
            return False
        if isinstance(error, OSError) and (
            error.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC))
            or getattr(error, "winerror", None) == 39
        ):
            return True
        error = error.__cause__ or error.__context__
    return False


def release_if_exhausted(failed_near: Path, path: Path | None = None) -> bool:
    """Delete the reserve when the volume holding ``failed_near`` is nearly full.

    ``failed_near`` is a path on the volume where a write just failed.
    Returns True when this call deleted the reserve or its staging file,
    so the caller should retry once. Returns False when the failure is
    not about space (the reserve is kept), the reserve is on another volume, or
    there is none to release (never created, or another process just released
    it; that caller's retry succeeds and this one degrades for one call).
    Never raises.
    """
    try:
        return _release_if_exhausted(failed_near, path)
    except Exception as exc:  # the reserve must never be the failure
        _report_once("release", exc)
        return False


def _release_if_exhausted(failed_near: Path, path: Path | None) -> bool:
    size = reserve_bytes()
    path = path or reserve_path()
    if size <= 0:
        return False
    directory = failed_near if failed_near.is_dir() else failed_near.parent
    free = _free_bytes(directory)
    if free is None or free >= size // 2:
        return False
    if not _same_volume(directory, path.parent):
        return False
    released = False
    for candidate in (path.with_name(path.name + ".tmp"), path):
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("Disk reserve could not remove %s: %s", candidate, exc)
        else:
            released = True
    if released:
        logger.warning("Disk is nearly full (%d bytes free); released disk reserve space "
                       "so autorun can keep writing its state", free)
    return released
