#!/usr/bin/env python3

"""Comprehensive unit tests for task lifecycle CLI commands.

Tests all CLI commands with proper isolation:
- cli_status: text/json/table formats, verbose mode, session filtering
- cli_export: JSON/CSV/markdown formats, completed task filtering
- cli_clear: single session, all sessions, confirmation handling
- cli_gc: dry-run, archive, pattern filtering, TTL, protection logic

CLI Usage (Modern Subcommand Structure):
    # Show task status
    autorun task status                      # Current session, text format
    autorun task status --verbose            # Detailed task info
    autorun task status --format json        # JSON output
    autorun task status --session abc123     # Specific session

    # Export task data
    autorun task export tasks.json           # Export to JSON
    autorun task export tasks.csv --format csv  # Export to CSV
    autorun task export --include-completed  # Include completed tasks

    # Clear task data (DESTRUCTIVE)
    autorun task clear                       # Clear current session
    autorun task clear --session abc123      # Clear specific session
    autorun task clear --all                 # Clear ALL sessions
    autorun task clear --no-confirm          # Skip confirmation

    # Garbage collection (DESTRUCTIVE)
    autorun task gc --dry-run                # Preview (RECOMMENDED first)
    autorun task gc                          # Run with confirmation
    autorun task gc --no-confirm             # Skip confirmation
    autorun task gc --pattern "test-*"       # Filter by pattern
    autorun task gc --ttl 7                  # Only sessions older than 7 days
    autorun task gc --no-archive             # Skip archiving (DANGEROUS)
"""

import sys
import json
import time
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timedelta
from io import StringIO
from unittest.mock import patch

# Add src to path
plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root / 'src'))

from autorun.task_lifecycle import TaskLifecycle, TaskLifecycleConfig


# ============================================================================
# Test Fixtures and Helpers
# ============================================================================

def create_test_manager_with_tasks(session_id: str) -> TaskLifecycle:
    """Create a manager with sample tasks for testing.

    Args:
        session_id: Unique session ID for isolation

    Returns:
        TaskLifecycle manager with 5 sample tasks
    """
    manager = TaskLifecycle(session_id=session_id)

    # Task 1: Pending
    manager.create_task('1', {
        'subject': 'Fix login bug',
        'description': 'Users cannot log in with special characters',
        'activeForm': 'Fixing...'
    }, 'Created task 1')

    # Task 2: In progress
    manager.create_task('2', {
        'subject': 'Add unit tests',
        'description': 'Need tests for auth module',
        'activeForm': 'Writing tests...'
    }, 'Created task 2')
    manager.update_task('2', {'status': 'in_progress'}, 'Started task 2')

    # Task 3: Completed
    manager.create_task('3', {
        'subject': 'Update documentation',
        'description': 'Add installation guide',
        'activeForm': 'Updating docs...'
    }, 'Created task 3')
    manager.update_task('3', {'status': 'completed'}, 'Finished task 3')

    # Task 4: Deleted
    manager.create_task('4', {
        'subject': 'Old feature',
        'description': 'No longer needed',
        'activeForm': 'Working...'
    }, 'Created task 4')
    manager.update_task('4', {'status': 'deleted'}, 'Removed task 4')

    # Task 5: Paused
    manager.create_task('5', {
        'subject': 'Performance optimization',
        'description': 'Database query improvements',
        'activeForm': 'Optimizing...'
    }, 'Created task 5')
    manager.update_task('5', {'status': 'paused'}, 'Paused task 5')

    return manager


def capture_stdout(func, *args, **kwargs):
    """Capture stdout from a function call.

    Args:
        func: Function to call
        *args: Positional arguments
        **kwargs: Keyword arguments

    Returns:
        Tuple of (return_value, stdout_string)
    """
    old_stdout = sys.stdout
    sys.stdout = StringIO()
    try:
        result = func(*args, **kwargs)
        output = sys.stdout.getvalue()
        return result, output
    finally:
        sys.stdout = old_stdout


# ============================================================================
# cli_status Tests
# ============================================================================

def test_cli_status_text_format():
    """Test cli_status with default text format."""
    session_id = 'test-cli-status-text'
    manager = create_test_manager_with_tasks(session_id)

    # Mock CLAUDE_SESSION_ID env var
    with patch.dict('os.environ', {'CLAUDE_SESSION_ID': session_id}):
        exit_code, output = capture_stdout(
            TaskLifecycle.cli_status,
            session_id=session_id,
            verbose=False,
            format='text'
        )

    assert exit_code == 0, "cli_status should succeed"
    assert session_id in output, "Output should show session ID"
    assert "Total tasks: 5" in output, "Should show total task count"
    assert "Incomplete:" in output, "Should show incomplete count"
    print("✅ cli_status text format works")


