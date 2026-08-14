"""Unit tests for task lifecycle bug fixes and plan acceptance notification.

Covers 10 task lifecycle bugs (see notes/2026_03_11_task_system_architecture_and_bugs.md):
- Fix 1: NON_BLOCKING_STATUSES rename (was misleading BLOCKING_STATUSES)
- Fix 2: Ghost task update_task() returns 'ghost_skip' sentinel
- Fix 4: stop_block_count resets to 0 on task completion
- Fix 5: is_premature_stop() docstring documents chain ordering mitigation
- Fix 6: No standalone helper functions; get_plan_approval_injection() returns str
- Fix 7: PlanNotifyConfig @dataclass load/save roundtrip
- Fix 9: stage2_completion lowercase (was duplicate of ALL-CAPS stage2_message)

TDD approach: tests written BEFORE implementation to verify failures, then fixes applied.
"""

import multiprocessing
import threading
import time
from pathlib import Path

import pytest
from contextlib import contextmanager
from unittest.mock import MagicMock

from autorun.config import CONFIG
from autorun.core import EventContext
from autorun.task_lifecycle import (
    TaskLifecycle,
    TaskLifecycleConfig,
    _reset_ghost_counter,
)
from autorun.session_manager import SessionStateManager


def _task_projection_process_worker(
    state_dir: str,
    config_dir: str,
    session_id: str,
    action: str,
    results,
    start_gate=None,
    task_id: str = "shared",
    work_dir: str | None = None,
) -> None:
    """Write or read one task projection in a fresh spawned interpreter."""
    import os

    from autorun.core import EventContext
    from autorun.session_manager import _reset_for_testing
    from autorun.task_lifecycle import TaskLifecycle, TaskLifecycleConfig

    os.environ["AUTORUN_TEST_STATE_DIR"] = state_dir
    if work_dir is not None:
        os.chdir(work_dir)
    _reset_for_testing()
    lifecycle = TaskLifecycle(
        session_id=session_id,
        config=TaskLifecycleConfig(
            enabled=True,
            storage_dir=Path(config_dir),
            task_ttl_days=30,
            max_resume_tasks=10,
        ),
    )
    if start_gate is not None:
        if not start_gate.wait(timeout=10):
            results.put({"error": "start gate timed out"})
            _reset_for_testing()
            return
    if action == "write":
        lifecycle.create_task(task_id, {"subject": f"Task {task_id}"}, "created")
        lifecycle.update_task(task_id, {"status": "in_progress"}, "started")
        results.put({"written": task_id})
    elif action == "complete":
        lifecycle.update_task(task_id, {"status": "completed"}, "completed")
        results.put({"completed": task_id})
    elif action == "inspect":
        stop = lifecycle.handle_stop(
            EventContext(session_id=session_id, event="Stop", cli_type="pi")
        )
        results.put(
            {
                "projection": lifecycle.task_projection(limit=10),
                "stop_blocked": "block" in str(stop).lower(),
            }
        )
    else:
        results.put(lifecycle.task_projection(limit=10))
    _reset_for_testing()


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
    from autorun import session_manager
    from autorun.session_manager import _reset_for_testing

    temp_state_dir = tmp_path / "sessions"
    temp_state_dir.mkdir(parents=True, exist_ok=True)

    _reset_for_testing()
    new_manager = SessionStateManager(state_dir=temp_state_dir)
    session_manager._manager = new_manager
    session_manager._store = new_manager._store

    yield new_manager

    _reset_for_testing()


# --- Fix 9: stage2_completion must differ from stage2_message ---


class TestFix9Stage2Completion:
    def test_stage2_completion_differs_from_stage2_message(self):
        assert CONFIG["stage2_completion"] != CONFIG["stage2_message"], "stage2_completion should be a lowercase descriptive, not identical to stage2_message"

    def test_stage2_completion_is_lowercase(self):
        assert not CONFIG["stage2_completion"].isupper(), "stage2_completion should be lowercase descriptive text"


# --- Fix 1: BLOCKING_STATUSES renamed to NON_BLOCKING_STATUSES ---


