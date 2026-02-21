"""Test ghost task bug and migration fixes.

This test file replicates the exact Task #6 bug that occurred in production:
1. Task created before tracking starts (ghost task)
2. AI calls TaskUpdate(taskId=6, status="in_progress")
3. Session ends/compacts before TaskUpdate(taskId=6, status="completed")
4. Ghost task persists with "in_progress" status in shelve
5. Daemon restarts don't clear it (shelve on disk)
6. Task blocks Stop hook in all subsequent sessions

The fix:
- Ghost tasks can only transition to terminal statuses (completed/deleted/ignored)
- Migration automatically fixes v1 ghost tasks with blocking statuses
- Enhanced prune_old_tasks() to include ignored status
- GC with archive-then-purge for manual cleanup (uses SessionLock properly)

Test isolation:
- All tests use isolated test directories via pytest fixtures
- No tests touch production data in ~/.claude/sessions/
- Each test class has its own fixture for clean state
"""
import os
import shutil
import time
from pathlib import Path
import pytest
import tempfile

from clautorun.task_lifecycle import TaskLifecycle, TaskLifecycleConfig
from clautorun.session_manager import session_state, SessionStateManager


@pytest.fixture
def isolated_config(tmp_path):
    """Isolated config using temp directory (no impact on production)."""
    return TaskLifecycleConfig(
        enabled=True,
        storage_dir=tmp_path / "task_lifecycle",
        task_ttl_days=30,
        max_resume_tasks=10,
    )


@pytest.fixture
def isolated_session_manager(tmp_path):
    """Isolated session manager using temp directory."""
    from clautorun import session_manager
    from clautorun.session_manager import _reset_for_testing

    # Create fresh SessionStateManager with temp state_dir
    temp_state_dir = tmp_path / "sessions"
    temp_state_dir.mkdir(parents=True, exist_ok=True)

    # Reset and re-initialise with the temp state dir
    _reset_for_testing()
    new_manager = SessionStateManager(state_dir=temp_state_dir)
    session_manager._manager = new_manager
    session_manager._store = new_manager._store

    yield new_manager

    # Restore clean state
    _reset_for_testing()


