"""The tasks table keeps upgrading in place, and nothing about it reaches the user.

``user_version`` is the compatibility major: every older build (1.0.0rc1 to
rc3 and ``bdcdb9ac``) refuses any value but 2 at daemon start, so it stays 2.
``schema_meta('schema_revision')`` records additive changes those builds can
open. Revision 3 rewrites the stored ``tasks`` definition with SQLite's
documented ``writable_schema`` procedure for removing a CHECK: the status list
leaves the CHECK, and the generated ``blocks_stop``/``prunable`` columns, which
only older builds still read, block unless a status is known not to.

Each test drives an entry point a user reaches: ``SQLiteStore.initialize``
(every process that opens state), ``session_manager._build_store`` (the
daemon's path when a COMPLETE migration receipt exists), or
``autorun.__main__.main`` (the maintenance commands).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from autorun import session_manager as sm
from autorun import task_status
from autorun.session_manager import SCHEMA_APPLICATION_ID, SQLiteStore, TaskRepository
from test_task_status_not_in_schema import (
    EVENT_ROWS,
    SESSION,
    TASK_ROWS,
    _create_pre_3472b3ee_database,
)

SRC_DIR = Path(sm.__file__).resolve().parents[1]

# The schema 1.0.0 development builds wrote at user_version 1 (session_manager.py
# at 6a5624c0): no CHECK, no generated columns, and a partial index that lists
# the terminal statuses.
V1_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta ( key TEXT PRIMARY KEY, value TEXT NOT NULL );
CREATE TABLE IF NOT EXISTS sessions ( session TEXT PRIMARY KEY, namespace TEXT NOT NULL, last_modified REAL NOT NULL );
CREATE TABLE IF NOT EXISTS state ( session TEXT NOT NULL REFERENCES sessions(session) ON DELETE CASCADE, field TEXT NOT NULL, value_json TEXT NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY (session, field) );
CREATE INDEX IF NOT EXISTS sessions_retention ON sessions(namespace, last_modified);
CREATE TABLE IF NOT EXISTS tasks ( session TEXT NOT NULL REFERENCES sessions(session) ON DELETE CASCADE, task_id TEXT NOT NULL, status TEXT NOT NULL, updated_at REAL NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY (session, task_id) );
CREATE INDEX IF NOT EXISTS tasks_retention ON tasks(session, status, updated_at);
CREATE INDEX IF NOT EXISTS tasks_incomplete ON tasks(session, task_id) WHERE status NOT IN ('completed', 'deleted', 'ignored');
CREATE TABLE IF NOT EXISTS task_events ( session TEXT NOT NULL REFERENCES sessions(session) ON DELETE CASCADE, task_id TEXT, event_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, event_type TEXT NOT NULL, created_at REAL NOT NULL, payload_json TEXT NOT NULL, projected_at REAL, PRIMARY KEY (session, event_id), UNIQUE (session, idempotency_key), FOREIGN KEY (session, task_id) REFERENCES tasks(session, task_id) ON DELETE CASCADE );
CREATE INDEX IF NOT EXISTS task_events_order ON task_events(session, task_id, created_at, event_id);
CREATE INDEX IF NOT EXISTS task_events_pending_projection ON task_events(projected_at, session, created_at, event_id) WHERE projected_at IS NULL;
CREATE TABLE IF NOT EXISTS publication_receipts ( receipt_id TEXT PRIMARY KEY, kind TEXT NOT NULL, identity TEXT NOT NULL, generation INTEGER NOT NULL, phase TEXT NOT NULL, payload_json TEXT NOT NULL, updated_at REAL NOT NULL, UNIQUE (kind, identity, generation) );
"""


def _create_v1_database(path: Path, schema_meta=(), sessions=(), state=()) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(V1_SCHEMA)
        conn.execute(f"PRAGMA application_id = {SCHEMA_APPLICATION_ID}")
        conn.execute("PRAGMA user_version = 1")
        conn.executemany("INSERT INTO schema_meta VALUES (?, ?)", schema_meta)
        conn.executemany("INSERT INTO sessions VALUES (?, ?, ?)", sessions)
        conn.executemany("INSERT INTO state VALUES (?, ?, ?, ?)", state)
        conn.execute("INSERT INTO sessions VALUES (?, 'task_lifecycle', 1762891600.0)", (SESSION,))
        conn.executemany(
            "INSERT INTO tasks VALUES (?, ?, ?, 1762891629.0, ?)",
            [(SESSION, task_id, status, json.dumps(payload)) for task_id, status, payload in TASK_ROWS],
        )
        conn.executemany(
            "INSERT INTO task_events VALUES (?, ?, ?, ?, ?, 1762891629.0, '{}', 1762891629.0)",
            [(SESSION, task_id, event_id, key, kind) for task_id, event_id, key, kind in EVENT_ROWS],
        )
        conn.commit()
    finally:
        conn.close()