class TestFix1NonBlockingStatuses:
    def test_non_blocking_statuses_exists(self):
        assert hasattr(TaskLifecycle, "NON_BLOCKING_STATUSES"), "TaskLifecycle should have NON_BLOCKING_STATUSES attribute"

    def test_blocking_statuses_removed(self):
        assert not hasattr(TaskLifecycle, "BLOCKING_STATUSES"), "TaskLifecycle should NOT have old BLOCKING_STATUSES attribute"

    def test_non_blocking_statuses_values(self):
        assert TaskLifecycle.NON_BLOCKING_STATUSES == frozenset(["completed", "deleted", "paused", "ignored", "delegated"])


# --- Fix 2: Ghost task returns sentinel ---


class TestFix2GhostTaskSentinel:
    def test_ghost_task_returns_ghost_skip(self, isolated_config, isolated_session_manager):
        session_id = f"test-ghost-sentinel-{time.time()}"
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)
        result = manager.update_task("999", {"status": "in_progress"}, "Ghost update")
        assert result == "ghost_skip", "Ghost task non-terminal update should return 'ghost_skip'"

    def test_ghost_task_terminal_returns_none(self, isolated_config, isolated_session_manager):
        session_id = f"test-ghost-terminal-{time.time()}"
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)
        # First touch creates the ghost
        manager.update_task("999", {"status": "in_progress"}, "Ghost create")
        # Terminal status should succeed
        result = manager.update_task("999", {"status": "completed"}, "Complete ghost")
        assert result is None, "Ghost task terminal update should return None (success)"

    def test_normal_task_returns_none(self, isolated_config, isolated_session_manager):
        session_id = f"test-normal-{time.time()}"
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)
        manager.create_task("1", {"subject": "Normal task"}, "Created")
        result = manager.update_task("1", {"status": "in_progress"}, "Start")
        assert result is None, "Normal task update should return None"


class TestCodexPlanChecklistSync:
    def _ctx(self, session_id, plan):
        return EventContext(
            session_id=session_id,
            event="PostToolUse",
            prompt="",
            tool_name="update_plan",
            tool_input={"plan": plan, "explanation": "sync checklist"},
            tool_result="plan updated",
            cli_type="codex",
        )

    def test_update_plan_syncs_native_checklist_tasks(self, isolated_config, isolated_session_manager):
        session_id = f"test-codex-plan-sync-{time.time()}"
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)

        manager.handle_plan_checklist(
            self._ctx(
                session_id,
                [
                    {"step": "Write focused regression", "status": "completed"},
                    {"step": "Implement native checklist sync", "status": "in_progress"},
                    {"step": "Run focused tests", "status": "pending"},
                ],
            )
        )

        tasks = manager.tasks
        assert tasks["plan-1"]["subject"] == "Write focused regression"
        assert tasks["plan-1"]["status"] == "completed"
        assert tasks["plan-2"]["status"] == "in_progress"
        assert tasks["plan-2"]["metadata"]["source"] == "plan_checklist"
        assert tasks["plan-2"]["metadata"]["platform"] == "codex"

    def test_update_plan_replacement_keeps_explicit_tasks(self, isolated_config, isolated_session_manager):
        session_id = f"test-codex-plan-keeps-explicit-{time.time()}"
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)
        manager.create_task("1", {"subject": "Explicit Claude task"}, "created")

        manager.handle_plan_checklist(
            self._ctx(
                session_id,
                [
                    {"step": "Codex checklist item", "status": "in_progress"},
                ],
            )
        )
        manager.handle_plan_checklist(
            self._ctx(
                session_id,
                [
                    {"step": "Codex checklist item updated", "status": "completed"},
                ],
            )
        )

        tasks = manager.tasks
        assert tasks["1"]["subject"] == "Explicit Claude task"
        assert tasks["1"]["status"] == "pending"
        assert tasks["plan-1"]["subject"] == "Codex checklist item updated"
        assert tasks["plan-1"]["status"] == "completed"

    def test_update_plan_removes_only_own_missing_checklist_items(self, isolated_config, isolated_session_manager):
        session_id = f"test-codex-plan-removes-stale-{time.time()}"
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)
        manager.create_task("external", {"subject": "External explicit task"}, "created")

        manager.handle_plan_checklist(
            self._ctx(
                session_id,
                [
                    {"step": "Keep this item", "status": "in_progress"},
                    {"step": "Remove this item", "status": "pending"},
                ],
            )
        )
        manager.handle_plan_checklist(
            self._ctx(
                session_id,
                [
                    {"step": "Keep this item", "status": "in_progress"},
                ],
            )
        )

        tasks = manager.tasks
        assert tasks["plan-1"]["status"] == "in_progress"
        assert tasks["plan-2"]["status"] == "deleted"
        assert tasks["external"]["status"] == "pending"

    def test_update_plan_is_session_scoped(self, isolated_config, isolated_session_manager):
        session_a = f"test-codex-plan-session-a-{time.time()}"
        session_b = f"test-codex-plan-session-b-{time.time()}"
        manager_a = TaskLifecycle(session_id=session_a, config=isolated_config)
        manager_b = TaskLifecycle(session_id=session_b, config=isolated_config)

        manager_a.handle_plan_checklist(
            self._ctx(
                session_a,
                [
                    {"step": "A checklist item", "status": "in_progress"},
                ],
            )
        )
        manager_b.handle_plan_checklist(
            self._ctx(
                session_b,
                [
                    {"step": "B checklist item", "status": "pending"},
                ],
            )
        )

        assert manager_a.tasks["plan-1"]["subject"] == "A checklist item"
        assert manager_b.tasks["plan-1"]["subject"] == "B checklist item"


