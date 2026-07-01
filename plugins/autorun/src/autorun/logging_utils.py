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
from logging.handlers import RotatingFileHandler

from . import ipc

LOG_FILE = ipc.AUTORUN_LOG_FILE
DEBUG_ENABLED = os.environ.get('AUTORUN_DEBUG') == '1'


def _has_non_null_handler(logger: logging.Logger) -> bool:
    """Return True when logger already has a real output handler."""
    return any(not isinstance(handler, logging.NullHandler) for handler in logger.handlers)


def configure_file_logging(
    name: str = "autorun",
    *,
    level: int = logging.INFO,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 3,
) -> logging.Logger:
    """Configure rotating file logging for long-running daemon processes.

    Importing autorun must stay side-effect-light: commands like
    ``autorun --version`` should not require write access to ~/.autorun. Daemon
    entry points call this function explicitly when file logging is useful.
    If the log path is unavailable, fall back to a NullHandler instead of
    crashing a hook or metadata command.
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

    try:
        ipc.ensure_config_dir()
        handler = RotatingFileHandler(LOG_FILE, maxBytes=max_bytes, backupCount=backup_count)
    except OSError:
        handler = logging.NullHandler()
        logger.setLevel(logging.CRITICAL + 1)
        if not logger.handlers:
            logger.addHandler(handler)
        return logger

    handler.setFormatter(
        logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    )
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
            # Debug enabled - log to file
            handler = RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=3)
            handler.setFormatter(
                logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
            )
            logger.addHandler(handler)
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
