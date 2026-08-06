#!/usr/bin/env python3
"""What an uninstall does besides removing files, and the one thing it keeps.

The walk removes every tree autorun published. Three things remain, and each
was a separate procedure with its own error handling:

``locks``     the zero-byte lock files the publish transaction leaves in
              directories the user owns. Harmless, and unmistakably litter.
``daemon``    the running process. Left alive it keeps serving hooks that no
              longer have code behind them.
``kept``      ``~/.autorun`` — session state, task history and logs. Deleting a
              user's history as a side effect of removing a tool is not
              recoverable, so it stays, and saying so is the point: silently
              leaving a directory behind reads as an oversight rather than a
              decision.

Every step here is non-fatal by construction. An uninstall that cannot reach
the daemon, or cannot delete a lock in a directory that has become read-only,
must still complete — the alternative is a half-removed install and an error the
user cannot act on.

Complexity: O(directories scanned) for locks, bounded by ``depth``; one signal
and one status read for the daemon.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator

from .fs import INSTALL_LOCK_NAME, SKIP_NAMES
from .runtime import Outcome

__all__ = ["Teardown", "remove_locks", "retained_state", "stop_daemon", "teardown"]

#: How deep to look for stray lock files under a config directory. Locks are
#: written beside a published tree, and no publication nests deeper than this.
LOCK_SEARCH_DEPTH = 4


@dataclass(frozen=True, slots=True)
class Teardown:
    """What the non-file part of an uninstall did, in one reportable value."""

    locks: tuple[str, ...] = ()
    daemon: Outcome | None = None
    kept: Path | None = None

    def describe(self) -> Iterator[str]:
        if self.locks:
            yield f"Removed {len(self.locks)} install lock file(s)"
        if self.daemon is not None:
            yield self.daemon.describe()
        if self.kept is not None:
            yield f"Kept {self.kept} — it holds session state, task history and logs."
            yield "Delete it yourself if you want those gone too."


def _walk(root: Path, depth: int) -> Iterator[Path]:
    """Directories under ``root``, no deeper than ``depth``, skipping build junk."""
    if depth < 0 or not root.is_dir() or root.is_symlink():
        return
    yield root
    try:
        children = sorted(root.iterdir())
    except OSError:
        return  # unreadable is not an error to report; there is nothing to do
    for child in children:
        if child.is_dir() and not child.is_symlink() and child.name not in SKIP_NAMES:
            yield from _walk(child, depth - 1)


def remove_locks(roots: Iterable[Path], *, depth: int = LOCK_SEARCH_DEPTH) -> tuple[str, ...]:
    """Delete stray publish locks under each root. Returns what went.

    A lock that cannot be deleted is skipped rather than raised: it is a
    zero-byte file, and failing an uninstall over one would leave the rest of
    the removal undone for no gain.
    """
    removed = []
    for root in roots:
        for directory in _walk(Path(root), depth):
            lock = directory / INSTALL_LOCK_NAME
            if not lock.is_file():
                continue
            try:
                lock.unlink()
            except OSError:
                continue
            removed.append(str(lock))
    return tuple(removed)


def retained_state(state_dir: Path | None) -> Path | None:
    """The directory uninstall deliberately keeps, if it exists.

    Resolved by the caller and passed in, never read from a module constant:
    those bind at import time and report the wrong directory whenever ``HOME``
    or ``AUTORUN_HOME`` changed afterwards, which is exactly what an isolated
    test does.
    """
    return state_dir if state_dir is not None and state_dir.is_dir() else None


def stop_daemon(
    *,
    pid: Callable[[], int | None] | None = None,
    stop: Callable[[int], None] | None = None,
    clean: Callable[[], None] | None = None,
) -> Outcome | None:
    """Stop the daemon and clear what it leaves behind, or say why not.

    Returns None when no daemon is running, which is the common case and not
    worth a line of output. The three callables are injected so a test never
    signals the developer's live daemon; they default to the real ones, imported
    late because importing the daemon module at install time is a cost every
    other path would pay for nothing.
    """
    if pid is None or stop is None or clean is None:
        try:
            from ..restart_daemon import _stop_daemon, cleanup_stale_files, get_daemon_pid
        except ImportError as error:
            return Outcome("daemon", False, f"unavailable: {error}")
        pid = pid or get_daemon_pid
        stop = stop or _stop_daemon
        clean = clean or cleanup_stale_files

    try:
        running = pid()
    except Exception as error:  # a daemon that cannot be queried is not fatal
        return Outcome("daemon", False, f"could not check: {error}")
    if not running:
        return None
    try:
        stop(running)
        clean()
    except Exception as error:
        return Outcome("daemon", False, f"stop failed: {error}")
    return Outcome("daemon", True, "stopped")


def teardown(
    roots: Iterable[Path],
    *,
    state_dir: Path | None = None,
    depth: int = LOCK_SEARCH_DEPTH,
    **daemon: object,
) -> Teardown:
    """Everything an uninstall does after the files are gone, in one call."""
    return Teardown(
        locks=remove_locks(roots, depth=depth),
        daemon=stop_daemon(**daemon),  # type: ignore[arg-type]
        kept=retained_state(state_dir),
    )


def demo() -> None:
    """Self-check: litter goes, history stays, and nothing here can raise."""
    import os
    import stat
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        # Locks are found at depth and removed; other files are untouched.
        nested = root / "a" / "b" / "c"
        nested.mkdir(parents=True)
        for directory in (root, root / "a", nested):
            (directory / INSTALL_LOCK_NAME).write_text("", encoding="utf-8")
        (root / "a" / "keep.md").write_text("theirs\n", encoding="utf-8")

        removed = remove_locks([root])
        assert len(removed) == 3, removed
        assert not list(root.rglob(INSTALL_LOCK_NAME))
        assert (root / "a" / "keep.md").is_file()

        # Deeper than the search goes is left alone rather than walked forever.
        deep = root / "1" / "2" / "3" / "4" / "5" / "6"
        deep.mkdir(parents=True)
        (deep / INSTALL_LOCK_NAME).write_text("", encoding="utf-8")
        assert remove_locks([root], depth=2) == ()

        # A lock that cannot be deleted is skipped, not raised.
        locked = root / "readonly"
        locked.mkdir()
        (locked / INSTALL_LOCK_NAME).write_text("", encoding="utf-8")
        os.chmod(locked, stat.S_IRUSR | stat.S_IXUSR)
        try:
            assert remove_locks([locked]) == ()
        finally:
            os.chmod(locked, 0o755)

        # A root that does not exist is not an error.
        assert remove_locks([root / "nope"]) == ()

        # State is reported when present and silent when absent.
        state = root / ".autorun"
        assert retained_state(state) is None
        state.mkdir()
        assert retained_state(state) == state
        assert retained_state(None) is None

        # No daemon running is None, not a line of output.
        assert stop_daemon(pid=lambda: None, stop=lambda _p: None, clean=lambda: None) is None

        # A running one is stopped and its files cleared.
        calls: list[str] = []
        outcome = stop_daemon(
            pid=lambda: 4242,
            stop=lambda p: calls.append(f"stop {p}"),
            clean=lambda: calls.append("clean"),
        )
        assert outcome is not None and outcome.ok
        assert calls == ["stop 4242", "clean"]

        # Every failure is reported, never raised into the uninstall.
        def explodes(*_args):
            raise RuntimeError("socket gone")

        failed = stop_daemon(pid=lambda: 1, stop=explodes, clean=lambda: None)
        assert failed is not None and not failed.ok and "socket gone" in failed.detail

        unqueryable = stop_daemon(pid=explodes, stop=lambda _p: None, clean=lambda: None)
        assert unqueryable is not None and not unqueryable.ok

        # The whole teardown reports in one value, and says what it kept.
        (root / INSTALL_LOCK_NAME).write_text("", encoding="utf-8")
        result = teardown(
            [root], state_dir=state,
            pid=lambda: None, stop=lambda _p: None, clean=lambda: None,
        )
        assert result.locks and result.kept == state and result.daemon is None
        lines = list(result.describe())
        assert any("install lock" in line for line in lines)
        assert any("session state" in line for line in lines)

    print("installer.teardown: all self-checks passed")


if __name__ == "__main__":
    demo()
