#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Autorun File-Only Logging Utility.

CRITICAL: Never writes to stdout/stderr to avoid breaking Claude Code hooks.
Any stderr output causes "hook error" and silently disables all protections.

Debug Mode:
    Set AUTORUN_DEBUG=1 environment variable to enable debug logging.
    Without this flag, logging is disabled (no overhead, no file writes).

Usage:
    from autorun.logging_utils import get_logger
    logger = get_logger(__name__)
    logger.info("Message goes to ~/.autorun/daemon.log only when debug enabled")
"""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import ipc
from .config import CONFIG

LOG_FILE = ipc.AUTORUN_LOG_FILE
DEBUG_ENABLED = os.environ.get('AUTORUN_DEBUG') == '1'

#: One ceiling for every entry point below. Previously each carried its own
#: copy of the same two literals, which could drift and neither of which was
#: tunable beside the other artifact ceilings in CONFIG.
LOG_MAX_BYTES = int(CONFIG.get("log_file_max_bytes", 5 * 1024 * 1024))
LOG_BACKUP_COUNT = int(CONFIG.get("log_file_backup_count", 3))


class _ExhaustionTolerantRotatingFileHandler(RotatingFileHandler):
    """A rotating handler that stays silent when its disk is exhausted.

    ``logging.Handler.handleError`` prints a traceback to ``sys.stderr``
    whenever ``logging.raiseExceptions`` is true, which is Python's default.
    For this package that default is not a nuisance, it is a failure mode:
    Claude Code treats ANY stderr from a hook as a hook error, discards that
    hook's response, and every autorun protection silently stops working while
    the session still looks healthy. A full disk would therefore switch the
    safety system off rather than merely cost it a log line.

    Overriding the method is deliberately narrower than setting
    ``logging.raiseExceptions = False``, which is process-global and would
    silence error reporting for handlers this package does not own.

    Covers rollover as well as writes: ``RotatingFileHandler.emit`` performs
    ``doRollover`` inside the try block that routes failures here, so a rename
    that fails degrades to "no logging" instead of raising.

    That rollover case matters more on Windows than on POSIX and needs no
    platform branch to handle. Windows refuses to rename a file another process
    holds open (WinError 32), and several autorun processes share
    ``daemon.log``, so rollover there fails routinely rather than only on a full
    disk — and every one of those failures used to print to stderr.
    """

    def handleError(self, record):  # noqa: D102 - stdlib contract
        # Intentionally empty. See the class docstring: writing here is the
        # exact behavior this class exists to prevent, and there is nowhere
        # else to report to, since the log is what just failed.
        return


def build_rotating_handler(
    level_formatter: str, path: "Path | None" = None
) -> logging.Handler:
    """A bounded, stderr-free file handler, or a NullHandler if unavailable.

    REQUIREMENT: this is the only place in the package that may construct a
    rotating file handler, enforced by
    test_logging_survives_exhaustion.py::
    test_no_source_file_builds_a_rotating_handler_outside_the_one_builder.
    Every caller shares it so limits, and behavior on an unwritable path,
    cannot diverge between them. Three call sites previously built their own:
    two carried duplicate copies of the size literals, and two had no guard at
    all around construction.

    Returning a NullHandler rather than raising is load-bearing. ``get_logger``
    is called at module scope across this package, so a raise here happens
    during ``import autorun...`` inside a hook — the unimportable-runtime state
    ``hook_entry.py`` treats as unrecoverable. Losing the log is survivable;
    losing the import is not.

    ``path`` defaults to the daemon log under AUTORUN_HOME. Pass one only for a
    genuinely separate artifact with its own lifetime.
    """
    target = Path(path) if path is not None else LOG_FILE
    try:
        if path is None:
            ipc.ensure_config_dir()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
        handler = _ExhaustionTolerantRotatingFileHandler(
            target, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT
        )
    except OSError:
        return logging.NullHandler()
    handler.setFormatter(logging.Formatter(level_formatter))
    return handler


def use_utf8_output() -> None:
    """Make stdout and stderr tolerate text their declared encoding cannot carry.

    Windows gives a non-UTF-8 console and pipe encoding (cp1252 on the CI
    runners), and autorun's own status text contains characters it cannot
    represent, so `autorun task clear` died with "'charmap' codec can't encode
    characters in position 0-1". Preserve the stream's advertised encoding so
    a parent using ``text=True`` decodes the same bytes; changing a cp1252 pipe
    to UTF-8 caused its reader to fail with ``UnicodeDecodeError``. Replacing
    only unrepresentable characters keeps the protocol internally consistent.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="replace")
        except (OSError, ValueError):  # pragma: no cover - detached stream
            pass


