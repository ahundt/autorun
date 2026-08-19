"""Task guidance must name actions the running harness can actually perform.

Four defects, all observed live, all in messages autorun sends the AI:

  1. Stop/SessionStart guidance told the AI to call
     TaskUpdate(taskId="X", status="delegated"). Claude Code's TaskUpdate
     rejects that value outright:
         InputValidationError: expected one of
         "pending"|"in_progress"|"completed" ... expected "deleted"
     so the one action offered for unblocking a Stop while a subagent runs
     could not be performed. Reproduced independently on 2026-07-02 in an
     unrelated repository ("The hook's status="delegated" suggestion is
     rejected by the task tool's schema (hook/tool mismatch)"), so this is
     not specific to one execution surface.

  2. The stale-task escape sentence hardcoded Claude's tool names
     ("Claude's TaskList ... TaskUpdate returns Task not found") and was
     emitted verbatim to every harness, including ones whose message
     otherwise correctly speaks update_plan.

  3. SessionStart re-sent the identical resume injection on every fire. A
     Codex transcript showed the same block six consecutive times.

  4. When every incomplete task was older than recent_task_days, the resume
     message named no task at all yet still appended both "[... and N more]"
     and "[N older task(s) also incomplete]" — the same tasks counted twice,
     none identified.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root / "src"))

from autorun.core import EventContext, ThreadSafeDB
from autorun.task_lifecycle import TaskLifecycle, TaskLifecycleConfig
from autorun.session_manager import SessionStateManager
from autorun import session_manager as _sm_module
from autorun.session_manager import _reset_for_testing


@pytest.fixture
def isolated_session(tmp_path):
    temp_dir = tmp_path / "sessions"
    temp_dir.mkdir(parents=True, exist_ok=True)
    _reset_for_testing()
    mgr = SessionStateManager(state_dir=temp_dir)
    _sm_module._manager = mgr
    _sm_module._store = mgr._store
    yield mgr
    _reset_for_testing()


@pytest.fixture
def cfg(tmp_path):
    return TaskLifecycleConfig(
        enabled=True,
        storage_dir=tmp_path / "task_lifecycle",
        max_resume_tasks=10,
    )


def _ctx(session_id: str, event: str, cli_type: str = "claude", source: str = "startup",
         tool_result: str = "") -> EventContext:
    ctx = EventContext(
        session_id=session_id,
        event=event,
        prompt="",
        tool_name="",
        tool_input={},
        tool_result=tool_result,
        session_transcript=[],
        store=ThreadSafeDB(),
        source=source,
        cli_type=cli_type,
    )
    ctx.autorun_active = False
    ctx.autorun_stage = EventContext.STAGE_INACTIVE
    return ctx


def _seed(mgr: TaskLifecycle, task_id: str, subject: str) -> str:
    """Create an in_progress task through the real two-call API."""
    mgr.create_task(task_id, {"subject": subject}, "created")
    mgr.update_task(task_id, {"status": "in_progress"}, "started")
    return task_id


PLAN_SKILLS = ("plannew", "planrefine", "planupdate", "planprocess")


def test_every_plan_skill_explains_pi_durable_note_workflow():
    for name in PLAN_SKILLS:
        text = (plugin_root / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        assert "## Harness-specific plan lifecycle" in text, name
        assert "Pi" in text and "durable plan note" in text, name
        assert "/ar pp <path>" in text, name
        assert "Do not call `EnterPlanMode` or `ExitPlanMode` on Pi" in text, name
        assert "**IMPORTANT:** If not already in plan mode, use `EnterPlanMode` tool NOW." not in text, name
        assert "**Call ExitPlanMode when ALL planning tasks are complete**" not in text, name
        assert "call the **ExitPlanMode** tool" not in text, name


# ── 1. Delegation guidance must be performable on the running harness ─────────

class TestDelegateGuidanceIsPerformable:
    def test_claude_stop_block_does_not_instruct_unsupported_status(self, isolated_session, cfg):
        """Claude Code's TaskUpdate has no "delegated" status; do not name it."""
        mgr = TaskLifecycle(config=cfg, session_id="deleg-claude")
        _seed(mgr, "1", "Blocking work")
        result = mgr.handle_stop(_ctx("deleg-claude", "Stop", "claude"))

        assert result is not None
        reason = result.get("reason", "")
        assert 'status="delegated"' not in reason, (
            "Guidance instructs a TaskUpdate status value Claude Code rejects "
            f"with InputValidationError. Got: {reason}"
        )

    def test_claude_stop_block_offers_a_usable_delegation_action(self, isolated_session, cfg):
        """Removing the bad advice must not remove the capability."""
        mgr = TaskLifecycle(config=cfg, session_id="deleg-claude-2")
        _seed(mgr, "1", "Blocking work")
        result = mgr.handle_stop(_ctx("deleg-claude-2", "Stop", "claude"))

        reason = result.get("reason", "")
        assert "AUTORUN_TASK_DELEGATED" in reason, (
            "Stop guidance must still offer a way to mark a task delegated "
            f"while a subagent runs. Got: {reason}"
        )

    def test_delegate_marker_marks_task_delegated_and_unblocks_stop(self, isolated_session, cfg):
        """The advertised marker must actually work end to end."""
        from autorun import task_lifecycle as tl

        mgr = TaskLifecycle(config=cfg, session_id="deleg-marker")
        task_id = _seed(mgr, "7", "Handed to a subagent")

        ids = tl.extract_delegate_task_ids(f"AUTORUN_TASK_DELEGATED({task_id})")
        assert ids == [str(task_id)], f"marker must parse the task id, got {ids}"

        delegated = mgr.delegate_tasks_from_markers(ids)
        assert delegated == [str(task_id)]
        assert mgr.tasks[str(task_id)]["status"] == "delegated"

        # Delegated is in NON_BLOCKING_STATUSES, so Stop is now free.
        assert mgr.handle_stop(_ctx("deleg-marker", "Stop", "claude")) is None

    def test_harness_with_native_delegated_status_uses_its_own_tool(self, isolated_session, cfg, monkeypatch):
        """WOLOG: a harness whose task tool accepts the status keeps using it.

        Guards against hardcoding the marker as the only path — the fix is a
        per-harness capability, not a blanket removal.
        """
        import dataclasses

        from autorun import platforms, task_lifecycle as tl

        claude = platforms.platform_for("claude")
        supports_delegated = dataclasses.replace(
            claude,
            native_task_statuses=frozenset(claude.native_task_statuses | {"delegated"}),
        )
        monkeypatch.setattr(
            tl, "platform_for",
            lambda cli: supports_delegated if cli == "claude" else platforms.platform_for(cli),
        )
        mgr = TaskLifecycle(config=cfg, session_id="deleg-native")
        _seed(mgr, "1", "Blocking work")
        reason = mgr.handle_stop(_ctx("deleg-native", "Stop", "claude")).get("reason", "")
        assert 'status="delegated"' in reason