def test_cli_status_verbose_shows_details():
    """Test cli_status verbose mode shows full task details."""
    session_id = 'test-cli-status-verbose'
    manager = create_test_manager_with_tasks(session_id)

    exit_code, output = capture_stdout(
        TaskLifecycle.cli_status,
        session_id=session_id,
        verbose=True,
        format='text'
    )

    assert exit_code == 0, "cli_status should succeed"
    assert "Fix login bug" in output, "Should show task subject"
    assert "Status:" in output, "Should show status field"
    assert "Created:" in output, "Should show creation timestamp"
    print("✅ cli_status verbose mode shows details")


def test_cli_status_json_format():
    """Test cli_status with JSON output format."""
    session_id = 'test-cli-status-json'
    manager = create_test_manager_with_tasks(session_id)

    exit_code, output = capture_stdout(
        TaskLifecycle.cli_status,
        session_id=session_id,
        verbose=False,
        format='json'
    )

    assert exit_code == 0, "cli_status should succeed"

    # Parse JSON output
    data = json.loads(output)
    assert data['session_id'] == session_id, "JSON should include session ID"
    assert data['total_tasks'] == 5, "JSON should show total tasks"
    assert data['incomplete_tasks'] == 3, "JSON should count incomplete tasks (pending + in_progress + paused)"
    assert 'tasks' in data, "JSON should include tasks dictionary"
    assert '1' in data['tasks'], "JSON should include task 1"
    print("✅ cli_status JSON format works")


def test_cli_status_table_format():
    """Test cli_status with table output format."""
    session_id = 'test-cli-status-table'
    manager = create_test_manager_with_tasks(session_id)

    exit_code, output = capture_stdout(
        TaskLifecycle.cli_status,
        session_id=session_id,
        verbose=False,
        format='table'
    )

    assert exit_code == 0, "cli_status should succeed"
    assert "Task Status" in output, "Should show table header"
    assert session_id[:8] in output, "Should show session ID prefix"
    assert "Fix login bug" in output, "Should show task subjects"
    assert "🔄" in output or "⏸️" in output or "✅" in output, "Should show status icons"
    print("✅ cli_status table format works")


def test_cli_status_no_session_id_fails():
    """Test cli_status fails gracefully when session ID missing."""
    # No CLAUDE_SESSION_ID env var
    with patch.dict('os.environ', {}, clear=True):
        exit_code, output = capture_stdout(
            TaskLifecycle.cli_status,
            session_id=None,
            verbose=False,
            format='text'
        )

    assert exit_code == 1, "cli_status should fail when session ID missing"
    # Note: Error messages go to stderr, not captured by our stdout capture
    print("✅ cli_status fails gracefully without session ID")


# ============================================================================
# cli_export Tests
# ============================================================================

def test_cli_export_json_format():
    """Test cli_export with JSON format."""
    session_id = 'test-cli-export-json'
    manager = create_test_manager_with_tasks(session_id)

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "export.json"

        exit_code = TaskLifecycle.cli_export(
            session_id=session_id,
            output_path=str(output_path),
            format='json',
            include_completed=False
        )

        assert exit_code == 0, "cli_export should succeed"
        assert output_path.exists(), "Export file should be created"

        # Verify JSON content
        data = json.loads(output_path.read_text(encoding="utf-8"))
        assert 'session_id' in data, "Export should include session ID"
        assert 'tasks' in data, "Export should include tasks"
        assert '1' in data['tasks'], "Export should include pending task"
        assert '2' in data['tasks'], "Export should include in_progress task"
        assert '3' not in data['tasks'], "Export should exclude completed tasks by default"

    print("✅ cli_export JSON format works")


def test_cli_export_with_completed_tasks():
    """Test cli_export includes completed tasks when requested."""
    session_id = 'test-cli-export-completed'
    manager = create_test_manager_with_tasks(session_id)

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "export_all.json"

        exit_code = TaskLifecycle.cli_export(
            session_id=session_id,
            output_path=str(output_path),
            format='json',
            include_completed=True
        )

        assert exit_code == 0, "cli_export should succeed"

        # Verify all tasks included
        data = json.loads(output_path.read_text(encoding="utf-8"))
        assert '3' in data['tasks'], "Export should include completed task when requested"
        assert '4' in data['tasks'], "Export should include deleted task when requested"
        assert len(data['tasks']) == 5, "Export should include all 5 tasks"

    print("✅ cli_export includes completed tasks when requested")