def _receipt_backed(state_dir: Path, create) -> Path:
    """A state directory whose COMPLETE receipt matches a database built by ``create``.

    The receipt's generation evidence lives in ``schema_meta``, so the rows a
    real migration wrote are carried into the replacement database.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    db_path = state_dir / "daemon_state.sqlite3"
    (state_dir / "daemon_state.json").write_text(json.dumps({"sess-a/file_policy": "SEARCH"}), encoding="utf-8")
    migrator = sm.StateMigrator(state_dir / "daemon_state.json", db_path, state_dir / "daemon_state.migration.json")
    assert migrator.migrate()["phase"] == "COMPLETE"
    conn = sqlite3.connect(db_path)
    try:
        carried = {
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
    create(db_path, **carried)
    return db_path


def _read(path: Path, sql: str, params=()):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _tasks_ddl(path: Path) -> str:
    return _read(path, "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tasks'")[0][0]


def _revision(path: Path):
    rows = _read(path, "SELECT value FROM schema_meta WHERE key = 'schema_revision'")
    return rows[0][0] if rows else None


def _columns(path: Path):
    return _read(path, "SELECT name, type, \"notnull\", pk, hidden FROM pragma_table_xinfo('tasks')")


def _content_digest(path: Path) -> str:
    rows = _read(path, "SELECT session, task_id, status, updated_at, payload_json FROM tasks ORDER BY session, task_id")
    events = _read(path, "SELECT * FROM task_events ORDER BY session, event_id")
    return hashlib.sha256(repr((rows, events)).encode()).hexdigest()


def _status_list(statuses) -> str:
    return "(" + ", ".join(f"'{status}'" for status in sorted(statuses)) + ")"


def _force_defensive_sqlite(monkeypatch) -> None:
    """Make the in-place rewrite meet the refusal Apple's system SQLite gives.

    Python 3.12+ can turn SQLite's defensive mode on per connection, which is
    the real refusal ("table sqlite_master may not be modified"). Older
    Pythons cannot, so the rewrite step raises that refusal itself.
    """
    if hasattr(sqlite3, "SQLITE_DBCONFIG_DEFENSIVE"):
        monkeypatch.setattr(
            sm, "_allow_schema_rewrite",
            lambda conn: conn.setconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE, True),
        )
    else:
        def refuse(conn, sql):
            raise sqlite3.OperationalError("table sqlite_master may not be modified")
        monkeypatch.setattr(sm, "_rewrite_tasks_definition", refuse)


# ── The definition rewrite ──────────────────────────────────────────────────


def test_first_start_rewrites_the_tasks_definition_and_keeps_every_row(tmp_path):
    db_path = tmp_path / "state" / "daemon_state.sqlite3"
    _create_pre_3472b3ee_database(db_path)
    columns, digest = _columns(db_path), _content_digest(db_path)
    indexes = _read(db_path, "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'tasks' ORDER BY name")

    SQLiteStore(db_path).initialize()

    ddl = _tasks_ddl(db_path)
    assert "CHECK" not in ddl, f"the status CHECK survived the upgrade: {ddl}"
    blocks = (
        "blocks_stop  INTEGER GENERATED ALWAYS AS "
        f"(CASE WHEN status IN {_status_list(task_status.NON_BLOCKING_TASK_STATUSES - {'awaiting-user'})} THEN 0 ELSE 1 END) VIRTUAL"
    )
    prunable = (
        "prunable     INTEGER GENERATED ALWAYS AS "
        f"(CASE WHEN status IN {_status_list(task_status.PRUNABLE_TASK_STATUSES)} THEN 1 ELSE 0 END) VIRTUAL"
    )
    assert blocks in ddl and prunable in ddl, f"the compatibility columns are not the fail-closed ones: {ddl}"
    assert _columns(db_path) == columns, "the rewrite changed the column list older builds read"
    assert _content_digest(db_path) == digest, "a tasks or task_events row changed in the upgrade"
    assert _read(db_path, "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'tasks' ORDER BY name") == indexes
    assert _read(db_path, "PRAGMA user_version") == [(2,)], "user_version moved; every older build would refuse the file"
    assert _revision(db_path) == "3"
    assert _read(db_path, "PRAGMA integrity_check") == [("ok",)]

    cookie = _read(db_path, "PRAGMA schema_version")
    master = _read(db_path, "SELECT type, name, sql FROM sqlite_master ORDER BY name")
    SQLiteStore(db_path).initialize()
    assert _read(db_path, "PRAGMA schema_version") == cookie, "a second start changed the schema again"
    assert _read(db_path, "SELECT type, name, sql FROM sqlite_master ORDER BY name") == master


def test_a_fresh_database_is_created_at_revision_3(tmp_path):
    db_path = tmp_path / "state" / "daemon_state.sqlite3"
    SQLiteStore(db_path).initialize()
    assert _revision(db_path) == "3"
    assert "CHECK" not in _tasks_ddl(db_path)
    assert _read(db_path, "PRAGMA user_version") == [(2,)]


def test_earlier_revision_3_awaiting_user_fails_closed_without_reindex(tmp_path, monkeypatch):
    path = tmp_path / "earlier.sqlite3"
    # The first revision-3 definition exempted awaiting-user for older readers.
    columns = dict(sm._TASK_COLUMNS)
    columns["blocks_stop"] = (
        "blocks_stop INTEGER GENERATED ALWAYS AS (CASE WHEN status IN "
        f"{sm._sql_status_list(task_status.NON_BLOCKING_TASK_STATUSES)} THEN 0 ELSE 1 END) VIRTUAL"
    )
    sql = "CREATE TABLE tasks (" + ",".join(columns.values()) + ",PRIMARY KEY(session, task_id))"
    with sqlite3.connect(path) as conn:
        for statement in sm._SCHEMA_STATEMENTS:
            conn.execute(sql if "CREATE TABLE IF NOT EXISTS tasks (" in statement else statement)
        conn.execute(f"PRAGMA application_id = {sm.SCHEMA_APPLICATION_ID}")
        conn.execute("PRAGMA user_version = 2")
        conn.executemany("INSERT INTO schema_meta VALUES (?, ?)", [
            ("schema_revision", "3"),
            ("blocks_stop_non_blocking", json.dumps(sorted(task_status.NON_BLOCKING_TASK_STATUSES))),
        ])
        conn.execute("INSERT INTO sessions VALUES ('old-r3', 'task_lifecycle', 1)")
        conn.execute("INSERT INTO tasks(session,task_id,status,updated_at,payload_json) VALUES ('old-r3','wait','awaiting-user',1,'{\"id\":\"wait\"}')")
    statements = []
    store = SQLiteStore(path)
    real = store._connect
    def connect(*args, **kwargs):
        conn = real(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn
    monkeypatch.setattr(store, "_connect", connect)
    store.initialize()
    assert _read(path, "SELECT blocks_stop FROM tasks") == [(1,)], "older builds still exempt awaiting-user"
    assert _read(path, "PRAGMA integrity_check") == [("ok",)]
    assert TaskRepository(store).list_incomplete("old-r3") == [], "current Python policy must keep awaiting-user waiting"
    assert not any(sql.startswith(("REINDEX", "UPDATE tasks ", "INSERT INTO tasks ", "DROP ")) for sql in statements), statements


def test_a_version_1_database_reaches_revision_3_with_fail_closed_columns(tmp_path):
    db_path = tmp_path / "state" / "daemon_state.sqlite3"
    _create_v1_database(db_path)
    raw = sqlite3.connect(db_path)
    raw.execute("INSERT INTO tasks VALUES (?, 'zz', 'zz-future-status', 1.0, '{}')", (SESSION,))
    raw.commit()
    raw.close()

    SQLiteStore(db_path).initialize()

    assert _read(db_path, "PRAGMA user_version") == [(2,)]
    assert _revision(db_path) == "3"
    flags = dict(
        (task_id, (blocks, prunable))
        for task_id, blocks, prunable in _read(db_path, "SELECT task_id, blocks_stop, prunable FROM tasks")
    )
    assert flags["479"] == (1, 0) and flags["480"] == (0, 1) and flags["478"] == (0, 0)
    assert flags["zz"] == (1, 0), "a status this build does not know must block for older builds too"


def test_a_table_an_older_build_upgraded_from_version_1_is_reindexed(tmp_path):
    """No CHECK guarded this table, so a status its old blocks_stop did not list may be stored.

    Builds before this one gave a version-1 table its generated columns with
    ``IN (<blocking statuses>)``, so a delegation-returned row was stored with
    blocks_stop 0 and left out of the partial index. The rewrite changes the
    expression, so the index must be rebuilt or it disagrees with the table.
    """
    db_path = tmp_path / "state" / "daemon_state.sqlite3"
    _create_v1_database(db_path)
    raw = sqlite3.connect(db_path)
    raw.execute(
        "ALTER TABLE tasks ADD COLUMN blocks_stop INTEGER GENERATED ALWAYS AS "
        "(CASE WHEN status IN ('in_progress', 'pending') THEN 1 ELSE 0 END) VIRTUAL"
    )
    raw.execute(
        "ALTER TABLE tasks ADD COLUMN prunable INTEGER GENERATED ALWAYS AS "
        "(CASE WHEN status IN ('completed', 'deleted', 'ignored') THEN 1 ELSE 0 END) VIRTUAL"
    )
    raw.execute("DROP INDEX tasks_incomplete")
    raw.execute("DROP INDEX tasks_retention")
    raw.execute("CREATE INDEX tasks_incomplete ON tasks(session, task_id) WHERE blocks_stop = 1")
    raw.execute("CREATE INDEX tasks_retention ON tasks(session, prunable, updated_at)")
    raw.execute("UPDATE tasks SET status = 'delegation-returned' WHERE task_id = '478'")
    raw.execute("PRAGMA user_version = 2")
    raw.commit()
    raw.close()

    store = SQLiteStore(db_path)
    store.initialize()

    assert _revision(db_path) == "3"
    assert _read(db_path, "PRAGMA integrity_check") == [("ok",)]
    assert {task["id"] for task in TaskRepository(store).list_incomplete(SESSION)} == {"478", "479"}


def test_a_higher_schema_revision_opens_and_is_left_alone(tmp_path):
    db_path = tmp_path / "state" / "daemon_state.sqlite3"
    SQLiteStore(db_path).initialize()
    raw = sqlite3.connect(db_path)
    raw.execute("UPDATE schema_meta SET value = '9' WHERE key = 'schema_revision'")
    raw.commit()
    raw.close()
    master = _read(db_path, "SELECT type, name, sql FROM sqlite_master ORDER BY name")

    SQLiteStore(db_path).initialize()

    assert _revision(db_path) == "9", "a newer additive revision was rewritten downward"
    assert _read(db_path, "SELECT type, name, sql FROM sqlite_master ORDER BY name") == master


def test_existing_status_policy_never_changes():
    """Skipping REINDEX is correct only while no shipped status changes meaning.

    Each table is ``STATUS_POLICY`` (blocks_stop, prunable) as committed in
    that revision of task_status.py. A status keeps its meaning forever;
    changing one is an incompatible change that needs a new user_version and
    its own reverse step (plugins/autorun/AGENTS.md).
    """
    shipped = {
        "dfc29c0d": {
            "pending": (True, False), "in_progress": (True, False), "paused": (False, False),
            "delegated": (False, False), "completed": (False, True), "deleted": (False, True),
            "ignored": (False, True),
        },
        "3472b3ee": {
            "pending": (True, False), "in_progress": (True, False), "paused": (False, False),
            "delegated": (False, False), "delegation-returned": (True, False), "completed": (False, True),
            "deleted": (False, True), "ignored": (False, True),
        },
        "72e44747": {
            "pending": (True, False), "in_progress": (True, False), "paused": (False, False),
            "delegated": (False, False), "delegation-returned": (True, False), "completed": (False, True),
            "deleted": (False, True), "ignored": (False, True),
        },
    }
    for revision, policy in shipped.items():
        for status, (blocks_stop, prunable) in policy.items():
            assert status in task_status.STATUS_POLICY, f"{status!r} (shipped in {revision}) was removed"
            now = task_status.STATUS_POLICY[status]
            assert (now.blocks_stop, now.prunable) == (blocks_stop, prunable), (
                f"{status!r} changed meaning since {revision}: was blocks_stop={blocks_stop}, "
                f"prunable={prunable}; now {now.blocks_stop}, {now.prunable}"
            )


def test_a_rewrite_that_does_not_read_back_is_rolled_back_and_the_store_still_opens(tmp_path, monkeypatch):
    db_path = tmp_path / "state" / "daemon_state.sqlite3"
    _create_pre_3472b3ee_database(db_path)
    old_ddl, digest = _tasks_ddl(db_path), _content_digest(db_path)
    monkeypatch.setattr(sm, "_verify_tasks_definition", lambda conn, sql, columns: ["columns read back as []"])

    store = SQLiteStore(db_path)
    store.initialize()

    assert _tasks_ddl(db_path) == old_ddl, "a rewrite that failed its check was committed"
    assert _revision(db_path) is None
    assert _content_digest(db_path) == digest
    assert TaskRepository(store).get_task(SESSION, "479")["status"] == "in_progress"


# ── Interruption and concurrency ────────────────────────────────────────────

_PAUSE_BEFORE_COMMIT = """
import sys, time
sys.path.insert(0, {src!r})
from autorun import session_manager as sm
original = sm._record_schema_revision
def pause(conn):
    original(conn)
    print("ready", flush=True)
    time.sleep(600)
