"""The tasks table keeps no copy of the status policy, and existing databases keep opening.

Today's failure: commit 3472b3ee added ``delegation-returned`` to
``task_status.STATUS_POLICY`` but not to databases already created, whose
``tasks`` table embeds the status list three times (the CHECK and the
``blocks_stop`` and ``prunable`` generated columns). The live database
rejects every SubagentStop return ("CHECK constraint failed", 41 times in
daemon.log), so delegations sat until the one-hour TTL reverted them.
stop_gate_design.md 3f removes the status list from the DDL so that
``task_status.py`` is its only owner, and an unknown status fails closed.

Scope, on the maintainer's instruction: these tests pin properties that hold
under any migration approach. They assert no ``user_version`` number, no
backup file and no migration function name. Migration mechanics
(reversibility, rollback by an older build, size and time bounds on a large
database) are left for the migration redesign.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from types import MappingProxyType

import pytest

from autorun import session_manager as sm
from autorun import task_status
from autorun.session_manager import SCHEMA_APPLICATION_ID, SQLiteStore, TaskRepository
from autorun.task_status import TaskStatusPolicy
from stop_gate_support import Session, blocks, gate_env_fixture, text  # noqa: F401  (registers the gate_env fixture)

# The schema of a version-2 database created before 3472b3ee, frozen: the
# statements of session_manager._SCHEMA_STATEMENTS at 3472b3ee^ with their
# status lists rendered, matching the live database's stored CREATE text.
PRE_3472B3EE_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta ( key TEXT PRIMARY KEY, value TEXT NOT NULL );
CREATE TABLE IF NOT EXISTS sessions ( session TEXT PRIMARY KEY, namespace TEXT NOT NULL, last_modified REAL NOT NULL );
CREATE TABLE IF NOT EXISTS state ( session TEXT NOT NULL REFERENCES sessions(session) ON DELETE CASCADE, field TEXT NOT NULL, value_json TEXT NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY (session, field) );
CREATE INDEX IF NOT EXISTS sessions_retention ON sessions(namespace, last_modified);
CREATE TABLE IF NOT EXISTS tasks ( session TEXT NOT NULL REFERENCES sessions(session) ON DELETE CASCADE, task_id TEXT NOT NULL, status TEXT NOT NULL CHECK (status IN ('completed', 'delegated', 'deleted', 'ignored', 'in_progress', 'paused', 'pending')), blocks_stop INTEGER GENERATED ALWAYS AS (CASE WHEN status IN ('in_progress', 'pending') THEN 1 ELSE 0 END) VIRTUAL, prunable INTEGER GENERATED ALWAYS AS (CASE WHEN status IN ('completed', 'deleted', 'ignored') THEN 1 ELSE 0 END) VIRTUAL, updated_at REAL NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY (session, task_id) );
CREATE INDEX IF NOT EXISTS tasks_retention ON tasks(session, prunable, updated_at);
CREATE INDEX IF NOT EXISTS tasks_incomplete ON tasks(session, task_id) WHERE blocks_stop = 1;
CREATE TABLE IF NOT EXISTS task_events ( session TEXT NOT NULL REFERENCES sessions(session) ON DELETE CASCADE, task_id TEXT, event_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, event_type TEXT NOT NULL, created_at REAL NOT NULL, payload_json TEXT NOT NULL, projected_at REAL, PRIMARY KEY (session, event_id), UNIQUE (session, idempotency_key), FOREIGN KEY (session, task_id) REFERENCES tasks(session, task_id) ON DELETE CASCADE );
CREATE INDEX IF NOT EXISTS task_events_order ON task_events(session, task_id, created_at, event_id);
CREATE INDEX IF NOT EXISTS task_events_pending_projection ON task_events(projected_at, session, created_at, event_id) WHERE projected_at IS NULL;
CREATE TABLE IF NOT EXISTS publication_receipts ( receipt_id TEXT PRIMARY KEY, kind TEXT NOT NULL, identity TEXT NOT NULL, generation INTEGER NOT NULL, phase TEXT NOT NULL, payload_json TEXT NOT NULL, updated_at REAL NOT NULL, UNIQUE (kind, identity, generation) );
"""
SESSION = "__task_lifecycle__live-6eaa3402"
TASK_ROWS = [
    ("478", "delegated", {"id": "478", "subject": "Audit the installer", "metadata": {"delegated_to_session": "a1b2c3d4e5f6a7b8c", "delegated_at": 1762891629.0}}),
    ("479", "in_progress", {"id": "479", "subject": "Fix what the audit finds", "blockedBy": ["478"]}),
    ("480", "completed", {"id": "480", "subject": "Write the changelog"}),
]
EVENT_ROWS = [
    ("478", "evt-478-delegate", "key-478-delegate", "DELEGATE"),
    ("479", "evt-479-start", "key-479-start", "START"),
]


