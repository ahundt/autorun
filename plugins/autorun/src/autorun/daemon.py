#!/usr/bin/env python3
"""Autorun daemon entry point.

The package installer owns dependency resolution. Daemon startup never installs
packages or waits for a background package manager: a missing dependency is a
visible installation error, not a partially active safety daemon.
"""

from __future__ import annotations

import asyncio

from .core import AutorunDaemon, app, logger
from .logging_utils import configure_file_logging


def main() -> None:
    """Load handlers and run one daemon until shutdown."""
    configure_file_logging("autorun")

    from autorun import __build_time__, __commit__, __version__

    logger.info(f"=== autorun Daemon v{__version__} starting ===")
    logger.info(f"Commit: {__commit__}")
    logger.info(f"Build Time: {__build_time__}")

    try:
        from . import plugins  # noqa: F401
    except ImportError as error:
        logger.error(
            "Plugin import failed: %s. Reinstall autorun before daemon startup.",
            error,
        )
        raise SystemExit(1) from error
    logger.info("Plugins loaded successfully")

    # Build the shell parser now. It is lazy so a one-shot hook process pays
    # only when it parses, but here the first PreToolUse after every start
    # would pay ~0.6 s of LALR table construction inside its dispatch budget.
    from .command_detection import _bashlex

    _bashlex()

    daemon = AutorunDaemon(app)
    try:
        asyncio.run(daemon.start())
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as error:
        logger.error(f"Daemon error: {error}", exc_info=True)
        raise SystemExit(1) from error
    finally:
        # A no-op unless this process still holds the lifecycle lock, so a
        # concurrent startup loser leaves the winner's files alone.
        try:
            daemon._cleanup_files()
        except OSError as error:
            logger.warning(f"Final cleanup error: {error}")

    logger.info("Daemon exited")


if __name__ == "__main__":
    main()
