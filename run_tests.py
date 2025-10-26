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
Test runner script for clautorun

Makes it easy to run all tests with different options
"""
import subprocess
import sys
import argparse
from pathlib import Path


def run_command(cmd, cwd=None):
    """Run a command and return the result"""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False
        )
        return result.returncode, result.stdout, result.stderr
    except Exception as e:
        return 1, "", str(e)


def main():
    parser = argparse.ArgumentParser(description="Test runner for clautorun")
    parser.add_argument(
        "--coverage",
        action="store_true",
        help="Run tests with coverage report"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose output"
    )
    parser.add_argument(
        "--unit",
        action="store_true",
        help="Run only unit tests"
    )
    parser.add_argument(
        "--integration",
        action="store_true",
        help="Run only integration tests"
    )
    parser.add_argument(
        "--plugin",
        action="store_true",
        help="Run only plugin tests"
    )
    parser.add_argument(
        "--hook",
        action="store_true",
        help="Run only hook tests"
    )
    parser.add_argument(
        "--compatibility",
        action="store_true",
        help="Run only compatibility tests"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all tests (default)"
    )
    parser.add_argument(
        "--install-deps",
        action="store_true",
        help="Install test dependencies first"
    )
    parser.add_argument(
        "--report",
        choices=["text", "html", "xml"],
        default="text",
        help="Test report format (default: text)"
    )

    args = parser.parse_args()

    # Get project root directory
    project_root = Path(__file__).parent

    # Install dependencies if requested
    if args.install_deps:
        print("📦 Installing test dependencies...")
        returncode, stdout, stderr = run_command([
            sys.executable, "-m", "pip", "install", "-e", ".[dev]"
        ], cwd=project_root)

        if returncode != 0:
            print("❌ Failed to install dependencies:")
            print(f"Error: {stderr}")
            return 1
        print("✅ Dependencies installed successfully")

    # Build pytest command
    pytest_cmd = [sys.executable, "-m", "pytest"]

    # Add coverage if requested
    if args.coverage:
        pytest_cmd.extend([
            "--cov=src/clautorun",
            f"--cov-report={args.report}",
            "--cov-report=term-missing"
        ])

    # Add verbosity if requested
    if args.verbose:
        pytest_cmd.append("-v")

    # Add test selection options
    test_selection = []
    if args.unit:
        test_selection.extend(["-m", "unit"])
    if args.integration:
        test_selection.extend(["-m", "integration"])
    if args.plugin:
        test_selection.extend(["-m", "plugin"])
    if args.hook:
        test_selection.extend(["-m", "hook"])
    if args.compatibility:
        test_selection.append("tests/test_autorun_compatibility.py")

    # If no specific selection, run all tests
    if not test_selection and not args.all:
        test_selection = ["tests/"]

    pytest_cmd.extend(test_selection)

    # Run tests
    print(f"🧪 Running tests: {' '.join(pytest_cmd[4:])}")
    print(f"📁 Working directory: {project_root}")
    print("-" * 50)

    returncode, stdout, stderr = run_command(pytest_cmd, cwd=project_root)

    # Print results
    if stdout:
        print(stdout)
    if stderr:
        print(f"⚠️ Warnings/Errors:\n{stderr}")

    # Print coverage report location if generated
    if args.coverage and args.report == "html":
        coverage_dir = project_root / "htmlcov"
        if coverage_dir.exists():
            print(f"📊 Coverage report generated: {coverage_dir}/index.html")

    return returncode


def quick_test():
    """Run a quick test to verify basic functionality"""
    print("🚀 Running quick functionality test...")

    project_root = Path(__file__).parent

    # Test basic import
    try:
        sys.path.insert(0, str(project_root / "src"))
        from clautorun.main import CONFIG, COMMAND_HANDLERS
        print("✅ Basic imports successful")
    except Exception as e:
        print(f"❌ Import failed: {e}")
        return 1

    # Test configuration
    try:
        assert "completion_marker" in CONFIG
        assert "policies" in CONFIG
        assert "command_mappings" in CONFIG
        print("✅ Configuration validation successful")
    except Exception as e:
        print(f"❌ Configuration validation failed: {e}")
        return 1

    # Test command handlers
    try:
        required_handlers = ["SEARCH", "ALLOW", "JUSTIFY", "STATUS"]
        for handler in required_handlers:
            assert handler in COMMAND_HANDLERS
            assert callable(COMMAND_HANDLERS[handler])
        print("✅ Command handlers validation successful")
    except Exception as e:
        print(f"❌ Command handlers validation failed: {e}")
        return 1

    print("🎯 Quick test passed! Basic functionality verified.")
    return 0


if __name__ == "__main__":
    # Handle special case for quick test
    if len(sys.argv) > 1 and sys.argv[1] == "--quick":
        sys.exit(quick_test())

    # Handle normal operation
    sys.exit(main())