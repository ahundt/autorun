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
from pathlib import Path

from . import ipc

LOG_FILE = ipc.AUTORUN_LOG_FILE
DEBUG_ENABLED = os.environ.get('AUTORUN_DEBUG') == '1'


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
            handler = logging.FileHandler(LOG_FILE)
            handler.setFormatter(
                logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
            )
            logger.addHandler(handler)
            logger.setLevel(logging.DEBUG)
        else:
            # Debug disabled - add null handler to prevent default stderr handler
            logger.addHandler(logging.NullHandler())
            logger.setLevel(logging.CRITICAL + 1)  # Effectively disabled

    return logger