# --- Fix 4: Block count resets on task completion ---


class TestFix4BlockCountReset:
    def test_block_count_resets_on_completion(self, isolated_config, isolated_session_manager):
        session_id = f"test-block-reset-{time.time()}"
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)
        manager.create_task("1", {"subject": "Task A"}, "Created")

        # Simulate stop blocks
        def set_count(metadata):
            metadata["stop_block_count"] = 3

        manager.atomic_update_metadata(set_count)

        # Complete task should reset counter
        manager.update_task("1", {"status": "completed"}, "Done")
        assert manager.session_metadata.get("stop_block_count", 0) == 0, "Block count should reset to 0 on task completion"

    def test_block_count_no_reset_on_ghost_skip(self, isolated_config, isolated_session_manager):
        session_id = f"test-block-ghost-{time.time()}"
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)

        def set_count(metadata):
            metadata["stop_block_count"] = 3

        manager.atomic_update_metadata(set_count)

        # Ghost task skip should NOT reset counter
        manager.update_task("999", {"status": "in_progress"}, "Ghost")
        assert manager.session_metadata.get("stop_block_count", 0) == 3, "Block count should NOT reset on ghost skip"


class TestMetadataNoOpPersistence:
    def test_noop_ghost_reset_does_not_save_shared_state(
        self,
        isolated_config,
        monkeypatch,
    ):
        manager = TaskLifecycle(
            session_id=f"test-noop-ghost-reset-{time.time()}",
            config=isolated_config,
        )

        class TrackingState(dict):
            writes = 0

            def __setitem__(self, key, value):
                self.writes += 1
                super().__setitem__(key, value)

        state = TrackingState(
            {
                "session_metadata": {
                    "session_id": manager.session_id,
                    "stop_block_count": 0,
                    "consecutive_identical_stop_block_count": 0,
                }
            }
        )

        @contextmanager
        def isolated_state():
            yield state

        monkeypatch.setattr(manager, "_session_state", isolated_state)

        manager.atomic_update_metadata(_reset_ghost_counter)

        assert state.writes == 0, "An already-clear ghost counter must not rewrite shared state"

    def test_changed_ghost_metadata_saves_once(
        self,
        isolated_config,
        monkeypatch,
    ):
        manager = TaskLifecycle(
            session_id=f"test-changed-ghost-reset-{time.time()}",
            config=isolated_config,
        )

        class TrackingState(dict):
            writes = 0

            def __setitem__(self, key, value):
                self.writes += 1
                super().__setitem__(key, value)

        state = TrackingState(
            {
                "session_metadata": {
                    "session_id": manager.session_id,
                    "stop_block_count": 0,
                    "consecutive_identical_stop_block_count": 2,
                    "last_stop_block_id_hash": "abc123",
                }
            }
        )

        @contextmanager
        def isolated_state():
            yield state

        monkeypatch.setattr(manager, "_session_state", isolated_state)

        manager.atomic_update_metadata(_reset_ghost_counter)

        assert state.writes == 1
        metadata = state["session_metadata"]
        assert metadata.get("consecutive_identical_stop_block_count") == 0
        assert "last_stop_block_id_hash" not in metadata


