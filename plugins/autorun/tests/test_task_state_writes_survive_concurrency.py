"""Concurrent task-state writes must not be silently discarded.

Observed live: three TaskUpdate calls issued in one assistant turn produced
exactly one recorded update, and the daemon log carried two warnings at the
same instant:

    Task tracking error: Could not acquire state lock for
    '__task_lifecycle__<session>' after 0.25s

The two dropped updates left a task the harness had already completed sitting
at "pending" in autorun's mirror. That blocks Stop indefinitely and cannot be
repaired through the normal path, because the harness — which did complete the
task — answers "Task not found" to every retry.

Two independent defects, one class each:

  1. TaskLifecycle opened session_state() directly, outside the daemon's
     ThreadSafeDB lock. Every other daemon write path (ThreadSafeDB.update,
     _persist_many) holds that lock across the file lock, so concurrent hook
     threads serving one session queue up instead of racing. Task writes did
     not, so parallel PostToolUse hooks contended for a single short budget
     and the losers raised.

  2. track_task_operations caught the resulting SessionTimeoutError and only
     wrote a log warning, so neither the AI nor the user ever learned the
     update had been dropped.

The third class pins the cost of the fix: serializing on the daemon lock is
only affordable if it does not also add a pointless file read per operation.
"""
from __future__ import annotations

import contextlib
import sys
import threading
import time
from pathlib import Path

import pytest

plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root / "src"))

from autorun import session_manager as _sm_module
from autorun.core import EventContext, ThreadSafeDB
from autorun.session_manager import SessionStateManager, SessionTimeoutError, _reset_for_testing
from autorun.task_lifecycle import TaskLifecycle, TaskLifecycleConfig

# Long enough that a writer holding the file lock outlasts the budget below,
# so an unserialized competitor is guaranteed to raise rather than flake.
SAVE_HOLD_SECONDS = 0.15
HOOK_LOCK_BUDGET_SECONDS = 0.05


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
        hook_state_lock_timeout_seconds=HOOK_LOCK_BUDGET_SECONDS,
    )


@pytest.fixture
def slow_persistence(monkeypatch):
    """Make each state save hold the file lock longer than the hook budget.

    Reproduces real contention deterministically: on a busy machine the save
    (write + fsync + replace) is what a competing writer actually waits on.
    """
    original_save = _sm_module._JSONStore._save

    def slow_save(self):
        time.sleep(SAVE_HOLD_SECONDS)
        return original_save(self)

    monkeypatch.setattr(_sm_module._JSONStore, "_save", slow_save)


def _ctx(session_id: str, store: ThreadSafeDB, *, tool_name: str = "",
         tool_input: dict | None = None, tool_result: str = "") -> EventContext:
    ctx = EventContext(
        session_id=session_id,
        event="PostToolUse",
        prompt="",
        tool_name=tool_name,
        tool_input=tool_input or {},
        tool_result=tool_result,
        session_transcript=[],
        store=store,
        cli_type="claude",
    )
    ctx.autorun_active = False
    ctx.autorun_stage = EventContext.STAGE_INACTIVE
    return ctx


# ── 1. Parallel writes for one session must all land ─────────────────────────

