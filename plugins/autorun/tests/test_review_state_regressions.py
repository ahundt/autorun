"""Dispatch regressions for incomplete guard evaluation and concurrent task waits."""

import contextlib
import copy
import json
import time

import pytest

from autorun import core, plugins
from autorun import cache_guard
from autorun import session_manager as sm
from autorun.platforms import PLATFORMS
from autorun.scoped_allow import ScopedAllow
from autorun.task_lifecycle import TaskLifecycle
from stop_gate_support import Session, awaiting, gate_env_fixture  # noqa: F401


def context(event="PreToolUse", cli_type="claude", store=None, **kwargs):
    return core.EventContext(
        session_id="review-state", event=event, cli_type=cli_type,
        store=store if store is not None else core.ThreadSafeDB(), **kwargs,
    )



def assert_gate_failure_guidance(response):
    """Lock contention says wait; any other state failure says how to recover."""
    reason = str(response)
    contended = "state_lock_contended" in reason and "Do not run `autorun --restart-daemon`" in reason
    failed = "state_evaluation_failed" in reason and "AUTORUN_DISABLE=1" in reason
    assert contended != failed, response

@pytest.mark.parametrize("error_type", [sm.SessionTimeoutError, sm.SessionPersistenceError])
@pytest.mark.parametrize("field,scope,tool,setup", [
    ("session_blocked_patterns", "review-state", "Bash", {}),
    ("global_blocked_patterns", "__global__", "Bash", {}),
    ("session_allowed_patterns", "review-state", "Bash", {}),
    ("global_allowed_patterns", "__global__", "Bash", {}),
    ("file_policy", "review-state", "Write", {"file_policy": "SEARCH"}),
    ("task_enforcement_pause", "review-state", "Bash", {}),
    ("cache/toggle", "review-state", "Bash", {}),
    ("cache/toggle", "__global__", "Bash", {}),
    ("cache/threshold", "review-state", "Bash", {}),
    ("cache/threshold", "__global__", "Bash", {}),
    ("cache/last_usage", "review-state", "Bash", {"cache/toggle": {"enabled": True}}),
    ("task_staleness_enforce_next", "review-state", "Bash", {}),
    ("autorun_active", "review-state", "ExitPlanMode", {}),
    ("autorun_stage", "review-state", "ExitPlanMode", {"autorun_active": True}),
])
def test_policy_read_failure_denies_dispatch(gate_env, monkeypatch, error_type, field, scope, tool, setup):
    command = "git status" if field.endswith("_patterns") else "echo hello"
    ctx = context(tool_name=tool, tool_input={"command": command, "file_path": "/nonexistent/review-file"})
    for name, value in setup.items():
        ctx.state_set(name, value)
    if field.endswith("_patterns"):
        block_field = field if "blocked" in field else "session_blocked_patterns"
        ctx.state_set(block_field, [{"pattern": "git status", "suggestion": "stored review block"}], session_id=scope if "blocked" in field else None)
    # Policy attributes must be loaded through the same path as a fresh hook.
    ctx = context(store=object.__getattribute__(ctx, "_store"), tool_name=tool, tool_input=ctx.tool_input)
    method = "get_stored" if field == "task_enforcement_pause" else "get"
    real = getattr(core.ThreadSafeDB, method)
    reached = []

    def fail_one(self, key, *args, **kwargs):
        if key == f"{scope}:{field}" and not reached:
            reached.append(key)
            raise error_type(f"injected {field} read failure")
        return real(self, key, *args, **kwargs)

    monkeypatch.setattr(core.ThreadSafeDB, method, fail_one)
    response = core.app.dispatch(ctx)
    assert reached == [f"{scope}:{field}"]
    assert response["hookSpecificOutput"].get("permissionDecision") == "deny", response
    reason = str(response)
    if error_type is sm.SessionTimeoutError:
        # A lost lock race clears by itself; restart advice here caused a restart storm.
        assert "state_lock_contended" in reason and "Do not run `autorun --restart-daemon`" in reason, response
    else:
        assert "state_evaluation_failed" in reason and "AUTORUN_DISABLE=1" in reason, response


@pytest.mark.parametrize("blocked", [False, True])
def test_healthy_policy_read_dispatch(gate_env, blocked):
    ctx = context(tool_name="Bash", tool_input={"command": "git status"})
    if blocked:
        ctx.state_set("session_blocked_patterns", [{"pattern": "git status", "suggestion": "stored review block"}])
    response = core.app.dispatch(ctx)
    decision = (response or {}).get("hookSpecificOutput", {}).get("permissionDecision")
    assert (decision == "deny") == blocked, response
    if blocked:
        assert "stored review block" in str(response)
        assert "AUTORUN_DISABLE=1" not in str(response)