# --- Fix 5: is_premature_stop documents task mitigation ---


class TestFix5IsPrematureStopDocstring:
    def test_docstring_mentions_task_checking(self):
        import inspect
        from autorun.plugins import is_premature_stop

        source = inspect.getsource(is_premature_stop)
        assert "prevent_premature_stop" in source, "is_premature_stop should document that task checking is in prevent_premature_stop"


# --- Fix 7: PlanNotifyConfig ---


class TestFix7PlanNotifyConfig:
    def test_plan_notify_config_load_defaults(self, tmp_path, monkeypatch):
        import autorun.task_lifecycle as tl

        monkeypatch.setattr(tl, "PLAN_NOTIFY_CONFIG_PATH", tmp_path / "pn.json")
        from autorun.task_lifecycle import PlanNotifyConfig

        cfg = PlanNotifyConfig.load()
        assert cfg.tdd_scaffolding is True
        assert cfg.task_update_enforcement is True
        assert cfg.dependency_wiring is True

    def test_plan_notify_config_save_load_roundtrip(self, tmp_path, monkeypatch):
        import autorun.task_lifecycle as tl

        monkeypatch.setattr(tl, "PLAN_NOTIFY_CONFIG_PATH", tmp_path / "pn.json")
        from autorun.task_lifecycle import PlanNotifyConfig

        cfg = PlanNotifyConfig(tdd_scaffolding=False)
        cfg.save()
        cfg2 = PlanNotifyConfig.load()
        assert cfg2.tdd_scaffolding is False
        assert cfg2.task_update_enforcement is True


# --- Fix 6: No standalone helper functions ---


class TestFix6NoStandaloneHelpers:
    def test_no_standalone_helper_functions(self):
        import inspect
        from autorun import plugins

        source = inspect.getsource(plugins)
        assert "_load_plan_notify_config" not in source
        assert "_get_plan_task_injection" not in source
        assert "_build_plan_acceptance_notification" not in source


# --- Fix 6: get_plan_approval_injection returns string ---


class TestFix6PlanApprovalInjection:
    def test_returns_none_without_plan_key(self, isolated_config, isolated_session_manager):
        session_id = f"test-plan-inject-{time.time()}"
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)
        ctx = MagicMock()
        ctx.plan_arguments = ""
        result = manager.get_plan_approval_injection(ctx)
        assert result is None

    def test_returns_string_with_tasks(self, isolated_config, isolated_session_manager):
        session_id = f"test-plan-tasks-{time.time()}"
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)
        # Create task and link to plan
        manager.create_task("1", {"subject": "Test task"}, "Created")
        manager.link_task_to_plan("1", "test-plan")
        ctx = MagicMock()
        ctx.plan_arguments = "test-plan"
        result = manager.get_plan_approval_injection(ctx)
        assert isinstance(result, str), "get_plan_approval_injection should return a string, not a dict"
        assert "Test task" in result


# --- Regression: handle_task_create/update with string tool_result (MagicMock fix) ---