def _has_non_null_handler(logger: logging.Logger) -> bool:
    """Return True when logger already has a real output handler."""
    return any(not isinstance(handler, logging.NullHandler) for handler in logger.handlers)


def configure_file_logging(
    name: str = "autorun",
    *,
    level: int = logging.INFO,
) -> logging.Logger:
    """Configure rotating file logging for long-running daemon processes.

    Importing autorun must stay side-effect-light: commands like
    ``autorun --version`` should not require write access to ~/.autorun. Daemon
    entry points call this function explicitly when file logging is useful.
    If the log path is unavailable, fall back to a NullHandler instead of
    crashing a hook or metadata command.

    REQUIREMENT: rotation limits are not parameters. They came from CONFIG so
    that this function and ``get_logger`` cannot disagree about how large
    autorun's own log may grow; passing them per call site is what let two
    copies of the same literal drift. Change ``log_file_max_bytes`` /
    ``log_file_backup_count`` in CONFIG instead of reintroducing arguments.
    """
    logger = logging.getLogger(name)

    if _has_non_null_handler(logger):
        logger.setLevel(level)
        logger.propagate = True
        return logger

    logger.handlers = [
        handler for handler in logger.handlers
        if not isinstance(handler, logging.NullHandler)
    ]

    handler = build_rotating_handler('%(asctime)s [%(levelname)s] %(message)s')
    if isinstance(handler, logging.NullHandler):
        logger.setLevel(logging.CRITICAL + 1)
        if not logger.handlers:
            logger.addHandler(handler)
        return logger

    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = True
    return logger


def get_logger(name: str) -> logging.Logger:
    """Get file-only logger (never writes to stdout/stderr).

    Logging is ONLY enabled when AUTORUN_DEBUG=1 environment variable is set.
    When debug is disabled, logger is configured but set to CRITICAL level (effectively disabled).

    Args:
        name: Logger name (typically __name__)

    Returns:
        Logger instance configured for file-only output (only active when DEBUG_ENABLED)

    Example:
        logger = get_logger(__name__)
        logger.info("Hook executed")  # Only logged when AUTORUN_DEBUG=1
        logger.debug("Detailed diagnostic")  # Only when AUTORUN_DEBUG=1
    """
    logger = logging.getLogger(name)

    # Only configure if not already configured (avoid duplicate handlers)
    if not logger.handlers:
        if DEBUG_ENABLED:
            # Debug enabled - log to file. Shares the one builder with
            # configure_file_logging: this call site previously constructed its
            # own handler with no guard, so an unwritable log directory raised
            # here -- and get_logger(__name__) runs at module scope across this
            # package, which turns that into a failed `import autorun...`
            # inside a hook.
            logger.addHandler(
                build_rotating_handler(
                    '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
                )
            )
            logger.setLevel(logging.DEBUG)
            logger.propagate = True
        else:
            # Debug disabled - add null handler to prevent default stderr handler
            logger.addHandler(logging.NullHandler())
            # Do not raise the logger level here. Tests and callers still need
            # warning/error records to be observable through explicit handlers
            # such as pytest caplog, while NullHandler keeps ordinary imports
            # from writing to stderr or daemon.log.
            logger.setLevel(logging.NOTSET)
            logger.propagate = True

    return logger
