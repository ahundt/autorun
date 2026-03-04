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
Python version guard and checking utilities for autorun.

WHY THIS FILE EXISTS
--------------------
AI assistants (and users) frequently invoke `python` or `python3` directly,
which on many systems points to Python 2.7 or a Python 3.x < 3.10 that is
incompatible with autorun. This module provides a clear, actionable error
message instead of a cryptic SyntaxError or ImportError.

REQUIREMENTS AND CONSTRAINTS
-----------------------------
- This file MUST remain parseable by Python 2.7 and Python 3.x < 3.10.
  Do NOT use: f-strings, walrus operators (:=), match statements, or type
  annotations at module/class scope (only function-level annotations where
  guarded by `from __future__ import annotations`).
- The Python 2 error-output path MUST use only ASCII characters: Python 2's
  default stdout encoding is ASCII and emoji/unicode raises UnicodeEncodeError.
- The `try/except (ImportError, SyntaxError, ValueError)` around the relative
  import of show_comprehensive_uv_error is intentional: when this file is
  loaded directly by file path (e.g. by conftest.py to avoid triggering
  autorun/__init__.py which contains Python 3-only syntax), the relative import
  fails with ValueError("Attempted relative import in non-package").

HOW TO USE (one-liner guard)
-----------------------------
From any Python file that must be Python 3.10+:

    # Inside the autorun package (Python 3.10 guaranteed):
    from autorun.python_check import check_and_exit; check_and_exit()

    # From conftest.py or other files that must be Python 2/3 compatible AND
    # cannot import the package (e.g. before __init__.py is parsed):
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src', 'autorun'))
    import python_check  # auto-exits with helpful message if Python < 3.10
    sys.path.pop(0)
    del python_check

AUTO-EXECUTE ON IMPORT
----------------------
This module calls check_and_exit() when sys.version_info < (3, 10) at module
load time. When imported as part of the autorun package (Python 3.10+
guaranteed by the time __init__.py runs), this is a no-op. When imported
directly by file path from Python 2/3 < 3.10, it exits with a helpful message.
"""

import sys

# Import centralized error handling for consistency
try:
    from .error_handling import show_comprehensive_uv_error
except (ImportError, SyntaxError, ValueError):
    # SyntaxError is caught so Python 2 (which can't parse error_handling.py's
    # f-strings) gracefully falls back to this inline function.
    def show_comprehensive_uv_error(error_type="IMPORT ERROR", error_message="Module structure issue detected"):
        """Fallback UV error message — pure Python 2/3 compatible syntax."""
        print("=" * 70)
        print("❌ {}: {}".format(error_type, error_message))
        print("=" * 70)
        print("UV environment not properly configured. Install UV and activate environment.")


def check_python_version():
    """
    Check Python version and provide helpful error messages for incompatible versions.

    Returns:
        bool: True if Python version is compatible, False otherwise
    """
    # Check for Python 2.x - critical error
    # NOTE: No emoji here -- Python 2 stdout is ASCII-only, emoji raises UnicodeEncodeError.
    if sys.version_info[0] < 3:
        print("=" * 70)
        print("PYTHON VERSION ERROR: autorun requires Python 3.10+")
        print("You have Python {}.{} -- use 'uv run' instead of 'python'".format(
            sys.version_info[0], sys.version_info[1]))
        print("=" * 70)
        print("")
        print("Since you are running this code, autorun is already on your system.")
        print("Run from the autorun repo root:")
        print("")
        print("  STEP 1 -- Install as UV tool (one-time setup):")
        print("    cd plugins/autorun && uv tool install --force --editable . && cd ../..")
        print("    autorun --restart-daemon")
        print("")
        print("  STEP 2 -- Always invoke via uv run or the installed autorun command:")
        print("    uv run --project plugins/autorun python -m autorun --status")
        print("    autorun --status")
        print("")
        print("  If uv is not installed:")
        print("    curl -LsSf https://astral.sh/uv/install.sh | sh   # Linux/macOS")
        print("    brew install uv                                     # macOS Homebrew")
        print("")
        print("  GitHub / docs:  https://github.com/ahundt/autorun")
        print("  Local docs:     plugins/autorun/CLAUDE.md")
        print("=" * 70)
        return False

    # Check for Python 3.0-3.9 - error, autorun requires 3.10+
    if sys.version_info < (3, 10):
        print("=" * 70)
        print("❌ PYTHON VERSION ERROR: autorun requires Python 3.10+")
        print("   You have Python {}.{}.{} -- use 'uv run' instead".format(
            sys.version_info[0], sys.version_info[1], sys.version_info[2]))
        print("=" * 70)
        print()
        print("Since you are running this code, autorun is already on your system.")
        print("Run from the autorun repo root:")
        print()
        print("  STEP 1 -- Install as UV tool (one-time setup):")
        print("    cd plugins/autorun && uv tool install --force --editable . && cd ../..")
        print("    autorun --restart-daemon")
        print()
        print("  STEP 2 -- Always invoke via uv run or the installed autorun command:")
        print("    uv run --project plugins/autorun python -m autorun --status")
        print("    autorun --status")
        print()
        print("  GitHub / docs:  https://github.com/ahundt/autorun")
        print("  Local docs:     plugins/autorun/CLAUDE.md")
        print("=" * 70)

    return True


def get_python_version_info():
    """
    Get formatted Python version information.

    Returns:
        str: Formatted Python version string
    """
    return "{}.{}.{}".format(
        sys.version_info[0],
        sys.version_info[1],
        sys.version_info[2]
    )


def is_uv_environment():
    """
    Check if running in a UV virtual environment.

    Returns:
        bool: True if in UV virtual environment
    """
    # Check for UV-specific environment variables or paths
    import os
    venv_path = os.environ.get('VIRTUAL_ENV', '')
    return '.venv' in venv_path or 'uv' in venv_path.lower()


def check_and_exit():
    """Check Python version and sys.exit() with a helpful message if < 3.10.

    One-liner guard for use in entry points, conftest.py, etc.:
        from autorun.python_check import check_and_exit; check_and_exit()

    When imported directly by file path (bypassing the package, as conftest.py
    does to avoid parsing autorun/__init__.py with Python 2), the relative
    import of show_comprehensive_uv_error above falls back to the inline
    version, so this function always works regardless of import method.
    """
    if not check_python_version():
        sys.exit(1)


# Auto-execute when this module is imported directly (e.g. by conftest.py via
# sys.path trick). When imported as part of the autorun package (Python 3.10+
# guaranteed by the time __init__.py imports us), this is a no-op.
if sys.version_info < (3, 10):
    check_and_exit()