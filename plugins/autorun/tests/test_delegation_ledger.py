"""Delegation gains a lifecycle: spawn ledger, returned gate, TTL revert.

"delegated" used to be a permanent exemption — nothing recorded which
subagent took the work and nothing reacted when it finished, so a delegated
task never blocked again. Three pieces close that:

1. PostToolUse for an agent-spawn tool records the child's identity in a
   session-state ledger, with no reliance on the model transcribing ids.
2. A delegation marker with no ``agent_session_id`` claims the latest
   unclaimed spawn, so the task knows where its work went.
3. SubagentStop flips matching delegated tasks to ``delegation-returned``,
   which blocks the Stop gate again with verification wording; parallel
   subagents share the parent session id (anthropics/claude-code#7881), so
   identity comes from the transcript path or the ledger, and ambiguity
   returns every ledger-linked delegation rather than guessing one.

The spawn-result fixture below is derived from a captured live Agent-tool
result (ids and paths generalized): the extraction regex and tool-name
allowlist must match what the harness actually sends, never a guess.
"""
from __future__ import annotations

import sys
import time
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

# Generalized from a captured Agent-tool result: the live text carries
# "agentId: <id>" and "output_file: .../tasks/<id>.output" in prose.
SPAWN_RESULT = (
    "Async agent launched successfully. (This tool result is internal "
    "metadata — never quote or paste any part of it.)\n"
    "agentId: a1b2c3d4e5f6a7b8c (internal ID - do not mention to user. Use "
    "SendMessage with to: 'a1b2c3d4e5f6a7b8c' to continue this agent.)\n"
    "The agent is working in the background.\n"
    "output_file: /tmp/example-session/tasks/a1b2c3d4e5f6a7b8c.output\n"
)
SPAWN_ID = "a1b2c3d4e5f6a7b8c"


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


def _ctx(
    session_id,
    store,
    event="PostToolUse",
    tool_name="Agent",
    tool_result="",
    assistant_text="",
    transcript_path="",
):
    ctx = EventContext(
        session_id=session_id,
        event=event,
        prompt="",
        tool_name=tool_name,
        tool_input={},
        tool_result=tool_result,
        session_transcript=(
            [{"role": "assistant", "content": assistant_text}] if assistant_text else []
        ),
        store=store,
        cli_type="claude",
        transcript_path=transcript_path,
    )
    ctx.autorun_active = True
    ctx.autorun_stage = EventContext.STAGE_1
    return ctx


def _spawn(session_id, store, cfg, result=SPAWN_RESULT, tool_name="Agent"):
    ctx = _ctx(session_id, store, tool_name=tool_name, tool_result=result)
    TaskLifecycle(ctx=ctx, config=cfg).record_agent_spawn(ctx)
    return ctx


def _state_key(session_id):
    """TaskLifecycle persists under its own namespaced key, not the raw id."""
    return f"__task_lifecycle__{session_id}"


def _ledger(session_id):
    with sm.session_state(_state_key(session_id)) as state:
        return list(state.get("session_metadata", {}).get("agent_spawns", []))


class TestSpawnLedger:
    def test_agent_spawn_recorded_from_posttooluse(self, isolated_state, cfg):
        store = ThreadSafeDB()
        _spawn("dl-record", store, cfg)

        entries = _ledger("dl-record")
        assert [entry["id"] for entry in entries] == [SPAWN_ID]
        assert entries[0]["claimed"] is False

    def test_non_spawn_tools_never_touch_the_ledger(self, isolated_state, cfg):
        """Forgery discipline: a Bash result QUOTING a spawn payload is noise."""
        store = ThreadSafeDB()
        _spawn("dl-forge", store, cfg, tool_name="Bash")

        assert _ledger("dl-forge") == []


class TestMarkerClaimsSpawn:
    def test_marker_without_session_claims_latest_spawn(self, isolated_state, cfg):
        store = ThreadSafeDB()
        session_id = "dl-claim"
        seed = TaskLifecycle(config=cfg, session_id=session_id)
        seed.create_task("7", {"subject": "Delegated sweep"}, "created")
        _spawn(session_id, store, cfg)

        stop = _ctx(
            session_id,
            store,
            event="Stop",
            tool_name="",
            assistant_text=DELEGATE.format(id="7"),
        )
        manager = TaskLifecycle(ctx=stop, config=cfg)
        manager.handle_stop(stop)

        task = manager.tasks["7"]
        assert task["status"] == "delegated"
        assert task["metadata"]["delegated_to_session"] == SPAWN_ID
        assert _ledger(session_id)[0]["claimed"] is True