class TestTaskProjection:
    def test_projection_prioritizes_incomplete_work_and_bounds_history(
        self, isolated_config, isolated_session_manager
    ):
        manager = TaskLifecycle(
            session_id=f"test-task-projection-{time.time()}",
            config=isolated_config,
        )
        manager.create_task("ready", {"subject": "Ready"}, "created")
        manager.update_task("ready", {"status": "in_progress"}, "started")
        manager.create_task("blocked", {"subject": "Blocked"}, "created")
        manager.update_task(
            "blocked", {"addBlockedBy": ["ready"]}, "blocked"
        )
        manager.create_task("done", {"subject": "Done"}, "created")
        manager.update_task("done", {"status": "completed"}, "done")

        projection = manager.task_projection(limit=2)

        assert projection == {
            "tasks": [manager.tasks["ready"], manager.tasks["blocked"]],
            "total": 3,
            "truncated": True,
        }

    def test_projection_remains_valid_during_concurrent_updates(
        self, isolated_config, isolated_session_manager
    ):
        manager = TaskLifecycle(
            session_id=f"test-task-projection-concurrent-{time.time()}",
            config=isolated_config,
        )
        for index in range(8):
            manager.create_task(str(index), {"subject": f"Task {index}"}, "created")

        errors = []
        barrier = threading.Barrier(3)

        def mutate():
            try:
                barrier.wait(timeout=10)
                for index in range(8):
                    manager.update_task(
                        str(index), {"status": "in_progress"}, "started"
                    )
                    manager.update_task(
                        str(index), {"status": "completed"}, "done"
                    )
            except Exception as error:  # noqa: BLE001 - asserted below
                errors.append(error)

        def project():
            try:
                barrier.wait(timeout=10)
                for _ in range(20):
                    result = manager.task_projection(limit=5)
                    assert len(result["tasks"]) <= 5
                    assert result["total"] == 8
                    assert len({task["id"] for task in result["tasks"]}) == len(
                        result["tasks"]
                    )
            except Exception as error:  # noqa: BLE001 - asserted below
                errors.append(error)

        threads = [threading.Thread(target=mutate), threading.Thread(target=project)]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=10)
        for thread in threads:
            thread.join(timeout=30)

        assert not any(thread.is_alive() for thread in threads)
        assert errors == []
        assert manager.task_projection(limit=8)["total"] == 8

    def test_projection_persists_across_spawned_processes(
        self, tmp_path, isolated_session_manager
    ):
        spawn = multiprocessing.get_context("spawn")
        results = spawn.Queue()
        state_dir = str(isolated_session_manager.state_dir)
        config_dir = str(tmp_path / "task-config")
        session_id = f"test-task-projection-process-{time.time()}"

        writer = spawn.Process(
            target=_task_projection_process_worker,
            args=(state_dir, config_dir, session_id, "write", results),
        )
        writer.start()
        writer.join(timeout=30)
        assert writer.exitcode == 0
        assert results.get(timeout=5) == {"written": "shared"}

        reader = spawn.Process(
            target=_task_projection_process_worker,
            args=(state_dir, config_dir, session_id, "read", results),
        )
        reader.start()
        reader.join(timeout=30)
        assert reader.exitcode == 0
        projection = results.get(timeout=5)

        assert projection["total"] == 1
        assert projection["truncated"] is False
        assert projection["tasks"][0]["id"] == "shared"
        assert projection["tasks"][0]["status"] == "in_progress"

    @pytest.mark.parametrize(
        ("same_session", "expected"),
        [(True, {"left", "right"}), (False, {"left"})],
    )
    def test_simultaneous_spawned_processes_preserve_session_boundaries(
        self, tmp_path, isolated_session_manager, same_session, expected
    ):
        """Concurrent processes lose no writes and do not cross session IDs."""
        spawn = multiprocessing.get_context("spawn")
        results = spawn.Queue()
        start_gate = spawn.Event()
        state_dir = str(isolated_session_manager.state_dir)
        config_dir = str(tmp_path / "process-config")
        first_session = f"process-a-{time.time_ns()}"
        second_session = first_session if same_session else f"process-b-{time.time_ns()}"
        common_work_dir = tmp_path / "common-work"
        left_work_dir = common_work_dir if not same_session else tmp_path / "left-work"
        right_work_dir = common_work_dir if not same_session else tmp_path / "right-work"
        for work_dir in {left_work_dir, right_work_dir}:
            work_dir.mkdir()
        workers = [
            spawn.Process(
                target=_task_projection_process_worker,
                args=(
                    state_dir,
                    config_dir,
                    session_id,
                    "write",
                    results,
                    start_gate,
                    task_id,
                    str(work_dir),
                ),
            )
            for session_id, task_id, work_dir in (
                (first_session, "left", left_work_dir),
                (second_session, "right", right_work_dir),
            )
        ]
        for worker in workers:
            worker.start()
        start_gate.set()
        for worker in workers:
            worker.join(timeout=30)

        assert all(worker.exitcode == 0 for worker in workers)
        receipts = {results.get(timeout=5)["written"] for _ in workers}
        assert receipts == {"left", "right"}

        def read_in_fresh_process(session_id):
            reader = spawn.Process(
                target=_task_projection_process_worker,
                args=(state_dir, config_dir, session_id, "read", results),
            )
            reader.start()
            reader.join(timeout=30)
            assert reader.exitcode == 0
            return results.get(timeout=5)

        assert {task["id"] for task in read_in_fresh_process(first_session)["tasks"]} == expected
        if not same_session:
            assert {
                task["id"] for task in read_in_fresh_process(second_session)["tasks"]
            } == {"right"}

    def test_spawned_process_lifecycle_blocks_until_concurrent_work_completes(
        self, tmp_path, isolated_session_manager
    ):
        """Fresh processes share create/update/Stop lifecycle without lost writes."""
        spawn = multiprocessing.get_context("spawn")
        results = spawn.Queue()
        state_dir = str(isolated_session_manager.state_dir)
        config_dir = str(tmp_path / "lifecycle-process-config")
        session_id = f"process-lifecycle-{time.time_ns()}"

        def run_pair(action, receipt_key):
            gate = spawn.Event()
            workers = [
                spawn.Process(
                    target=_task_projection_process_worker,
                    args=(
                        state_dir,
                        config_dir,
                        session_id,
                        action,
                        results,
                        gate,
                        task_id,
                    ),
                )
                for task_id in ("left", "right")
            ]
            for worker in workers:
                worker.start()
            gate.set()
            for worker in workers:
                worker.join(timeout=30)
            assert all(worker.exitcode == 0 for worker in workers)
            assert {results.get(timeout=5)[receipt_key] for _ in workers} == {
                "left",
                "right",
            }

        def inspect():
            reader = spawn.Process(
                target=_task_projection_process_worker,
                args=(state_dir, config_dir, session_id, "inspect", results),
            )
            reader.start()
            reader.join(timeout=30)
            assert reader.exitcode == 0
            return results.get(timeout=5)

        run_pair("write", "written")
        pending = inspect()
        assert pending["stop_blocked"] is True
        assert {task["id"] for task in pending["projection"]["tasks"]} == {
            "left",
            "right",
        }

        run_pair("complete", "completed")
        completed = inspect()
        assert completed["stop_blocked"] is False
        assert {task["status"] for task in completed["projection"]["tasks"]} == {
            "completed"
        }

    def test_lifecycle_resumes_across_working_directories_and_harness_surfaces(
        self, isolated_config, isolated_session_manager, tmp_path
    ):
        """cwd is not identity; supported harness tools share lifecycle state."""
        session_id = f"cross-cwd-{time.time_ns()}"
        create_ctx = EventContext(
            session_id=session_id,
            event="PostToolUse",
            cwd=str(tmp_path / "project-a"),
            tool_name="TaskCreate",
            tool_input={"subject": "Cross-directory task"},
            tool_result={"task": {"id": "cross-dir"}},
            cli_type="claude",
        )
        TaskLifecycle(ctx=create_ctx, config=isolated_config).handle_task_create(create_ctx)

        resumed = TaskLifecycle(
            ctx=EventContext(
                session_id=session_id,
                event="SessionStart",
                cwd=str(tmp_path / "project-b"),
                cli_type="pi",
            ),
            config=isolated_config,
        )
        resume_response = resumed.handle_session_start(resumed.ctx)
        assert "outstanding incomplete tasks" in str(resume_response)
        stop_response = resumed.handle_stop(
            EventContext(
                session_id=session_id,
                event="Stop",
                cwd=str(tmp_path / "project-b"),
                cli_type="pi",
            )
        )
        assert "block" in str(stop_response).lower()

        update_ctx = EventContext(
            session_id=session_id,
            event="PostToolUse",
            cwd=str(tmp_path / "project-c"),
            tool_name="TaskUpdate",
            tool_input={"taskId": "cross-dir", "status": "completed"},
            tool_result={"taskId": "cross-dir"},
            cli_type="pi",
        )
        TaskLifecycle(ctx=update_ctx, config=isolated_config).handle_task_update(update_ctx)
        assert TaskLifecycle(ctx=update_ctx, config=isolated_config).tasks["cross-dir"][
            "status"
        ] == "completed"
        assert TaskLifecycle(ctx=update_ctx, config=isolated_config).handle_stop(
            EventContext(
                session_id=session_id,
                event="Stop",
                cwd=str(tmp_path / "project-c"),
                cli_type="claude",
            )
        ) is None

    def test_same_session_id_is_isolated_by_state_directory(self, tmp_path, monkeypatch):
        """Two redirected runtime roots cannot see each other's same-named session."""
        from autorun.session_manager import _reset_for_testing

        session_id = f"same-name-{time.time_ns()}"
        observed = []
        try:
            for root, task_id in ((tmp_path / "one", "one"), (tmp_path / "two", "two")):
                monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(root / "state"))
                _reset_for_testing()
                config = TaskLifecycleConfig(enabled=True, storage_dir=root / "tasks")
                lifecycle = TaskLifecycle(session_id=session_id, config=config)
                lifecycle.create_task(task_id, {"subject": task_id}, "created")
                observed.append(set(lifecycle.tasks))
        finally:
            _reset_for_testing()

        assert observed == [{"one"}, {"two"}]

    def test_projection_rejects_invalid_limit(
        self, isolated_config, isolated_session_manager
    ):
        manager = TaskLifecycle(
            session_id=f"test-task-projection-limit-{time.time()}",
            config=isolated_config,
        )

        with pytest.raises(ValueError, match="positive integer"):
            manager.task_projection(limit=0)