class TestConcurrentTaskWritesAllPersist:
    """REAL CONTENTION IS THE ASSERTION — do not serialize to speed these up.

    Every test here uses the `slow_persistence` fixture to widen the window in
    which two writers actually overlap. That deliberate slowness is the
    experiment: the defect these guard is a write silently lost to a peer, and
    it cannot occur if the writers never meet. Running them one at a time, or
    shrinking the delay until they no longer interleave, leaves the suite green
    and the bug reachable.
    """

    def test_parallel_updates_from_one_session_are_not_dropped(
        self, isolated_session, cfg, slow_persistence
    ):
        """Three TaskUpdates in one turn must produce three recorded updates.

        This is the reported failure verbatim: an assistant message carrying
        several tool calls makes the harness fire several PostToolUse hooks at
        once, and the daemon runs them on separate threads of one pool.
        """
        session_id = "concurrent-writes"
        store = ThreadSafeDB()
        seed = TaskLifecycle(config=cfg, session_id=session_id)
        task_ids = ["1", "2", "3"]
        for tid in task_ids:
            seed.create_task(tid, {"subject": f"Task {tid}"}, "created")

        errors: list[Exception] = []
        barrier = threading.Barrier(len(task_ids))

        def complete(tid: str) -> None:
            manager = TaskLifecycle(ctx=_ctx(session_id, store), config=cfg)
            try:
                barrier.wait(timeout=5)  # Force genuine overlap, not luck.
                manager.update_task(tid, {"status": "completed"}, "finished")
            except Exception as exc:  # noqa: BLE001 - the assertion is the report
                errors.append(exc)

        threads = [threading.Thread(target=complete, args=(tid,)) for tid in task_ids]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert not errors, (
            "Concurrent task updates raised instead of queueing. Task state is "
            "written outside the daemon lock that serializes every other state "
            f"write, so the losers ran out of budget: {errors}"
        )
        recorded = TaskLifecycle(config=cfg, session_id=session_id).tasks
        stuck = {tid: recorded[tid]["status"] for tid in task_ids
                 if recorded[tid]["status"] != "completed"}
        assert not stuck, (
            "A task the AI completed stayed incomplete in autorun's mirror. It "
            "will block Stop forever, and the harness answers 'Task not found' "
            f"to every repair attempt. Stuck: {stuck}"
        )

    def test_concurrent_metadata_updates_do_not_lose_increments(
        self, isolated_session, cfg, slow_persistence
    ):
        """The same contention must not corrupt the counters Stop decisions read."""
        session_id = "concurrent-metadata"
        store = ThreadSafeDB()
        TaskLifecycle(config=cfg, session_id=session_id).atomic_update_metadata(
            lambda metadata: metadata.__setitem__("hits", 0)
        )

        errors: list[Exception] = []
        writers = 3
        barrier = threading.Barrier(writers)

        def bump() -> None:
            manager = TaskLifecycle(ctx=_ctx(session_id, store), config=cfg)
            try:
                barrier.wait(timeout=5)
                manager.atomic_update_metadata(
                    lambda metadata: metadata.__setitem__("hits", metadata.get("hits", 0) + 1)
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=bump) for _ in range(writers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert not errors, f"Concurrent metadata updates raised: {errors}"
        hits = TaskLifecycle(config=cfg, session_id=session_id).session_metadata["hits"]
        assert hits == writers, (
            f"Lost {writers - hits} of {writers} atomic increments — the "
            "compare-and-set ran outside the daemon's serialization."
        )


# ── 2. A dropped write must be reported, never swallowed ─────────────────────

class TestPersistenceFailureIsReported:
    def test_lock_timeout_tells_the_ai_the_update_was_not_recorded(
        self, isolated_session, cfg, monkeypatch
    ):
        """Silence here is what makes the bug unrecoverable.

        The tool call itself succeeded, so the AI has no reason to suspect the
        mirror disagrees. Unless the hook says so, the mismatch only surfaces
        later as a Stop block naming a task that is already done.
        """
        from autorun import plugins, task_lifecycle as tl

        session_id = "report-timeout"
        monkeypatch.setattr(tl.TaskLifecycleConfig, "load", staticmethod(lambda *a, **k: cfg))

        def raise_timeout(self, ctx):
            raise SessionTimeoutError(
                f"Could not acquire state lock for '__task_lifecycle__{session_id}' after 0.25s"
            )

        monkeypatch.setattr(tl.TaskLifecycle, "handle_task_update", raise_timeout)

        ctx = _ctx(
            session_id,
            ThreadSafeDB(),
            tool_name="TaskUpdate",
            tool_input={"taskId": "7", "status": "completed"},
            tool_result="Updated task #7 status",
        )
        response = plugins.app._run_chain(ctx, "PostToolUse") or {}

        # _run_chain drains accumulated notifications into the wire response,
        # so assert on what the harness actually receives.
        ai_text = (response.get("hookSpecificOutput") or {}).get("additionalContext", "")
        human_text = response.get("systemMessage", "")
        assert "7" in ai_text, (
            "A dropped task write never reached the AI. The tool call reported "
            "success, so nothing else prompts a re-issue and the mismatch only "
            f"surfaces later as a Stop block. Response: {response}"
        )
        assert "7" in human_text, (
            "The user needs this too — it explains the Stop block that follows. "
            f"Response: {response}"
        )

    def test_tracking_failure_still_allows_the_tool_to_complete(
        self, isolated_session, cfg, monkeypatch
    ):
        """Reporting must not become blocking: tracking is advisory, not a gate."""
        from autorun import plugins, task_lifecycle as tl

        monkeypatch.setattr(tl.TaskLifecycleConfig, "load", staticmethod(lambda *a, **k: cfg))

        def explode(self, ctx):
            raise SessionTimeoutError("Could not acquire state lock for 'x' after 0.25s")

        monkeypatch.setattr(tl.TaskLifecycle, "handle_task_update", explode)

        ctx = _ctx(
            "report-nonblocking",
            ThreadSafeDB(),
            tool_name="TaskUpdate",
            tool_input={"taskId": "9", "status": "completed"},
            tool_result="Updated task #9 status",
        )
        result = plugins.app._run_chain(ctx, "PostToolUse")

        assert result is None or result.get("decision") != "block", (
            f"PostToolUse tracking must never block the tool chain. Got: {result}"
        )


# ── 3. Serializing on the daemon lock must not add pointless file reads ──────

class TestSynchronizeSessionRefreshCost:
    @staticmethod
    def _counting_session_state(monkeypatch, values):
        """Replace session_state() with a fake that records every acquisition."""
        from autorun import core

        calls: list[str] = []

        @contextlib.contextmanager
        def fake_session_state(session_id, timeout=None, **_kwargs):
            calls.append(session_id)
            yield values.setdefault(session_id, {})

        monkeypatch.setattr(core, "session_state", fake_session_state)
        return calls

    def test_unhydrated_session_is_not_re_read(self, monkeypatch):
        """Refreshing a cache that was never populated is pure overhead.

        Task state lives under its own '__task_lifecycle__*' key that nothing
        reads through the daemon cache, so refreshing it after every write
        would double the file lock traffic this fix exists to reduce.
        """
        values: dict[str, dict] = {}
        calls = self._counting_session_state(monkeypatch, values)
        store = ThreadSafeDB()

        store.synchronize_session("__task_lifecycle__never-read", lambda: "done")

        assert calls == [], (
            "synchronize_session re-read a session the cache never held. "
            f"Acquisitions: {calls}"
        )

    def test_hydrated_session_is_still_refreshed(self, monkeypatch):
        """Skipping the refresh must not let a warm cache go stale."""
        values = {"warm": {"status": "before"}}
        calls = self._counting_session_state(monkeypatch, values)
        store = ThreadSafeDB()

        assert store.get("warm:status") == "before"

        def mutate():
            values["warm"]["status"] = "after"

        store.synchronize_session("warm", mutate)

        assert store.get("warm:status") == "after", (
            "A hydrated session must be re-read after an external mutation, or "
            "callers keep serving the pre-mutation value."
        )
        assert len(calls) == 2, f"Expected hydrate + refresh, got {calls}"
