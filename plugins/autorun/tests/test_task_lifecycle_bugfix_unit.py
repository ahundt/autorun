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
import json
import time
import pytest
from unittest.mock import MagicMock

from autorun.config import CONFIG
from autorun.task_lifecycle import TaskLifecycle, TaskLifecycleConfig
from autorun.session_manager import session_state, SessionStateManager


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
        assert CONFIG["stage2_completion"] != CONFIG["stage2_message"], \
            "stage2_completion should be a lowercase descriptive, not identical to stage2_message"

    def test_stage2_completion_is_lowercase(self):
        assert not CONFIG["stage2_completion"].isupper(), \
            "stage2_completion should be lowercase descriptive text"


# --- Fix 1: BLOCKING_STATUSES renamed to NON_BLOCKING_STATUSES ---

class TestFix1NonBlockingStatuses:
    def test_non_blocking_statuses_exists(self):
        assert hasattr(TaskLifecycle, 'NON_BLOCKING_STATUSES'), \
            "TaskLifecycle should have NON_BLOCKING_STATUSES attribute"

    def test_blocking_statuses_removed(self):
        assert not hasattr(TaskLifecycle, 'BLOCKING_STATUSES'), \
            "TaskLifecycle should NOT have old BLOCKING_STATUSES attribute"

    def test_non_blocking_statuses_values(self):
        assert TaskLifecycle.NON_BLOCKING_STATUSES == frozenset(
            ["completed", "deleted", "paused", "ignored"])


# --- Fix 2: Ghost task returns sentinel ---

class TestFix2GhostTaskSentinel:
    def test_ghost_task_returns_ghost_skip(self, isolated_config, isolated_session_manager):
        session_id = f"test-ghost-sentinel-{time.time()}"
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)
        result = manager.update_task('999', {'status': 'in_progress'}, 'Ghost update')
        assert result == "ghost_skip", \
            "Ghost task non-terminal update should return 'ghost_skip'"

    def test_ghost_task_terminal_returns_none(self, isolated_config, isolated_session_manager):
        session_id = f"test-ghost-terminal-{time.time()}"
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)
        # First touch creates the ghost
        manager.update_task('999', {'status': 'in_progress'}, 'Ghost create')
        # Terminal status should succeed
        result = manager.update_task('999', {'status': 'completed'}, 'Complete ghost')
        assert result is None, \
            "Ghost task terminal update should return None (success)"

    def test_normal_task_returns_none(self, isolated_config, isolated_session_manager):
        session_id = f"test-normal-{time.time()}"
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)
        manager.create_task('1', {'subject': 'Normal task'}, 'Created')
        result = manager.update_task('1', {'status': 'in_progress'}, 'Start')
        assert result is None, \
            "Normal task update should return None"


# --- Fix 4: Block count resets on task completion ---

class TestFix4BlockCountReset:
    def test_block_count_resets_on_completion(self, isolated_config, isolated_session_manager):
        session_id = f"test-block-reset-{time.time()}"
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)
        manager.create_task('1', {'subject': 'Task A'}, 'Created')

        # Simulate stop blocks
        def set_count(metadata):
            metadata['stop_block_count'] = 3
        manager.atomic_update_metadata(set_count)

        # Complete task should reset counter
        manager.update_task('1', {'status': 'completed'}, 'Done')
        assert manager.session_metadata.get('stop_block_count', 0) == 0, \
            "Block count should reset to 0 on task completion"

    def test_block_count_no_reset_on_ghost_skip(self, isolated_config, isolated_session_manager):
        session_id = f"test-block-ghost-{time.time()}"
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)

        def set_count(metadata):
            metadata['stop_block_count'] = 3
        manager.atomic_update_metadata(set_count)

        # Ghost task skip should NOT reset counter
        manager.update_task('999', {'status': 'in_progress'}, 'Ghost')
        assert manager.session_metadata.get('stop_block_count', 0) == 3, \
            "Block count should NOT reset on ghost skip"


# --- Fix 5: is_premature_stop documents task mitigation ---

class TestFix5IsPrematureStopDocstring:
    def test_docstring_mentions_task_checking(self):
        import inspect
        from autorun.plugins import is_premature_stop
        source = inspect.getsource(is_premature_stop)
        assert "prevent_premature_stop" in source, \
            "is_premature_stop should document that task checking is in prevent_premature_stop"


# --- Fix 7: PlanNotifyConfig ---

class TestFix7PlanNotifyConfig:
    def test_plan_notify_config_load_defaults(self, tmp_path, monkeypatch):
        import autorun.task_lifecycle as tl
        monkeypatch.setattr(tl, 'PLAN_NOTIFY_CONFIG_PATH', tmp_path / 'pn.json')
        from autorun.task_lifecycle import PlanNotifyConfig
        cfg = PlanNotifyConfig.load()
        assert cfg.tdd_scaffolding is True
        assert cfg.task_update_enforcement is True
        assert cfg.dependency_wiring is True

    def test_plan_notify_config_save_load_roundtrip(self, tmp_path, monkeypatch):
        import autorun.task_lifecycle as tl
        monkeypatch.setattr(tl, 'PLAN_NOTIFY_CONFIG_PATH', tmp_path / 'pn.json')
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
        assert '_load_plan_notify_config' not in source
        assert '_get_plan_task_injection' not in source
        assert '_build_plan_acceptance_notification' not in source


# --- Fix 6: get_plan_approval_injection returns string ---

class TestFix6PlanApprovalInjection:
    def test_returns_none_without_plan_key(self, isolated_config, isolated_session_manager):
        session_id = f"test-plan-inject-{time.time()}"
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)
        ctx = MagicMock()
        ctx.plan_arguments = ''
        result = manager.get_plan_approval_injection(ctx)
        assert result is None

    def test_returns_string_with_tasks(self, isolated_config, isolated_session_manager):
        session_id = f"test-plan-tasks-{time.time()}"
        manager = TaskLifecycle(session_id=session_id, config=isolated_config)
        # Create task and link to plan
        manager.create_task('1', {'subject': 'Test task'}, 'Created')
        manager.link_task_to_plan('1', 'test-plan')
        ctx = MagicMock()
        ctx.plan_arguments = 'test-plan'
        result = manager.get_plan_approval_injection(ctx)
        assert isinstance(result, str), \
            "get_plan_approval_injection should return a string, not a dict"
        assert "Test task" in result


# --- Regression: handle_task_create/update with string tool_result (MagicMock fix) ---

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
        assert isinstance(task["tool_outputs"][0], str), \
            "tool_outputs should contain a string, not a MagicMock"
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
        assert hso.get("permissionDecision") == "deny", \
            "Gemini BeforeTool HSO must keep permissionDecision for portable assertions"
        assert "Read tool" in hso.get("permissionDecisionReason", ""), \
            "Gemini BeforeTool HSO must keep permissionDecisionReason"

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
        assert "description" in msg, \
            "task_staleness_message must include 'description' in TaskCreate example"

    def test_staleness_message_2nd_includes_description(self):
        """Second staleness message TaskCreate example includes description."""
        msg = CONFIG["task_staleness_message_2nd"]
        assert "description" in msg, \
            "task_staleness_message_2nd must include 'description' in TaskCreate example"
