#!/usr/bin/env python3

"""Integration tests for CLI argument parsing and command routing.

Tests the argparse configuration and command dispatch logic without executing
actual operations. Uses parse_args() to verify arguments are correctly parsed
and routed to the appropriate handlers.

These tests verify:
- Argument parsing works correctly for all commands
- Subcommand structure is properly configured
- Default values are set correctly
- Short flags (-v, -n, -f) map to correct long flags
- Invalid arguments are rejected
"""

import sys
from pathlib import Path
from io import StringIO
from unittest.mock import patch

# Add src to path
plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root / 'src'))

from autorun.__main__ import create_parser


# ============================================================================
# Test Fixtures and Helpers
# ============================================================================

def parse_args(args_list):
    """Parse arguments safely without executing commands.

    Args:
        args_list: List of command-line arguments (e.g., ['task', 'status', '--verbose'])

    Returns:
        Parsed argparse.Namespace object
    """
    parser = create_parser()
    return parser.parse_args(args_list)


def parse_args_expect_error(args_list):
    """Parse arguments expecting a parsing error.

    Args:
        args_list: List of command-line arguments that should fail

    Returns:
        True if parsing failed as expected, False otherwise
    """
    parser = create_parser()
    old_stderr = sys.stderr
    sys.stderr = StringIO()
    try:
        parser.parse_args(args_list)
        sys.stderr = old_stderr
        return False  # Parsing succeeded when it should have failed
    except SystemExit as e:
        sys.stderr = old_stderr
        return e.code != 0  # Non-zero exit = error


# ============================================================================
# Installation Command Tests
# ============================================================================

def test_install_command_parsing():
    """Test --install flag parsing."""
    # Install all (default)
    args = parse_args(['--install'])
    assert args.install == 'all', "Default install should be 'all'"

    # Install specific plugin
    args = parse_args(['--install', 'autorun'])
    assert args.install == 'autorun', "Should install specific plugin"

    # Install with force
    args = parse_args(['--install', '--force'])
    assert args.force is True, "Force install flag should be set"

    print("✅ Install command parsing works")


def test_status_command_parsing():
    """Test --status flag parsing."""
    args = parse_args(['--status'])
    assert args.status is True, "Status flag should be set"

    print("✅ Status command parsing works")


def test_update_command_parsing():
    """Test --update flag parsing."""
    args = parse_args(['--update'])
    assert args.update is True, "Update flag should be set"

    # With method
    args = parse_args(['--update', '--update-method', 'uv'])
    assert args.update_method == 'uv', "Update method should be set"

    print("✅ Update command parsing works")


def test_version_command_parsing():
    """Test --version flag parsing."""
    args = parse_args(['--version'])
    assert args.version is True, "Version flag should be set"

    print("✅ Version command parsing works")


# ============================================================================
# Task Subcommand Tests
# ============================================================================

def test_task_status_parsing():
    """Test 'task status' subcommand parsing."""
    # Basic
    args = parse_args(['task', 'status'])
    assert args.command == 'task', "Should route to task command"
    assert args.task_command == 'status', "Should route to status subcommand"

    # With verbose
    args = parse_args(['task', 'status', '--verbose'])
    assert args.verbose is True, "Verbose flag should be set"

    # With short verbose
    args = parse_args(['task', 'status', '-v'])
    assert args.verbose is True, "Short verbose flag should work"

    # With format
    args = parse_args(['task', 'status', '--format', 'json'])
    assert args.format == 'json', "Format should be json"

    # With session
    args = parse_args(['task', 'status', '--session', 'abc123'])
    assert args.session == 'abc123', "Session ID should be set"

    print("✅ Task status parsing works")


def test_task_export_parsing():
    """Test 'task export' subcommand parsing."""
    # Basic (positional argument)
    args = parse_args(['task', 'export', 'output.json'])
    assert args.command == 'task', "Should route to task command"
    assert args.task_command == 'export', "Should route to export subcommand"
    assert args.output == 'output.json', "Output path should be set"

    # With format
    args = parse_args(['task', 'export', 'out.csv', '--format', 'csv'])
    assert args.format == 'csv', "Format should be csv"

    # With short format
    args = parse_args(['task', 'export', 'out.json', '-f', 'json'])
    assert args.format == 'json', "Short format flag should work"

    # With include-completed
    args = parse_args(['task', 'export', 'out.json', '--include-completed'])
    assert args.include_completed is True, "Include completed flag should be set"

    # With short include-completed
    args = parse_args(['task', 'export', 'out.json', '-c'])
    assert args.include_completed is True, "Short include-completed flag should work"

    # With session
    args = parse_args(['task', 'export', 'out.json', '--session', 'xyz789'])
    assert args.session == 'xyz789', "Session ID should be set"

    print("✅ Task export parsing works")


def test_task_clear_parsing():
    """Test 'task clear' subcommand parsing."""
    # Basic
    args = parse_args(['task', 'clear'])
    assert args.command == 'task', "Should route to task command"
    assert args.task_command == 'clear', "Should route to clear subcommand"

    # With session
    args = parse_args(['task', 'clear', '--session', 'def456'])
    assert args.session == 'def456', "Session ID should be set"

    # With --all
    args = parse_args(['task', 'clear', '--all'])
    assert args.all is True, "All flag should be set"

    # With short --all
    args = parse_args(['task', 'clear', '-a'])
    assert args.all is True, "Short all flag should work"

    # With --no-confirm
    args = parse_args(['task', 'clear', '--no-confirm'])
    assert args.no_confirm is True, "No-confirm flag should be set"

    print("✅ Task clear parsing works")