sm._record_schema_revision = pause
sm.SQLiteStore({db!r}).initialize()
"""


def test_a_migration_killed_before_commit_leaves_the_old_definition_and_resumes(tmp_path):
    db_path = tmp_path / "state" / "daemon_state.sqlite3"
    _create_pre_3472b3ee_database(db_path)
    old_ddl, digest = _tasks_ddl(db_path), _content_digest(db_path)

    child = subprocess.Popen(
        [sys.executable, "-c", _PAUSE_BEFORE_COMMIT.format(src=str(SRC_DIR), db=str(db_path))],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert child.stdout.readline().strip() == "ready", "the child never reached the point before COMMIT"
    finally:
        child.kill()  # SIGKILL on POSIX, TerminateProcess on Windows
        child.wait(timeout=30)

    assert _tasks_ddl(db_path) == old_ddl, "a killed migration left a changed definition"
    assert _revision(db_path) is None
    assert _content_digest(db_path) == digest
    assert _read(db_path, "PRAGMA integrity_check") == [("ok",)]

    SQLiteStore(db_path).initialize()
    assert _revision(db_path) == "3"
    assert "CHECK" not in _tasks_ddl(db_path)
    assert _content_digest(db_path) == digest


_START_TOGETHER = """
import logging, sys, time
from pathlib import Path
sys.path.insert(0, {src!r})
from autorun import session_manager as sm
rewrites = []
class Count(logging.Handler):
    def emit(self, record):
        if record.getMessage().startswith("Rewrote the tasks table definition"):
            rewrites.append(record)
