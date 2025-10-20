#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
clautorun - Claude Agent SDK Command Interceptor

Intercepts autorun commands before they reach Claude Code, saving tokens and providing instant responses.
"""

import sys

# Check Python version compatibility first (before any imports that require Python 3)
if sys.version_info[0] >= 3:
    # Only import if we have Python 3+
    try:
        from pathlib import Path
        # Add src directory to path
        sys.path.insert(0, str(Path(__file__).parent / "src"))
        from clautorun.python_check import check_python_version
        if not check_python_version():
            sys.exit(1)
        from clautorun.main import main
    except ImportError as e:
        print("=" * 70)
        print("❌ IMPORT ERROR: {}".format(str(e)))
        print("=" * 70)
        print()
        print("This might be a Python version compatibility issue.")
        print("Make sure you're using Python 3.10+ and have activated your UV environment.")
        print()
        print("🔧 SOLUTIONS:")
        print("1. Use python3 explicitly:")
        print("   python3 -m clautorun install")
        print()
        print("2. Activate your UV virtual environment:")
        print("   source .venv/bin/activate")
        print("   python -m clautorun install")
        print()
        print("=" * 70)
        sys.exit(1)
else:
    # Python 2.x - provide helpful error message
    print("=" * 70)
    print("❌ PYTHON VERSION ERROR: clautorun requires Python 3.10 or higher")
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
    print("3. Install UV package manager for proper Python management:")
    print("   curl -LsSf https://astral.sh/uv/install.sh | sh")
    print("   uv venv")
    print("   source .venv/bin/activate")
    print("   uv sync --extra claude-code")
    print()
    print("=" * 70)
    sys.exit(1)

if __name__ == "__main__":
    main()