# ── 2. No Claude tool vocabulary in other harnesses' messages ─────────────────

class TestNoCrossHarnessVocabularyLeak:
    @pytest.mark.parametrize("cli_type", ["codex", "gemini", "qwen"])
    def test_stale_escape_sentence_uses_the_harness_own_tools(self, isolated_session, cfg, cli_type):
        mgr = TaskLifecycle(config=cfg, session_id=f"vocab-{cli_type}")
        _seed(mgr, "1", "Blocking work")
        reason = mgr.handle_stop(_ctx(f"vocab-{cli_type}", "Stop", cli_type)).get("reason", "")

        assert "Claude's TaskList" not in reason, (
            f"[{cli_type}] message names Claude's tools to a different harness. Got: {reason}"
        )
        assert "TaskUpdate returns" not in reason, (
            f"[{cli_type}] message names Claude's TaskUpdate to a different harness. Got: {reason}"
        )


# --- BUG #80305/#80401 TESTS START --- DELETE WHEN FIXED ---
_TASK_TOOL_BUG_FLAGS = (
    "AUTORUN_BUG_CLAUDE_CODE_TASK_TOOLS_GATED_OFF_BUG_80305_WORKAROUND_ENABLED",
    "AUTORUN_BUG_CLAUDE_CODE_TASK_TOOLS_VANISH_MID_SESSION_BUG_80401_WORKAROUND_ENABLED",
)


