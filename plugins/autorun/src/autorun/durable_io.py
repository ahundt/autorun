"""Small crash-durable publication primitives shared by state features."""
from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable


@contextlib.contextmanager
def _owned_descriptor(path: Path, flags: int):
    """Own one raw file descriptor for exactly one scope."""
    try:
        descriptor = os.open(str(path), flags)
    except OSError as exc:
        raise OSError(f"Could not open directory for fsync {path}: {exc}") from exc
    active_error = None
    try:
        yield descriptor
    except BaseException as exc:
        active_error = exc
        raise
    finally:
        try:
            os.close(descriptor)
        except OSError as close_error:
            if active_error is not None:
                raise active_error from close_error
            raise OSError(
                f"Could not close directory descriptor for {path}: {close_error}"
            ) from close_error


def sync_directory(directory: Path) -> None:
    """Fsync a published directory entry or report that durability is unknown."""
    # Windows cannot open a directory through ``os.open`` for ``os.fsync``.
    # Its native directory handle requires CreateFileW with
    # FILE_FLAG_BACKUP_SEMANTICS, and FlushFileBuffers is not documented for
    # directory handles. Keep the portable file fsync + atomic replace there;
    # do not turn every Windows state write into a guaranteed failure.
    if sys.platform == "win32":
        return
    with _owned_descriptor(directory, os.O_RDONLY) as descriptor:
        try:
            os.fsync(descriptor)
        except OSError as exc:
            raise OSError(f"Could not fsync directory {directory}: {exc}") from exc


def sync_file(path: Path) -> None:
    """Flush a completed regular file and its directory entry."""
    # Windows rejects ``fsync`` on a read-only descriptor (EBADF), while POSIX
    # accepts it.  Keep POSIX read-only support and use a read/write descriptor
    # only where the Windows runtime requires it.
    with path.open("r+b" if sys.platform == "win32" else "rb") as handle:
        os.fsync(handle.fileno())
    sync_directory(path.parent)


def reserve_unique_path(
    candidates: Iterable[Path], *, exhausted_message: str
) -> Path:
    """Atomically reserve the first unused path from ``candidates``.

    Checking ``exists()`` and writing later lets concurrent publishers choose
    the same name. Exclusive creation makes selection and reservation one
    filesystem operation. The caller replaces the empty reservation with its
    durable artifact and removes it if publication fails.
    """
    for candidate in candidates:
        try:
            with open(candidate, "x", encoding="utf-8"):
                pass
            return candidate
        except FileExistsError:
            continue
    raise OSError(exhausted_message)


def atomic_write_text(path: Path, text: str) -> None:
    """Publish one complete, fsynced text file with a same-directory rename.

    The text counterpart of :func:`atomic_write_json`, for user-owned files
    that are read-modify-written rather than regenerated — agent memory files
    such as ``~/.codex/AGENTS.md``. A partial write there loses guidance the
    user wrote, so the staged file is only renamed over the target once it is
    complete and fsynced.
    """
    _atomic_publish(path, lambda handle: handle.write(text))


def atomic_write_json(path: Path, payload: Any, *, sort_keys: bool = False) -> None:
    """Publish one complete, fsynced JSON value with a same-directory rename."""
    _atomic_publish(path, lambda handle: json.dump(payload, handle, sort_keys=sort_keys))


def _atomic_publish(path: Path, emit) -> None:
    """Stage ``emit``'s output beside ``path``, fsync it, then rename it into place.

    Staging in the target's own directory keeps the rename atomic: ``os.replace``
    is only guaranteed within a filesystem. The staged file is removed on any
    failure so a crashed publication never leaves a partial artifact visible.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staged_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.tmp-"
    )
    staged = Path(staged_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            emit(handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staged, path)
        sync_directory(path.parent)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
