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
"""
import asyncio
import signal
from .core import app, ClautorunDaemon, SOCKET_PATH, logger


def main():
    """
    Daemon entry point with signal handling.

    Registers SIGTERM/SIGINT handlers for graceful shutdown.
    Ensures socket cleanup on exit.
    """
    daemon = None

    # Handle signals for graceful shutdown
    def signal_handler(sig, frame):
        nonlocal daemon
        if daemon is not None:
            logger.info(f"Received signal {sig}")
            daemon.stop()

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Import plugins to register handlers (deferred to avoid circular imports)
    try:
        from . import plugins  # noqa: F401
    except ImportError as e:
        logger.warning(f"plugins.py not found or import error: {e} - daemon has no handlers")

    daemon = ClautorunDaemon(app)

    try:
        asyncio.run(daemon.start())
    except KeyboardInterrupt:
        pass
    finally:
        # Ensure socket cleanup
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()


if __name__ == "__main__":
    main()
