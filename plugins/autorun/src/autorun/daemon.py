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
Autorun v0.7 Daemon Entry Point

Run with: python -m autorun.daemon

Bootstrap Order (background thread, non-blocking):
1. Install UV via pip (if missing) - UV is 10-100x faster than pip
2. Install autorun CLI via UV (if missing) - enables fast hook path
3. Install bashlex via UV/pip (if missing) - better command parsing

Shutdown Mechanisms:
- SIGTERM/SIGINT: Handled via loop.add_signal_handler() for async safety
- Idle timeout: Watchdog shuts down after 30min of inactivity
- atexit: Fallback cleanup for unexpected termination

The daemon sets up signal handlers internally via AutorunDaemon._setup_signal_handlers(),
so no external signal handling is needed here.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from .core import app, AutorunDaemon, LOCK_PATH, logger
from . import ipc


# =============================================================================
# Bootstrap Helpers (module-level for testability)
# =============================================================================


def _get_pip_command() -> list[str] | None:
    """
    Get the pip command to use for package installation.

    Returns ['pip3', 'install', '--user', '-q'] or ['pip', ...] or None.
    """
    if shutil.which('pip3'):
        return ['pip3', 'install', '--user', '-q']
    elif shutil.which('pip'):
        return ['pip', 'install', '--user', '-q']
    return None


def _get_plugin_root() -> Path | None:
    """
    Get the plugin root directory for local installation.

    Tries CLAUDE_PLUGIN_ROOT env var first, then calculates from file location.
    Returns path to plugin root (containing pyproject.toml), or None.
    """
    # Try environment variable first
    env_root = os.environ.get('CLAUDE_PLUGIN_ROOT')
    if env_root:
        root = Path(env_root)
        if (root / 'pyproject.toml').exists():
            return root

    # Calculate from file location: daemon.py -> autorun -> src -> plugin_root
    try:
        current = Path(__file__).resolve()
        # daemon.py is in src/autorun/, go up to plugin root
        plugin_root = current.parent.parent.parent
        if (plugin_root / 'pyproject.toml').exists():
            return plugin_root
    except Exception:
        pass

    return None


def _ensure_uv() -> bool:
    """
    Install UV via pip if not available.

    UV is 10-100x faster than pip for package installs.
    Returns True if UV is available (was already installed or install succeeded).
    """
    if shutil.which('uv'):
        return True  # UV already available

    pip_cmd = _get_pip_command()
    if not pip_cmd:
        logger.debug("No pip found, cannot install UV")
        return False

    try:
        cmd = pip_cmd + ['uv']
        subprocess.run(cmd, capture_output=True, timeout=120)
        logger.info("Installed UV for fast package management")
        return True
    except subprocess.TimeoutExpired:
        logger.debug("UV install timed out")
    except Exception as e:
        logger.debug(f"UV install failed: {e}")
    return False


def _install_autorun() -> bool:
    """
    Install autorun CLI via UV tool install from local plugin directory.

    This enables the fast path in hook_entry.py (try_cli).
    Skips if autorun is already available in PATH.
    Returns True if autorun is available after this call.
    """
    if shutil.which('autorun'):
        return True  # Already installed

    if not shutil.which('uv'):
        logger.debug("UV not available, cannot install autorun CLI")
        return False

    plugin_root = _get_plugin_root()
    if not plugin_root:
        logger.debug("Cannot find plugin root, skipping autorun CLI install")
        return False

    try:
        # Use 'uv tool install' for global CLI availability
        cmd = ['uv', 'tool', 'install', '--force', str(plugin_root)]
        subprocess.run(cmd, capture_output=True, timeout=120)
        logger.info(f"Installed autorun CLI from {plugin_root}")
        return True
    except subprocess.TimeoutExpired:
        logger.debug("autorun install timed out")
    except Exception as e:
        logger.debug(f"autorun install failed: {e}")
    return False


def _install_bashlex() -> None:
    """
    Install bashlex for better command parsing.

    Bashlex provides AST-based bash command parsing.
    Falls back to shlex (stdlib) if bashlex is unavailable.
    """
    # Skip if already available
    try:
        import bashlex  # noqa: F401
        return
    except ImportError:
        pass

    # Prefer UV (much faster than pip)
    if shutil.which('uv'):
        cmd = ['uv', 'pip', 'install', '-q', 'bashlex']
    else:
        pip_cmd = _get_pip_command()
        if not pip_cmd:
            logger.debug("No package manager found, skipping bashlex install")
            return
        cmd = pip_cmd + ['bashlex']

    try:
        subprocess.run(cmd, capture_output=True, timeout=60)
        logger.info("Installed bashlex for improved command parsing")
    except subprocess.TimeoutExpired:
        logger.debug("bashlex install timed out, using shlex fallback")
    except Exception as e:
        logger.debug(f"bashlex install failed: {e}, using shlex fallback")