class TestClaudeTaskToolGateRecovery:
    def test_claude_stop_block_names_one_load_attempt_and_restart_fix(
        self, isolated_session, cfg, monkeypatch
    ):
        from autorun import config

        for flag in _TASK_TOOL_BUG_FLAGS:
            monkeypatch.delenv(flag, raising=False)
            monkeypatch.setitem(config.CONFIG, flag, True)
        mgr = TaskLifecycle(config=cfg, session_id="gated-stop")
        _seed(mgr, "1", "Cannot update without task tools")

        reason = mgr.handle_stop(_ctx("gated-stop", "Stop", "claude"))["reason"]

        assert "ToolSearch" in reason and "select:TaskCreate,TaskUpdate,TaskList,TaskGet" in reason
        assert "CLAUDE_CODE_ENABLE_TODO_TOOLS=1" in reason
        assert "CLAUDE_CODE_ENABLE_TASKS=1" in reason
        assert "do not retry" in reason.lower()

    def test_both_never_flags_remove_the_bug_workaround(
        self, isolated_session, cfg, monkeypatch
    ):
        for flag in _TASK_TOOL_BUG_FLAGS:
            monkeypatch.setenv(flag, "never")
        mgr = TaskLifecycle(config=cfg, session_id="gated-off")
        _seed(mgr, "1", "Ordinary task")

        reason = mgr.handle_stop(_ctx("gated-off", "Stop", "claude"))["reason"]

        assert "CLAUDE_CODE_ENABLE_TODO_TOOLS" not in reason

    def test_unaffected_harness_gets_no_claude_recovery_by_default(
        self, isolated_session, cfg, monkeypatch
    ):
        from autorun import config

        for flag in _TASK_TOOL_BUG_FLAGS:
            monkeypatch.delenv(flag, raising=False)
            monkeypatch.setitem(config.CONFIG, flag, True)
        mgr = TaskLifecycle(config=cfg, session_id="gated-codex")
        _seed(mgr, "1", "Codex checklist")

        reason = mgr.handle_stop(_ctx("gated-codex", "Stop", "codex"))["reason"]

        assert "CLAUDE_CODE_ENABLE_TODO_TOOLS" not in reason

    @pytest.mark.parametrize("flag", _TASK_TOOL_BUG_FLAGS)
    def test_each_flag_honors_auto_unaffected_and_always(self, monkeypatch, flag):
        from autorun import config, task_lifecycle as tl

        monkeypatch.setitem(config.CONFIG, flag, True)
        monkeypatch.setenv(flag, "auto")
        assert "CLAUDE_CODE_ENABLE_TODO_TOOLS" in tl.task_tool_recovery_sentence("claude")

        other = _TASK_TOOL_BUG_FLAGS[1] if flag == _TASK_TOOL_BUG_FLAGS[0] else _TASK_TOOL_BUG_FLAGS[0]
        monkeypatch.setenv(other, "never")
        assert tl.task_tool_recovery_sentence("codex") == ""

        monkeypatch.setenv(flag, "always")
        assert "CLAUDE_CODE_ENABLE_TODO_TOOLS" in tl.task_tool_recovery_sentence("codex")

    def test_staleness_enforcement_also_names_recovery(self, monkeypatch):
        from autorun import config, plugins, task_lifecycle as tl

        for flag in _TASK_TOOL_BUG_FLAGS:
            monkeypatch.delenv(flag, raising=False)
            monkeypatch.setitem(config.CONFIG, flag, True)

        text = plugins._task_staleness_instructions(
            _ctx("stale-gated", "PreToolUse", "claude")
        ) + tl.task_tool_recovery_sentence("claude")

        assert "ToolSearch" in text and "CLAUDE_CODE_ENABLE_TODO_TOOLS=1" in text

    def test_always_flag_can_force_the_workaround_for_diagnostics(
        self, isolated_session, cfg, monkeypatch
    ):
        monkeypatch.setenv(_TASK_TOOL_BUG_FLAGS[0], "always")
        monkeypatch.setenv(_TASK_TOOL_BUG_FLAGS[1], "never")
        mgr = TaskLifecycle(config=cfg, session_id="gated-always")
        _seed(mgr, "1", "Diagnostic")

        reason = mgr.handle_stop(_ctx("gated-always", "Stop", "codex"))["reason"]

        assert "CLAUDE_CODE_ENABLE_TODO_TOOLS=1" in reason


