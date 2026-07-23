"""Schema, durability settings, and resource ownership of the SQLite store.

The store this file describes replaces a whole-file JSON rewrite, so the
properties worth testing are not "does a value round trip" — the contract
tests already pin that — but the ones a storage engine has to get right and a
file rewrite never had to:

  * every connection and transaction has exactly one owner and is released on
    every exit path, including the ones that fail while failing;
  * the durability and maintenance settings are the ones deliberately chosen,
    not whatever the library defaults to that week;
  * a database that is not ours, or is newer than us, is refused rather than
    guessed at;
  * a contended writer waits and then reports a timeout distinguishable from
    a disk or schema failure, because only one of those is worth retrying.

Timeouts here are short on purpose. A test that proves a writer waits must
not spend the real hook budget doing it.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent
SRC_DIR = PLUGIN_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autorun.session_manager import (  # noqa: E402
    SCHEMA_APPLICATION_ID,
    SCHEMA_USER_VERSION,
    SessionBackendError,
    SessionTimeoutError,
    SQLiteStore,
    _migrate_schema_v1_to_v2,
)

SHORT_TIMEOUT = 0.05


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "state" / "daemon_state.sqlite3"


@pytest.fixture
def store(db_path):
    store = SQLiteStore(db_path)
    yield store


def _open_reader(db_path):
    """A plain connection for inspecting the file without going through the store."""
    return sqlite3.connect(str(db_path))


def _inject_connection_fault(monkeypatch, *, failing_statement=None, fail_close=False):
    """Make the store's next connection fail at a chosen point.

    ``sqlite3.Connection`` is immutable, so the fault is installed by handing
    ``sqlite3.connect`` a subclass through its ``factory`` argument rather
    than by patching methods onto the type.
    """
    real_connect = sqlite3.connect

    class FaultyConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if failing_statement and sql.strip().upper().startswith(failing_statement):
                raise sqlite3.OperationalError(f"{failing_statement.lower()} failed")
            return super().execute(sql, *args, **kwargs)

        def close(self):
            super().close()
            if fail_close:
                raise sqlite3.OperationalError("close failed")

    def connect(*args, **kwargs):
        kwargs["factory"] = FaultyConnection
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)


# ── Schema ───────────────────────────────────────────────────────────────────

class TestSchema:
    def test_the_database_identifies_itself(self, store, db_path):
        store.initialize()
        conn = _open_reader(db_path)
        try:
            assert conn.execute("PRAGMA application_id").fetchone()[0] == SCHEMA_APPLICATION_ID
            assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_USER_VERSION
        finally:
            conn.close()

    def test_versioned_database_without_autorun_identity_is_refused(self, db_path):
        db_path.parent.mkdir(parents=True)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE TABLE unrelated (value TEXT)")
            conn.execute("PRAGMA user_version = 1")
            conn.commit()
        finally:
            conn.close()

        with pytest.raises(SessionBackendError, match="application id|another program"):
            SQLiteStore(db_path).initialize()

    def test_autorun_header_without_required_schema_is_refused(self, db_path):
        db_path.parent.mkdir(parents=True)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(f"PRAGMA application_id = {SCHEMA_APPLICATION_ID}")
            conn.execute(f"PRAGMA user_version = {SCHEMA_USER_VERSION}")
            conn.execute("CREATE TABLE unrelated (value TEXT)")
            conn.commit()
        finally:
            conn.close()

        with pytest.raises(SessionBackendError, match="schema|missing"):
            SQLiteStore(db_path).initialize()

    def test_v1_task_rows_gain_generated_policy_columns(self, db_path):
        db_path.parent.mkdir(parents=True)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "CREATE TABLE tasks (session TEXT, task_id TEXT, status TEXT, "
                "updated_at REAL, payload_json TEXT, PRIMARY KEY(session, task_id))"
            )
            conn.executemany(
                "INSERT INTO tasks VALUES (?, ?, ?, ?, ?)",
                [
                    ("s", "open", "in_progress", 1.0, "{}"),
                    ("s", "paused", "paused", 1.0, "{}"),
                    ("s", "done", "completed", 1.0, "{}"),
                ],
            )
            _migrate_schema_v1_to_v2(conn)
            rows = conn.execute(
                "SELECT task_id, blocks_stop, prunable FROM tasks ORDER BY task_id"
            ).fetchall()
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        finally:
            conn.close()

        assert rows == [("done", 0, 1), ("open", 1, 0), ("paused", 0, 0)]
        assert version == SCHEMA_USER_VERSION

    def test_every_table_and_index_exists(self, store, db_path):
        store.initialize()
        conn = _open_reader(db_path)
        try:
            names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','index')"
                )
            }
        finally:
            conn.close()

        expected = {
            "schema_meta",
            "sessions",
            "state",
            "tasks",
            "task_events",
            "publication_receipts",
            "sessions_retention",
            "tasks_retention",
            "task_events_order",
            "task_events_pending_projection",
            "tasks_incomplete",
        }
        assert expected <= names, f"missing: {sorted(expected - names)}"

    def test_initialization_is_idempotent(self, store):
        store.initialize()
        store.initialize()
        store.initialize()

    def test_a_state_row_requires_its_session_row(self, store, db_path):
        """Foreign keys are enforced, so writes must create the owning session.

        This is what forces the session upsert to happen inside the same
        transaction as the field write, which in turn is the single place
        ``last_modified`` may advance.
        """
        store.initialize()
        with store.operation_scope(SHORT_TIMEOUT) as owner:
            with store.write_transaction(owner) as conn:
                with pytest.raises(sqlite3.IntegrityError):
                    conn.execute(
                        "INSERT INTO state (session, field, value_json, updated_at) "
                        "VALUES (?, ?, ?, ?)",
                        ("orphan", "f", "1", time.time()),
                    )

    def test_deleting_a_session_removes_its_dependent_rows(self, store):
        store.initialize()
        now = time.time()
        with store.operation_scope(SHORT_TIMEOUT) as owner:
            with store.write_transaction(owner) as conn:
                conn.execute(
                    "INSERT INTO sessions (session, namespace, last_modified) VALUES (?,?,?)",
                    ("s", "default", now),
                )
                conn.execute(
                    "INSERT INTO state (session, field, value_json, updated_at) VALUES (?,?,?,?)",
                    ("s", "f", "1", now),
                )
                conn.execute(
                    "INSERT INTO tasks (session, task_id, status, updated_at, payload_json) "
                    "VALUES (?,?,?,?,?)",
                    ("s", "t1", "pending", now, "{}"),
                )
                conn.execute(
                    "INSERT INTO task_events "
                    "(session, task_id, event_id, idempotency_key, event_type, created_at, payload_json) "
                    "VALUES (?,?,?,?,?,?,?)",
                    ("s", "t1", "e1", "k1", "created", now, "{}"),
                )

        with store.operation_scope(SHORT_TIMEOUT) as owner:
            with store.write_transaction(owner) as conn:
                conn.execute("DELETE FROM sessions WHERE session = ?", ("s",))

        with store.operation_scope(SHORT_TIMEOUT) as owner:
            with store.write_transaction(owner) as conn:
                for table in ("state", "tasks", "task_events"):
                    remaining = conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE session = ?", ("s",)
                    ).fetchone()[0]
                    assert remaining == 0, f"{table} kept rows for a deleted session"

    def test_an_event_cannot_reuse_an_idempotency_key(self, store):
        """Retrying a hook must not append the same event twice."""
        store.initialize()
        now = time.time()
        with store.operation_scope(SHORT_TIMEOUT) as owner:
            with store.write_transaction(owner) as conn:
                conn.execute(
                    "INSERT INTO sessions (session, namespace, last_modified) VALUES (?,?,?)",
                    ("s", "default", now),
                )
                conn.execute(
                    "INSERT INTO task_events "
                    "(session, task_id, event_id, idempotency_key, event_type, created_at, payload_json) "
                    "VALUES (?,?,?,?,?,?,?)",
                    ("s", None, "e1", "key", "audit", now, "{}"),
                )
                with pytest.raises(sqlite3.IntegrityError):
                    conn.execute(
                        "INSERT INTO task_events "
                        "(session, task_id, event_id, idempotency_key, event_type, created_at, payload_json) "
                        "VALUES (?,?,?,?,?,?,?)",
                        ("s", None, "e2", "key", "audit", now, "{}"),
                    )


# ── Connection settings ──────────────────────────────────────────────────────

class TestConnectionSettings:
    def test_the_durability_and_concurrency_settings_are_the_chosen_ones(self, store):
        store.initialize()
        with store.operation_scope(SHORT_TIMEOUT) as owner:
            conn = owner.connection
            assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal", (
                "Readers must not block behind a writer."
            )
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2, (
                "Acknowledged hook state must survive a crash, so FULL is the "
                "starting point; NORMAL would be a measured tradeoff, not a "
                "default inherited from a reconstructable index."
            )

    def test_the_maintenance_settings_bound_sidecar_growth(self, store):
        store.initialize()
        with store.operation_scope(SHORT_TIMEOUT) as owner:
            conn = owner.connection
            assert conn.execute("PRAGMA auto_vacuum").fetchone()[0] == 2, (
                "Incremental, so reclaiming space is a maintenance decision "
                "rather than something that happens inside a hook."
            )
            assert conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0] > 0
            assert conn.execute("PRAGMA journal_size_limit").fetchone()[0] > 0

    def test_repeated_writes_do_not_grow_the_write_ahead_log_without_bound(self, store, db_path):
        """A leaked reader or a disabled checkpoint policy shows up here.

        Both keep the write-ahead log from being reclaimed, so it grows with
        every commit until the disk fills.
        """
        store.initialize()
        wal = Path(str(db_path) + "-wal")
        now = time.time()
        with store.operation_scope(30.0) as owner:
            with store.write_transaction(owner) as conn:
                conn.execute(
                    "INSERT INTO sessions (session, namespace, last_modified) VALUES (?,?,?)",
                    ("s", "default", now),
                )

        for _ in range(150):
            with store.operation_scope(30.0) as owner:
                with store.write_transaction(owner) as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO state (session, field, value_json, updated_at) "
                        "VALUES (?,?,?,?)",
                        ("s", "f", '"' + "x" * 512 + '"', time.time()),
                    )

        size = wal.stat().st_size if wal.exists() else 0
        assert size < 8 * 1024 * 1024, (
            f"The write-ahead log reached {size} bytes over 150 small writes, "
            "so it is being pinned open instead of reclaimed."
        )


# ── Resource ownership ───────────────────────────────────────────────────────

class TestResourceOwnership:
    def test_all_state_read_does_not_begin_a_write_transaction(
        self, store, monkeypatch
    ):
        store.initialize()
        with store.session("read-only", SHORT_TIMEOUT) as state:
            state["field"] = "value"

        def forbid_write_transaction(*_args, **_kwargs):
            raise AssertionError("read-only all_state acquired a write transaction")

        monkeypatch.setattr(store, "write_transaction", forbid_write_transaction)
        with store.all_state(write=False) as state:
            assert state["read-only/field"] == "value"

    def test_a_completed_scope_leaves_nothing_open(self, store):
        store.initialize()
        with store.operation_scope(SHORT_TIMEOUT) as owner:
            with store.write_transaction(owner):
                pass
        assert owner.connection is None
        assert owner.transaction_depth == 0
        assert store.open_connection_count() == 0

    @pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt, MemoryError])
    def test_a_failing_body_still_releases_everything(self, store, failure):
        store.initialize()
        with pytest.raises(failure):
            with store.operation_scope(SHORT_TIMEOUT) as owner:
                with store.write_transaction(owner) as conn:
                    conn.execute(
                        "INSERT INTO sessions (session, namespace, last_modified) VALUES (?,?,?)",
                        ("doomed", "default", time.time()),
                    )
                    raise failure("failed mid-transaction")

        assert owner.connection is None
        assert owner.transaction_depth == 0
        assert store.open_connection_count() == 0

        with store.operation_scope(SHORT_TIMEOUT) as owner2:
            with store.write_transaction(owner2) as conn:
                count = conn.execute(
                    "SELECT COUNT(*) FROM sessions WHERE session = ?", ("doomed",)
                ).fetchone()[0]
        assert count == 0, "a rolled-back insert was still visible"

    def test_a_transaction_left_open_by_a_caller_is_reported(self, store):
        """Leaking a transaction out of its scope must be loud, not silent."""
        store.initialize()
        held = []
        with pytest.raises(SessionBackendError, match="transaction"):
            with store.operation_scope(SHORT_TIMEOUT) as owner:
                # Entered and deliberately never exited, which is what a caller
                # that stashes the context manager somewhere would do. The
                # reference has to outlive the statement, or the abandoned
                # generator is finalized immediately and cleans up after
                # itself, hiding exactly the mistake being tested for.
                manager = store.write_transaction(owner)
                manager.__enter__()
                held.append(manager)

        assert owner.connection is None
        assert store.open_connection_count() == 0

    def test_a_close_failure_does_not_hide_the_original_error(self, store, monkeypatch):
        store.initialize()
        _inject_connection_fault(monkeypatch, fail_close=True)

        with pytest.raises(RuntimeError, match="the real problem"):
            with store.operation_scope(SHORT_TIMEOUT):
                raise RuntimeError("the real problem")

    def test_a_close_failure_with_no_other_error_is_visible(self, store, monkeypatch):
        """A connection that will not close may still hold a lock."""
        store.initialize()
        _inject_connection_fault(monkeypatch, fail_close=True)

        with pytest.raises(SessionBackendError, match="close"):
            with store.operation_scope(SHORT_TIMEOUT):
                pass

    def test_a_rollback_failure_does_not_hide_the_original_error(self, store, monkeypatch):
        store.initialize()
        _inject_connection_fault(monkeypatch, failing_statement="ROLLBACK")

        with pytest.raises(ValueError, match="the real problem"):
            with store.operation_scope(SHORT_TIMEOUT) as owner:
                with store.write_transaction(owner):
                    raise ValueError("the real problem")

    def test_a_commit_failure_surfaces_as_a_backend_error(self, store, monkeypatch):
        store.initialize()
        _inject_connection_fault(monkeypatch, failing_statement="COMMIT")

        with pytest.raises(SessionBackendError, match="commit"):
            with store.operation_scope(SHORT_TIMEOUT) as owner:
                with store.write_transaction(owner):
                    pass

        assert store.open_connection_count() == 0


# ── Nesting ──────────────────────────────────────────────────────────────────

class TestNesting:
    def test_a_nested_transaction_joins_the_outer_one(self, store):
        store.initialize()
        with store.operation_scope(SHORT_TIMEOUT) as owner:
            with store.write_transaction(owner) as outer:
                assert owner.transaction_depth == 1
                with store.write_transaction(owner) as inner:
                    assert inner is outer, (
                        "A nested write must reuse the open transaction; a "
                        "second BEGIN on the same connection is an error and "
                        "a second connection would deadlock against it."
                    )
                    assert owner.transaction_depth == 2
                assert owner.transaction_depth == 1
        assert owner.transaction_depth == 0

    def test_a_nested_scope_reuses_the_outer_connection(self, store):
        store.initialize()
        with store.operation_scope(SHORT_TIMEOUT) as outer:
            connection = outer.connection
            with store.operation_scope(SHORT_TIMEOUT) as inner:
                assert inner is outer
                assert inner.connection is connection
            assert outer.connection is connection, (
                "The inner scope does not own the connection and must not "
                "close it out from under the outer one."
            )
        assert outer.connection is None

    def test_an_inner_failure_rolls_back_the_whole_outer_transaction(self, store):
        store.initialize()
        with pytest.raises(RuntimeError):
            with store.operation_scope(SHORT_TIMEOUT) as owner:
                with store.write_transaction(owner) as conn:
                    conn.execute(
                        "INSERT INTO sessions (session, namespace, last_modified) VALUES (?,?,?)",
                        ("outer", "default", time.time()),
                    )
                    with store.write_transaction(owner) as conn2:
                        conn2.execute(
                            "INSERT INTO sessions (session, namespace, last_modified) VALUES (?,?,?)",
                            ("inner", "default", time.time()),
                        )
                        raise RuntimeError("inner failed")

        with store.operation_scope(SHORT_TIMEOUT) as owner:
            with store.write_transaction(owner) as conn:
                count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        assert count == 0, (
            "The inner block is part of the outer transaction, so its failure "
            "must discard the outer writes too."
        )


# ── Contention ───────────────────────────────────────────────────────────────

class TestContention:
    def test_a_writer_that_cannot_start_reports_a_timeout(self, store, db_path):
        """A blocked writer must be distinguishable from a broken one.

        Only a timeout is worth retrying; a schema or disk failure is not.
        """
        store.initialize()
        blocker = sqlite3.connect(str(db_path), isolation_level=None)
        blocker.execute("PRAGMA busy_timeout = 5000")
        blocker.execute("BEGIN IMMEDIATE")
        try:
            start = time.monotonic()
            with pytest.raises(SessionTimeoutError):
                with store.operation_scope(SHORT_TIMEOUT) as owner:
                    with store.write_transaction(owner):
                        pass
            waited = time.monotonic() - start
            assert waited >= SHORT_TIMEOUT * 0.5, (
                f"The writer gave up after {waited:.3f}s without waiting for "
                "its budget; contending writers are supposed to queue."
            )
        finally:
            blocker.execute("ROLLBACK")
            blocker.close()

        assert store.open_connection_count() == 0

    def test_a_writer_waits_for_a_short_holder_and_then_succeeds(self, store, db_path):
        store.initialize()
        holder_started = threading.Event()
        hold_seconds = 0.15

        def hold():
            conn = sqlite3.connect(str(db_path), isolation_level=None)
            conn.execute("BEGIN IMMEDIATE")
            holder_started.set()
            time.sleep(hold_seconds)
            conn.execute("ROLLBACK")
            conn.close()

        thread = threading.Thread(target=hold)
        thread.start()
        try:
            assert holder_started.wait(timeout=5)
            with store.operation_scope(5.0) as owner:
                with store.write_transaction(owner) as conn:
                    conn.execute(
                        "INSERT INTO sessions (session, namespace, last_modified) VALUES (?,?,?)",
                        ("queued", "default", time.time()),
                    )
        finally:
            thread.join(timeout=10)

        with store.operation_scope(5.0) as owner:
            with store.write_transaction(owner) as conn:
                assert conn.execute(
                    "SELECT COUNT(*) FROM sessions WHERE session = ?", ("queued",)
                ).fetchone()[0] == 1

    def test_the_deadline_is_shared_by_every_attempt_in_one_scope(self, store, db_path):
        """Two waits inside one operation must not each get a fresh budget."""
        store.initialize()
        blocker = sqlite3.connect(str(db_path), isolation_level=None)
        blocker.execute("BEGIN IMMEDIATE")
        try:
            start = time.monotonic()
            with pytest.raises(SessionTimeoutError):
                with store.operation_scope(0.2) as owner:
                    try:
                        with store.write_transaction(owner):
                            pass
                    except SessionTimeoutError:
                        pass
                    with store.write_transaction(owner):
                        pass
            elapsed = time.monotonic() - start
            assert elapsed < 0.2 * 2, (
                f"Two attempts took {elapsed:.3f}s, so each one restarted the "
                "budget instead of sharing one deadline."
            )
        finally:
            blocker.execute("ROLLBACK")
            blocker.close()


# ── Ownership across threads and processes ───────────────────────────────────

class TestOwnerAffinity:
    def test_a_scope_belongs_to_the_thread_that_opened_it(self, store):
        store.initialize()
        failures = []

        with store.operation_scope(SHORT_TIMEOUT) as owner:
            def use_from_another_thread():
                try:
                    with store.write_transaction(owner):
                        failures.append("a foreign thread was allowed to use the connection")
                except SessionBackendError:
                    pass
                except Exception as exc:  # noqa: BLE001 - reported by the assertion
                    failures.append(f"unexpected error: {exc!r}")

            thread = threading.Thread(target=use_from_another_thread)
            thread.start()
            thread.join(timeout=10)

        assert not failures, failures

    def test_each_thread_gets_its_own_scope(self, store):
        store.initialize()
        seen = {}
        barrier = threading.Barrier(3)

        def work(index):
            with store.operation_scope(5.0) as owner:
                barrier.wait(timeout=10)
                seen[index] = id(owner.connection)
                with store.write_transaction(owner) as conn:
                    conn.execute(
                        "INSERT INTO sessions (session, namespace, last_modified) VALUES (?,?,?)",
                        (f"s{index}", "default", time.time()),
                    )

        threads = [threading.Thread(target=work, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert len(set(seen.values())) == 3, (
            "Concurrent threads shared a connection; SQLite connections are "
            "opened with check_same_thread and cannot be shared."
        )
        assert store.open_connection_count() == 0


_FORK_PROBE = textwrap.dedent(
    """
    import os, sys, time
    sys.path.insert(0, sys.argv[1])
    from autorun.session_manager import SQLiteStore

    store = SQLiteStore(sys.argv[2])
    store.initialize()

    with store.operation_scope(5.0) as owner:
        parent_conn = owner.connection
        pid = os.fork()
        if pid == 0:
            # The child inherits the owner object but not a usable connection.
            try:
                with store.operation_scope(5.0) as child_owner:
                    assert child_owner.connection is not parent_conn, "child reused parent connection"
                    with store.write_transaction(child_owner) as conn:
                        conn.execute(
                            "INSERT INTO sessions (session, namespace, last_modified) VALUES (?,?,?)",
                            ("from-child", "default", time.time()),
                        )
            except BaseException as exc:
                print("CHILD-FAIL", repr(exc))
                os._exit(1)
            os._exit(0)
        _, status = os.waitpid(pid, 0)

    print("CHILD-STATUS", os.waitstatus_to_exitcode(status))
    """
)


@pytest.mark.subprocess
@pytest.mark.serial
@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork is unavailable")
class TestForkSafety:
    def test_a_forked_child_opens_its_own_connection(self, db_path, tmp_path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            [sys.executable, "-c", _FORK_PROBE, str(SRC_DIR), str(db_path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert completed.returncode == 0, completed.stderr
        assert "CHILD-STATUS 0" in completed.stdout, completed.stdout + completed.stderr


# ── Refusing databases we do not understand ──────────────────────────────────

class TestSchemaGuards:
    def test_a_database_belonging_to_another_application_is_refused(self, db_path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA application_id = 305419896")
        conn.execute("CREATE TABLE unrelated (x)")
        conn.commit()
        conn.close()

        store = SQLiteStore(db_path)
        with pytest.raises(SessionBackendError, match="application"):
            store.initialize()

    def test_a_newer_schema_is_refused_rather_than_downgraded(self, db_path):
        store = SQLiteStore(db_path)
        store.initialize()

        conn = sqlite3.connect(str(db_path))
        conn.execute(f"PRAGMA user_version = {SCHEMA_USER_VERSION + 5}")
        conn.commit()
        conn.close()

        newer = SQLiteStore(db_path)
        with pytest.raises(SessionBackendError, match="version"):
            newer.initialize()

    def test_a_newer_schema_is_left_byte_identical(self, db_path):
        store = SQLiteStore(db_path)
        store.initialize()
        conn = sqlite3.connect(str(db_path))
        conn.execute(f"PRAGMA user_version = {SCHEMA_USER_VERSION + 5}")
        conn.commit()
        conn.close()

        before = db_path.read_bytes()
        with pytest.raises(SessionBackendError):
            SQLiteStore(db_path).initialize()
        assert db_path.read_bytes() == before, (
            "A refused database must not be modified on the way out."
        )

    def test_an_unwritable_directory_reports_a_backend_error(self, tmp_path):
        directory = tmp_path / "readonly"
        directory.mkdir()
        os.chmod(directory, 0o500)
        try:
            store = SQLiteStore(directory / "daemon_state.sqlite3")
            with pytest.raises(SessionBackendError):
                store.initialize()
        finally:
            os.chmod(directory, 0o700)


_BOOTSTRAP_PROBE = textwrap.dedent(
    """
    import sys, time
    sys.path.insert(0, sys.argv[1])
    from autorun.session_manager import SQLiteStore

    ready_at = float(sys.argv[3])
    while time.time() < ready_at:
        time.sleep(0.005)

    store = SQLiteStore(sys.argv[2])
    store.initialize()
    with store.operation_scope(30.0) as owner:
        with store.write_transaction(owner) as conn:
            conn.execute(
                "INSERT INTO sessions (session, namespace, last_modified) VALUES (?,?,?)",
                (sys.argv[4], "default", time.time()),
            )
    print("OK")
    """
)


@pytest.mark.subprocess
@pytest.mark.serial
class TestConcurrentBootstrap:
    def test_header_validation_holds_the_sqlite_writer_slot(
        self, db_path, monkeypatch
    ):
        """Validation cannot observe another initializer's partial header."""
        validations = []
        original = SQLiteStore._validate_existing_database

        def assert_writer_transaction(store, conn):
            validations.append(conn.in_transaction)
            return original(store, conn)

        monkeypatch.setattr(
            SQLiteStore, "_validate_existing_database", assert_writer_transaction
        )

        SQLiteStore(db_path).initialize()

        assert validations == [True, True], (
            "Fresh initialization must validate both before and after the "
            "auto_vacuum boundary while holding BEGIN IMMEDIATE."
        )

    def test_processes_racing_to_create_the_database_all_succeed(self, db_path):
        """Several sessions can start at once on a machine with no database yet."""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        ready_at = time.time() + 1.0
        children = [
            subprocess.Popen(
                [sys.executable, "-c", _BOOTSTRAP_PROBE, str(SRC_DIR), str(db_path),
                 str(ready_at), f"racer-{i}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for i in range(4)
        ]
        for child in children:
            out, err = child.communicate(timeout=120)
            assert child.returncode == 0, f"bootstrap race failed: {err}"
            assert "OK" in out

        conn = _open_reader(db_path)
        try:
            names = {row[0] for row in conn.execute("SELECT session FROM sessions")}
            assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_USER_VERSION
        finally:
            conn.close()
        assert names == {f"racer-{i}" for i in range(4)}, (
            "A racing bootstrap lost a write, so initialization is not "
            f"idempotent under concurrency. Present: {sorted(names)}"
        )