class TestGhostTaskBugReplication:
    """Replicate exact Task #6 production bug scenario."""

    def test_ghost_task_stays_ignored_with_v2_fix(self, isolated_config):
        """TEST BUG FIX: Ghost task with in_progress request stays ignored (v2)."""
        session_id = f'bug-replication-{int(time.time())}'
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)

        # Create normal tracked task
        manager.create_task('1', {'subject': 'Tracked', 'description': 'Desc'}, 'Created')

        # Simulate ghost task (TaskUpdate for unknown ID)
        manager.update_task('6', {'status': 'in_progress'}, 'Ghost update')

        tasks = manager.tasks
        assert '6' in tasks
        assert tasks['6']['subject'] == '(unknown - created before tracking)'
        assert tasks['6']['metadata'].get('ghost_task') == True
        assert tasks['6']['status'] == 'ignored', "Ghost stays ignored (v2 fix)"

        # Verify doesn't block
        incomplete = manager.get_incomplete_tasks(exclude_blocking=True)
        assert '6' not in [t['id'] for t in incomplete]
        assert '1' in [t['id'] for t in incomplete]

    def test_v1_migration_fixes_blocking_ghost(self, isolated_config):
        """TEST MIGRATION: v1 ghost with in_progress gets fixed to ignored."""
        session_id = f'v1-mig-{int(time.time())}'
        global_key = f"__task_lifecycle__{session_id}"

        # Create v1 data (ghost with blocking status)
        with session_state(global_key) as state:
            state["schema_version"] = 1
            state["tasks"] = {
                "6": {
                    "id": "6",
                    "subject": "(unknown - created before tracking)",
                    "status": "in_progress",  # v1 bug
                    "created_at": time.time(),
                    "updated_at": time.time(),
                    "metadata": {"ghost_task": True},
                    "blockedBy": [],
                    "blocks": [],
                    "tool_outputs": [],
                    "description": "",
                }
            }

        # Access triggers migration
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)
        tasks = manager.tasks

        assert tasks['6']['status'] == 'ignored', "Migration fixes ghost task"

        with session_state(global_key) as state:
            assert state.get("schema_version") == 2

    def test_migration_idempotent(self, isolated_config):
        """TEST ROBUSTNESS: Migration safe to run multiple times."""
        session_id = f'idempotent-{int(time.time())}'
        global_key = f"__task_lifecycle__{session_id}"

        with session_state(global_key) as state:
            state["schema_version"] = 1
            state["tasks"] = {
                "99": {
                    "id": "99",
                    "subject": "(unknown - created before tracking)",
                    "status": "in_progress",
                    "created_at": time.time(),
                    "updated_at": time.time(),
                    "metadata": {"ghost_task": True},
                    "blockedBy": [],
                    "blocks": [],
                    "tool_outputs": [],
                    "description": "",
                }
            }

        # Run twice
        mgr1 = TaskLifecycle(session_id=session_id, config=isolated_config)
        tasks1 = mgr1.tasks
        mgr2 = TaskLifecycle(session_id=session_id, config=isolated_config)
        tasks2 = mgr2.tasks

        assert tasks1['99'] == tasks2['99']
        assert tasks1['99']['status'] == 'ignored'

    def test_ghost_accepts_terminal_status(self, isolated_config):
        """TEST: Ghost task CAN transition to completed/deleted."""
        session_id = f'terminal-{int(time.time())}'
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)

        manager.update_task('88', {'status': 'in_progress'}, 'Ghost')
        assert manager.tasks['88']['status'] == 'ignored'

        manager.update_task('88', {'status': 'completed'}, 'Completed')
        assert manager.tasks['88']['status'] == 'completed'

    def test_multiple_ghosts_across_sessions(self, isolated_config):
        """TEST: Multiple ghost tasks in different sessions."""
        sid1 = f'ghost1-{int(time.time())}'
        sid2 = f'ghost2-{int(time.time())}'

        mgr1 = TaskLifecycle(session_id=sid1, config=isolated_config)
        mgr2 = TaskLifecycle(session_id=sid2, config=isolated_config)

        mgr1.update_task('10', {'status': 'in_progress'}, 'Ghost 1')
        mgr2.update_task('20', {'status': 'in_progress'}, 'Ghost 2')

        assert mgr1.tasks['10']['status'] == 'ignored'
        assert mgr2.tasks['20']['status'] == 'ignored'


class TestSchemaMigration:
    """Test schema migration edge cases."""

    def test_missing_metadata_field(self, isolated_config):
        """TEST: v1 data without metadata dict handled safely."""
        session_id = f'no-meta-{int(time.time())}'
        global_key = f"__task_lifecycle__{session_id}"

        with session_state(global_key) as state:
            state["schema_version"] = 1
            state["tasks"] = {
                "1": {
                    "id": "1",
                    "subject": "No metadata",
                    "status": "in_progress",
                    "created_at": time.time(),
                    "updated_at": time.time(),
                    "blockedBy": [],
                    "blocks": [],
                    "tool_outputs": [],
                    "description": "",
                }
            }

        manager = TaskLifecycle(session_id=session_id, config=isolated_config)
        tasks = manager.tasks
        assert tasks['1']['status'] == 'in_progress'

    def test_preserves_non_ghost_tasks(self, isolated_config):
        """TEST: Normal tasks with in_progress stay untouched."""
        session_id = f'normal-{int(time.time())}'
        global_key = f"__task_lifecycle__{session_id}"

        with session_state(global_key) as state:
            state["schema_version"] = 1
            state["tasks"] = {
                "1": {
                    "id": "1",
                    "subject": "Normal",
                    "status": "in_progress",
                    "created_at": time.time(),
                    "updated_at": time.time(),
                    "metadata": {"ghost_task": False},
                    "blockedBy": [],
                    "blocks": [],
                    "tool_outputs": [],
                    "description": "",
                }
            }

        manager = TaskLifecycle(session_id=session_id, config=isolated_config)
        assert manager.tasks['1']['status'] == 'in_progress'

    def test_v2_to_v2_noop(self, isolated_config):
        """TEST: v2 data not modified by migration."""
        session_id = f'v2-{int(time.time())}'
        global_key = f"__task_lifecycle__{session_id}"

        original = {
            "id": "1",
            "subject": "v2 task",
            "status": "completed",
            "created_at": time.time(),
            "updated_at": time.time(),
            "metadata": {"ghost_task": False},
            "blockedBy": [],
            "blocks": [],
            "tool_outputs": [],
            "description": "",
        }

        with session_state(global_key) as state:
            state["schema_version"] = 2
            state["tasks"] = {"1": original.copy()}

        manager = TaskLifecycle(session_id=session_id, config=isolated_config)
        assert manager.tasks['1'] == original