# --- BUG #80305/#80401 TESTS END --- DELETE WHEN FIXED ---


class TestClaudeDeferredTaskToolLoading:
    def test_claude_session_start_asks_the_model_to_load_deferred_task_tools(
        self, isolated_session
    ):
        from autorun import plugins

        result = plugins.app.dispatch(_ctx("load-tasks", "SessionStart", "claude"))
        text = (
            result.get("systemMessage", "")
            + result.get("reason", "")
            + result.get("hookSpecificOutput", {}).get("additionalContext", "")
        )

        assert "ToolSearch" in text
        assert "select:TaskCreate,TaskUpdate,TaskList,TaskGet" in text

    def test_deferred_tool_instruction_is_once_per_fresh_context(
        self, isolated_session
    ):
        from autorun import plugins

        first = plugins.app.dispatch(_ctx("load-once", "SessionStart", "claude"))
        second = plugins.app.dispatch(_ctx("load-once", "SessionStart", "claude"))

        assert "ToolSearch" in str(first)
        assert second is None or "ToolSearch" not in str(second)

    def test_load_instruction_fails_open_when_its_claim_cannot_persist(
        self, isolated_session, monkeypatch
    ):
        from autorun import plugins, task_lifecycle as tl

        def unavailable(*_args, **_kwargs):
            raise OSError("state unavailable")

        monkeypatch.setattr(tl.TaskLifecycle, "atomic_update_metadata", unavailable)

        result = plugins.app.dispatch(_ctx("load-fail-open", "SessionStart", "claude"))
        assert result is None or "daemon failure" not in str(result).lower()

    def test_non_claude_session_start_does_not_name_claude_tools(
        self, isolated_session
    ):
        from autorun import plugins

        result = plugins.app.dispatch(_ctx("load-codex", "SessionStart", "codex"))

        assert result is None or "TaskCreate,TaskUpdate" not in str(result)


# ── 3. SessionStart must not repeat an identical injection ───────────────────

class TestSessionStartIsNotRepeated:
    def test_six_identical_session_starts_inject_once(self, isolated_session, cfg):
        """The reported Codex symptom: same block rendered six times."""
        mgr = TaskLifecycle(config=cfg, session_id="ss-repeat")
        _seed(mgr, "1", "Carried over")

        delivered = [
            mgr.handle_session_start(_ctx("ss-repeat", "SessionStart", "codex"))
            for _ in range(6)
        ]
        non_null = [d for d in delivered if d]
        assert len(non_null) == 1, (
            f"SessionStart injected {len(non_null)} times for one unchanged task "
            "list; the AI sees the identical block repeated."
        )

    def test_session_start_injects_again_when_task_list_changes(self, isolated_session, cfg):
        """Suppression must be content-scoped, not a permanent one-shot."""
        mgr = TaskLifecycle(config=cfg, session_id="ss-change")
        _seed(mgr, "1", "First")
        assert mgr.handle_session_start(_ctx("ss-change", "SessionStart", "codex"))
        assert mgr.handle_session_start(_ctx("ss-change", "SessionStart", "codex")) is None

        _seed(mgr, "2", "Second appeared")
        assert mgr.handle_session_start(_ctx("ss-change", "SessionStart", "codex")), (
            "A changed task list must re-inject — the AI needs the new task."
        )

    def test_compaction_source_reinjects_identical_list(self, isolated_session, cfg):
        """After compaction the AI lost its context, so re-injection is correct."""
        mgr = TaskLifecycle(config=cfg, session_id="ss-compact")
        _seed(mgr, "1", "Carried over")
        assert mgr.handle_session_start(_ctx("ss-compact", "SessionStart", "codex", source="startup"))
        assert mgr.handle_session_start(
            _ctx("ss-compact", "SessionStart", "codex", source="compact")
        ), "A compaction wipes context; the same list must be re-injected."


