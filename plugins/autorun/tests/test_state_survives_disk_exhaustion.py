#!/usr/bin/env python3
"""What the state store does when the disk underneath it is exhausted.

A live session on 2026-08-18 logged 67 of these in ten minutes:

    SessionBackendError: Could not configure state connection for
    ~/.claude/sessions/daemon_state.sqlite3: unable to open database file

and then recovered on its own. The operator had no way to know that recovery
was expected, because the message named neither a cause nor the fact that the
next hook would simply work once the condition cleared.

DESIGN NOTE, deliberately recorded so it is not "fixed" later: durable state
does NOT fall back to an in-memory copy when the disk is unavailable. That
sounds like resilience and is the opposite. One daemon serves many concurrent
sessions and several processes share this database, so a memory copy that
outlived a failed write would make those processes disagree about a permission
decision, and the disagreement would survive into the next restart as silent
divergence. `test_state_persistence_failures.py` states the rule as "memory
must not outvote storage", and it is right.

What exhaustion handling means here is therefore narrower and honest:
  * a failed write still raises and still names what was lost;
  * the failure explains the likely cause and that recovery is automatic;
  * nothing latches, so the first call after space returns succeeds.

Advisory, non-durable counters are a separate matter and already live in memory
under `volatile_state_max_*` bounds.
"""

import sqlite3

import pytest

from autorun.session_manager import SessionBackendError, SQLiteStore


def _message_for(monkeypatch, tmp_path, sqlite_message):
    """The SessionBackendError text produced when a PRAGMA fails."""
    real_connect = sqlite3.connect

    class _FailingConnection:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, *_args, **_kwargs):
            raise sqlite3.OperationalError(sqlite_message)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def fake_connect(*args, **kwargs):
        return _FailingConnection(real_connect(*args, **kwargs))

    monkeypatch.setattr(sqlite3, "connect", fake_connect)
    store = SQLiteStore(tmp_path / "daemon_state.sqlite3")
    with pytest.raises(SessionBackendError) as raised:
        store.initialize()
    return str(raised.value)


@pytest.mark.parametrize(
    "sqlite_message,expected_cause",
    [
        ("database or disk is full", "space"),
        ("unable to open database file", "space"),
        ("disk I/O error", "space"),
        ("attempt to write a readonly database", "permission"),
    ],
)
def test_a_storage_failure_names_a_cause_the_operator_can_act_on(
    monkeypatch, tmp_path, sqlite_message, expected_cause
):
    """Principle 6: say what failed, why, and what to do about it.

    SQLite's own wording is not actionable on its own -- "unable to open
    database file" is what a full disk, a missing directory and a permission
    problem all look like from the caller's side.
    """
    message = _message_for(monkeypatch, tmp_path, sqlite_message)
    assert sqlite_message in message, "the underlying error must survive"
    assert expected_cause in message.lower(), (
        f"the message should point at a {expected_cause} cause: {message}"
    )


def test_a_storage_failure_says_recovery_is_automatic(monkeypatch, tmp_path):
    """The 2026-08-18 incident cleared itself and nobody could tell it would.

    Without this the operator's options look like "restart something" -- and
    the restart command is itself a tool call the permission gate is blocking
    at that moment.
    """
    message = _message_for(monkeypatch, tmp_path, "database or disk is full")
    assert "automatic" in message.lower() or "next" in message.lower(), (
        f"say that the next attempt recovers on its own: {message}"
    )


def test_an_orphaned_stage_sidecar_pair_is_swept(tmp_path):
    """A -wal/-shm pair whose staged database is gone is not evidence.

    `_discard_stage_sidecars` deliberately leaves a *failed* migration's stage
    in place so a maintainer can inspect it, and that stays true. But once the
    stage database itself is gone the retained pair proves nothing on its own,
    and nothing ever removes it: the next migration picks a fresh generation
    suffix, so every interrupted publication leaves a permanent pair behind.
    One such orphan was found on a live machine with no matching stage file.
    """
    db_path = tmp_path / "daemon_state.sqlite3"
    orphan_shm = tmp_path / "daemon_state.sqlite3.stage.abc123-shm"
    orphan_wal = tmp_path / "daemon_state.sqlite3.stage.abc123-wal"
    orphan_shm.write_bytes(b"x")
    orphan_wal.write_bytes(b"")

    kept_stage = tmp_path / "daemon_state.sqlite3.stage.def456"
    kept_shm = tmp_path / "daemon_state.sqlite3.stage.def456-shm"
    kept_stage.write_bytes(b"")
    kept_shm.write_bytes(b"x")

    SQLiteStore(db_path).initialize()

    assert not orphan_shm.exists(), "an orphaned -shm was kept"
    assert not orphan_wal.exists(), "an orphaned -wal was kept"
    assert kept_stage.exists() and kept_shm.exists(), (
        "a stage that still has its database is evidence and must survive"
    )


def test_nothing_latches_so_the_first_call_after_recovery_succeeds(monkeypatch, tmp_path):
    """A transient full disk must not wedge the store until a restart.

    Outages here last minutes to days. If a failed open were remembered, the
    daemon would keep refusing after the operator freed space, and the only
    remaining move would be the restart the gate is blocking.
    """
    db_path = tmp_path / "daemon_state.sqlite3"
    real_connect = sqlite3.connect
    failures = {"remaining": 1}

    class _FailingOnce:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, *args, **kwargs):
            if failures["remaining"]:
                failures["remaining"] -= 1
                raise sqlite3.OperationalError("database or disk is full")
            return self._inner.execute(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def fake_connect(*args, **kwargs):
        return _FailingOnce(real_connect(*args, **kwargs))

    monkeypatch.setattr(sqlite3, "connect", fake_connect)
    store = SQLiteStore(db_path)
    with pytest.raises(SessionBackendError):
        store.initialize()

    # "Space returned": the next connection behaves normally.
    store.initialize()
    with store.session("session-a") as state:
        state["file_policy"] = "ALLOW"
    with store.session("session-a") as state:
        assert state["file_policy"] == "ALLOW"