def test_task_gc_parsing():
    """Test 'task gc' subcommand parsing."""
    # Basic
    args = parse_args(['task', 'gc'])
    assert args.command == 'task', "Should route to task command"
    assert args.task_command == 'gc', "Should route to gc subcommand"

    # With --dry-run
    args = parse_args(['task', 'gc', '--dry-run'])
    assert args.dry_run is True, "Dry-run flag should be set"

    # With short --dry-run
    args = parse_args(['task', 'gc', '-n'])
    assert args.dry_run is True, "Short dry-run flag should work"

    # With --no-confirm
    args = parse_args(['task', 'gc', '--no-confirm'])
    assert args.no_confirm is True, "No-confirm flag should be set"

    # With --pattern
    args = parse_args(['task', 'gc', '--pattern', 'test-*'])
    assert args.pattern == 'test-*', "Pattern should be set"

    # With short --pattern
    args = parse_args(['task', 'gc', '-p', 'prod-*'])
    assert args.pattern == 'prod-*', "Short pattern flag should work"

    # With --ttl
    args = parse_args(['task', 'gc', '--ttl', '7'])
    assert args.ttl == 7, "TTL should be set"

    # With short --ttl
    args = parse_args(['task', 'gc', '-t', '14'])
    assert args.ttl == 14, "Short TTL flag should work"

    # With --no-archive
    args = parse_args(['task', 'gc', '--no-archive'])
    assert args.no_archive is True, "No-archive flag should be set"

    # Combined flags
    args = parse_args(['task', 'gc', '-n', '-p', 'test-*', '-t', '30'])
    assert args.dry_run is True, "Dry-run should be set"
    assert args.pattern == 'test-*', "Pattern should be set"
    assert args.ttl == 30, "TTL should be set"

    print("✅ Task gc parsing works")


def test_task_default_values():
    """Test default values for task commands."""
    # Status defaults
    args = parse_args(['task', 'status'])
    assert args.verbose is False, "Verbose should default to False"
    assert args.format == 'text', "Format should default to text"
    assert args.session is None, "Session should default to None"

    # Export defaults
    args = parse_args(['task', 'export', 'out.json'])
    assert args.format == 'json', "Export format should default to json"
    assert args.include_completed is False, "Include-completed should default to False"

    # Clear defaults
    args = parse_args(['task', 'clear'])
    assert args.all is False, "All should default to False"
    assert args.no_confirm is False, "No-confirm should default to False"

    # GC defaults
    args = parse_args(['task', 'gc'])
    assert args.dry_run is False, "Dry-run should default to False"
    assert args.no_confirm is False, "No-confirm should default to False"
    assert args.pattern == '*', "Pattern should default to *"
    assert args.ttl is None, "TTL should default to None"
    assert args.no_archive is False, "No-archive should default to False"

    print("✅ Task default values are correct")


# ============================================================================
# Error Handling Tests
# ============================================================================

def test_invalid_task_subcommand():
    """Test that invalid task subcommands are rejected."""
    # Invalid subcommand
    assert parse_args_expect_error(['task', 'invalid']), \
        "Should reject invalid task subcommand"

    print("✅ Invalid task subcommands are rejected")


def test_missing_required_arguments():
    """Test that missing required arguments are rejected."""
    # Export without file path
    assert parse_args_expect_error(['task', 'export']), \
        "Should reject export without file path"

    print("✅ Missing required arguments are rejected")


def test_invalid_format_choices():
    """Test that invalid format choices are rejected."""
    # Invalid status format
    assert parse_args_expect_error(['task', 'status', '--format', 'xml']), \
        "Should reject invalid status format"

    # Invalid export format
    assert parse_args_expect_error(['task', 'export', 'out.txt', '--format', 'xml']), \
        "Should reject invalid export format"

    print("✅ Invalid format choices are rejected")


def test_task_no_subcommand():
    """Test 'task' without subcommand is allowed (shows help in main())."""
    # Just "task" with no subcommand is allowed by argparse
    # main() function checks for this and shows help
    args = parse_args(['task'])
    assert args.command == 'task', "Should route to task command"
    assert not hasattr(args, 'task_command') or args.task_command is None, \
        "Should have no task subcommand"

    print("✅ Task without subcommand is parsed (main() shows help)")


# ============================================================================
# Main Test Runner
# ============================================================================

if __name__ == '__main__':
    print("\n" + "="*70)
    print("Running CLI Integration Tests")
    print("="*70 + "\n")

    # Installation commands
    print("Testing installation commands...")
    test_install_command_parsing()
    test_status_command_parsing()
    test_update_command_parsing()
    test_version_command_parsing()

    # Task subcommands
    print("\nTesting task subcommands...")
    test_task_status_parsing()
    test_task_export_parsing()
    test_task_clear_parsing()
    test_task_gc_parsing()
    test_task_default_values()

    # Error handling
    print("\nTesting error handling...")
    test_invalid_task_subcommand()
    test_missing_required_arguments()
    test_invalid_format_choices()
    test_task_no_subcommand()

    print("\n" + "="*70)
    print("All CLI Integration Tests Passed! ✅")
    print("="*70 + "\n")
