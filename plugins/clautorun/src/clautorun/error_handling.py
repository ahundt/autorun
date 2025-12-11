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
Centralized error handling utilities for clautorun
Follows DRY principles - use this for all import/module structure errors
Compatible with Python 2.7+ and Python 3.x
"""

import sys

# Python 2/3 compatibility
import os
try:
    # Python 3
    from pathlib import Path
    import subprocess
except ImportError:
    # Python 2.7 fallback
    try:
        from pathlib2 import Path
        import subprocess
    except ImportError:
        # If pathlib2 not available, create simple Path fallback
        class Path:
            def __init__(self, path):
                self.path = str(path)
            def exists(self):
                return os.path.exists(self.path)
            def parent(self):
                return Path(os.path.dirname(self.path))
            def __str__(self):
                return self.path
            def __truediv__(self, other):
                return Path(os.path.join(self.path, str(other)))
            def __div__(self, other):  # Python 2 division
                return Path(os.path.join(self.path, str(other)))


def show_comprehensive_uv_error(error_type="IMPORT ERROR", error_message="Module structure issue detected"):
    """
    Display comprehensive UV-first error message for module import/structure issues.

    This function follows DRY principles by providing a single, reusable
    error message that can be used across all clautorun components.

    Args:
        error_type (str): Type of error (e.g., "IMPORT ERROR", "MODULE ERROR")
        error_message (str): Specific error message to display
    """
    print("=" * 70)
    print(f"❌ {error_type}: {error_message}")
    print("=" * 70)
    print()
    print("The clautorun module structure is not properly configured.")
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
    print("2. CREATE AND ACTIVATE UV VIRTUAL ENVIRONMENT:")
    print("   # Create virtual environment (Python 3.10+ preferred for full compatibility):")
    print("   uv venv")
    print("   # Or specify Python version (3.10+ recommended):")
    print("   uv venv --python 3.10")
    print("   # Activate the environment:")
    print("   source .venv/bin/activate")
    print("   # Install dependencies:")
    print("   uv sync --extra claude-code")
    print()
    print("3. INSTALL PLUGIN USING UV:")
    print("   # Method A: Install via UV (recommended)")
    print("   uv run clautorun install")
    print("   # Method B: Manual installation with activated environment")
    print("   source .venv/bin/activate")
    print("   python src/clautorun/install.py install")
    print()
    print("4. CHECK INSTALLATION:")
    print("   # Verify plugin is working:")
    print("   uv run clautorun check")
    print("   # Test plugin functionality:")
    print("   echo '{\"prompt\": \"/afst\", \"session_id\": \"test\"}' | uv run python src/clautorun/claude_code_plugin.py")
    print()
    print("🔧 ALTERNATIVE SOLUTIONS:")
    print("5. INSTALL FROM GITHUB (production):")
    print("   /plugin install https://github.com/ahundt/clautorun.git")
    print()
    print("6. USE EXPLICIT PYTHON PATH (development):")
    print("   PYTHONPATH=/path/to/clautorun/src python3 plugin.py")
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
        'clautorun_available': False
    }

    try:
        # Check if UV is installed - Python 2/3 compatible
        if hasattr(subprocess, 'run'):  # Python 3.5+
            result = subprocess.run(
                ["uv", "--version"],
                capture_output=True,
                text=True,
                timeout=10
            )
            uv_output = result.stdout
            returncode = result.returncode
        else:  # Python 2.7 fallback
            import subprocess as sp
            result = sp.Popen(["uv", "--version"], stdout=sp.PIPE, stderr=sp.PIPE)
            uv_output, error = result.communicate()
            returncode = result.returncode
            if isinstance(uv_output, bytes):
                uv_output = uv_output.decode('utf-8', errors='ignore')

        if returncode == 0:
            details['uv_installed'] = True
            details['uv_version'] = uv_output.strip()

        # Check if we're in a UV project - Python 2/3 compatible
        try:
            current_dir = Path.cwd()
        except AttributeError:
            # Python 2.7 fallback
            current_dir = Path(os.getcwd())

        while str(current_dir) != str(current_dir.parent):
            uv_toml = current_dir / "pyproject.toml"
            uv_lock = current_dir / "uv.lock"
            venv_dir = current_dir / ".venv"

            if uv_toml.exists():
                if uv_lock.exists():
                    details['dependencies_synced'] = True
                if venv_dir.exists():
                    details['venv_exists'] = True

                    # Check if clautorun commands are available - Python 2/3 compatible
                    try:
                        if hasattr(subprocess, 'run'):  # Python 3.5+
                            clautorun_result = subprocess.run(
                                ["uv", "run", "which", "clautorun"],
                                capture_output=True,
                                text=True,
                                timeout=5
                            )
                            if clautorun_result.returncode == 0:
                                details['clautorun_available'] = True
                        else:  # Python 2.7 fallback
                            import subprocess as sp
                            clautorun_result = sp.Popen(["uv", "run", "which", "clautorun"],
                                                       stdout=sp.PIPE, stderr=sp.PIPE)
                            output, error = clautorun_result.communicate()
                            if clautorun_result.returncode == 0:
                                details['clautorun_available'] = True
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

    This function should be used consistently across all clautorun modules
    when handling import-related errors.

    Args:
        import_error (ImportError): The import exception that occurred
        exit_on_error (bool): Whether to call sys.exit(1) after showing error

    Returns:
        bool: True if error was handled, False if it's a different type of error
    """
    error_str = str(import_error)

    # Check for module structure issues
    if "clautorun.python_check" in error_str or "is not a package" in error_str:
        show_comprehensive_uv_error("IMPORT ERROR", "clautorun module structure issue detected")
        if exit_on_error:
            sys.exit(1)
        return True

    # Check for session manager import issues
    elif "session_manager" in error_str or "session state" in error_str.lower():
        show_comprehensive_uv_error("SESSION MANAGER ERROR", "Session manager module not available")
        if exit_on_error:
            sys.exit(1)
        return True

    # Check for general clautorun import issues
    elif "clautorun" in error_str and "No module named" in error_str:
        show_comprehensive_uv_error("MODULE ERROR", f"clautorun module not found: {error_str}")
        if exit_on_error:
            sys.exit(1)
        return True

    # Not a recognized clautorun import error
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
    print(f"   clautorun Available: {'✅' if details['clautorun_available'] else '❌'}")

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
            print("   uv sync --extra claude-code")

    return is_configured