def test_cli_export_csv_format():
    """Test cli_export with CSV format."""
    session_id = 'test-cli-export-csv'
    manager = create_test_manager_with_tasks(session_id)

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "export.csv"

        exit_code = TaskLifecycle.cli_export(
            session_id=session_id,
            output_path=str(output_path),
            format='csv',
            include_completed=False
        )

        assert exit_code == 0, "cli_export should succeed"
        assert output_path.exists(), "CSV file should be created"

        # Verify CSV content
        csv_content = output_path.read_text(encoding="utf-8")
        assert "id,subject,status" in csv_content, "CSV should have headers"
        assert "Fix login bug" in csv_content, "CSV should include task subjects"
        assert "pending" in csv_content or "in_progress" in csv_content, "CSV should include statuses"

    print("✅ cli_export CSV format works")


def test_cli_export_markdown_format():
    """Test cli_export with Markdown format."""
    session_id = 'test-cli-export-markdown'
    manager = create_test_manager_with_tasks(session_id)

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "export.md"

        exit_code = TaskLifecycle.cli_export(
            session_id=session_id,
            output_path=str(output_path),
            format='markdown',
            include_completed=False
        )

        assert exit_code == 0, "cli_export should succeed"
        assert output_path.exists(), "Markdown file should be created"

        # Verify Markdown content
        md_content = output_path.read_text(encoding="utf-8")
        assert "# Task Export" in md_content or "## Session" in md_content, "Should have Markdown headers"
        assert "Fix login bug" in md_content, "Should include task subjects"

    print("✅ cli_export Markdown format works")


# ============================================================================
# cli_clear Tests
# ============================================================================

def test_cli_clear_single_session_no_confirm():
    """Test cli_clear removes single session without confirmation."""
    session_id = 'test-cli-clear-single'
    manager = create_test_manager_with_tasks(session_id)

    # Verify tasks exist
    assert len(manager.tasks) == 5, "Should have 5 tasks before clear"

    # Clear without confirmation
    exit_code, output = capture_stdout(
        TaskLifecycle.cli_clear,
        session_id=session_id,
        all_sessions=False,
        confirm=False
    )

    assert exit_code == 0, "cli_clear should succeed"
    assert "Cleared 5 task(s)" in output, "Should report cleared task count"

    # Verify tasks cleared
    new_manager = TaskLifecycle(session_id=session_id)
    assert len(new_manager.tasks) == 0, "Tasks should be cleared"

    print("✅ cli_clear single session works")


def test_cli_clear_with_confirm_in_non_interactive():
    """Test cli_clear refuses confirmation in non-interactive mode."""
    session_id = 'test-cli-clear-confirm'
    manager = create_test_manager_with_tasks(session_id)

    # Mock non-interactive terminal
    with patch('sys.stdin.isatty', return_value=False):
        exit_code, output = capture_stdout(
            TaskLifecycle.cli_clear,
            session_id=session_id,
            all_sessions=False,
            confirm=True
        )

    assert exit_code == 2, "cli_clear should return 2 (cancelled) in non-interactive mode"
    assert "Refusing to clear" in output, "Should warn about non-interactive mode"

    # Verify tasks NOT cleared
    verify_manager = TaskLifecycle(session_id=session_id)
    assert len(verify_manager.tasks) == 5, "Tasks should NOT be cleared"

    print("✅ cli_clear refuses confirmation in non-interactive mode")


def test_cli_clear_all_sessions():
    """Test cli_clear can remove all sessions."""
    config = TaskLifecycleConfig.load()

    # Create multiple test sessions with unique prefixes
    session1 = 'test-cli-clear-all-unique-1'
    session2 = 'test-cli-clear-all-unique-2'

    manager1 = create_test_manager_with_tasks(session1)
    manager2 = create_test_manager_with_tasks(session2)

    # Verify storage directories exist
    storage1 = config.storage_dir / session1
    storage2 = config.storage_dir / session2
    assert storage1.exists(), "Session 1 storage should exist"
    assert storage2.exists(), "Session 2 storage should exist"

    # Clear all sessions without confirmation
    exit_code, output = capture_stdout(
        TaskLifecycle.cli_clear,
        session_id=None,
        all_sessions=True,
        confirm=False
    )

    assert exit_code == 0, "cli_clear should succeed"
    assert "Cleared" in output, "Should report cleared sessions"

    # Verify storage directories removed
    # Note: This clears ALL sessions, not just our test sessions
    # So we just verify the operation succeeded and returned correct exit code
    assert not storage1.exists(), "Session 1 storage should be removed"
    assert not storage2.exists(), "Session 2 storage should be removed"

    print("✅ cli_clear all sessions works")


