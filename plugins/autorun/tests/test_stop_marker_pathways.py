"""A marker the AI prints must be honored whether or not a tool ran.

The stop block tells the assistant to print ``AUTORUN_TASK_DELEGATED(N)`` to
hand a task to a subagent and keep working. Both markers were read only on
PostToolUse, which fires after a tool call — and the natural way to comply
with "print this marker" is a plain-text reply with no tool call at all.

So the marker was printed, nothing read it, and the next Stop blocked on the
same task with the same instruction. The assistant complied again. That loop
has no exit reachable by doing what it was asked, which is the worst shape a
gate can have: the correct action produces no effect, indefinitely.

Both pathways are supported now. PostToolUse still applies a marker the
moment it appears, and the stop gate applies it at the moment it decides.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent
SRC_DIR = PLUGIN_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autorun import session_manager as sm  # noqa: E402
from autorun.config import CONFIG  # noqa: E402
from autorun.core import EventContext, ThreadSafeDB  # noqa: E402
from autorun.task_lifecycle import TaskLifecycle, TaskLifecycleConfig  # noqa: E402

DELEGATE = CONFIG["delegate_marker_template"]
STALE_CLEAR = CONFIG["ghost_clear_marker_template"]


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    directory = tmp_path / "sessions"
    directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(directory))
    sm._reset_for_testing()
    yield directory
    sm._reset_for_testing()


@pytest.fixture
def cfg(tmp_path):
    return TaskLifecycleConfig(
        enabled=True,
        storage_dir=tmp_path / "task_lifecycle",
        max_resume_tasks=10,
    )


def _blocked_text(response) -> str:
    """The block message out of a hook response, or "" when nothing blocked."""
    if not response:
        return ""
    if isinstance(response, str):
        return response
    return str(response.get("reason") or response.get("systemMessage") or "")


def _stop_context(session_id, assistant_text, store=None, event="Stop"):
    """A Stop event carrying what the assistant just said, and no tool call."""
    ctx = EventContext(
        session_id=session_id,
        event=event,
        prompt="",
        tool_name="",
        tool_input={},
        tool_result="",
        session_transcript=[{"role": "assistant", "content": assistant_text}],
        store=store or ThreadSafeDB(),
        cli_type="claude",
    )
    ctx.autorun_active = True
    ctx.autorun_stage = EventContext.STAGE_1
    return ctx


def _stop_context_with_messages(session_id, messages, store=None):
    ctx = EventContext(
        session_id=session_id,
        event="Stop",
        prompt="",
        tool_name="",
        tool_input={},
        tool_result="",
        session_transcript=messages,
        store=store or ThreadSafeDB(),
        cli_type="claude",
    )
    ctx.autorun_active = True
    ctx.autorun_stage = EventContext.STAGE_1
    return ctx


class TestDelegationMarkerWithoutAToolCall:
    def test_a_delegated_task_stops_blocking(self, isolated_state, cfg):
        """The failure verbatim: marker printed, no tool call, still blocked."""
        session_id = "stop-delegate"
        seed = TaskLifecycle(config=cfg, session_id=session_id)
        seed.create_task("77", {"subject": "Sweep for philosophy violations"}, "created")
        seed.update_task("77", {"status": "in_progress"}, "started")

        ctx = _stop_context(
            session_id,
            f"Delegating that one now.\n\n{DELEGATE.format(id='77')}\n",
        )
        manager = TaskLifecycle(ctx=ctx, config=cfg)

        blocked = manager.handle_stop(ctx)

        assert blocked is None, (
            "The stop hook blocked on a task the assistant had just delegated. "
            "It printed the marker it was told to print, no tool call "
            "followed, so nothing read it — and the same block will repeat "
            f"forever. Message: {blocked!r}"
        )
        assert manager.tasks["77"]["status"] == "delegated"

    def test_the_delegation_is_reported(self, isolated_state, cfg):
        session_id = "stop-delegate-report"
        seed = TaskLifecycle(config=cfg, session_id=session_id)
        seed.create_task("77", {"subject": "Something"}, "created")
        seed.update_task("77", {"status": "in_progress"}, "started")

        ctx = _stop_context(session_id, DELEGATE.format(id="77"))
        TaskLifecycle(ctx=ctx, config=cfg).handle_stop(ctx)

        messages = " ".join(message for message, _channel in ctx._chain_notifications)
        assert "77" in messages, (
            f"A silent state change. Notifications: {messages!r}"
        )

    def test_other_tasks_still_block(self, isolated_state, cfg):
        """Delegating one task must not release the rest."""
        session_id = "stop-delegate-partial"
        seed = TaskLifecycle(config=cfg, session_id=session_id)
        for task_id in ("77", "78"):
            seed.create_task(task_id, {"subject": f"Task {task_id}"}, "created")
            seed.update_task(task_id, {"status": "in_progress"}, "started")

        ctx = _stop_context(session_id, DELEGATE.format(id="77"))
        manager = TaskLifecycle(ctx=ctx, config=cfg)

        blocked = manager.handle_stop(ctx)

        assert "78" in _blocked_text(blocked)
        assert manager.tasks["77"]["status"] == "delegated"
        assert manager.tasks["78"]["status"] == "in_progress"

    def test_a_marker_for_an_unknown_task_changes_nothing(self, isolated_state, cfg):
        session_id = "stop-delegate-unknown"
        seed = TaskLifecycle(config=cfg, session_id=session_id)
        seed.create_task("77", {"subject": "Real task"}, "created")
        seed.update_task("77", {"status": "in_progress"}, "started")

        ctx = _stop_context(session_id, DELEGATE.format(id="999"))
        manager = TaskLifecycle(ctx=ctx, config=cfg)

        blocked = manager.handle_stop(ctx)

        assert blocked is not None, "an unknown id released the gate"
        assert "999" not in manager.tasks, "a marker invented a task"
        assert manager.tasks["77"]["status"] == "in_progress"

    def test_a_marker_cannot_reopen_a_finished_task(self, isolated_state, cfg):
        """Markers apply only to what is blocking right now."""
        session_id = "stop-delegate-finished"
        seed = TaskLifecycle(config=cfg, session_id=session_id)
        seed.create_task("77", {"subject": "Already done"}, "created")
        seed.update_task("77", {"status": "completed"}, "finished")
        seed.create_task("78", {"subject": "Still open"}, "created")
        seed.update_task("78", {"status": "in_progress"}, "started")

        ctx = _stop_context(session_id, DELEGATE.format(id="77"))
        manager = TaskLifecycle(ctx=ctx, config=cfg)
        manager.handle_stop(ctx)

        assert manager.tasks["77"]["status"] == "completed", (
            "A delegation marker changed a task that was already finished."
        )

    def test_several_markers_in_one_reply_all_apply(self, isolated_state, cfg):
        session_id = "stop-delegate-many"
        seed = TaskLifecycle(config=cfg, session_id=session_id)
        for task_id in ("77", "78", "79"):
            seed.create_task(task_id, {"subject": f"Task {task_id}"}, "created")
            seed.update_task(task_id, {"status": "in_progress"}, "started")

        text = "\n".join(DELEGATE.format(id=t) for t in ("77", "78", "79"))
        ctx = _stop_context(session_id, text)
        manager = TaskLifecycle(ctx=ctx, config=cfg)

        assert manager.handle_stop(ctx) is None
        for task_id in ("77", "78", "79"):
            assert manager.tasks[task_id]["status"] == "delegated"

    def test_repeating_the_marker_is_harmless(self, isolated_state, cfg):
        """The assistant retried after the first attempt appeared to do nothing."""
        session_id = "stop-delegate-repeat"
        seed = TaskLifecycle(config=cfg, session_id=session_id)
        seed.create_task("77", {"subject": "Task"}, "created")
        seed.update_task("77", {"status": "in_progress"}, "started")

        for _ in range(3):
            ctx = _stop_context(session_id, DELEGATE.format(id="77"))
            manager = TaskLifecycle(ctx=ctx, config=cfg)
            assert manager.handle_stop(ctx) is None

        assert manager.tasks["77"]["status"] == "delegated"

    def test_a_consumed_historical_marker_cannot_redelegate_resumed_work(
        self, isolated_state, cfg
    ):
        session_id = "stop-delegate-historical"
        seed = TaskLifecycle(config=cfg, session_id=session_id)
        seed.create_task("77", {"subject": "Task"}, "created")
        seed.update_task("77", {"status": "in_progress"}, "started")

        transcript = [{"role": "assistant", "content": DELEGATE.format(id="77")}]
        first = _stop_context_with_messages(session_id, transcript)
        assert TaskLifecycle(ctx=first, config=cfg).handle_stop(first) is None

        TaskLifecycle(config=cfg, session_id=session_id).update_task(
            "77", {"status": "in_progress"}, "subagent returned; resume locally"
        )
        replay = _stop_context_with_messages(session_id, transcript)
        blocked = TaskLifecycle(ctx=replay, config=cfg).handle_stop(replay)

        assert "77" in _blocked_text(blocked)
        assert TaskLifecycle(config=cfg, session_id=session_id).tasks["77"]["status"] \
            == "in_progress"

    def test_only_the_latest_assistant_message_can_issue_a_marker(
        self, isolated_state, cfg
    ):
        session_id = "stop-delegate-latest-only"
        seed = TaskLifecycle(config=cfg, session_id=session_id)
        seed.create_task("77", {"subject": "Task"}, "created")
        seed.update_task("77", {"status": "in_progress"}, "started")
        ctx = _stop_context_with_messages(
            session_id,
            [
                {"role": "assistant", "content": DELEGATE.format(id="77")},
                {"role": "user", "content": "Resume it locally"},
                {"role": "assistant", "content": "Continuing without delegation."},
            ],
        )

        blocked = TaskLifecycle(ctx=ctx, config=cfg).handle_stop(ctx)
        assert "77" in _blocked_text(blocked)

    def test_a_new_identical_marker_has_a_distinct_message_identity(
        self, isolated_state, cfg
    ):
        session_id = "stop-delegate-new-identical"
        seed = TaskLifecycle(config=cfg, session_id=session_id)
        seed.create_task("77", {"subject": "Task"}, "created")
        seed.update_task("77", {"status": "in_progress"}, "started")
        marker = DELEGATE.format(id="77")
        first = _stop_context_with_messages(
            session_id, [{"role": "assistant", "content": marker}]
        )
        TaskLifecycle(ctx=first, config=cfg).handle_stop(first)
        TaskLifecycle(config=cfg, session_id=session_id).update_task(
            "77", {"status": "in_progress"}, "resumed"
        )

        second = _stop_context_with_messages(
            session_id,
            [
                {"role": "assistant", "content": marker},
                {"role": "user", "content": "delegate it again"},
                {"role": "assistant", "content": marker},
            ],
        )
        assert TaskLifecycle(ctx=second, config=cfg).handle_stop(second) is None
        assert TaskLifecycle(config=cfg, session_id=session_id).tasks["77"]["status"] \
            == "delegated"


class TestTheGateIsUnchangedWithoutAMarker:
    def test_an_incomplete_task_still_blocks(self, isolated_state, cfg):
        session_id = "stop-no-marker"
        seed = TaskLifecycle(config=cfg, session_id=session_id)
        seed.create_task("77", {"subject": "Unfinished work"}, "created")
        seed.update_task("77", {"status": "in_progress"}, "started")

        ctx = _stop_context(session_id, "Just some prose with no marker in it.")
        blocked = TaskLifecycle(ctx=ctx, config=cfg).handle_stop(ctx)

        assert "77" in _blocked_text(blocked), (
            "Adding marker handling weakened the gate itself."
        )

    def test_text_that_merely_mentions_the_marker_does_not_trigger_it(
        self, isolated_state, cfg
    ):
        """Explaining the mechanism must not invoke it."""
        session_id = "stop-marker-mention"
        seed = TaskLifecycle(config=cfg, session_id=session_id)
        seed.create_task("77", {"subject": "Work"}, "created")
        seed.update_task("77", {"status": "in_progress"}, "started")

        ctx = _stop_context(
            session_id,
            "You can delegate a task by printing the delegation marker with "
            "the task id in parentheses.",
        )
        blocked = TaskLifecycle(ctx=ctx, config=cfg).handle_stop(ctx)

        assert blocked is not None, "prose about the marker released the gate"

    def test_subagentstop_is_still_never_blocked(self, isolated_state, cfg):
        """Blocking it deadlocks the parent waiting on the child."""
        session_id = "subagent-stop"
        seed = TaskLifecycle(config=cfg, session_id=session_id)
        seed.create_task("77", {"subject": "Work"}, "created")
        seed.update_task("77", {"status": "in_progress"}, "started")

        ctx = _stop_context(session_id, "no marker", event="SubagentStop")

        assert TaskLifecycle(ctx=ctx, config=cfg).handle_stop(ctx) is None


class TestBothPathwaysAgree:
    def test_the_post_tool_use_pathway_still_applies_the_marker(
        self, isolated_state, cfg, monkeypatch
    ):
        """The fast path is kept: a marker beside a tool call still lands."""
        from autorun import plugins, task_lifecycle as tl

        session_id = "posttooluse-delegate"
        monkeypatch.setattr(tl.TaskLifecycleConfig, "load",
                            staticmethod(lambda *a, **k: cfg))

        seed = TaskLifecycle(config=cfg, session_id=session_id)
        seed.create_task("77", {"subject": "Work"}, "created")
        seed.update_task("77", {"status": "in_progress"}, "started")

        ctx = EventContext(
            session_id=session_id,
            event="PostToolUse",
            prompt="",
            tool_name="Read",
            tool_input={"file_path": "/tmp/x"},
            tool_result=DELEGATE.format(id="77"),
            session_transcript=[],
            store=ThreadSafeDB(),
            cli_type="claude",
        )
        ctx.autorun_active = True
        ctx.autorun_stage = EventContext.STAGE_1

        plugins.delegate_marked_tasks(ctx)

        assert TaskLifecycle(config=cfg, session_id=session_id).tasks["77"]["status"] \
            == "delegated"

    def test_the_stale_clear_marker_also_works_without_a_tool_call(
        self, isolated_state, cfg
    ):
        """The other marker had the stop pathway already; keep it that way."""
        session_id = "stop-stale-clear"
        cfg.ghost_clear_enabled = True
        seed = TaskLifecycle(config=cfg, session_id=session_id)
        seed.create_task("77", {"subject": "Ghost"}, "created")
        seed.update_task("77", {"status": "in_progress"}, "started")

        # The escape hatch only opens after repeated identical blocks.
        blocked = None
        for _ in range(5):
            ctx = _stop_context(session_id, STALE_CLEAR.format(id="77"))
            ctx.ghost_clear_min_consecutive_blocks_override = 1
            blocked = TaskLifecycle(ctx=ctx, config=cfg).handle_stop(ctx)
            if blocked is None:
                break

        assert blocked is None, (
            f"The stale-clear marker no longer releases the gate: {blocked!r}"
        )
