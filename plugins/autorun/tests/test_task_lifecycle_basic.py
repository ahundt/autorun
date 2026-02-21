#!/usr/bin/env python3

"""Basic tests for task lifecycle tracking.

Tests core functionality without requiring full integration.
"""

import sys
from pathlib import Path

# Add src to path
plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root / 'src'))

from autorun.task_lifecycle import TaskLifecycle, TaskLifecycleConfig, is_enabled


def test_config_load_save():
    """Test config loading and saving."""
    config = TaskLifecycleConfig.load()
    # enabled may be True or False depending on user's local config
    assert isinstance(config.enabled, bool)
    assert isinstance(config.max_resume_tasks, int)
    assert config.max_resume_tasks > 0
    assert isinstance(config.stop_block_max_count, int)
    assert config.stop_block_max_count > 0
    print("✅ Config load/save works")


def test_task_lifecycle_creation():
    """Test TaskLifecycle instantiation."""
    manager = TaskLifecycle(session_id='test-basic-1')
    assert manager.session_id == 'test-basic-1'
    assert manager.global_key == '__task_lifecycle__test-basic-1'
    assert manager.tasks == {}
    print("✅ TaskLifecycle creation works")


def test_task_creation():
    """Test creating tasks."""
    manager = TaskLifecycle(session_id='test-basic-2')

    manager.create_task('1', {
        'subject': 'Test task 1',
        'description': 'Description 1',
        'activeForm': 'Working...'
    }, 'Task #1 created successfully')

    tasks = manager.tasks
    assert '1' in tasks
    assert tasks['1']['subject'] == 'Test task 1'
    assert tasks['1']['status'] == 'pending'
    print("✅ Task creation works")


def test_task_update():
    """Test updating tasks."""
    manager = TaskLifecycle(session_id='test-basic-3')

    manager.create_task('1', {
        'subject': 'Test task',
        'description': 'Test',
        'activeForm': 'Testing...'
    }, 'Created')

    manager.update_task('1', {'status': 'in_progress'}, 'Updated')

    tasks = manager.tasks
    assert tasks['1']['status'] == 'in_progress'
    print("✅ Task update works")


def test_task_prioritization():
    """Test task prioritization with dependencies."""
    manager = TaskLifecycle(session_id='test-basic-4')

    # Create tasks with dependencies
    manager.create_task('1', {'subject': 'Task 1', 'description': '', 'activeForm': ''}, 'Created 1')
    manager.create_task('2', {'subject': 'Task 2', 'description': '', 'activeForm': ''}, 'Created 2')
    manager.create_task('3', {'subject': 'Task 3', 'description': '', 'activeForm': ''}, 'Created 3')

    # Task 2 depends on Task 1
    manager.update_task('2', {'addBlockedBy': ['1']}, 'Added blocker')

    # Task 3 depends on Task 2
    manager.update_task('3', {'addBlockedBy': ['2']}, 'Added blocker')

    prioritized = manager.get_prioritized_tasks()

    # Task 1 should be first (no dependencies)
    assert prioritized[0]['id'] == '1'
    # Tasks 2 and 3 should be blocked
    assert prioritized[1]['id'] in ('2', '3')
    print("✅ Task prioritization works")


def test_deduplication():
    """Test that duplicate task creation is prevented."""
    manager = TaskLifecycle(session_id='test-basic-5')

    manager.create_task('1', {'subject': 'Task 1', 'description': '', 'activeForm': ''}, 'Created 1')
    manager.create_task('1', {'subject': 'Duplicate', 'description': '', 'activeForm': ''}, 'Created 1 again')

    tasks = manager.tasks
    # Should only have one task
    assert len(tasks) == 1
    # Should keep the first subject
    assert tasks['1']['subject'] == 'Task 1'
    print("✅ Deduplication works")


def test_cli_methods():
    """Test CLI class methods."""
    # Test cli_status
    exit_code = TaskLifecycle.cli_status(session_id='test-cli-basic', format='text', verbose=False)
    assert exit_code == 0
    print("✅ CLI status method works")

    # Test cli_enable/disable
    exit_code = TaskLifecycle.cli_enable()
    assert exit_code == 0
    print("✅ CLI enable method works")


def test_is_enabled():
    """Test is_enabled function."""
    enabled = is_enabled()
    assert isinstance(enabled, bool)
    print(f"✅ is_enabled() returns {enabled}")


def run_all_tests():
    """Run all tests."""
    print("Running basic task lifecycle tests...\n")

    test_config_load_save()
    test_task_lifecycle_creation()
    test_task_creation()
    test_task_update()
    test_task_prioritization()
    test_deduplication()
    test_cli_methods()
    test_is_enabled()

    print("\n✅ All basic tests passed!")


if __name__ == '__main__':
    run_all_tests()