# ============================================================================
# cli_gc Tests
# ============================================================================

def test_cli_gc_dry_run_previews_without_changes():
    """Test cli_gc dry-run mode reports without making changes."""
    session_id = 'test-cli-gc-dry-run'
    manager = create_test_manager_with_tasks(session_id)

    # Make session old enough to be GC'd
    time.sleep(0.1)

    exit_code, output = capture_stdout(
        TaskLifecycle.cli_gc,
        archive=True,
        dry_run=True,
        pattern="*",
        ttl_days=0,  # No TTL = immediate GC eligibility
        confirm=False  # Skip confirmation in tests
    )

    assert exit_code == 0, "cli_gc should succeed"
    assert "DRY RUN" in output or "would" in output.lower(), "Should indicate dry-run mode"

    # Verify no changes made
    verify_manager = TaskLifecycle(session_id=session_id)
    assert len(verify_manager.tasks) == 5, "Tasks should NOT be cleared in dry-run"

    print("✅ cli_gc dry-run previews without changes")


def test_cli_gc_archives_before_deletion():
    """Test cli_gc archives data to JSON before deletion."""
    session_id = 'test-cli-gc-archive'
    config = TaskLifecycleConfig.load()

    # Create old session with completed tasks only
    manager = TaskLifecycle(session_id=session_id)
    manager.create_task('1', {'subject': 'Old task', 'description': '', 'activeForm': ''}, 'Created')
    manager.update_task('1', {'status': 'completed'}, 'Completed')

    # Wait to ensure age check passes
    time.sleep(0.1)

    # Run GC with archiving
    exit_code, output = capture_stdout(
        TaskLifecycle.cli_gc,
        archive=True,
        dry_run=False,
        pattern=session_id,  # Only target this session
        ttl_days=0,
        confirm=False  # Skip confirmation in tests
    )

    assert exit_code == 0, "cli_gc should succeed"

    # Verify archive file created
    archive_path = config.storage_dir / "archive" / f"{session_id}.json"
    assert archive_path.exists(), f"Archive should be created at {archive_path}"

    # Verify archive content
    archive_data = json.loads(archive_path.read_text(encoding="utf-8"))
    assert archive_data['session_id'] == session_id, "Archive should include session ID"
    assert '1' in archive_data['tasks'], "Archive should include tasks"

    print("✅ cli_gc archives before deletion")


def test_cli_gc_pattern_filtering():
    """Test cli_gc filters sessions by glob pattern."""
    # Create sessions with different prefixes
    session1 = 'test-gc-pattern-keep'
    session2 = 'prod-gc-pattern-clean'

    manager1 = create_test_manager_with_tasks(session1)
    manager2 = create_test_manager_with_tasks(session2)

    # Mark both as completed
    for mgr in [manager1, manager2]:
        for task_id in ['1', '2', '3', '4', '5']:
            mgr.update_task(task_id, {'status': 'completed'}, 'Done')

    time.sleep(0.1)

    # GC only prod-* sessions
    exit_code, output = capture_stdout(
        TaskLifecycle.cli_gc,
        archive=True,
        dry_run=False,
        pattern="prod-*",
        ttl_days=0,
        confirm=False  # Skip confirmation in tests
    )

    assert exit_code == 0, "cli_gc should succeed"

    # Verify test-* session preserved, prod-* session cleaned
    verify1 = TaskLifecycle(session_id=session1)
    verify2 = TaskLifecycle(session_id=session2)
    assert len(verify1.tasks) == 5, "test-* session should be preserved"
    assert len(verify2.tasks) == 0, "prod-* session should be cleaned"

    print("✅ cli_gc pattern filtering works")


def test_cli_gc_ttl_protects_recent_sessions():
    """Test cli_gc respects TTL and protects recent sessions."""
    session_id = 'test-cli-gc-ttl'
    manager = create_test_manager_with_tasks(session_id)

    # Mark all as completed
    for task_id in ['1', '2', '3', '4', '5']:
        manager.update_task(task_id, {'status': 'completed'}, 'Done')

    # Run GC with 1-day TTL (session is too recent)
    exit_code, output = capture_stdout(
        TaskLifecycle.cli_gc,
        archive=True,
        dry_run=False,
        pattern=session_id,
        ttl_days=1,  # Require 1 day age
        confirm=False  # Skip confirmation in tests
    )

    assert exit_code == 0, "cli_gc should succeed"

    # Verify session NOT cleaned (too recent)
    verify_manager = TaskLifecycle(session_id=session_id)
    assert len(verify_manager.tasks) == 5, "Recent session should be protected by TTL"

    print("✅ cli_gc respects TTL protection")