@pytest.mark.parametrize("scope", ["review-state", "__global__"])
@pytest.mark.parametrize("error_type", [sm.SessionTimeoutError, sm.SessionPersistenceError])
def test_cache_override_read_failure_denies_dispatch(gate_env, monkeypatch, scope, error_type):
    ctx = context(tool_name="Bash", tool_input={"command": "echo hello"})
    ctx.state_set("cache/toggle", {"enabled": True})
    ctx.state_set("cache/threshold", {"cache_hit_ratio_min": 0.9})
    ctx.state_set("cache/last_usage", {"cache_hit_ratio": 0.1, "observed_at": time.time()})
    real = cache_guard.session_state
    reached = []

    @contextlib.contextmanager
    def fail_override(session_id, *args, **kwargs):
        if session_id == scope:
            reached.append(session_id)
            raise error_type("injected cache override read failure")
        with real(session_id, *args, **kwargs) as state:
            yield state

    monkeypatch.setattr(cache_guard, "session_state", fail_override)
    response = core.app.dispatch(ctx)
    assert reached == [scope]
    assert (response or {}).get("hookSpecificOutput", {}).get("permissionDecision") == "deny", response
    assert_gate_failure_guidance(response)


@pytest.mark.parametrize("error_type", [sm.SessionTimeoutError, sm.SessionPersistenceError])
def test_unbatched_policy_read_failure_denies_dispatch(gate_env, error_type):
    class Unreadable(dict):
        def get(self, key, default=None):
            if key == "review-state:session_blocked_patterns":
                raise error_type("injected unbatched policy read failure")
            return super().get(key, default)

    ctx = context(store=Unreadable(sentinel=True), tool_name="Bash", tool_input={"command": "git status"})
    response = core.app.dispatch(ctx)
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert_gate_failure_guidance(response)


@pytest.mark.parametrize("error_type", [sm.SessionTimeoutError, sm.SessionPersistenceError])
def test_pause_pruning_read_failure_denies_dispatch(gate_env, monkeypatch, error_type):
    ctx = context(tool_name="Bash", tool_input={"command": "echo hello"})
    ctx.state_set("task_enforcement_pause", {"origin": "invalid"})
    real = core.ThreadSafeDB.update
    reached = []

    def fail_one(self, key, *args, **kwargs):
        if key == "review-state:task_enforcement_pause":
            reached.append(key)
            raise error_type("injected pause pruning read failure")
        return real(self, key, *args, **kwargs)

    monkeypatch.setattr(core.ThreadSafeDB, "update", fail_one)
    response = core.app.dispatch(ctx)
    assert reached
    assert (response or {}).get("hookSpecificOutput", {}).get("permissionDecision") == "deny", response
    assert_gate_failure_guidance(response)


@pytest.mark.parametrize("error_type", [sm.SessionTimeoutError, sm.SessionPersistenceError])
def test_task_capability_snapshot_failure_denies_dispatch(gate_env, monkeypatch, error_type):
    ctx = context(tool_name="Bash", tool_input={"command": "echo hello"}, active_tools=["TaskCreate", "TaskUpdate", "TaskList"])
    ctx.state_set("task_staleness_enforce_next", True)
    reached = []

    def fail(*args):
        reached.append(True)
        raise error_type("injected task capability snapshot read failure")

    monkeypatch.setattr(TaskLifecycle, "get_unfinished_tasks", fail)
    response = core.app.dispatch(ctx)
    assert reached
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny", response
    assert_gate_failure_guidance(response)


@pytest.mark.parametrize("field", ["processed_transcript_policy_commands", "session_blocked_patterns"])
@pytest.mark.parametrize("error_type", [sm.SessionTimeoutError, sm.SessionPersistenceError])
def test_transcript_policy_read_failure_denies_dispatch(gate_env, tmp_path, monkeypatch, field, error_type):
    transcript = tmp_path / "policy.jsonl"
    transcript.write_text(json.dumps({"type": "response_item", "payload": {
        "type": "message", "role": "user",
        "content": [{"type": "input_text", "text": "ar:no 'git status'"}],
    }}) + "\n")
    reached = []
    for proxy in (sm._StateProxy, sm._SQLiteStateProxy):
        real = proxy.get

        def fail_one(self, key, default=None, _real=real):
            if key == field and not reached:
                reached.append(key)
                raise error_type(f"injected transcript {field} read failure")
            return _real(self, key, default)

        monkeypatch.setattr(proxy, "get", fail_one)
    response = core.app.dispatch(context(
        cli_type="codex", transcript_path=str(transcript),
        tool_name="Bash", tool_input={"command": "git status"},
    ))
    assert reached == [field]
    assert_gate_failure_guidance(response)
    response = core.app.dispatch(context(
        cli_type="codex", transcript_path=str(transcript),
        tool_name="Bash", tool_input={"command": "git status"},
    ))
    assert "AUTORUN_DISABLE=1" not in str(response)
    assert (response or {}).get("hookSpecificOutput", {}).get("permissionDecision") == "deny", response


