#!/usr/bin/env python3
"""Logging must degrade silently when the disk it writes to is exhausted.

`logging_utils` opens with "CRITICAL: Never writes to stdout/stderr to avoid
breaking Claude Code hooks. Any stderr output causes 'hook error' and silently
disables all protections." That is the property under test here, in the one
circumstance that makes the logging system itself break it: a write failure.

`logging.Handler.handleError` prints a traceback to `sys.stderr` whenever
`logging.raiseExceptions` is true, which is Python's default and which nothing
in this package changes. So on a full disk the safety system does not merely
lose its log -- it turns itself off, while the session still looks healthy.
"""

import errno
import logging
from pathlib import Path

import pytest

from autorun import logging_utils
from autorun.config import CONFIG

#: Construction sites allowed to name the stdlib rotating handler: the tolerant
#: subclass's own ``class`` statement, and uses of that subclass.
_TOLERANT = "_ExhaustionTolerantRotatingFileHandler("


class _ExhaustedStream:
    """A stream whose every write fails the way a full disk does."""

    def __init__(self):
        self.closed = False

    def write(self, _text):
        raise OSError(errno.ENOSPC, "No space left on device")

    def flush(self):
        raise OSError(errno.ENOSPC, "No space left on device")

    def close(self):
        self.closed = True


@pytest.fixture
def isolated_log(tmp_path, monkeypatch):
    """Point the module's log file inside tmp_path for one test."""
    log_path = tmp_path / "daemon.log"
    monkeypatch.setattr(logging_utils, "LOG_FILE", log_path)
    return log_path


def _fresh_logger(name):
    """A logger with no inherited handlers, removed again after use."""
    logger = logging.getLogger(name)
    logger.handlers = []
    return logger


def test_a_full_disk_during_emit_never_writes_to_stderr(isolated_log, capsys):
    """The whole point of the module, in the case that breaks it.

    Reproduces ENOSPC at the stream rather than mocking `handleError`, so the
    real `StreamHandler.emit` -> `handleError` path runs. Any stderr byte here
    is a disabled hook in production.
    """
    _fresh_logger("autorun_enospc_emit")
    logger = logging_utils.configure_file_logging("autorun_enospc_emit")
    handler = next(
        h for h in logger.handlers if not isinstance(h, logging.NullHandler)
    )
    handler.stream = _ExhaustedStream()

    logger.error("a message that cannot be written")

    captured = capsys.readouterr()
    assert captured.err == "", (
        "a full disk made the logging system write to stderr, which Claude Code "
        f"treats as a hook error and which disables every protection: {captured.err!r}"
    )
    assert captured.out == ""


def test_get_logger_survives_a_log_path_that_cannot_be_opened(tmp_path, monkeypatch, capsys):
    """`get_logger` is called at module scope, so it must not raise on ENOSPC.

    `configure_file_logging` already falls back to a NullHandler when the path
    is unavailable and says why in its docstring. `get_logger` constructs the
    same handler with no such guard, and a raise there happens during
    `import autorun...` inside a hook -- the unimportable-runtime state that
    hook_entry.py treats as unrecoverable.
    """
    unwritable = tmp_path / "missing-parent" / "nested" / "daemon.log"
    monkeypatch.setattr(logging_utils, "LOG_FILE", unwritable)
    monkeypatch.setattr(logging_utils, "DEBUG_ENABLED", True)
    _fresh_logger("autorun_unopenable_path")

    logger = logging_utils.get_logger("autorun_unopenable_path")

    logger.error("still must not raise or print")
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


def test_no_source_file_builds_a_rotating_handler_outside_the_one_builder():
    """Spec check: constrain the regression class, not one call site.

    The behavioral tests above prove the two current entry points are safe. They
    say nothing about a third one added later, and a raw ``RotatingFileHandler``
    reinstates the stderr-on-full-disk path silently -- nothing fails, the log
    just starts writing tracebacks to stderr the first time a disk fills, which
    is when every hook stops working.

    REQUIREMENT for future edits: construct
    ``_ExhaustionTolerantRotatingFileHandler`` (normally via
    ``logging_utils.build_rotating_handler``), never the stdlib class directly.
    If a genuinely different handler is needed, give it the same ``handleError``
    override and extend the allowance here deliberately.
    """
    roots = [
        Path(logging_utils.__file__).resolve().parent,
        Path(__file__).resolve().parents[1] / "hooks",
    ]
    offenders = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                stripped = line.strip()
                if "RotatingFileHandler(" not in stripped:
                    continue
                if stripped.startswith(f"class {_TOLERANT}"):
                    continue
                if _TOLERANT in stripped:
                    continue
                offenders.append(f"{path}:{number}: {stripped}")

    assert not offenders, (
        "a raw stdlib RotatingFileHandler is constructed here; on a full disk "
        "its handleError writes a traceback to stderr, and any stderr from a "
        "hook makes Claude Code discard the hook response and silently disable "
        "every autorun protection. Use "
        "logging_utils.build_rotating_handler instead:\n  "
        + "\n  ".join(offenders)
    )


def test_rotation_limits_are_config_backed_and_shared_by_both_entry_points():
    """One ceiling per artifact, in CONFIG, not two copies of a literal.

    `configure_file_logging` carried `5 * 1024 * 1024` and `3` as signature
    defaults and `get_logger` repeated the same two literals, so the two could
    drift and neither was tunable beside the other state ceilings such as
    `state_journal_size_limit_bytes`.
    """
    assert CONFIG["log_file_max_bytes"] > 0
    assert CONFIG["log_file_backup_count"] >= 1
    assert logging_utils.LOG_MAX_BYTES == CONFIG["log_file_max_bytes"]
    assert logging_utils.LOG_BACKUP_COUNT == CONFIG["log_file_backup_count"]


def test_both_entry_points_build_the_same_bounded_handler(isolated_log, monkeypatch):
    """`get_logger` and `configure_file_logging` must not diverge on limits."""
    monkeypatch.setattr(logging_utils, "DEBUG_ENABLED", True)
    _fresh_logger("autorun_limits_a")
    _fresh_logger("autorun_limits_b")

    configured = logging_utils.configure_file_logging("autorun_limits_a")
    fetched = logging_utils.get_logger("autorun_limits_b")

    def _rotating(logger):
        return next(
            h for h in logger.handlers if not isinstance(h, logging.NullHandler)
        )

    for handler in (_rotating(configured), _rotating(fetched)):
        assert handler.maxBytes == CONFIG["log_file_max_bytes"]
        assert handler.backupCount == CONFIG["log_file_backup_count"]