def test_cli_gc_protects_current_session():
    """Test cli_gc never cleans the current active session."""
    session_id = 'test-cli-gc-current'
    manager = create_test_manager_with_tasks(session_id)

    # Mark all as completed
    for task_id in ['1', '2', '3', '4', '5']:
        manager.update_task(task_id, {'status': 'completed'}, 'Done')

    time.sleep(0.1)

    # Mock this as the current session
    with patch.dict('os.environ', {'CLAUDE_SESSION_ID': session_id}):
        exit_code, output = capture_stdout(
            TaskLifecycle.cli_gc,
            archive=True,
            dry_run=False,
            pattern=session_id,
            ttl_days=0,
            confirm=False  # Skip confirmation in tests
        )

    assert exit_code == 0, "cli_gc should succeed"

    # Verify current session NOT cleaned
    verify_manager = TaskLifecycle(session_id=session_id)
    assert len(verify_manager.tasks) == 5, "Current session should be protected"

    print("✅ cli_gc protects current active session")


def test_cli_gc_protects_incomplete_tasks():
    """Test cli_gc skips sessions with incomplete tasks."""
    session_id = 'test-cli-gc-incomplete'
    manager = create_test_manager_with_tasks(session_id)

    # Session has in_progress task (task 2) - should be protected
    time.sleep(0.1)

    exit_code, output = capture_stdout(
        TaskLifecycle.cli_gc,
        archive=True,
        dry_run=False,
        pattern=session_id,
        ttl_days=0,
        confirm=False  # Skip confirmation in tests
    )

    assert exit_code == 0, "cli_gc should succeed"

    # Verify session NOT cleaned (has incomplete tasks)
    verify_manager = TaskLifecycle(session_id=session_id)
    assert len(verify_manager.tasks) == 5, "Session with incomplete tasks should be protected"

    print("✅ cli_gc protects sessions with incomplete tasks")


def test_cli_gc_requires_confirmation_in_non_interactive():
    """Test cli_gc refuses to proceed without confirmation in non-interactive mode."""
    session_id = 'test-cli-gc-confirm'
    manager = create_test_manager_with_tasks(session_id)

    # Mark all as completed
    for task_id in ['1', '2', '3', '4', '5']:
        manager.update_task(task_id, {'status': 'completed'}, 'Done')

    time.sleep(0.1)

    # Mock non-interactive terminal
    with patch('sys.stdin.isatty', return_value=False):
        exit_code, output = capture_stdout(
            TaskLifecycle.cli_gc,
            archive=True,
            dry_run=False,
            pattern=session_id,
            ttl_days=0,
            confirm=True  # Require confirmation (should fail in non-interactive)
        )

    assert exit_code == 2, "cli_gc should return 2 (cancelled) in non-interactive mode"
    assert "Cannot prompt for confirmation" in output or "non-interactive" in output, "Should warn about non-interactive mode"

    # Verify session NOT cleaned
    verify_manager = TaskLifecycle(session_id=session_id)
    assert len(verify_manager.tasks) == 5, "Session should NOT be cleaned without confirmation"

    print("✅ cli_gc requires confirmation in non-interactive mode")


# ============================================================================
# Main Test Runner
# ============================================================================

if __name__ == '__main__':
    print("\n" + "="*70)
    print("Running CLI Command Tests")
    print("="*70 + "\n")

    # cli_status tests
    print("Testing cli_status...")
    test_cli_status_text_format()
    test_cli_status_verbose_shows_details()
    test_cli_status_json_format()
    test_cli_status_table_format()
    test_cli_status_no_session_id_fails()

    # cli_export tests
    print("\nTesting cli_export...")
    test_cli_export_json_format()
    test_cli_export_with_completed_tasks()
    test_cli_export_csv_format()
    test_cli_export_markdown_format()

    # cli_clear tests
    print("\nTesting cli_clear...")
    test_cli_clear_single_session_no_confirm()
    test_cli_clear_with_confirm_in_non_interactive()
    test_cli_clear_all_sessions()

    # cli_gc tests
    print("\nTesting cli_gc...")
    test_cli_gc_dry_run_previews_without_changes()
    test_cli_gc_archives_before_deletion()
    test_cli_gc_pattern_filtering()
    test_cli_gc_ttl_protects_recent_sessions()
    test_cli_gc_protects_current_session()
    test_cli_gc_protects_incomplete_tasks()
    test_cli_gc_requires_confirmation_in_non_interactive()

    print("\n" + "="*70)
    print("All Tests Passed! ✅")
    print("="*70 + "\n")