@pytest.mark.parametrize("event,cli_type", [("BeforeTool", "gemini"), ("PermissionRequest", "claude"), ("PostToolUse", "claude")])
@pytest.mark.parametrize("error_type", [sm.SessionTimeoutError, sm.SessionPersistenceError])
def test_state_read_failure_event_policy(gate_env, monkeypatch, event, cli_type, error_type):
    def read(ctx):
        assert ctx.state_get("session_blocked_patterns", []) == []

    def fail(*args, **kwargs):
        raise error_type("injected policy read failure")

    monkeypatch.setitem(core.app.chains, event, [read])
    monkeypatch.setattr(core.ThreadSafeDB, "get", fail)
    response = core.app.dispatch(context(event, cli_type))
    if event == "PostToolUse":
        assert response.get("continue", True) is True
        assert "injected policy read failure" in str(response)
    else:
        assert_gate_failure_guidance(response)
        if event == "PermissionRequest":
            assert response["hookSpecificOutput"]["decision"]["behavior"] == "deny"
        else:
            assert response["decision"] == "deny"


@pytest.mark.parametrize("error_type", [sm.SessionPersistenceError, sm.SessionTimeoutError])
def test_expired_allow_cleanup_failure_denies_dispatch(monkeypatch, error_type):
    command = " ".join(["rm", "-rf", "victim"])
    ctx = context(tool_name="Bash", tool_input={"command": command})
    ctx.state_set("session_allowed_patterns", [ScopedAllow(
        pattern="rm", granted_at=1, ttl_seconds=1,
    ).to_dict()])
    real_update = core.EventContext.state_update
    reached = []

    def fail_cleanup(self, key, *args, **kwargs):
        if key == "session_allowed_patterns":
            reached.append(key)
            raise error_type("injected expired-allow cleanup failure")
        return real_update(self, key, *args, **kwargs)

    monkeypatch.setattr(core.EventContext, "state_update", fail_cleanup)
    response = core.app.dispatch(ctx)
    assert reached == ["session_allowed_patterns"]
    assert response["hookSpecificOutput"].get("permissionDecision") == "deny", response
    assert_gate_failure_guidance(response)


@pytest.mark.parametrize("event,cli_type", [
    ("BeforeTool", "gemini"), ("PermissionRequest", "claude"),
    ("PostToolUse", "claude"),
])
@pytest.mark.parametrize("error_type", [sm.SessionPersistenceError, sm.SessionTimeoutError])
def test_handler_state_failure_gate_policy(monkeypatch, event, cli_type, error_type):
    def fail(_ctx):
        raise error_type("injected handler state failure")

    monkeypatch.setitem(core.app.chains, event, [fail])
    response = core.app.dispatch(context(event, cli_type))
    if event == "PostToolUse":
        assert response.get("continue", True) is True, response
        assert "injected handler state failure" in str(response)
        assert response.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"
    elif event == "PermissionRequest":
        assert response["hookSpecificOutput"]["decision"]["behavior"] == "deny", response
    else:
        assert response["decision"] == "deny", response


@pytest.mark.parametrize("cli_type", [
    name for name, platform in PLATFORMS.items() if platform.schema_type != "none"
])
def test_notice_preserves_dispatch_deny(monkeypatch, cli_type):
    monkeypatch.setitem(core.app.chains, "PreToolUse", [lambda ctx: ctx.deny("guard denied")])
    ctx = context(cli_type=cli_type)
    original = core.app.dispatch(ctx)
    response = core._attach_state_failure_notice(original, ctx, "state failure notice")
    for key, value in original.items():
        if key == "hookSpecificOutput":
            for field in ("permissionDecision", "permissionDecisionReason", "decision"):
                if field in value:
                    assert response.get(key, {}).get(field) == value[field], response
        elif key in {"decision", "block", "continue", "cancel", "error"}:
            assert response.get(key) == value, response
    assert original, f"{cli_type} did not build a deny"


@pytest.mark.parametrize("decision", [None, "deny"])
@pytest.mark.parametrize("error_type", [sm.SessionPersistenceError, sm.SessionTimeoutError])
def test_finished_dispatch_keeps_decision_when_flush_fails(monkeypatch, decision, error_type):
    ctx = context()

    @contextlib.contextmanager
    def fail_flush(**kwargs):
        yield
        raise error_type("injected flush failure")

    monkeypatch.setattr(object.__getattribute__(ctx, "_store"), "batch_writes", fail_flush)
    monkeypatch.setitem(core.app.chains, "PreToolUse", [
        lambda ctx: ctx.deny("guard denied") if decision else None,
    ])
    response = core.app.dispatch(ctx)
    assert response.get("hookSpecificOutput", {}).get("permissionDecision") == decision
    assert "injected flush failure" in str(response)


