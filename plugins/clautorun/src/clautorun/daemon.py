#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright 2025 Andrew Hundt <ATHundt@gmail.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
Clautorun v0.7 Daemon Entry Point

Run with: python -m clautorun.daemon

Shutdown Mechanisms:
- SIGTERM/SIGINT: Handled via loop.add_signal_handler() for async safety
- Idle timeout: Watchdog shuts down after 30min of inactivity
- atexit: Fallback cleanup for unexpected termination

The daemon sets up signal handlers internally via ClautorunDaemon._setup_signal_handlers(),
so no external signal handling is needed here.
"""
import asyncio
import sys
from .core import app, ClautorunDaemon, SOCKET_PATH, LOCK_PATH, logger


def main():
    """
    Daemon entry point.

    Signal handling is done internally by ClautorunDaemon via:
    - loop.add_signal_handler() for SIGTERM, SIGINT, SIGHUP
    - atexit registration for cleanup on unexpected exit
    - Shutdown event for coordinated async termination

    The main function just needs to:
    1. Import plugins to register handlers
    2. Create and start the daemon
    3. Handle any startup errors
    """
    # Import plugins to register handlers (deferred to avoid circular imports)
    try:
        from . import plugins  # noqa: F401
        logger.info("Plugins loaded successfully")
    except ImportError as e:
        logger.warning(f"plugins.py not found or import error: {e} - daemon has no handlers")

    daemon = ClautorunDaemon(app)

    try:
        asyncio.run(daemon.start())
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as e:
        logger.error(f"Daemon error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Safety cleanup - daemon.async_stop() should have already cleaned up,
        # but ensure files are removed if something went wrong
        try:
            if SOCKET_PATH.exists():
                SOCKET_PATH.unlink()
                logger.debug("Final cleanup: removed socket")
            if LOCK_PATH.exists():
                LOCK_PATH.unlink()
                logger.debug("Final cleanup: removed lock")
        except OSError as e:
            logger.warning(f"Final cleanup error: {e}")

    logger.info("Daemon exited")


if __name__ == "__main__":
    main()
