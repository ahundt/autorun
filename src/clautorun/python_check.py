#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Python version checking utilities for clautorun
"""

import sys

# Import centralized error handling for consistency
try:
    from .error_handling import show_comprehensive_uv_error
except ImportError:
    def show_comprehensive_uv_error(error_type="IMPORT ERROR", error_message="Module structure issue detected"):
        """Fallback UV error message"""
        print("=" * 70)
        print(f"❌ {error_type}: {error_message}")
        print("=" * 70)
        print("UV environment not properly configured. Install UV and activate environment.")


def check_python_version():
    """
    Check Python version and provide helpful error messages for incompatible versions.

    Returns:
        bool: True if Python version is compatible, False otherwise
    """
    # Check for Python 2.x - critical error
    if sys.version_info[0] < 3:
        print("=" * 70)
        print("❌ PYTHON VERSION ERROR: clautorun requires Python 3.0 or higher (3.10+ preferred)")
        print("=" * 70)
        print()
        print("You are using Python {}.{} which is incompatible.".format(
            sys.version_info[0], sys.version_info[1]))
        print()
        print("🔧 SOLUTIONS:")
        print("1. Use python3 explicitly:")
        print("   python3 -m clautorun install")
        print()
        print("2. Activate your UV virtual environment:")
        print("   source .venv/bin/activate")
        print("   python -m clautorun install")
        print()
        print("3. Update your system default (if you have admin rights):")
        print("   ln -sf /usr/bin/python3 /usr/local/bin/python")
        print()
        print("4. Install UV package manager for proper Python management:")
        print("   curl -LsSf https://astral.sh/uv/install.sh | sh")
        print("   uv venv")
        print("   source .venv/bin/activate")
        print("   uv sync --extra claude-code")
        print("   # For Python 3.10+ (recommended): uv venv --python 3.10")
        print()
        print("=" * 70)
        print("For comprehensive troubleshooting, run:")
        print("   python3 -c \"from clautorun.error_handling import show_uv_environment_status; show_uv_environment_status()\"")
        print("=" * 70)
        return False

    # Check for Python 3.0-3.9 - warning but allow usage
    if sys.version_info < (3, 10):
        print("=" * 70)
        print("⚠️  PYTHON VERSION WARNING: Python 3.10+ preferred for full compatibility")
        print("=" * 70)
        print()
        print("You are using Python {}.{}.{}.".format(
            sys.version_info[0], sys.version_info[1], sys.version_info[2]))
        print("clautorun requires Python 3.10+ for full compatibility.")
        print()
        print("🔧 RECOMMENDED SOLUTIONS:")
        print("1. Use UV with Python 3.10+:")
        print("   uv venv --python 3.10")
        print("   source .venv/bin/activate")
        print("   uv sync --extra claude-code")
        print()
        print("2. Install Python 3.10+ and activate virtual environment:")
        print("   python3.10 -m venv .venv")
        print("   source .venv/bin/activate")
        print()
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