# ── 4. Resume message must name the tasks it counts ──────────────────────────

class TestResumeMessageNamesItsTasks:
    def test_all_older_tasks_are_named_not_just_counted(self, isolated_session, cfg):
        """Live Codex symptom: 'incomplete tasks from previous session:  [... and
        2 more] [2 older task(s) also incomplete]' — naming nothing, counting
        the same two tasks twice."""
        mgr = TaskLifecycle(config=cfg, session_id="older-only")
        stale = time.time() - (cfg.recent_task_days + 30) * 86400
        for tid, subject in (("1", "Ancient task one"), ("2", "Ancient task two")):
            _seed(mgr, tid, subject)
        mgr.atomic_update_tasks(
            lambda tasks: [task.update({"created_at": stale}) for task in tasks.values()]
        )

        result = mgr.handle_session_start(_ctx("older-only", "SessionStart", "codex"))
        assert result is not None
        text = result.get("systemMessage", "") or result.get("reason", "") or str(result)

        assert "Ancient task" in text, (
            f"Resume message counts tasks without naming any. Got: {text}"
        )
        assert not ("and 2 more" in text and "2 older task(s)" in text), (
            f"The same two tasks are reported twice under different labels. Got: {text}"
        )


# ── 5. The marker works through the real hook chain, not just the manager ────

class TestDelegateMarkerReachesHookChain:
    def test_posttooluse_handler_delegates_and_reports(self, isolated_session, cfg, monkeypatch):
        """Guidance is worthless if nothing parses the marker in a live hook."""
        from autorun import plugins, task_lifecycle as tl

        mgr = TaskLifecycle(config=cfg, session_id="hook-deleg")
        _seed(mgr, "3", "Handed off")
        monkeypatch.setattr(tl.TaskLifecycleConfig, "load", staticmethod(lambda *a, **k: cfg))

        ctx = _ctx("hook-deleg", "PostToolUse", "claude",
                   tool_result="Spawning subagent. AUTORUN_TASK_DELEGATED(3)")

        assert plugins.delegate_marked_tasks(ctx) is None
        assert TaskLifecycle(config=cfg, session_id="hook-deleg").tasks["3"]["status"] == "delegated"
        assert any(
            "#3" in msg and ch == "both" for msg, ch in ctx._chain_notifications
        ), f"delegation must be reported on both channels, got {ctx._chain_notifications}"

    def test_marker_cannot_touch_a_non_blocking_task(self, isolated_session, cfg, monkeypatch):
        """A marker naming a completed task must be ignored, not applied."""
        from autorun import plugins, task_lifecycle as tl

        mgr = TaskLifecycle(config=cfg, session_id="hook-deleg-guard")
        _seed(mgr, "4", "Already done")
        mgr.update_task("4", {"status": "completed"}, "finished")
        monkeypatch.setattr(tl.TaskLifecycleConfig, "load", staticmethod(lambda *a, **k: cfg))

        ctx = _ctx("hook-deleg-guard", "PostToolUse", "claude",
                   tool_result="AUTORUN_TASK_DELEGATED(4)")
        plugins.delegate_marked_tasks(ctx)

        assert TaskLifecycle(config=cfg, session_id="hook-deleg-guard").tasks["4"]["status"] == "completed"