class TestHandleTaskCreateStringResult:
    """Regression: handle_task_create and handle_task_update must work when
    ctx.tool_result is a plain string (Gemini CLI, test mocks).

    Root cause: code used ctx.tool_result_str (JSON-serialized) for regex fallback
    and create_task/update_task calls. When ctx is a MagicMock, tool_result_str is
    also a MagicMock which can't be JSON-serialized or regex-matched.

    Fix: check isinstance(raw_result, str) and use it directly.
    """

    def test_create_task_with_string_result(self, isolated_config, isolated_session_manager):
        """handle_task_create extracts ID and creates task from string result."""
        session_id = f"test-str-create-{time.time()}"
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)

        ctx = MagicMock()
        ctx.tool_result = "Task #42 created successfully"
        ctx.tool_input = {"subject": "Test task", "description": "desc"}
        ctx.plan_active = False

        manager.handle_task_create(ctx)

        assert "42" in manager.tasks, "Task ID should be extracted from string result"
        assert manager.tasks["42"]["subject"] == "Test task"

    def test_pi_create_task_is_tagged_with_internal_source(self, isolated_config, isolated_session_manager):
        session_id = f"test-pi-source-{time.time()}"
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)
        manager._cli_type = "pi"

        ctx = MagicMock()
        ctx.tool_result = {"task": {"id": "pi-1"}}
        ctx.tool_result_str = '{"task":{"id":"pi-1"}}'
        ctx.tool_input = {
            "subject": "Pi task",
            "description": "desc",
            "metadata": {"caller": "model"},
        }
        ctx.plan_active = False

        manager.handle_task_create(ctx)

        assert manager.tasks["pi-1"]["metadata"] == {
            "caller": "model",
            "source": "pi_task_tool",
        }

    def test_pi_unknown_update_is_tagged_for_branch_reprojection(
        self, isolated_config, isolated_session_manager
    ):
        manager = TaskLifecycle(
            session_id=f"test-pi-ghost-source-{time.time()}",
            config=isolated_config,
        )
        manager._cli_type = "pi"
        ctx = MagicMock()
        ctx.tool_result = "Updated task pi-ghost"
        ctx.tool_input = {"taskId": "pi-ghost", "status": "in_progress"}

        assert manager.handle_task_update(ctx) == "ghost_skip"
        assert manager.tasks["pi-ghost"]["metadata"] == {
            "ghost_task": True,
            "source": "pi_task_tool",
        }

    def test_pi_branch_projection_replaces_only_pi_sourced_rows(self, isolated_config, isolated_session_manager):
        manager = TaskLifecycle(session_id=f"test-pi-reproject-{time.time()}", config=isolated_config)
        manager.create_task("pi-old", {"subject": "Old", "metadata": {"source": "pi_task_tool"}}, "old")
        manager.create_task("shared", {"subject": "Keep", "metadata": {"source": "manual"}}, "keep")

        manager.replace_task_projection([
            {
                "id": "pi-new", "subject": "New", "description": "", "activeForm": "Doing",
                "status": "in_progress", "owner": None, "blockedBy": [], "blocks": [],
                "metadata": {"source": "pi_task_tool"}, "tool_outputs": [],
            }
        ], source="pi_task_tool")

        assert set(manager.tasks) == {"shared", "pi-new"}
        assert manager.tasks["shared"]["subject"] == "Keep"
        assert manager.tasks["pi-new"]["status"] == "in_progress"

    def test_create_task_stores_string_in_tool_outputs(self, isolated_config, isolated_session_manager):
        """create_task stores the string result in tool_outputs (not MagicMock)."""
        session_id = f"test-str-outputs-{time.time()}"
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)

        ctx = MagicMock()
        ctx.tool_result = "Task #7 created successfully"
        ctx.tool_input = {"subject": "String result task", "description": ""}
        ctx.plan_active = False

        manager.handle_task_create(ctx)

        task = manager.tasks["7"]
        assert isinstance(task["tool_outputs"][0], str), "tool_outputs should contain a string, not a MagicMock"
        assert "Task #7" in task["tool_outputs"][0]

    def test_update_task_with_string_result(self, isolated_config, isolated_session_manager):
        """handle_task_update works with string ctx.tool_result."""
        session_id = f"test-str-update-{time.time()}"
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)

        # Create task first
        manager.create_task("1", {"subject": "To update"}, "Created")

        ctx = MagicMock()
        ctx.tool_result = "Task #1 updated successfully"
        ctx.tool_input = {"taskId": "1", "status": "in_progress"}

        result = manager.handle_task_update(ctx)
        assert result is None  # Normal update returns None
        assert manager.tasks["1"]["status"] == "in_progress"