def _install_pdf_deps() -> None:
    """
    Install pdf-extractor core dependencies if plugin is present and deps are missing.

    Core deps: pdfplumber, pdfminer.six, PyPDF2, markitdown, tqdm
    Falls back gracefully if plugin not present or installation fails.
    """
    # Skip if pdf-extractor plugin not present
    plugin_root = _get_plugin_root()
    if not plugin_root:
        return
    pdf_dir = plugin_root.parent / "pdf-extractor"
    if not pdf_dir.exists():
        return

    # Check if any core dep is missing (package names differ from import names)
    deps_to_check = ["pdfplumber", "pdfminer", "PyPDF2", "markitdown", "tqdm"]
    missing = []
    for dep in deps_to_check:
        # Special case: pdfminer package is pdfminer.six, import is just pdfminer
        import_name = "pdfminer" if dep == "pdfminer.six" else dep
        try:
            __import__(import_name)
        except ImportError:
            missing.append(dep)

    if not missing:
        return

    # Install via uv pip (prefer UV, fall back to pip)
    pip_names = ["pdfplumber", "pdfminer.six", "PyPDF2", "markitdown", "tqdm"]
    if shutil.which('uv'):
        cmd = ['uv', 'pip', 'install', '--python', sys.executable, '-q'] + pip_names
    else:
        pip_cmd = _get_pip_command()
        if not pip_cmd:
            logger.debug("No package manager found, skipping pdf-extractor deps install")
            return
        cmd = pip_cmd + pip_names

    try:
        subprocess.run(cmd, capture_output=True, timeout=120)
        logger.info("Installed pdf-extractor core dependencies")
    except subprocess.TimeoutExpired:
        logger.debug("pdf-extractor deps install timed out")
    except Exception as e:
        logger.debug(f"pdf-extractor deps install failed: {e}")


# =============================================================================
# Bootstrap Orchestration
# =============================================================================


def _bootstrap_optional_deps() -> None:
    """
    Non-blocking background install of optional dependencies.

    Bootstrap order:
    1. Install UV via pip (if missing) - 10-100x faster package manager
    2. Install autorun CLI (if missing) - enables fast hook path
    3. Install bashlex (if missing) - better command parsing
    4. Install pdf-extractor deps (if plugin present and deps missing)

    Runs in background thread so daemon starts immediately.
    """
    def _install():
        """Bootstrap all optional dependencies in order."""
        _ensure_uv()           # Step 1: UV first (makes subsequent installs faster)
        _install_autorun()   # Step 2: autorun CLI (enables fast hook path)
        _install_bashlex()     # Step 3: bashlex (better command parsing)
        _install_pdf_deps()    # Step 4: pdf-extractor deps (if plugin present)

    # Run in background thread - don't block daemon startup
    thread = threading.Thread(target=_install, daemon=True, name="bootstrap-deps")
    thread.start()


# =============================================================================
# Daemon Entry Point
# =============================================================================


def main():
    """
    Daemon entry point.

    Signal handling is done internally by AutorunDaemon via:
    - loop.add_signal_handler() for SIGTERM, SIGINT, SIGHUP
    - atexit registration for cleanup on unexpected exit
    - Shutdown event for coordinated async termination

    The main function just needs to:
    1. Import plugins to register handlers
    2. Create and start the daemon
    3. Handle any startup errors
    """
    # Bootstrap optional dependencies in background (non-blocking)
    _bootstrap_optional_deps()

    from autorun import __version__, __commit__, __build_time__
    logger.info(f"=== autorun Daemon v{__version__} starting ===")
    logger.info(f"Commit: {__commit__}")
    logger.info(f"Build Time: {__build_time__}")

    # Import plugins to register handlers (deferred to avoid circular imports)
    # May fail on first run if claude-agent-sdk not yet installed by bootstrap
    MAX_BOOTSTRAP_WAIT_SECONDS = 30
    for attempt in range(6):  # 6 attempts × 5s = 30s max wait
        try:
            from . import plugins  # noqa: F401
            logger.info("Plugins loaded successfully")
            break
        except ImportError as e:
            if attempt == 0:
                logger.warning(f"Plugin import failed: {e} — waiting for bootstrap to complete")
            if attempt == 5:
                logger.error(f"Bootstrap timeout after {MAX_BOOTSTRAP_WAIT_SECONDS}s — claude-agent-sdk still missing")
                logger.error("Run 'autorun --install' to install dependencies before first daemon use")
                sys.exit(1)
            time.sleep(5)

    daemon = AutorunDaemon(app)

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
            ipc.cleanup_socket()
            if LOCK_PATH.exists():
                LOCK_PATH.unlink()
                logger.debug("Final cleanup: removed lock")
        except OSError as e:
            logger.warning(f"Final cleanup error: {e}")

    logger.info("Daemon exited")


if __name__ == "__main__":
    main()
