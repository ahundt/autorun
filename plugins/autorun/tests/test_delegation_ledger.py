"""Delegation gains a lifecycle: spawn ledger, returned gate, TTL revert.

"delegated" used to be a permanent exemption — nothing recorded which
subagent took the work and nothing reacted when it finished, so a delegated
task never blocked again. Three pieces close that:

1. PostToolUse for an agent-spawn tool records the child's identity in a
   session-state ledger, with no reliance on the model transcribing ids.
2. A delegation marker with no ``agent_session_id`` claims the latest
   unclaimed spawn, so the task knows where its work went.
3. SubagentStop flips matching delegated tasks to ``delegation-returned``,
   which blocks the parent Stop gate again with verification wording. Claude
   can share the parent session while Codex fires from the child session; a
   spawn-time receipt preserves parent authority across both payload shapes.

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
from autorun.core import EventContext, ThreadSafeDB, normalize_hook_payload  # noqa: E402
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

# Codex's spawn_agent result, generalized: a JSON object whose id field is
# snake_case, unlike Claude's camelCase `agentId`.
CODEX_SPAWN_ID = "01997b21-0e6f-7bb2-9d1e-4f0a2c3d5e6f"
CODEX_SPAWN_RESULT = {"agent_id": CODEX_SPAWN_ID, "status": "spawned"}

# What a live `claude -p` fan-out actually put in PostToolUse tool_response on
# 2026-08-05 (ids, paths, and model generalized). The prose form above is what
# the transcript records; the hook is handed this structured launch record, so
# an extractor that only reads the prose silently records nothing.
SPAWN_RESULT_STRUCTURED = {
    "isAsync": True,
    "status": "async_launched",
    "agentId": SPAWN_ID,
    "description": "Delegated sweep",
    "resolvedModel": "example-model",
    "prompt": "Reply with the single word done",
    "outputFile": f"/tmp/example-session/tasks/{SPAWN_ID}.output",
    "canReadOutputFile": True,
}


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
    agent_transcript_path="",
    agent_id=None,
    cli_type="claude",
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
        cli_type=cli_type,
        transcript_path=transcript_path,
        agent_transcript_path=agent_transcript_path,
        agent_id=agent_id,
    )
    ctx.autorun_active = True
    ctx.autorun_stage = EventContext.STAGE_1
    return ctx


def _spawn(session_id, store, cfg, result=SPAWN_RESULT, tool_name="Agent", cli_type="claude"):
    ctx = _ctx(session_id, store, tool_name=tool_name, tool_result=result, cli_type=cli_type)
    TaskLifecycle(ctx=ctx, config=cfg).record_agent_spawn(ctx)
    return ctx


def _state_key(session_id):
    """TaskLifecycle persists under its own namespaced key, not the raw id."""
    return f"__task_lifecycle__{session_id}"


def _ledger(session_id):
    with sm.session_state(_state_key(session_id)) as state:
        return list(state.get("session_metadata", {}).get("agent_spawns", []))


def _receipts():
    with sm.session_state("__task_delegation_receipts__") as state:
        return dict(state.get("receipts", {}))


class TestSpawnLedger:
    def test_agent_spawn_recorded_from_posttooluse(self, isolated_state, cfg):
        store = ThreadSafeDB()
        _spawn("dl-record", store, cfg)

        entries = _ledger("dl-record")
        assert [entry["id"] for entry in entries] == [SPAWN_ID]
        assert entries[0]["claimed"] is False

    def test_structured_launch_record_is_recorded(self, isolated_state, cfg):
        """The live wire shape: tool_response is a dict, not the prose text.

        A live fan-out recorded nothing because the extractor only matched
        "agentId: <id>" while the hook was handed {"agentId": "<id>", ...},
        which reaches the extractor JSON-encoded as '"agentId": "<id>"'.
        """
        store = ThreadSafeDB()
        _spawn("dl-structured", store, cfg, result=SPAWN_RESULT_STRUCTURED)

        assert [entry["id"] for entry in _ledger("dl-structured")] == [SPAWN_ID]

    def test_codex_spawn_agent_result_is_recorded(self, isolated_state, cfg):
        """Codex names the tool `spawn_agent` and the field `agent_id`.

        codex-rs/core/src/tools/hook_names.rs:46 keeps `spawn_agent` as the
        name serialized into hook stdin and treats `Agent` only as a matcher
        alias for hook config, so an allowlist of {Agent, Task} never fires on
        Codex. Its result carries a snake_case `agent_id`
        (codex-rs/core/src/tools/handlers/multi_agents_tests.rs:257), which the
        camelCase-only pattern also missed. Both gaps left the ledger empty, so
        the SubagentStop gate Codex does emit had nothing to return.
        """
        store = ThreadSafeDB()
        _spawn(
            "dl-codex", store, cfg,
            result=CODEX_SPAWN_RESULT, tool_name="spawn_agent", cli_type="codex",
        )

        assert [entry["id"] for entry in _ledger("dl-codex")] == [CODEX_SPAWN_ID]

    def test_non_spawn_tools_never_touch_the_ledger(self, isolated_state, cfg):
        """Forgery discipline: a Bash result QUOTING a spawn payload is noise."""
        store = ThreadSafeDB()
        _spawn("dl-forge", store, cfg, tool_name="Bash")

        assert _ledger("dl-forge") == []

    @pytest.mark.parametrize("backend", ["json", "sqlite"])
    def test_unreturned_receipt_expires_with_delegation_ttl(
        self, isolated_state, cfg, monkeypatch, backend
    ):
        monkeypatch.setitem(sm._CONFIG, "state_backend", backend)
        sm._reset_for_testing()
        store = ThreadSafeDB()
        _spawn("dl-expired-receipt", store, cfg)
        key = f"claude:{SPAWN_ID}"
        expired = time.time() - float(CONFIG["delegation_ttl_seconds"]) - 60
        with sm.session_state("__task_delegation_receipts__") as state:
            receipts = dict(state["receipts"])
            receipts[key] = {**receipts[key], "at": expired}
            state["receipts"] = receipts

        child = TaskLifecycle(config=cfg, session_id="untrusted-child")
        assert child._delegation_receipt("claude", SPAWN_ID) is None
        assert key not in _receipts()

    @pytest.mark.parametrize("backend", ["json", "sqlite"])
    def test_clear_parent_removes_its_cross_session_receipts(
        self, isolated_state, cfg, monkeypatch, backend
    ):
        monkeypatch.setitem(sm._CONFIG, "state_backend", backend)
        sm._reset_for_testing()
        store = ThreadSafeDB()
        _spawn("dl-clear-parent", store, cfg)
        assert _receipts()

        assert TaskLifecycle.cli_clear(
            session_id="dl-clear-parent", confirm=False
        ) == 0

        assert _receipts() == {}

    @pytest.mark.parametrize("backend", ["json", "sqlite"])
    def test_clear_all_removes_global_delegation_registry(
        self, isolated_state, cfg, monkeypatch, backend
    ):
        monkeypatch.setitem(sm._CONFIG, "state_backend", backend)
        sm._reset_for_testing()
        store = ThreadSafeDB()
        _spawn("dl-clear-all-one", store, cfg)
        _spawn(
            "dl-clear-all-two",
            store,
            cfg,
            result=SPAWN_RESULT.replace(SPAWN_ID, "b1c2d3e4f5a6b7c8d"),
        )
        assert len(_receipts()) == 2

        assert TaskLifecycle.cli_clear(all_sessions=True, confirm=False) == 0

        assert _receipts() == {}

    def test_json_clear_write_failure_preserves_parent_and_receipt(
        self, isolated_state, cfg, monkeypatch
    ):
        """The JSON backend publishes parent and receipt deletion together."""
        monkeypatch.setitem(sm._CONFIG, "state_backend", "json")
        sm._reset_for_testing()
        store = ThreadSafeDB()
        session_id = "dl-json-clear-rollback"
        manager = TaskLifecycle(config=cfg, session_id=session_id)
        manager.create_task("7", {"subject": "Preserved"}, "created")
        _spawn(session_id, store, cfg)
        receipts_before = _receipts()
        backend = sm.get_session_manager()._store
        original_save = backend._save

        def reject_save():
            raise OSError("injected JSON clear failure")

        monkeypatch.setattr(backend, "_save", reject_save)
        assert TaskLifecycle.cli_clear(session_id=session_id, confirm=False) == 1
        monkeypatch.setattr(backend, "_save", original_save)

        assert set(TaskLifecycle(config=cfg, session_id=session_id).tasks) == {"7"}
        assert _receipts() == receipts_before

    def test_json_clear_all_write_failure_preserves_every_session_and_receipt(
        self, isolated_state, cfg, monkeypatch
    ):
        """A failed all-clear cannot publish a partially deleted JSON file."""
        monkeypatch.setitem(sm._CONFIG, "state_backend", "json")
        sm._reset_for_testing()
        store = ThreadSafeDB()
        sessions = ("dl-json-all-a", "dl-json-all-b")
        for session_id in sessions:
            manager = TaskLifecycle(config=cfg, session_id=session_id)
            manager.create_task("7", {"subject": session_id}, "created")
            agent_id = SPAWN_ID if session_id.endswith("a") else "b1c2d3e4f5a6b7c8d"
            _spawn(
                session_id,
                store,
                cfg,
                result=SPAWN_RESULT.replace(SPAWN_ID, agent_id),
            )
        receipts_before = _receipts()
        backend = sm.get_session_manager()._store
        original_save = backend._save

        monkeypatch.setattr(
            backend,
            "_save",
            lambda: (_ for _ in ()).throw(OSError("injected JSON all-clear failure")),
        )
        assert TaskLifecycle.cli_clear(all_sessions=True, confirm=False) == 1
        monkeypatch.setattr(backend, "_save", original_save)

        for session_id in sessions:
            assert set(TaskLifecycle(config=cfg, session_id=session_id).tasks) == {"7"}
        assert _receipts() == receipts_before


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

    def test_dashed_codex_transcript_returns_only_matching_child(
        self, isolated_state, cfg
    ):
        store = ThreadSafeDB()
        session_id = "dl-codex-return"
        manager = TaskLifecycle(config=cfg, session_id=session_id)
        first_id = CODEX_SPAWN_ID
        second_id = "01997b21-0e6f-7bb2-9d1e-aaaaaaaaaaaa"

        for task_id, agent_id in (("7", first_id), ("8", second_id)):
            manager.create_task(task_id, {"subject": f"Part {task_id}"}, "created")
            _spawn(
                session_id,
                store,
                cfg,
                result={"agent_id": agent_id, "status": "spawned"},
                tool_name="spawn_agent",
                cli_type="codex",
            )
            assert manager.delegate_tasks_from_markers(
                [task_id],
                allowed_task_ids=[task_id],
                session=agent_id,
                marker_identity=f"codex-marker-{task_id}",
            ) == [task_id]

        sub = _ctx(
            session_id,
            store,
            event="SubagentStop",
            tool_name="",
            transcript_path=f"/tmp/example-session/agent-{first_id}.jsonl",
            cli_type="codex",
        )
        assert TaskLifecycle(ctx=sub, config=cfg).handle_stop(sub) is None

        tasks = manager.tasks
        assert tasks["7"]["status"] == "delegation-returned"
        assert tasks["8"]["status"] == "delegated"

    def test_real_codex_child_session_returns_only_its_parent_task(
        self, isolated_state, cfg
    ):
        """Codex emits SubagentStop in the child session, keyed by agent_id."""
        store = ThreadSafeDB()
        parent_session = "codex-parent-session"
        child_session = "codex-child-session"
        parent = TaskLifecycle(config=cfg, session_id=parent_session)
        first_id = CODEX_SPAWN_ID
        second_id = "01997b21-0e6f-7bb2-9d1e-bbbbbbbbbbbb"

        for task_id, agent_id in (("7", first_id), ("8", second_id)):
            parent.create_task(task_id, {"subject": f"Part {task_id}"}, "created")
            _spawn(
                parent_session,
                store,
                cfg,
                result={"agent_id": agent_id, "status": "spawned"},
                tool_name="spawn_agent",
                cli_type="codex",
            )
            assert parent.delegate_tasks_from_markers(
                [task_id],
                allowed_task_ids=[task_id],
                session=agent_id,
                marker_identity=f"codex-real-wire-{task_id}",
            ) == [task_id]

        sub = _ctx(
            child_session,
            store,
            event="SubagentStop",
            tool_name="",
            transcript_path="/tmp/codex/parent-session.jsonl",
            agent_transcript_path=f"/tmp/codex/agent-{first_id}.jsonl",
            agent_id=first_id,
            cli_type="codex",
        )
        child = TaskLifecycle(ctx=sub, config=cfg)

        assert child.handle_stop(sub) is None
        assert parent.tasks["7"]["status"] == "delegation-returned"
        assert parent.tasks["8"]["status"] == "delegated"
        assert child.tasks == {}, "child state must not become task authority"

        assert child.handle_stop(sub) is None
        assert parent.tasks["7"]["status"] == "delegation-returned"

    @pytest.mark.parametrize("backend", ["json", "sqlite"])
    def test_duplicate_subagent_stop_preserves_return_timestamps(
        self, isolated_state, cfg, monkeypatch, backend
    ):
        """A retried return is a durable no-op, including eviction ordering."""
        monkeypatch.setitem(sm._CONFIG, "state_backend", backend)
        sm._reset_for_testing()
        store = ThreadSafeDB()
        session_id = f"dl-idempotent-{backend}"
        self._delegate(session_id, store, cfg)
        sub = _ctx(
            session_id,
            store,
            event="SubagentStop",
            tool_name="",
            transcript_path=f"/tmp/example-session/agent-{SPAWN_ID}.jsonl",
        )
        manager = TaskLifecycle(ctx=sub, config=cfg)

        assert manager.handle_stop(sub) is None
        first_ledger = _ledger(session_id)
        first_receipts = _receipts()
        time.sleep(0.01)
        assert manager.handle_stop(sub) is None

        assert _ledger(session_id) == first_ledger
        assert _receipts() == first_receipts

    def test_codex_agent_transcript_path_survives_normalization(self):
        normalized = normalize_hook_payload(
            {
                "cli_type": "codex",
                "hook_event_name": "SubagentStop",
                "session_id": "child",
                "agent_id": CODEX_SPAWN_ID,
                "transcript_path": "~/parent.jsonl",
                "agent_transcript_path": "~/child.jsonl",
            }
        )

        assert normalized["agent_id"] == CODEX_SPAWN_ID
        assert normalized["agent_transcript_path"].endswith("/child.jsonl")

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
        self._delegate(session_id, store, cfg)

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

    def test_active_fanout_is_not_evicted_before_ttl_recovery(
        self, isolated_state, cfg
    ):
        """The history bound must never discard an unresolved delegation."""
        store = ThreadSafeDB()
        session_id = "dl-over-ring-bound"
        manager = TaskLifecycle(config=cfg, session_id=session_id)

        for n in range(17):
            task_id = str(n)
            agent_id = f"d{n:02d}e4f5a6b7c8d9e{n:02d}"
            manager.create_task(task_id, {"subject": f"Part {n}"}, "created")
            _spawn(
                session_id,
                store,
                cfg,
                result=SPAWN_RESULT.replace(SPAWN_ID, agent_id),
            )
            assert manager.delegate_tasks_from_markers(
                [task_id],
                allowed_task_ids=[task_id],
                marker_identity=f"marker-{n}",
            ) == [task_id]

        oldest_id = "d00e4f5a6b7c8d9e00"
        assert oldest_id in {entry["id"] for entry in _ledger(session_id)}

        expired = time.time() - float(CONFIG["delegation_ttl_seconds"]) - 60
        manager.update_task(
            "0",
            {"metadata": {"delegated_at": expired}},
            "expire oldest",
        )

        def expire_oldest(metadata):
            for entry in metadata.get("agent_spawns", []):
                if entry.get("id") == oldest_id:
                    entry["at"] = expired

        manager.atomic_update_metadata(expire_oldest)
        stop = _ctx(session_id, store, event="Stop", tool_name="", assistant_text="done")
        TaskLifecycle(ctx=stop, config=cfg).handle_stop(stop)

        assert manager.tasks["0"]["status"] == "pending"


class TestHarnessNamesLiveInTheRegistry:
    """Per-harness behavior belongs on Platform, not in string comparisons.

    core.py:353 states the rule ("replaces hard-coded cli_type == 'claude'
    checks with a registry query"), and two defects in this file's own subject
    came from breaking it: the spawn allowlist matched only Claude's tool
    names, so Codex's `spawn_agent` never reached the ledger, and Conductor
    aggregation compared against "gemini", excluding the Qwen and Antigravity
    members that now carry the family's traffic.
    """

    def test_dispatch_modules_compare_no_harness_name_literals(self):
        import re

        offenders: dict[str, list[str]] = {}
        for module in ("task_lifecycle.py", "plugins.py"):
            source = (PLUGIN_ROOT / "src" / "autorun" / module).read_text(encoding="utf-8")
            # Strip comments: prose may quote a literal while describing why a
            # Platform field exists, and that is documentation, not dispatch.
            code = "\n".join(line.split("#", 1)[0] for line in source.splitlines())
            found = re.findall(r"cli_type\s*(?:==|!=)\s*\"[a-z]+\"", code)
            if found:
                offenders[module] = found
        assert not offenders, (
            "resolve harness behavior through Platform fields instead: "
            f"{offenders}"
        )

    def test_every_hook_capable_platform_declares_its_spawn_tools_or_none(self):
        """An empty set is a real answer; a missing field is an oversight."""
        from autorun.platforms import PLATFORMS, agent_spawn_tools_for

        for name, platform in PLATFORMS.items():
            resolved = agent_spawn_tools_for(name)
            assert resolved == platform.agent_spawn_tools, (
                f"{name} must resolve to its own declared spawn tools"
            )
            assert all(isinstance(tool, str) and tool for tool in resolved)

    def test_one_harness_spawn_name_cannot_seed_another_harness_ledger(
        self, isolated_state, cfg
    ):
        """Codex's `spawn_agent` arriving on a Claude session is not a spawn."""
        store = ThreadSafeDB()
        _spawn(
            "dl-crossharness", store, cfg,
            result=CODEX_SPAWN_RESULT, tool_name="spawn_agent", cli_type="claude",
        )

        assert _ledger("dl-crossharness") == []
