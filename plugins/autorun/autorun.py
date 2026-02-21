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
        # Use centralized error handling - follows DRY principles
        try:
            # Add src to path for error handling import
            sys.path.insert(0, str(Path(__file__).parent / "src"))
            from clautorun.error_handling import handle_import_error

            if handle_import_error(e):
                sys.exit(1)
            else:
                # Fallback to basic error message for non-clautorun import errors
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
        except ImportError:
            # If even error handling can't be imported, show basic message
            print("=" * 70)
            print("❌ IMPORT ERROR: {}".format(str(e)))
            print("=" * 70)
            print("Install UV and activate virtual environment: source .venv/bin/activate")
            sys.exit(1)
else:
    # Python 2.x - provide helpful error message
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
    print("3. Install UV package manager for proper Python management:")
    print("   curl -LsSf https://astral.sh/uv/install.sh | sh")
    print("   uv venv")
    print("   source .venv/bin/activate")
    print("   uv sync --extra claude-code")
    print("   # For Python 3.10+ (recommended): uv venv --python 3.10")
    print()
    print("=" * 70)
    sys.exit(1)

if __name__ == "__main__":
    main()