# --- Regression: validate_hook_response keeps permissionDecision in Gemini HSO ---


class TestGeminiHSOPermissionDecision:
    """Regression: Gemini BeforeTool HSO must include permissionDecision and
    permissionDecisionReason for portable test assertions.

    Root cause: validate_hook_response stripped these from Gemini PreToolUse HSO,
    keeping only hookEventName and tool_input. Tests checking
    hookSpecificOutput.permissionDecision got 'allow' (default) instead of 'deny'.
    """

    def test_gemini_pretooluse_deny_has_permission_decision(self):
        """Gemini PreToolUse deny response includes permissionDecision in HSO."""
        from autorun.core import validate_hook_response

        response = {
            "decision": "deny",
            "reason": "blocked",
            "continue": True,
            "stopReason": "",
            "suppressOutput": False,
            "systemMessage": "blocked",
            "hookSpecificOutput": {
                "hookEventName": "BeforeTool",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Use Read tool instead",
            },
        }
        filtered = validate_hook_response("PreToolUse", response, cli_type="gemini")
        hso = filtered.get("hookSpecificOutput", {})
        assert hso.get("permissionDecision") == "deny", "Gemini BeforeTool HSO must keep permissionDecision for portable assertions"
        assert "Read tool" in hso.get("permissionDecisionReason", ""), "Gemini BeforeTool HSO must keep permissionDecisionReason"

    def test_gemini_pretooluse_allow_has_permission_decision(self):
        """Gemini PreToolUse allow response includes permissionDecision in HSO."""
        from autorun.core import validate_hook_response

        response = {
            "decision": "allow",
            "reason": "",
            "continue": True,
            "stopReason": "",
            "suppressOutput": False,
            "systemMessage": "",
            "hookSpecificOutput": {
                "hookEventName": "BeforeTool",
                "permissionDecision": "allow",
                "permissionDecisionReason": "",
            },
        }
        filtered = validate_hook_response("PreToolUse", response, cli_type="gemini")
        hso = filtered.get("hookSpecificOutput", {})
        assert hso.get("permissionDecision") == "allow"


# --- Regression: task staleness message includes description parameter ---


class TestTaskStalenessMessageSchema:
    """Regression: task staleness messages must include 'description' parameter
    in TaskCreate examples to match actual tool schema.

    Root cause: messages showed TaskCreate(subject="...") without description,
    causing AI to call TaskCreate without required 'description' parameter.
    """

    def test_staleness_message_includes_description(self):
        """Main staleness message TaskCreate example includes description."""
        msg = CONFIG["task_staleness_message"]
        assert "description" in msg, "task_staleness_message must include 'description' in TaskCreate example"

    def test_staleness_message_2nd_includes_description(self):
        """Second staleness message TaskCreate example includes description."""
        msg = CONFIG["task_staleness_message_2nd"]
        assert "description" in msg, "task_staleness_message_2nd must include 'description' in TaskCreate example"
