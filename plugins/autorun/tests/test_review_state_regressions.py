"""Dispatch regressions for incomplete guard evaluation and concurrent task waits."""

import contextlib

import pytest

from autorun import core, plugins
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
    assert "AUTORUN_DISABLE=1" in str(response)


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


def test_noop_pretool_write_transactions(tmp_path, monkeypatch, record_property):
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
        store = core.ThreadSafeDB()
        ctx = context(store=store, tool_name="Bash", tool_input={"command": "echo hello"})
        response = core.app.dispatch(ctx)
        cold = len(transactions)
        assert response is None, response
        transactions.clear()
        response = core.app.dispatch(context(
            store=store, tool_name="Bash", tool_input={"command": "echo hello"},
        ))
        warm = len(transactions)
        record_property("noop_pretool_write_transactions", (cold, warm))
        assert response is None, response
        assert (cold, warm) == (2, 0)
    finally:
        sm._reset_for_testing()