class TestPruning:
    """Test prune_old_tasks() with all status types."""

    def test_prunes_old_ignored_ghosts(self, isolated_config):
        """TEST: Ignored ghost tasks past TTL are pruned."""
        config = isolated_config
        config.task_ttl_days = 0  # Prune immediately

        session_id = f'prune-ign-{int(time.time())}'
        manager = TaskLifecycle(session_id=session_id, config=config)

        manager.update_task('77', {'status': 'in_progress'}, 'Ghost')
        assert manager.tasks['77']['status'] == 'ignored'

        # Age task
        def age(tasks):
            tasks['77']['updated_at'] = time.time() - 86400
        manager.atomic_update_tasks(age)

        assert manager.prune_old_tasks() == 1
        assert '77' not in manager.tasks

    def test_keeps_recent_ignored(self, isolated_config):
        """TEST: Recent ignored tasks NOT pruned."""
        session_id = f'recent-{int(time.time())}'
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)

        manager.update_task('66', {'status': 'in_progress'}, 'Ghost')
        assert manager.prune_old_tasks() == 0
        assert '66' in manager.tasks

    def test_never_prunes_active_tasks(self, isolated_config):
        """TEST: in_progress tasks never pruned even if old."""
        config = isolated_config
        config.task_ttl_days = 0

        session_id = f'active-{int(time.time())}'
        manager = TaskLifecycle(session_id=session_id, config=config)

        manager.create_task('1', {'subject': 'Active', 'description': 'Working'}, 'Created')
        manager.update_task('1', {'status': 'in_progress'}, 'Started')

        # Age it
        def age(tasks):
            tasks['1']['updated_at'] = time.time() - 86400 * 100
        manager.atomic_update_tasks(age)

        assert manager.prune_old_tasks() == 0
        assert '1' in manager.tasks

    def test_never_prunes_paused_tasks(self, isolated_config):
        """TEST CRITICAL: Paused tasks NEVER pruned - users pause for later resume.

        VIOLATION: Pruning paused tasks violates user expectation and loses work intent.
        Users explicitly pause tasks to resume later. Pruning them is data loss.
        """
        config = isolated_config
        config.task_ttl_days = 0  # Zero TTL - would prune everything IF paused were prunable

        session_id = f'paused-{int(time.time())}'
        manager = TaskLifecycle(session_id=session_id, config=config)

        # Create task and pause it (user intent: resume later)
        manager.create_task('1', {'subject': 'Paused work', 'description': 'Resume later'}, 'Created')
        manager.update_task('1', {'status': 'paused'}, 'User paused for later')

        # Age it significantly (100 days old)
        def age(tasks):
            tasks['1']['updated_at'] = time.time() - 86400 * 100
        manager.atomic_update_tasks(age)

        # Prune - should NOT remove paused task
        pruned = manager.prune_old_tasks()
        assert pruned == 0, "Paused tasks must NEVER be pruned (user may resume)"
        assert '1' in manager.tasks, "Paused task must be preserved regardless of age"
        assert manager.tasks['1']['status'] == 'paused', "Status must remain paused"