class TestReturnedGate:
    def _delegate(self, session_id, store, cfg, task_id="7"):
        seed = TaskLifecycle(config=cfg, session_id=session_id)
        seed.create_task(task_id, {"subject": "Delegated sweep"}, "created")
        _spawn(session_id, store, cfg)
        stop = _ctx(
            session_id,
            store,
            event="Stop",
            tool_name="",
            assistant_text=DELEGATE.format(id=task_id),
        )
        manager = TaskLifecycle(ctx=stop, config=cfg)
        manager.handle_stop(stop)
        assert manager.tasks[task_id]["status"] == "delegated"
        return manager

    def test_subagent_stop_flips_delegated_to_returned(self, isolated_state, cfg):
        store = ThreadSafeDB()
        session_id = "dl-return"
        self._delegate(session_id, store, cfg)

        sub = _ctx(
            session_id,
            store,
            event="SubagentStop",
            tool_name="",
            transcript_path=f"/tmp/example-session/agent-{SPAWN_ID}.jsonl",
        )
        manager = TaskLifecycle(ctx=sub, config=cfg)
        assert manager.handle_stop(sub) is None, "SubagentStop must never block"

        task = manager.tasks["7"]
        assert task["status"] == "delegation-returned"

        # The gate re-arms: the parent's next Stop blocks with verification
        # wording that names every exit (verify-complete, re-delegate, ignore).
        stop = _ctx(session_id, store, event="Stop", tool_name="", assistant_text="done")
        response = TaskLifecycle(ctx=stop, config=cfg).handle_stop(stop)
        text = response if isinstance(response, str) else str(response)
        assert "verify" in text.lower()
        assert "re-delegate" in text.lower()
        assert "ignore" in text.lower()

    def test_ambiguous_stop_returns_all_live_delegations(self, isolated_state, cfg):
        """Two live spawns, no transcript id: over-ask, never guess-complete."""
        store = ThreadSafeDB()
        session_id = "dl-ambiguous"
        seed = TaskLifecycle(config=cfg, session_id=session_id)
        seed.create_task("7", {"subject": "First delegated"}, "created")
        seed.create_task("8", {"subject": "Second delegated"}, "created")
        _spawn(session_id, store, cfg)
        second = SPAWN_RESULT.replace(SPAWN_ID, "b2c3d4e5f6a7b8c9d")
        _spawn(session_id, store, cfg, result=second)

        stop = _ctx(
            session_id,
            store,
            event="Stop",
            tool_name="",
            assistant_text=DELEGATE.format(id="7") + "\n" + DELEGATE.format(id="8"),
        )
        manager = TaskLifecycle(ctx=stop, config=cfg)
        manager.handle_stop(stop)

        sub = _ctx(session_id, store, event="SubagentStop", tool_name="")
        manager = TaskLifecycle(ctx=sub, config=cfg)
        assert manager.handle_stop(sub) is None

        statuses = {tid: manager.tasks[tid]["status"] for tid in ("7", "8")}
        assert statuses == {
            "7": "delegation-returned",
            "8": "delegation-returned",
        }, "ambiguity must return every ledger-linked delegation, complete none"

    def test_fanout_storm_flips_each_task_once_and_idempotently(self, isolated_state, cfg):
        """Recorded fan-out: 8 spawns, 8 delegations, 16 SubagentStop firings.

        Parallel agents fire SubagentStop repeatedly (twice each here); the
        flip must be idempotent — every task ends delegation-returned exactly
        once, none completes, and repeated firings change nothing further.
        The stale-clear ghost escape also covers returned tasks: they appear
        in the blocking list its marker lines are generated from, so a wedged
        verification still has the plain no-longer-needed way out.
        """
        store = ThreadSafeDB()
        session_id = "dl-fanout"
        seed = TaskLifecycle(config=cfg, session_id=session_id)
        agent_ids = []
        for n in range(8):
            task_id = str(10 + n)
            seed.create_task(task_id, {"subject": f"Fanout part {n}"}, "created")
            agent_id = f"c{n}d4e5f6a7b8c9d{n:02d}"
            agent_ids.append(agent_id)
            _spawn(session_id, store, cfg, result=SPAWN_RESULT.replace(SPAWN_ID, agent_id))

        marker_text = "\n".join(DELEGATE.format(id=str(10 + n)) for n in range(8))
        stop = _ctx(session_id, store, event="Stop", tool_name="", assistant_text=marker_text)
        TaskLifecycle(ctx=stop, config=cfg).handle_stop(stop)

        for _round in range(2):
            for agent_id in agent_ids:
                sub = _ctx(
                    session_id,
                    store,
                    event="SubagentStop",
                    tool_name="",
                    transcript_path=f"/tmp/example-session/agent-{agent_id}.jsonl",
                )
                assert TaskLifecycle(ctx=sub, config=cfg).handle_stop(sub) is None

        manager = TaskLifecycle(ctx=stop, config=cfg)
        statuses = [manager.tasks[str(10 + n)]["status"] for n in range(8)]
        assert statuses == ["delegation-returned"] * 8
        assert not any(
            manager.tasks[str(10 + n)]["status"] == "completed" for n in range(8)
        ), "a fan-out storm must never complete anything"

    def test_dead_agent_ttl_reverts_to_pending(self, isolated_state, cfg, monkeypatch):
        store = ThreadSafeDB()
        session_id = "dl-ttl"
        manager = self._delegate(session_id, store, cfg)

        expired = time.time() - float(CONFIG["delegation_ttl_seconds"]) - 60
        with sm.session_state(_state_key(session_id)) as state:
            metadata = state.get("session_metadata", {})
            for entry in metadata.get("agent_spawns", []):
                entry["at"] = expired
            task = state["tasks"]["7"]
            task["metadata"]["delegated_at"] = expired
            state["tasks"] = state["tasks"]
            state["session_metadata"] = metadata

        stop = _ctx(session_id, store, event="Stop", tool_name="", assistant_text="done")
        response = TaskLifecycle(ctx=stop, config=cfg).handle_stop(stop)

        task = TaskLifecycle(ctx=stop, config=cfg).tasks["7"]
        assert task["status"] == "pending", (
            "a dead subagent must not exempt its task forever"
        )
        assert response is not None, "the reverted task blocks the stop again"
