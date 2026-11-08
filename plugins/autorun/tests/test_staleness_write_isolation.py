"""Direct durability and cache-read contracts for rc4 staleness state.

The staleness flag is touched on every tool event.  A clear flag must not
queue another durable transaction, while dropping an advisory value that never
hid a durable value must not throw away the session hydration and force a
storage read on the next lookup.
"""

from __future__ import annotations

import contextlib

import pytest

from autorun import core, session_manager
from autorun.core import EventContext, ThreadSafeDB
from autorun.task_lifecycle import clear_task_staleness_enforcement


@pytest.fixture(params=("json", "sqlite"))
def backend(request, tmp_path, monkeypatch):
    """Keep both state backends in the direct contract test."""
    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(tmp_path / "sessions"))
    monkeypatch.setitem(session_manager._CONFIG, "state_backend", request.param)
    session_manager._reset_for_testing()
    yield request.param
    session_manager._reset_for_testing()


@pytest.fixture
def transactions(monkeypatch):
    """Record actual state transactions, rather than implementation calls."""
    opened: list[str] = []
    real = core.session_state

    @contextlib.contextmanager
    def counting(session_id, *args, **kwargs):
        opened.append(session_id)
        with real(session_id, *args, **kwargs) as state:
            yield state

    monkeypatch.setattr(core, "session_state", counting)
    return opened


def _ctx(store: ThreadSafeDB, session_id: str) -> EventContext:
    return EventContext(
        session_id=session_id,
        event="PreToolUse",
        prompt="",
        store=store,
        session_identity_authority="payload",
        cli_type="codex",
    )


def test_redundant_clear_of_staleness_flag_does_not_write(backend, transactions):
    """An already-clear flag remains durable without another write transaction."""
    store = ThreadSafeDB()
    store.set("stale:task_staleness_enforce_next", False)
    ctx = _ctx(store, "stale")

    # Hydrate the session, as the daemon does before a normal tool event.
    assert ctx.state_get("task_staleness_enforce_next") is False
    transactions.clear()

    clear_task_staleness_enforcement(ctx)
    clear_task_staleness_enforcement(ctx)

    assert transactions == []
    assert ThreadSafeDB().get("stale:task_staleness_enforce_next") is False


def test_dropping_non_shadowing_staleness_value_keeps_session_hydrated(
    backend, transactions
):
    """Evicting memory-only state does not trigger a redundant storage read."""
    store = ThreadSafeDB(volatile_max_entries=1)
    assert store.get("memory-only:missing", "DEFAULT") == "DEFAULT"
    store.set_volatile("memory-only:staleness", True)
    store.set_volatile("memory-only/other:staleness", True)
    transactions.clear()

    # The first value was advisory only; its eviction must not discard the
    # whole session hydration.  A missing lookup therefore stays in-memory.
    assert store.get("memory-only:missing", "DEFAULT") == "DEFAULT"
    assert transactions == []