def _create_pre_3472b3ee_database(path: Path, schema_meta=(), sessions=(), state=()) -> None:
    """A version-2 database with the frozen DDL and the rows above."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(PRE_3472B3EE_SCHEMA)
        conn.execute(f"PRAGMA application_id = {SCHEMA_APPLICATION_ID}")
        conn.execute("PRAGMA user_version = 2")
        conn.executemany("INSERT INTO schema_meta VALUES (?, ?)", schema_meta)
        conn.executemany("INSERT INTO sessions VALUES (?, ?, ?)", sessions)
        conn.executemany("INSERT INTO state VALUES (?, ?, ?, ?)", state)
        conn.execute("INSERT INTO sessions VALUES (?, 'task_lifecycle', 1762891600.0)", (SESSION,))
        conn.executemany(
            "INSERT INTO tasks (session, task_id, status, updated_at, payload_json) VALUES (?, ?, ?, 1762891629.0, ?)",
            [(SESSION, task_id, status, json.dumps(payload)) for task_id, status, payload in TASK_ROWS],
        )
        conn.executemany(
            "INSERT INTO task_events VALUES (?, ?, ?, ?, ?, 1762891629.0, '{}', 1762891629.0)",
            [(SESSION, task_id, event_id, key, kind) for task_id, event_id, key, kind in EVENT_ROWS],
        )
        conn.commit()
    finally:
        conn.close()


def _raw_status_write(path: Path, task_id: str, status: str) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("UPDATE tasks SET status = ? WHERE session = ? AND task_id = ?", (status, SESSION, task_id))
        conn.commit()
    finally:
        conn.close()


def _tasks_ddl(path: Path) -> str:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tasks'").fetchone()[0]
    finally:
        conn.close()


def _assert_rows_intact(repository: TaskRepository) -> None:
    stored = repository.list_tasks(SESSION)
    for task_id, status, payload in TASK_ROWS:
        if task_id == "478":
            continue  # the test's own write moved it
        assert stored[task_id]["status"] == status, f"task {task_id} changed status in the upgrade"
        assert stored[task_id]["subject"] == payload["subject"], f"task {task_id} lost its payload"
    assert stored["478"]["metadata"]["delegated_to_session"] == "a1b2c3d4e5f6a7b8c"
    events = {(event["task_id"], event["event_id"]) for event in repository.events(SESSION)}
    assert events == {(task_id, event_id) for task_id, event_id, _key, _kind in EVENT_ROWS}, (
        f"task_events rows were lost or changed in the upgrade: {events}"
    )


def _store_returned(repository: TaskRepository) -> None:
    record = dict(repository.get_task(SESSION, "478"), status="delegation-returned")
    try:
        repository.put_task(SESSION, "478", record)
    except sm.SessionBackendError as error:
        pytest.fail(f"the opened database still rejects a delegation-returned write: {error}")
    assert repository.get_task(SESSION, "478")["status"] == "delegation-returned"


def test_pre_3472b3ee_database_stores_delegation_returned_once_opened(tmp_path):
    db_path = tmp_path / "state" / "daemon_state.sqlite3"
    _create_pre_3472b3ee_database(db_path)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        _raw_status_write(db_path, "478", "delegation-returned")  # the live failure, reproduced

    store = SQLiteStore(db_path)
    store.initialize()
    repository = TaskRepository(store)

    _store_returned(repository)
    _assert_rows_intact(repository)


def test_status_added_to_policy_needs_no_schema_change(tmp_path, monkeypatch):
    """A status the DDL has never seen is stored, read back and gated by the Python policy.

    The generated ``blocks_stop`` and ``prunable`` columns still name the
    statuses known when the table was written: older builds read them after a
    rollback. Nothing in this build takes policy from them, so adding a
    status changes ``task_status.py`` and no table definition.
    """
    extended = dict(task_status.STATUS_POLICY)
    extended["needs-maintainer-review"] = TaskStatusPolicy(blocks_stop=True, prunable=False, autorun_owned=True)
    extended["parked-for-later"] = TaskStatusPolicy(blocks_stop=False, prunable=False, autorun_owned=True)
    monkeypatch.setattr(task_status, "STATUS_POLICY", MappingProxyType(extended))
    monkeypatch.setattr(
        task_status, "NON_BLOCKING_TASK_STATUSES",
        frozenset(status for status, policy in extended.items() if not policy.blocks_stop),
    )

    for label, prepare in (("fresh", lambda path: None), ("pre-3472b3ee", _create_pre_3472b3ee_database)):
        db_path = tmp_path / label / "daemon_state.sqlite3"
        prepare(db_path)
        store = SQLiteStore(db_path)
        store.initialize()
        ddl = _tasks_ddl(db_path)
        repository = TaskRepository(store)
        try:
            repository.put_task(SESSION, "481", {"id": "481", "subject": "New status", "status": "needs-maintainer-review"})
            repository.put_task(SESSION, "482", {"id": "482", "subject": "Another", "status": "parked-for-later"})
        except sm.SessionBackendError as error:
            pytest.fail(f"{label} database: a status added only to STATUS_POLICY was refused by the DDL: {error}")
        assert repository.get_task(SESSION, "481")["status"] == "needs-maintainer-review"
        assert "CHECK" not in ddl, f"{label} database: the tasks DDL still restricts statuses: {ddl}"
        assert _tasks_ddl(db_path) == ddl, f"{label} database: storing a new status changed the table definition"
        incomplete = {task["id"] for task in repository.list_incomplete(SESSION)}
        assert "481" in incomplete, f"{label} database: a blocking status the DDL never saw does not block Stop"
        assert "482" not in incomplete, f"{label} database: Stop followed the DDL, not task_status.py"


def test_unknown_stored_status_blocks_stop(gate_env, monkeypatch):
    """A status written by a newer build fails closed: it blocks Stop and is named."""
    monkeypatch.setitem(sm._CONFIG, "state_backend", "sqlite")
    sm._reset_for_testing()
    session = Session(f"schema-unknown-{uuid.uuid4().hex[:8]}", "claude")
    session.seed("7", "Task a newer build moved to a status this build does not know")
    db_path = gate_env / "daemon_state.sqlite3"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("UPDATE tasks SET status = 'needs-maintainer-review' WHERE task_id = '7'")
        conn.commit()
    except sqlite3.IntegrityError as error:
        pytest.fail(f"the tasks DDL refuses a status a newer build may write: {error}")
    finally:
        conn.close()
    sm._reset_for_testing()

    response = session.stop("Done for now.")

    assert blocks(response, "claude"), f"an unknown stored status failed open and allowed the Stop: {response}"
    assert "#7" in text(response) and "needs-maintainer-review" in text(response), (
        f"the block does not name #7 and its unknown status: {response}"
    )


def test_existing_database_opens_through_store_selection(tmp_path, monkeypatch):
    """The live database's shape opens through ``session_manager._get_store``.

    The live machine has a COMPLETE migration receipt, so ``_select_store`` runs
    ``StateMigrator.migrate()`` (which validates the database generation)
    before ``SQLiteStore.initialize()`` whenever the receipt is COMPLETE. No
    version-mismatch error may reach the user and the daemon must be able to
    serve, which here means storing a returned delegation.
    """
    state_dir = tmp_path / "sessions"
    state_dir.mkdir()
    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(state_dir))
    db_path = state_dir / "daemon_state.sqlite3"
    (state_dir / "daemon_state.json").write_text(json.dumps({"sess-a/file_policy": "SEARCH"}), encoding="utf-8")
    migrator = sm.StateMigrator(state_dir / "daemon_state.json", db_path, state_dir / "daemon_state.migration.json")
    assert migrator.migrate()["phase"] == "COMPLETE"
    # Replace the published database with the live shape, keeping the rows
    # that carry the receipt's generation evidence and the migrated state.
    conn = sqlite3.connect(db_path)
    try:
        carried = {
            # The live database's schema_meta holds exactly these two rows.
            "schema_meta": conn.execute(
                "SELECT key, value FROM schema_meta WHERE key IN ('migration_generation', 'source_digest')"
            ).fetchall(),
            "sessions": conn.execute("SELECT session, namespace, last_modified FROM sessions").fetchall(),
            "state": conn.execute("SELECT session, field, value_json, updated_at FROM state").fetchall(),
        }
    finally:
        conn.close()
    for suffix in ("", "-wal", "-shm"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)
    _create_pre_3472b3ee_database(db_path, **carried)
    sm._reset_for_testing()

    try:
        store = sm._get_store()
    except sm.SessionStateError as error:
        pytest.fail(f"opening the existing database with a COMPLETE receipt raised to the user: {error}")
    finally:
        sm._reset_for_testing()

    assert isinstance(store, SQLiteStore)
    with sm.session_state("sess-a") as state:
        assert state["file_policy"] == "SEARCH", "state carried by the migration receipt was lost"
    repository = TaskRepository(store)
    _store_returned(repository)
    _assert_rows_intact(repository)