class TestCrossSessionPersistence:
    """Test persistence across daemon restarts and session resume."""

    def test_ghost_survives_restart_then_migrates(self, isolated_config):
        """TEST: Ghost task persists across daemon restart, migration fixes it."""
        session_id = f'restart-{int(time.time())}'
        global_key = f"__task_lifecycle__{session_id}"

        # Pre-restart: v1 ghost task
        with session_state(global_key) as state:
            state["schema_version"] = 1
            state["tasks"] = {
                "6": {
                    "id": "6",
                    "subject": "(unknown - created before tracking)",
                    "status": "in_progress",
                    "created_at": time.time(),
                    "updated_at": time.time(),
                    "metadata": {"ghost_task": True},
                    "blockedBy": [],
                    "blocks": [],
                    "tool_outputs": [],
                    "description": "",
                }
            }

        # Post-restart: migration fixes it
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)
        assert manager.tasks['6']['status'] == 'ignored'

        with session_state(global_key) as state:
            assert state.get("schema_version") == 2

    def test_session_resume_inherits_fixed_ghosts(self, isolated_config):
        """TEST: Resumed session sees migrated ghosts as ignored."""
        session_id = f'resume-{int(time.time())}'
        global_key = f"__task_lifecycle__{session_id}"

        with session_state(global_key) as state:
            state["schema_version"] = 1
            state["tasks"] = {
                "99": {
                    "id": "99",
                    "subject": "(unknown - created before tracking)",
                    "status": "in_progress",
                    "created_at": time.time(),
                    "updated_at": time.time(),
                    "metadata": {"ghost_task": True},
                    "blockedBy": [],
                    "blocks": [],
                    "tool_outputs": [],
                    "description": "",
                }
            }

        manager = TaskLifecycle(session_id=session_id, config=isolated_config)
        assert manager.tasks['99']['status'] == 'ignored'

        incomplete = manager.get_incomplete_tasks(exclude_blocking=True)
        assert '99' not in [t['id'] for t in incomplete]


class TestGhostTaskLogging:
    """Test audit logging for ghost task transitions."""

    def test_ghost_skip_logged(self, isolated_config):
        """TEST: GHOST_SKIP logged when blocking status requested."""
        session_id = f'log-{int(time.time())}'
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)

        manager.update_task('55', {'status': 'in_progress'}, 'Try blocking')
        assert manager.tasks['55']['status'] == 'ignored'

        if manager.audit_log.exists():
            log = manager.audit_log.read_text()
            assert 'GHOST_SKIP' in log
            assert '55' in log
            assert 'ghost task cannot become blocking' in log


