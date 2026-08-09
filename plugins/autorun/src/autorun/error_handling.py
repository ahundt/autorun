#!/usr/bin/env python3

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
Centralized error handling utilities for autorun
Follows DRY principles - use this for all import/module structure errors
Requires Python 3.10 or newer.
"""

import subprocess
import sys
from pathlib import Path


def show_comprehensive_uv_error(error_type="IMPORT ERROR", error_message="Module structure issue detected"):
    """
    Display comprehensive UV-first error message for module import/structure issues.

    This function follows DRY principles by providing a single, reusable
    error message that can be used across all autorun components.

    Args:
        error_type (str): Type of error (e.g., "IMPORT ERROR", "MODULE ERROR")
        error_message (str): Specific error message to display
    """
    print("=" * 70)
    print(f"❌ {error_type}: {error_message}")
    print("=" * 70)
    print()
    print("The autorun module structure is not properly configured.")
    print("This usually happens when the UV environment is not activated.")
    print()
    print("🔧 COMPREHENSIVE SOLUTIONS (UV First):")
    print()
    print("1. CHECK AND INSTALL UV (if needed):")
    print("   # Check if UV is already installed:")
    print("   uv --version")
    print("   # If UV is not installed, install it:")
    print("   curl -LsSf https://astral.sh/uv/install.sh | sh")
    print()
    print("2. INSTALL THE PYTHON TOOL:")
    print("   uv tool install 'git+https://github.com/ahundt/autorun.git#subdirectory=plugins/autorun'")
    print()
    print("3. PUBLISH AUTORUN TO DETECTED HARNESSES:")
    print("   autorun --install")
    print()
    print("4. CHECK INSTALLATION:")
    print("   # Verify plugin is working:")
    print("   autorun --status")
    print()
    print("🔧 ALTERNATIVE SOLUTIONS:")
    print("5. INSTALL THROUGH CLAUDE CODE INSTEAD:")
    print("   claude plugin marketplace add https://github.com/ahundt/autorun.git")
    print("   claude plugin install ar@autorun")
    print()
    print("6. RUN FROM A SOURCE CHECKOUT (development):")
    print("   uv sync --project plugins/autorun")
    print("   uv run --project plugins/autorun autorun --install")
    print()
    print("=" * 70)


def check_uv_environment():
    """
    Check if UV environment is properly configured.

    Returns:
        tuple: (is_available, is_configured, details_dict)
    """
    details = {
        'uv_installed': False,
        'uv_version': None,
        'venv_exists': False,
        'dependencies_synced': False,
        'autorun_available': False
    }

    try:
        result = subprocess.run(
            ["uv", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        uv_output = result.stdout
        returncode = result.returncode

        if returncode == 0:
            details['uv_installed'] = True
            details['uv_version'] = uv_output.strip()

        current_dir = Path.cwd()

        while str(current_dir) != str(current_dir.parent):
            uv_toml = current_dir / "pyproject.toml"
            uv_lock = current_dir / "uv.lock"
            venv_dir = current_dir / ".venv"

            if uv_toml.exists():
                if uv_lock.exists():
                    details['dependencies_synced'] = True
                if venv_dir.exists():
                    details['venv_exists'] = True

                    try:
                        autorun_result = subprocess.run(
                            [
                                "uv",
                                "run",
                                "python",
                                "-c",
                                "import shutil,sys;sys.exit(0 if shutil.which('autorun') else 1)",
                            ],
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        details['autorun_available'] = autorun_result.returncode == 0
                    except (OSError, FileNotFoundError):
                        pass
                break

            current_dir = current_dir.parent

    except (OSError, FileNotFoundError, PermissionError):
        pass

    is_available = details['uv_installed']
    is_configured = details['uv_installed'] and details['venv_exists'] and details['dependencies_synced']

    return is_available, is_configured, details


def handle_import_error(import_error, exit_on_error=True):
    """
    Handle import errors with comprehensive UV solutions.

    This function should be used consistently across all autorun modules
    when handling import-related errors.

    Args:
        import_error (ImportError): The import exception that occurred
        exit_on_error (bool): Whether to call sys.exit(1) after showing error

    Returns:
        bool: True if error was handled, False if it's a different type of error
    """
    error_str = str(import_error)

    # Check for module structure issues
    if "autorun.python_check" in error_str or "is not a package" in error_str:
        show_comprehensive_uv_error("IMPORT ERROR", "autorun module structure issue detected")
        if exit_on_error:
            sys.exit(1)
        return True

    # Check for session manager import issues
    elif "session_manager" in error_str or "session state" in error_str.lower():
        show_comprehensive_uv_error("SESSION MANAGER ERROR", "Session manager module not available")
        if exit_on_error:
            sys.exit(1)
        return True

    # Check for general autorun import issues
    elif "autorun" in error_str and "No module named" in error_str:
        show_comprehensive_uv_error("MODULE ERROR", f"autorun module not found: {error_str}")
        if exit_on_error:
            sys.exit(1)
        return True

    # Not a recognized autorun import error
    return False


def show_uv_environment_status():
    """
    Show current UV environment status for debugging purposes.

    Returns:
        bool: True if UV environment is properly configured
    """
    is_available, is_configured, details = check_uv_environment()

    print("🔍 UV Environment Status:")
    print(f"   UV Installed: {'✅' if details['uv_installed'] else '❌'}")
    if details['uv_version']:
        print(f"   UV Version: {details['uv_version']}")

    print(f"   Virtual Environment: {'✅' if details['venv_exists'] else '❌'}")
    print(f"   Dependencies Synced: {'✅' if details['dependencies_synced'] else '❌'}")
    print(f"   autorun Available: {'✅' if details['autorun_available'] else '❌'}")

    if not is_configured:
        print()
        print("⚠️  UV environment not properly configured")
        print("   Run the following commands:")
        if not details['uv_installed']:
            print("   curl -LsSf https://astral.sh/uv/install.sh | sh")
        if not details['venv_exists']:
            print("   uv venv --python 3.10")
            print("   source .venv/bin/activate")
        if not details['dependencies_synced']:
            print("   uv sync")

    return is_configured