def test_prompt_restore_keeps_concurrently_indexed_wait(gate_env, monkeypatch):
    session = Session("review-waits", "claude")
    session.seed("A", "First decision")
    session.seed("B", "Second decision")
    session.stop(awaiting("A", "approve first decision"))
    assert session.status("A") == "awaiting-user"
    real_update = TaskLifecycle._update_task_rows
    inserted = []

    def insert_wait(self, updaters, metadata_update=None):
        if "A" in updaters and not inserted:
            inserted.append("B")
            session.stop(awaiting("B", "approve second decision"))
            assert session.status("B") == "awaiting-user"
        return real_update(self, updaters, metadata_update)

    monkeypatch.setattr(TaskLifecycle, "_update_task_rows", insert_wait)
    session.prompt("first decision approved", prompt_author="user")
    assert inserted == ["B"]
    assert session.status("A") == "in_progress"
    assert session.status("B") == "awaiting-user"
    assert session.manager.session_metadata.get("awaiting_user_task_ids") == ["B"]
    session.prompt("second decision approved", prompt_author="user")
    assert session.status("B") == "in_progress"
    assert not session.manager.session_metadata.get("awaiting_user_task_ids")


@pytest.mark.parametrize("decision", ["approve newer decision", "approve first decision"])
def test_prompt_restore_keeps_newer_wait_on_same_task(gate_env, monkeypatch, decision):
    session = Session("review-newer-wait", "claude")
    session.seed("A", "First decision")
    session.stop(awaiting("A", "approve first decision"))
    real_update = TaskLifecycle._update_task_rows
    newer = []

    def replace_wait(self, updaters, metadata_update=None):
        if "A" in updaters and not newer:
            newer.append(None)
            session.prompt("first decision approved", prompt_author="user")
            session.stop(awaiting("A", decision))
            newer[0] = copy.deepcopy(session.task("A")["metadata"]["awaiting_user"])
        return real_update(self, updaters, metadata_update)

    monkeypatch.setattr(TaskLifecycle, "_update_task_rows", replace_wait)
    session.prompt("older reply", prompt_author="user")
    assert session.status("A") == "awaiting-user"
    assert session.task("A")["metadata"]["awaiting_user"] == newer[0]
    assert session.manager.session_metadata.get("awaiting_user_task_ids") == ["A"]
    session.prompt("newer decision approved", prompt_author="user")
    assert session.status("A") == "in_progress"
    assert not session.manager.session_metadata.get("awaiting_user_task_ids")


def test_noop_pretool_write_transactions(tmp_path, monkeypatch, record_property, capsys):
    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setitem(sm._CONFIG, "state_backend", "sqlite")
    sm._reset_for_testing()
    try:
        manager = sm.get_session_manager()
        real = manager._store.write_transaction
        transactions = []

        @contextlib.contextmanager
        def counted(owner):
            transactions.append(owner)
            with real(owner) as connection:
                yield connection

        monkeypatch.setattr(manager._store, "write_transaction", counted)
        reads = []
        for method in ("get", "get_stored"):
            real_read = getattr(core.ThreadSafeDB, method)

            def counted_read(self, key, *args, _real=real_read, _method=method, **kwargs):
                reads.append((_method, key))
                return _real(self, key, *args, **kwargs)

            monkeypatch.setattr(core.ThreadSafeDB, method, counted_read)
        store = core.ThreadSafeDB()
        ctx = context(store=store, tool_name="Bash", tool_input={"command": "echo hello"})
        response = core.app.dispatch(ctx)
        cold = len(transactions)
        cold_reads = len(reads)
        assert response is None, response
        transactions.clear()
        reads.clear()
        response = core.app.dispatch(context(
            store=store, tool_name="Bash", tool_input={"command": "echo hello"},
        ))
        warm = len(transactions)
        warm_reads = len(reads)
        with capsys.disabled():
            print(f"no-op PreToolUse: writes={(cold, warm)}, reads={(cold_reads, warm_reads)}")
        record_property("noop_pretool_write_transactions", (cold, warm))
        record_property("noop_pretool_state_reads", (cold_reads, warm_reads))
        assert response is None, response
        assert (cold, warm) == (2, 0)
        assert (cold_reads, warm_reads) == (12, 12)
    finally:
        sm._reset_for_testing()