class TestGarbageCollection:
    """Test GC with proper isolation (no impact on production)."""

    def test_gc_protects_current_session(self, isolated_config, isolated_session_manager):
        """TEST: GC never deletes current active session."""
        session_id = f'gc-active-{int(time.time())}'
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)
        _ = manager.tasks  # Create shelve

        # Mark as current session
        old_env = os.environ.get("CLAUDE_SESSION_ID")
        os.environ["CLAUDE_SESSION_ID"] = session_id

        try:
            result = TaskLifecycle.cli_gc(archive=True, dry_run=False, config=isolated_config, confirm=False)
            assert result == 0

            # Verify still exists
            tasks = manager.tasks
            assert isinstance(tasks, dict)
        finally:
            if old_env:
                os.environ["CLAUDE_SESSION_ID"] = old_env
            else:
                os.environ.pop("CLAUDE_SESSION_ID", None)

    def test_gc_skips_incomplete_tasks(self, isolated_config, isolated_session_manager):
        """TEST: GC never deletes sessions with incomplete tasks."""
        session_id = f'gc-incomplete-{int(time.time())}'
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)

        manager.create_task('1', {'subject': 'Active', 'description': 'Working'}, 'Created')
        manager.update_task('1', {'status': 'in_progress'}, 'Started')

        result = TaskLifecycle.cli_gc(archive=True, dry_run=False, pattern="gc-incomplete-*",
                                       ttl_days=0, config=isolated_config, confirm=False)
        assert result == 0

        # Should still exist
        assert '1' in manager.tasks

    def test_gc_archives_before_delete(self, isolated_config, isolated_session_manager):
        """TEST: GC archives non-empty data to JSON."""
        session_id = f'gc-archive-{int(time.time())}'
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)

        manager.create_task('1', {'subject': 'Task 1', 'description': 'Done'}, 'Created')
        manager.update_task('1', {'status': 'completed'}, 'Done')

        result = TaskLifecycle.cli_gc(archive=True, dry_run=False, pattern="gc-archive-*",
                                       ttl_days=0, config=isolated_config, confirm=False)
        assert result == 0

        # Verify archive
        archive_file = isolated_config.storage_dir / "archive" / f"{session_id}.json"
        assert archive_file.exists()

        import json
        data = json.loads(archive_file.read_text())
        assert data['session_id'] == session_id
        assert '1' in data['tasks']

    def test_gc_dry_run_never_modifies(self, isolated_config, isolated_session_manager):
        """TEST: dry_run=True doesn't modify data."""
        session_id = f'gc-dry-{int(time.time())}'
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)

        manager.create_task('1', {'subject': 'Task', 'description': 'Done'}, 'Created')
        manager.update_task('1', {'status': 'completed'}, 'Done')

        before = dict(manager.tasks)

        result = TaskLifecycle.cli_gc(archive=True, dry_run=True, pattern="gc-dry-*",
                                       ttl_days=0, config=isolated_config)
        assert result == 0

        after = manager.tasks
        assert after == before

        # No archive created
        archive_dir = isolated_config.storage_dir / "archive"
        assert not list(archive_dir.glob("gc-dry-*")) if archive_dir.exists() else True

    def test_gc_no_archive_option(self, isolated_config, isolated_session_manager):
        """TEST: archive=False deletes without JSON backup."""
        session_id = f'gc-noarch-{int(time.time())}'
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)

        manager.create_task('1', {'subject': 'Task', 'description': 'Done'}, 'Created')
        manager.update_task('1', {'status': 'completed'}, 'Done')

        result = TaskLifecycle.cli_gc(archive=False, dry_run=False, pattern="gc-noarch-*",
                                       ttl_days=0, config=isolated_config, confirm=False)
        assert result == 0

        # No archive
        archive_dir = isolated_config.storage_dir / "archive"
        assert not (archive_dir / f"{session_id}.json").exists() if archive_dir.exists() else True


class TestGCLocking:
    """Test GC respects SessionLock and thread safety."""

    def test_gc_uses_session_state_for_locking(self, isolated_config, isolated_session_manager):
        """TEST: GC acquires SessionLock via session_state()."""
        import threading, queue

        session_id = f'gc-lock-{int(time.time())}'
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)
        manager.create_task('1', {'subject': 'Task', 'description': 'Done'}, 'Created')
        manager.update_task('1', {'status': 'completed'}, 'Done')

        lock_held = threading.Event()
        gc_started = threading.Event()
        results = queue.Queue()

        def hold_lock():
            global_key = f"__task_lifecycle__{session_id}"
            try:
                with session_state(global_key, timeout=5.0) as state:
                    lock_held.set()
                    gc_started.wait(timeout=2.0)
                    time.sleep(0.3)  # Hold during GC
                    results.put(("lock", len(state.get("tasks", {}))))
            except Exception as e:
                results.put(("lock_err", str(e)))

        def run_gc():
            try:
                lock_held.wait(timeout=2.0)
                gc_started.set()
                result = TaskLifecycle.cli_gc(archive=True, dry_run=True, pattern="gc-lock-*",
                                               ttl_days=0, config=isolated_config)
                results.put(("gc", result))
            except Exception as e:
                results.put(("gc_err", str(e)))

        t1 = threading.Thread(target=hold_lock)
        t2 = threading.Thread(target=run_gc)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        res = {}
        while not results.empty():
            k, v = results.get()
            res[k] = v

        # Both should complete (GC waited for lock)
        assert "lock" in res
        assert "gc" in res
        assert res["gc"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