logger = logging.getLogger(sm.__name__)
logger.addHandler(Count())
logger.setLevel(logging.INFO)
go = Path({go!r})
while not go.exists():
    time.sleep(0.005)
sm._build_store({key!r})
print(len(rewrites), flush=True)
"""


def test_eight_processes_starting_at_once_migrate_exactly_once(tmp_path):
    state_dir = tmp_path / "sessions"
    db_path = _receipt_backed(state_dir, _create_pre_3472b3ee_database)
    digest = _content_digest(db_path)
    go = tmp_path / "go"
    env = dict(os.environ, AUTORUN_TEST_STATE_DIR=str(state_dir))
    children = [
        subprocess.Popen(
            [sys.executable, "-c", _START_TOGETHER.format(src=str(SRC_DIR), go=str(go), key=str(state_dir))],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
        )
        for _ in range(8)
    ]
    time.sleep(0.5)
    go.touch()
    results = [child.communicate(timeout=120) for child in children]

    assert [child.returncode for child in children] == [0] * 8, [err for _out, err in results]
    assert sum(int(out.strip()) for out, _err in results) == 1, f"rewrites per process: {[out.strip() for out, _ in results]}"
    assert _revision(db_path) == "3"
    assert _content_digest(db_path) == digest
    assert _read(db_path, "PRAGMA integrity_check") == [("ok",)]


# ── Version handling ────────────────────────────────────────────────────────

def _newer_database(db_path: Path, writer) -> bytes:
    SQLiteStore(db_path).initialize()
    raw = sqlite3.connect(db_path)
    raw.execute("PRAGMA journal_mode = DELETE")
    raw.execute("PRAGMA user_version = 3")
    raw.execute("DELETE FROM schema_meta WHERE key = 'schema_written_by'")
    if writer:
        raw.execute("INSERT INTO schema_meta VALUES ('schema_written_by', ?)", (writer,))
    raw.commit()
    raw.close()
    return db_path.read_bytes()


@pytest.mark.parametrize(
    "writer, expected",
    [
        (
            "9.9.9",
            "{path} was written by autorun 9.9.9 (schema 3); this autorun reads schema 2 and older, "
            "so it opened nothing and changed nothing. Install that release again: "
            "uv tool install --force autorun-ai==9.9.9, then autorun --install. "
            "autorun --state-status shows this again.",
        ),
        (
            None,
            "{path} was written by a newer autorun (schema 3); this autorun reads schema 2 and older, "
            "so it opened nothing and changed nothing. Install the newest release again: "
            "uv tool install --force autorun-ai, then autorun --install. "
            "autorun --state-status shows this again.",
        ),
    ],
    ids=["writer-recorded", "writer-unknown"],
)
def test_a_newer_user_version_is_refused_naming_its_writer_and_a_command_that_works(
    tmp_path, monkeypatch, capsys, writer, expected
):
    from autorun.__main__ import create_parser, main

    state_dir = tmp_path / "sessions"
    db_path = state_dir / "daemon_state.sqlite3"
    before = _newer_database(db_path, writer)
    message = expected.format(path=db_path)

    with pytest.raises(sm.SessionBackendError) as at_open:
        SQLiteStore(db_path).initialize()
    assert str(at_open.value) == message

    migrator = sm.StateMigrator(state_dir / "daemon_state.json", db_path, state_dir / "daemon_state.migration.json")
    with pytest.raises(sm.SessionBackendError) as at_receipt:
        migrator._validate_generation(db_path, "generation", "digest")
    assert str(at_receipt.value) == message, "the receipt path and the store disagree on the version rule"
    assert db_path.read_bytes() == before, "a refused database was modified"

    # The commands the message names exist and work.
    parser = create_parser()
    assert parser.parse_args(["--install"]).install is not None
    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(state_dir))
    assert main(["--state-status"]) == 0
    status = capsys.readouterr().out
    assert "schema             : 3 (newer than this autorun reads)" in status
    if writer:
        assert f"schema written by  : autorun {writer}" in status
    uv = os.environ.get("UV") or shutil.which("uv")
    assert uv, "the message names uv, so the test environment must have it"
    assert subprocess.run([uv, "tool", "install", "--help"], capture_output=True).returncode == 0


def test_a_refused_newer_database_names_no_command_that_does_not_exist(tmp_path, monkeypatch, capsys):
    """Reproduces the refusal that told users to "Run an explicit schema migration"."""
    from autorun.__main__ import main

    state_dir = tmp_path / "sessions"
    _newer_database(state_dir / "daemon_state.sqlite3", "9.9.9")
    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(state_dir))

    assert main(["--state-maintenance"]) == 1
    output = capsys.readouterr().out
    assert "explicit schema migration" not in output
    assert "uv tool install --force autorun-ai==9.9.9" in output


def test_a_version_1_database_with_a_receipt_reaches_its_upgrade_step(tmp_path, monkeypatch, capsys):
    from autorun import ipc
    from autorun.__main__ import main

    state_dir = tmp_path / "sessions"
    db_path = _receipt_backed(state_dir, _create_v1_database)
    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(state_dir))
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setattr(ipc, "AUTORUN_LOCK_PATH", runtime / "daemon.lock")

    assert main(["--state-migrate"]) == 0, capsys.readouterr().out
    sm._reset_for_testing()
    try:
        store = sm._build_store(str(state_dir))
    finally:
        sm._reset_for_testing()

    assert isinstance(store, SQLiteStore)
    assert _read(db_path, "PRAGMA user_version") == [(2,)]
    assert _revision(db_path) == "3"
    assert store.read_field("sess-a", "file_policy") == "SEARCH"


# ── When SQLite refuses the in-place rewrite ───────────────────────────────


def test_defensive_sqlite_takes_the_rebuild_path(tmp_path, monkeypatch, caplog):
    db_path = tmp_path / "state" / "daemon_state.sqlite3"
    _create_pre_3472b3ee_database(db_path)
    columns, digest = _columns(db_path), _content_digest(db_path)
    _force_defensive_sqlite(monkeypatch)

    store = SQLiteStore(db_path)
    with caplog.at_level("INFO", logger=sm.__name__):
        store.initialize()

    messages = [record.getMessage() for record in caplog.records]
    assert any(message.startswith("Upgrading autorun state (") for message in messages), messages
    assert not any(message.startswith("Rewrote the tasks table definition") for message in messages)

    assert "CHECK" not in _tasks_ddl(db_path)
    assert _revision(db_path) == "3"
    assert _columns(db_path) == columns
    assert _content_digest(db_path) == digest, "the rebuild lost or changed a tasks or task_events row"
    assert _read(db_path, "PRAGMA foreign_key_check") == []
    assert _read(db_path, "PRAGMA integrity_check") == [("ok",)]
    repository = TaskRepository(store)
    repository.put_task(SESSION, "478", dict(repository.get_task(SESSION, "478"), status="delegation-returned"))
    with store.operation_scope(5.0) as owner:
        assert owner.connection.execute("PRAGMA foreign_keys").fetchone() == (1,), "foreign keys stayed off"


def test_no_room_for_the_rebuild_keeps_revision_2_and_says_what_it_needs(tmp_path, monkeypatch, capsys):
    from autorun.__main__ import main

    state_dir = tmp_path / "sessions"
    db_path = state_dir / "daemon_state.sqlite3"
    _create_pre_3472b3ee_database(db_path)
    old_ddl = _tasks_ddl(db_path)
    _force_defensive_sqlite(monkeypatch)
    real = shutil.disk_usage
    monkeypatch.setattr(sm.shutil, "disk_usage", lambda path: real(path)._replace(free=512 * 1024))
    tasks_bytes = sum(
        size for (size,) in _read(
            db_path,
            "SELECT pgsize FROM dbstat WHERE name IN "
            "(SELECT name FROM sqlite_master WHERE tbl_name = 'tasks')",
        )
    )
    needed_mb = math.ceil(2.2 * tasks_bytes / (1024 * 1024))

    store = SQLiteStore(db_path)
    store.initialize()  # the store still opens

    assert _tasks_ddl(db_path) == old_ddl
    assert _revision(db_path) is None
    repository = TaskRepository(store)
    with pytest.raises(sm.SessionBackendError) as refused:
        repository.put_task(SESSION, "478", dict(repository.get_task(SESSION, "478"), status="delegation-returned"))
    assert str(refused.value).endswith(
        f"The task table of {db_path} predates this status, and replacing it needs "
        f"{needed_mb} MB free in {state_dir} (0 MB free now). Free that space; the next "
        "autorun start replaces the table."
    ), str(refused.value)
    repository.put_task(SESSION, "479", dict(repository.get_task(SESSION, "479"), status="completed"))

    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(state_dir))
    assert main(["--state-status"]) == 0
    status = capsys.readouterr().out
    assert "schema             : 2 (revision 2)" in status
    assert f"upgrade pending    : needs {needed_mb} MB free in {state_dir} (0 MB free now)" in status


# ── Rollback to JSON ───────────────────────────────────────────────────────


def test_state_rollback_brings_task_rows_back_to_json(tmp_path, monkeypatch):
    from autorun import ipc
    from autorun.__main__ import main
    from autorun.task_lifecycle import TaskLifecycle, TaskLifecycleConfig

    state_dir = tmp_path / "sessions"
    state_dir.mkdir()
    (state_dir / "daemon_state.json").write_text(json.dumps({"sess-a/file_policy": "SEARCH"}), encoding="utf-8")
    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(state_dir))
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setattr(ipc, "AUTORUN_LOCK_PATH", runtime / "daemon.lock")
    assert main(["--state-migrate"]) == 0

    def lifecycle():
        sm._reset_for_testing()
        return TaskLifecycle(session_id="rollback-tasks", config=TaskLifecycleConfig(storage_dir=tmp_path / "logs"))

    lifecycle().create_task("1", {"subject": "Keep me across the rollback"}, "")
    assert lifecycle().tasks["1"]["subject"] == "Keep me across the rollback"

    assert main(["--state-rollback"]) == 0
    monkeypatch.setitem(sm._CONFIG, "state_backend", "json")
    try:
        restored = lifecycle().tasks
    finally:
        sm._reset_for_testing()
    assert "1" in restored, f"--state-rollback returned to JSON without the session's tasks: {restored}"
    assert restored["1"]["subject"] == "Keep me across the rollback"
    assert restored["1"]["status"] == "pending"
