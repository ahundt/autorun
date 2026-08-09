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


def _owns_lifecycle_files(daemon: AutorunDaemon) -> bool:
    """Return True when this daemon instance owns socket/lock cleanup."""
    return bool(
        getattr(daemon, "running", False)
        or getattr(daemon, "_daemon_lock", None) is not None
    )


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

    daemon = AutorunDaemon(app)
    try:
        asyncio.run(daemon.start())
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as error:
        logger.error(f"Daemon error: {error}", exc_info=True)
        raise SystemExit(1) from error
    finally:
        # Only the process that acquired the lifecycle lock may remove the
        # shared socket and PID files. A concurrent startup loser owns neither.
        if _owns_lifecycle_files(daemon):
            try:
                daemon._cleanup_files()
            except OSError as error:
                logger.warning(f"Final cleanup error: {error}")
        else:
            logger.debug("Skipping final cleanup; daemon lifecycle files not owned")

    logger.info("Daemon exited")


if __name__ == "__main__":
    main()
