"""filelock+JSON-backed session state — replaces shelve+fcntl implementation.

Design:
- Single JSON file (~/.claude/sessions/daemon_state.json) for all sessions
- filelock for cross-process mutual exclusion
- threading.RLock for same-process thread serialization
- Atomic tempfile+rename writes for crash safety
- Re-read from disk on every lock acquisition (picks up other-process writes)
"""
import contextlib
import copy
import json
import logging
import os
import re
import sqlite3
import sys
import threading
import time
from pathlib import Path
from filelock import FileLock, Timeout as FileLockTimeout

from .config import CONFIG as _CONFIG

logger = logging.getLogger(__name__)

DEFAULT_SESSION_TIMEOUT = 30.0
SHARED_ACCESS_TIMEOUT = 5.0


class SessionStateError(Exception):
    pass


class SessionTimeoutError(SessionStateError):
    pass


class SessionBackendError(SessionStateError):
    pass


class SessionPersistenceError(SessionStateError):
    """A value was accepted in memory but never reached storage.

    Raised instead of logging and continuing, because a cache that keeps a
    value storage rejected reports success to every later reader in this
    process while another process — and this one after a restart — sees
    something else, with nothing to indicate which is right.
    """


class _StateProxy:
    """dict-like view of one session_id's keys within the shared JSON store."""

    def __init__(self, data: dict, prefix: str, store: "_JSONStore"):
        self._data = data
        self._prefix = prefix
        self._store = store

    def _k(self, key: str) -> str:
        return f"{self._prefix}/{key}"

    def get(self, key: str, default=None):
        return self._data.get(self._k(key), default)

    def __getitem__(self, key: str):
        full_key = self._k(key)
        if full_key not in self._data:
            raise KeyError(key)
        return self._data[full_key]

    def __setitem__(self, key: str, value):
        self._data[self._k(key)] = value
        self._store._dirty = True

    def __contains__(self, key: str) -> bool:
        return self._k(key) in self._data

    def __delitem__(self, key: str):
        del self._data[self._k(key)]
        self._store._dirty = True

    def _logical_keys(self):
        """Yield logical key names for this session (strips prefix)."""
        pfx = f"{self._prefix}/"
        for k in self._data:
            if k.startswith(pfx):
                yield k[len(pfx):]

    def __iter__(self):
        return self._logical_keys()

    def __len__(self):
        pfx = f"{self._prefix}/"
        return sum(1 for k in self._data if k.startswith(pfx))

    def keys(self):
        return list(self._logical_keys())

    def values(self):
        return [self._data[self._k(k)] for k in self._logical_keys()]

    def items(self):
        return [(k, self._data[self._k(k)]) for k in self._logical_keys()]

    def clear(self):
        """Remove all keys for this session."""
        pfx = f"{self._prefix}/"
        keys = [k for k in self._data if k.startswith(pfx)]
        for k in keys:
            del self._data[k]
        if keys:
            self._store._dirty = True

    def update(self, other=None, **kwargs):
        """Update from dict or keyword arguments."""
        if other is not None:
            items = other.items() if hasattr(other, "items") else other
            for k, v in items:
                self[k] = v
        for k, v in kwargs.items():
            self[k] = v

    def sync(self):
        pass  # no-op for API compat — writes are deferred to context exit

    def close(self):
        pass  # no-op for API compat — store is shared


class _JSONStore:
    """Thread-safe + process-safe JSON file store.

    Within one process: threading.RLock serializes concurrent threads.
    Across processes: filelock serializes concurrent writers.
    Reads re-load from disk inside the lock so they see latest state.
    Writes use atomic tempfile+rename for crash safety.

    Supports reentrant locking: the same thread can call session() while already
    inside a session() context. Inner calls share the same _data dict and save
    is deferred to the outermost context exit.
    """

    @property
    def state_dir(self) -> Path:
        """Directory holding this store's files."""
        return Path(os.path.dirname(self._state_file))

    def __init__(self, state_file: str, lock_file: str):
        self._state_file = state_file
        self._lock_file = lock_file
        self._rlock = threading.RLock()
        self._dirty = False
        self._data: dict = {}
        # Thread-local reentrancy tracking: _held_by.active = True while locked
        self._held_by = threading.local()

    def _load(self) -> dict:
        try:
            with open(self._state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save(self):
        os.makedirs(os.path.dirname(self._state_file), exist_ok=True)
        tmp = self._state_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self._state_file)

    @contextlib.contextmanager
    def _persistent_filelock(self, timeout: float):
        """Acquire FileLock and ensure the lock file persists after release.

        filelock >= 3.20 deletes the lock file on release. This context manager
        re-creates it so stale-lock detection and cross-process observability
        work correctly. Encapsulates the acquire-use-release-persist lifecycle
        as a single RAII unit.
        """
        file_lock = FileLock(self._lock_file, timeout=timeout)
        try:
            with file_lock:
                yield
        finally:
            try:
                Path(self._lock_file).touch(exist_ok=True)
            except OSError:
                pass  # Non-critical — locking still works without the file

    @contextlib.contextmanager
    def session(self, session_id: str, timeout: float = DEFAULT_SESSION_TIMEOUT):
        if not isinstance(session_id, str) or not session_id.strip():
            raise SessionStateError("session_id must be a non-empty string")

        # Reentrant support: same thread already holds the lock — share _data, defer save
        if getattr(self._held_by, 'active', False):
            proxy = _StateProxy(self._data, session_id, self)
            yield proxy
            return

        try:
            with self._persistent_filelock(timeout):
                with self._rlock:
                    # Re-read inside lock: pick up any changes from other processes
                    self._data = self._load()
                    self._dirty = False
                    self._held_by.active = True
                    try:
                        proxy = _StateProxy(self._data, session_id, self)
                        yield proxy
                        if self._dirty:
                            self._save()
                    finally:
                        self._held_by.active = False
        except FileLockTimeout as e:
            raise SessionTimeoutError(
                f"Could not acquire state lock for '{session_id}' after {timeout}s"
            ) from e


    @contextlib.contextmanager
    def all_state(self, timeout: float = DEFAULT_SESSION_TIMEOUT,
                  write: bool = False):
        """Yield every session's fields as one flat mapping, under one lock."""
        with self.session("__all_state__", timeout=timeout):
            yield self._data
            if write:
                self._dirty = True


class SessionLock:
    """No-op shim — filelock inside _JSONStore.session() handles concurrency."""

    def __init__(self, session_id: str, timeout: float = DEFAULT_SESSION_TIMEOUT,
                 state_dir=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


_store_lock = threading.Lock()
_stores: dict[str, "_JSONStore"] = {}
_store: "_JSONStore | None" = None  # compatibility alias for legacy test cleanup


def _state_dir_key(state_dir: "str | None" = None) -> str:
    """Return the canonical state directory for cache isolation."""
    d = state_dir or os.environ.get("AUTORUN_TEST_STATE_DIR") or os.path.expanduser("~/.claude/sessions")
    return str(Path(d).expanduser().resolve())


def _build_store(key: str):
    """Create the configured store for one state directory.

    THIS IS THE BACKEND SWITCH. Everything that reads or writes session state
    arrives here, so the four outcomes below are the whole set:

      state_backend   prior conversion   outcome
      -------------   ----------------   -------------------------------------
      "json"          none               JSON store, as it always was
      "json"          COMPLETE           refused: the original file was renamed
                                         by the conversion, so serving would
                                         mean serving nothing. Points at
                                         --state-rollback.
      "sqlite"        none / partial     convert (resumable), then SQLite store
      "sqlite"        COMPLETE           SQLite store; conversion is a no-op
      anything else                      refused rather than guessed

    Successors: `SessionStateManager` and the module-level `session_state()`
    call whichever store this returns; both stores implement the same
    `session()`, `state_dir`, and `all_state()` boundary.

    Predecessor: `_get_store()`, which caches one store per state directory,
    so this runs once per directory per process.

    Selecting the row store also converts whatever is already in the JSON
    file. Conversion belongs to the switch rather than to a command an
    operator has to remember: a backend that quietly began from empty state
    would look like it worked while every policy, task, and claim in the
    ignored file went missing.
    """
    backend = str(_CONFIG.get("state_backend", "json")).strip().lower()
    db_path = os.path.join(key, "daemon_state.sqlite3")
    json_path = os.path.join(key, "daemon_state.json")
    receipt_path = os.path.join(key, "daemon_state.migration.json")
    migrator = StateMigrator(json_path, db_path, receipt_path)

    if backend == "json":
        # Selecting JSON after a conversion is the dangerous direction. The
        # original file was renamed by the conversion, so a JSON store would
        # open a path that is not there and serve empty state — every policy,
        # task, and claim silently gone, with nothing reporting a problem.
        # Refuse, and say how to come back properly.
        status = migrator.status()
        if status["phase"] == "COMPLETE" and not status["source_present"]:
            raise SessionBackendError(
                "state_backend is 'json', but state was converted to SQLite "
                f"and {json_path} no longer exists. Starting now would serve "
                "empty state and lose everything recorded since the "
                "conversion.\n"
                "  To return to JSON, export current state first:\n"
                "    autorun --state-rollback\n"
                "  To keep using the converted store, set state_backend back "
                "to 'sqlite'.\n"
                f"  The pre-conversion file is kept at {status['backup']}"
            )
        return _JSONStore(
            json_path,
            os.path.join(key, "daemon_state.json.lock"),
        )

    if backend != "sqlite":
        raise SessionBackendError(
            f"Unknown state backend {backend!r}. Set state_backend to 'json' "
            "or 'sqlite'. Refusing to guess, because guessing wrong means "
            "reading from a store that does not hold this session's state."
        )

    # Idempotent, and resumable if a previous attempt was interrupted. Once
    # complete it reports so and leaves both artifacts alone, so a restart
    # cannot roll state back to the import.
    #
    # A failure here stops the store from opening at all, deliberately. The
    # alternative — quietly using JSON instead — would leave two writable
    # authorities with no record of which one a given write went to. Failing
    # is recoverable; that is not.
    try:
        migrator.migrate()
    except SessionStateError as exc:
        raise SessionBackendError(
            f"state_backend is 'sqlite' but converting {json_path} failed: "
            f"{exc}\n"
            "  Existing state is unchanged and still authoritative.\n"
            "  Inspect with: autorun --state-status\n"
            "  To keep running meanwhile, set state_backend back to 'json'."
        ) from exc

    store = SQLiteStore(db_path)
    store.initialize()
    return store


def _get_store(state_dir: "str | None" = None):
    global _store
    key = _state_dir_key(state_dir)
    if _store is None and _stores:
        # Some existing tests reset the legacy alias directly. Treat that as a
        # request to clear the keyed cache too so isolated temp dirs stay clean.
        _stores.clear()
    store = _stores.get(key)
    if store is None:
        with _store_lock:
            store = _stores.get(key)
            if store is None:
                store = _build_store(key)
                _stores[key] = store
                if _store is None:
                    _store = store
    return store


class SessionStateManager:
    def __init__(self, state_dir: "str | None" = None):
        self._store = _get_store(state_dir)

    @property
    def state_dir(self) -> "Path":
        """Directory holding this backend's state files."""
        return self._store.state_dir

    @contextlib.contextmanager
    def session_state(self, session_id: str,
                      timeout: float = DEFAULT_SESSION_TIMEOUT, **_):
        with self._store.session(session_id, timeout) as s:
            yield s

    @contextlib.contextmanager
    def shared_session_state(self, session_id: str,
                              timeout: float = SHARED_ACCESS_TIMEOUT, **_):
        with self._store.session(session_id, timeout) as s:
            yield s

    def clear_test_session(self, session_id: str):
        with self._store.session(session_id) as s:
            s.clear()

    def clear_test_sessions_batch(self, session_ids):
        """Clear multiple test sessions in one save operation (O(1) disk writes).

        Uses a single session() context for cross-process safety. The first
        session_id is used as the lock holder; all prefixes are cleared within
        the same lock acquisition.
        """
        if not session_ids:
            return
        # Use any session_id to enter the lock; clear all prefixes inside
        with self._store.session(session_ids[0]) as s:
            s.clear()  # clear the first
            for sid in session_ids[1:]:
                # Reentrant: same thread already holds the lock
                with self._store.session(sid) as s2:
                    s2.clear()

    @contextlib.contextmanager
    def all_state(self, timeout: float = DEFAULT_SESSION_TIMEOUT,
                  write: bool = False):
        """Yield the full shared state dict under one process/file lock.

        Use sparingly for maintenance operations such as archive/GC that need
        to inspect or remove many session prefixes. Normal hook paths should
        use session_state() so they stay scoped to one session.
        """
        with self._store.all_state(timeout=timeout, write=write) as data:
            yield data


_managers: dict[str, SessionStateManager] = {}
_manager: "SessionStateManager | None" = None  # compatibility alias for legacy test cleanup
_manager_lock = threading.Lock()


def get_session_manager(state_dir: "str | None" = None) -> SessionStateManager:
    global _manager
    key = _state_dir_key(state_dir)
    if _manager is None and _managers:
        _managers.clear()
    manager = _managers.get(key)
    if manager is None:
        with _manager_lock:
            manager = _managers.get(key)
            if manager is None:
                manager = SessionStateManager(state_dir)
                _managers[key] = manager
                if _manager is None:
                    _manager = manager
    return manager


@contextlib.contextmanager
def session_state(session_id: str, timeout: float = DEFAULT_SESSION_TIMEOUT,
                  state_dir: "str | None" = None, **_):
    with get_session_manager(state_dir).session_state(session_id, timeout) as s:
        yield s


@contextlib.contextmanager
def shared_session_state(session_id: str, timeout: float = SHARED_ACCESS_TIMEOUT,
                          state_dir: "str | None" = None, **_):
    with get_session_manager(state_dir).shared_session_state(session_id, timeout) as s:
        yield s


def clear_test_session_state(session_id: str, state_dir: "str | None" = None):
    get_session_manager(state_dir).clear_test_session(session_id)


def clear_test_session_states_batch(session_ids, state_dir: "str | None" = None):
    """Clear multiple test sessions in one save operation. Use instead of looping
    over clear_test_session_state to avoid O(n) disk writes."""
    get_session_manager(state_dir).clear_test_sessions_batch(session_ids)


@contextlib.contextmanager
def all_session_state(timeout: float = DEFAULT_SESSION_TIMEOUT,
                      state_dir: "str | None" = None,
                      write: bool = False):
    with get_session_manager(state_dir).all_state(timeout=timeout, write=write) as state:
        yield state


# =============================================================================
# SQLite state store
# =============================================================================
#
# HOW TO MOVE A DEPLOYMENT ONTO THIS STORE
#
#   1. Look first:   autorun --state-status
#      Reports the configured backend and whether a conversion has run. It
#      changes nothing, so it is safe at any point.
#   2. Flip it:      set "state_backend": "sqlite" in the autorun config
#      The next process converts whatever daemon_state.json holds and serves
#      from the database. The original is renamed, never deleted, to
#      daemon_state.json.migrated.<yyyy-mm-dd-hhmm>.
#   3. Restart the daemon so it stops holding the old store.
#   4. Watch for: "Could not acquire state lock", "Task tracking error", and
#      "skipped persistent state" in the daemon log. They should stop.
#
#   To go back:      autorun --state-rollback, then set "state_backend" back
#      to "json". Rollback exports what the database holds now, not the file
#      that was retired, so anything written since the conversion comes back
#      with it. Setting the backend to "json" without rolling back first is
#      refused rather than silently serving empty state.
#
# WHEN THIS CODE, AND THE CODE IT REPLACES, CAN BE RETIRED
#
#   Retire the JSON store (_JSONStore, and the "json" branch of
#   _build_store) only after all of:
#     - no supported deployment has "state_backend": "json";
#     - no daemon_state.json remains that has not been converted, since
#       _JSONStore is also how a rollback is read back; and
#     - StateMigrator.rollback has no remaining callers, because retiring the
#       old store removes the destination it writes for.
#   Until then the JSON store is not dead code — it is the way back.
#
#   Retire StateMigrator itself only when no deployment can still be carrying
#   pre-conversion state, which is later than the above: a machine that has
#   been offline for a long time still arrives with a JSON file.
#
#   The compatibility surface here — session(), the dict-like proxy,
#   all_state() — exists because ~35 production callers use it. It retires
#   per caller, as each moves to read_field/read_fields/mutate_fields, not in
#   one step.
#
# The JSON store above reads and rewrites every session on every mutation, so
# the cost of changing one field grows with the total amount of state ever
# recorded. This store writes one row instead, and relies on SQLite for the
# cross-process locking, transaction, and recovery machinery that would
# otherwise have to be written by hand.
#
# Two rules shape the code below and are worth stating once:
#
#   * One outer operation owns at most one connection, and that connection is
#     closed on every exit path. Nested operations and nested transactions
#     join the outer one rather than opening a second connection, because a
#     second connection would queue behind the first one's write lock and
#     deadlock against it.
#   * A contended writer and a broken writer must not look alike. Only
#     SQLITE_BUSY or SQLITE_LOCKED while starting a transaction becomes
#     SessionTimeoutError, which callers may retry. Everything else keeps its
#     own context as SessionBackendError.

# "AURN" as a big-endian integer. SQLite stores this in the file header, so a
# database created by something else is recognizable before any statement runs.
SCHEMA_APPLICATION_ID = 0x4155524E
SCHEMA_USER_VERSION = 1

# Maintenance settings, from CONFIG so they can be tuned without editing code.
# Both bound the write-ahead log rather than throughput, and neither discards
# stored data. Provisional: chosen to keep the log reclaimed, not by comparison.
WAL_AUTOCHECKPOINT_PAGES = int(_CONFIG.get("state_wal_autocheckpoint_pages", 1000))
JOURNAL_SIZE_LIMIT_BYTES = int(
    _CONFIG.get("state_journal_size_limit_bytes", 8 * 1024 * 1024)
)

# Parameters per SELECT when reading a named set of fields, kept under
# SQLite's variable limit. Larger requests are split, never refused.
QUERY_PARAMETER_CHUNK = int(_CONFIG.get("state_query_parameter_chunk", 500))

# Statuses that mean a task is over. Defined once, as SQL text, because a
# partial index predicate has to be a constant and the query that uses that
# index has to spell the predicate identically.
# Not configurable, deliberately: these are written into the tasks_incomplete
# partial index as literals when the schema is created. Making them settable
# would let a later config change stop the index from matching the query, with
# no error and no wrong answer — just a silent return to scanning every
# finished task, which is the thing the index exists to prevent.
TERMINAL_TASK_STATUSES = ("completed", "deleted", "ignored")
_TERMINAL_TASK_STATUS_SQL = (
    "(" + ", ".join(f"'{status}'" for status in TERMINAL_TASK_STATUSES) + ")"
)

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sessions (
        session       TEXT PRIMARY KEY,
        namespace     TEXT NOT NULL,
        last_modified REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS state (
        session    TEXT NOT NULL REFERENCES sessions(session) ON DELETE CASCADE,
        field      TEXT NOT NULL,
        value_json TEXT NOT NULL,
        updated_at REAL NOT NULL,
        PRIMARY KEY (session, field)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS sessions_retention
        ON sessions(namespace, last_modified)
    """,
    """
    CREATE TABLE IF NOT EXISTS tasks (
        session      TEXT NOT NULL REFERENCES sessions(session) ON DELETE CASCADE,
        task_id      TEXT NOT NULL,
        status       TEXT NOT NULL,
        updated_at   REAL NOT NULL,
        payload_json TEXT NOT NULL,
        PRIMARY KEY (session, task_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS tasks_retention
        ON tasks(session, status, updated_at)
    """,
    # "Is anything unfinished?" is asked on every Stop, and the answer is
    # almost always a handful of rows among thousands of finished ones. A
    # plain index cannot help a NOT IN predicate — SQLite still walks every
    # entry for the session — so the unfinished rows get an index of their
    # own. The statuses are written out because a partial index predicate
    # must be constant; TERMINAL_TASK_STATUS_SQL below is the one place they
    # are defined, and the query has to use the identical text to match.
    f"""
    CREATE INDEX IF NOT EXISTS tasks_incomplete
        ON tasks(session, task_id)
        WHERE status NOT IN {_TERMINAL_TASK_STATUS_SQL}
    """,
    """
    CREATE TABLE IF NOT EXISTS task_events (
        session         TEXT NOT NULL REFERENCES sessions(session) ON DELETE CASCADE,
        task_id         TEXT,
        event_id        TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        event_type      TEXT NOT NULL,
        created_at      REAL NOT NULL,
        payload_json    TEXT NOT NULL,
        projected_at    REAL,
        PRIMARY KEY (session, event_id),
        UNIQUE (session, idempotency_key),
        FOREIGN KEY (session, task_id)
            REFERENCES tasks(session, task_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS task_events_order
        ON task_events(session, task_id, created_at, event_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS task_events_pending_projection
        ON task_events(projected_at, session, created_at, event_id)
        WHERE projected_at IS NULL
    """,
    """
    CREATE TABLE IF NOT EXISTS publication_receipts (
        receipt_id   TEXT PRIMARY KEY,
        kind         TEXT NOT NULL,
        identity     TEXT NOT NULL,
        generation   INTEGER NOT NULL,
        phase        TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        updated_at   REAL NOT NULL,
        UNIQUE (kind, identity, generation)
    )
    """,
)

# SQLite reports contention through these two result codes. Matching on the
# code rather than the message keeps the classification stable across versions
# and locales.
_BUSY_RESULT_CODES = frozenset({5, 6})  # SQLITE_BUSY, SQLITE_LOCKED


def _is_contention_error(exc: sqlite3.Error) -> bool:
    """True when the statement failed because someone else held the lock."""
    code = getattr(exc, "sqlite_errorcode", None)
    if code is not None:
        return (code & 0xFF) in _BUSY_RESULT_CODES
    message = str(exc).lower()
    return "locked" in message or "busy" in message


def _rollback_without_masking_original(conn) -> None:
    """Undo an open transaction, reporting a rollback failure without raising.

    Called while an exception is already propagating. Raising here would
    replace the caller's actual failure with a secondary one and lose the
    reason the transaction was being abandoned in the first place.
    """
    if conn is None:
        return
    try:
        conn.execute("ROLLBACK")
    except sqlite3.Error as exc:
        logger.error("Could not roll back state transaction: %s", exc)


def _close_connection(conn, active_error) -> None:
    """Close a connection; raise only if nothing else already failed.

    A close failure means the connection may still hold a lock, so it cannot
    simply be ignored. But if the caller is already unwinding, their exception
    is the one worth propagating and this one is logged instead.
    """
    if conn is None:
        return
    try:
        conn.close()
    except Exception as exc:  # noqa: BLE001 - re-raised or logged below
        if active_error is not None:
            logger.error("Could not close state connection: %s", exc)
        else:
            raise SessionBackendError(
                f"Could not close state connection: {exc}"
            ) from exc


class _Missing:
    """Absence, as a value.

    ``None`` cannot serve as "no such field" because ``None`` is itself a
    storable value, and callers rely on telling "never set" from "set to
    nothing".
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self):
        return "MISSING"

    def __bool__(self):
        return False


MISSING = _Missing()


def _encode_state_value(field: str, value):
    """Encode one field's value, naming the field if it cannot be encoded.

    Keys are sorted so that the encoding of a value depends only on the value.
    Change detection compares these strings, and an unstable encoding would
    report spurious changes on every context.
    """
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise SessionBackendError(
            f"Field {field!r} holds a value that cannot be stored as JSON: "
            f"{exc}. Convert it to a list, dict, string, number, boolean, or "
            "None before storing it."
        ) from exc
    except RecursionError as exc:
        # Nesting deep enough to exhaust the interpreter stack. Reported as a
        # state error naming the field, because a bare RecursionError arrives
        # with a traceback through json and nothing that identifies the value.
        raise SessionBackendError(
            f"Field {field!r} holds a value nested too deeply to store: {exc}. "
            "Flatten it or store a reference instead."
        ) from exc


def _decode_state_value(session_id: str, field: str, raw: str):
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise SessionBackendError(
            f"Stored value for session {session_id!r} field {field!r} is not "
            f"valid JSON: {exc}"
        ) from exc


# Session-ID prefixes that retention policy distinguishes. An ID matching none
# of these is an ordinary session; retention must never invent a namespace for
# an ID it does not recognize, because the namespace decides what may be
# deleted.
_NAMESPACE_PREFIXES = (
    ("__global__", "global"),
    ("__plan_export__", "plan_export"),
    ("__task_lifecycle__", "task_lifecycle"),
    ("monitor_", "monitor"),
)


def _namespace_for(session_id: str) -> str:
    for prefix, namespace in _NAMESPACE_PREFIXES:
        if session_id == prefix or session_id.startswith(prefix):
            return namespace
    return "session"


def _validate_session_id(session_id) -> None:
    if not isinstance(session_id, str) or not session_id.strip():
        raise SessionStateError("session_id must be a non-empty string")


class SessionBuffer:
    """One session's fields while a context is open, plus what they started as.

    The starting values are kept in their encoded form rather than as a copy
    of the decoded objects. A shallow copy would share every nested object
    with the buffer, so a caller that edits a nested value in place would be
    compared against itself, report no change, and lose the write. Re-encoding
    at the end and comparing strings catches that.
    """

    __slots__ = ("session_id", "_original", "values", "proxy")

    def __init__(self, session_id: str, rows):
        self.session_id = session_id
        self._original = {field: raw for field, raw in rows}
        self.values = {
            field: _decode_state_value(session_id, field, raw)
            for field, raw in self._original.items()
        }
        self.proxy = _SQLiteStateProxy(self)

    def diff_encoded(self):
        """Fields whose stored bytes would change, and fields that are gone."""
        changed = {}
        for field, value in self.values.items():
            encoded = _encode_state_value(field, value)
            if self._original.get(field) != encoded:
                changed[field] = encoded
        removed = [f for f in self._original if f not in self.values]
        return changed, removed

    def mark_persisted(self, changed, removed) -> None:
        """Adopt what was just written as the new starting point.

        Without this, a nested context that reopens the same buffer would
        report the same fields as changed again and rewrite them.
        """
        self._original.update(changed)
        for field in removed:
            self._original.pop(field, None)


class _SQLiteStateProxy:
    """The dict-like view a session context yields.

    Deliberately the same surface as the JSON store's proxy, including the
    no-op ``sync`` and ``close`` that predate both backends, so that callers
    do not need to know which store is underneath.
    """

    __slots__ = ("_buffer",)

    def __init__(self, buffer: SessionBuffer):
        self._buffer = buffer

    def get(self, key, default=None):
        return self._buffer.values.get(key, default)

    def __getitem__(self, key):
        return self._buffer.values[key]

    def __setitem__(self, key, value):
        self._buffer.values[key] = value

    def __delitem__(self, key):
        del self._buffer.values[key]

    def __contains__(self, key):
        return key in self._buffer.values

    def __iter__(self):
        return iter(list(self._buffer.values))

    def __len__(self):
        return len(self._buffer.values)

    def keys(self):
        return list(self._buffer.values)

    def values(self):
        return list(self._buffer.values.values())

    def items(self):
        return list(self._buffer.values.items())

    def clear(self):
        self._buffer.values.clear()

    def update(self, other=None, **kwargs):
        if other is not None:
            items = other.items() if hasattr(other, "items") else other
            for key, value in items:
                self[key] = value
        for key, value in kwargs.items():
            self[key] = value

    def sync(self):
        pass  # Writes are staged at context exit; kept for API compatibility.

    def close(self):
        pass  # The store outlives any one context; kept for API compatibility.


class StateUnitOfWork:
    """The connection, deadline, and transaction depth of one operation.

    Exactly one of these is live per thread per store. Nested scopes and
    nested transactions increment a depth counter and reuse what is already
    open; only the outermost scope opens and closes anything.
    """

    __slots__ = (
        "connection",
        "deadline",
        "depth",
        "transaction_depth",
        "owner_thread",
        "owner_pid",
        "buffers",
    )

    def __init__(self):
        self.connection = None
        self.deadline = None
        self.depth = 0
        self.transaction_depth = 0
        self.owner_thread = None
        self.owner_pid = None
        # Session buffers open right now, keyed by session ID. A nested
        # context for a session already open joins its buffer instead of
        # loading a second, divergent copy.
        self.buffers = {}

    def clear(self) -> None:
        self.connection = None
        self.deadline = None
        self.depth = 0
        self.transaction_depth = 0
        self.owner_thread = None
        self.owner_pid = None
        self.buffers = {}

    def remaining_seconds(self) -> float:
        """Time left in this operation's budget, never negative.

        Measured against a monotonic clock so a system clock adjustment
        cannot extend or collapse a timeout.
        """
        if self.deadline is None:
            return 0.0
        return max(0.0, self.deadline - time.monotonic())

    def belongs_to_caller(self) -> bool:
        return (
            self.owner_thread == threading.get_ident()
            and self.owner_pid == os.getpid()
        )


class SQLiteStore:
    """Row-oriented state storage with one owned connection per operation."""

    def __init__(self, db_path):
        self._db_path = str(db_path)
        self._owners = threading.local()
        self._open_connections = 0
        self._counter_lock = threading.Lock()
        self._initialized = False

    # --- introspection -----------------------------------------------------

    @property
    def db_path(self) -> str:
        return self._db_path

    @property
    def state_dir(self) -> Path:
        """Directory holding this store's files."""
        return Path(os.path.dirname(self._db_path))

    @contextlib.contextmanager
    def all_state(self, timeout: float = DEFAULT_SESSION_TIMEOUT,
                  write: bool = False):
        """Every session's fields as one flat mapping, for maintenance.

        Presents the same ``session/field`` shape the JSON store exposed, so
        administrative callers work against either backend. Deliberately the
        expensive path: it reads and decodes everything.

        With ``write``, whatever the caller removed from the mapping is
        deleted and whatever it changed is written back, in one transaction.
        """
        with self.operation_scope(timeout) as owner:
            with self.write_transaction(owner) as conn:
                rows = conn.execute(
                    "SELECT session, field, value_json FROM state"
                ).fetchall()
                original = {
                    f"{session}/{field}": raw for session, field, raw in rows
                }
                view = {
                    key: _decode_state_value(key.split("/", 1)[0],
                                             key.split("/", 1)[1], raw)
                    for key, raw in original.items()
                }
                yield view
                if not write:
                    return

                removed = [key for key in original if key not in view]
                changed = {
                    key: encoded
                    for key, value in view.items()
                    for encoded in (_encode_state_value(key, value),)
                    if original.get(key) != encoded
                }
                for key in removed:
                    session, field = key.split("/", 1)
                    conn.execute(
                        "DELETE FROM state WHERE session = ? AND field = ?",
                        (session, field),
                    )
                for key, encoded in changed.items():
                    session, field = key.split("/", 1)
                    self._stage(conn, session, {field: encoded}, [])

    def open_connection_count(self) -> int:
        """Connections this store currently owns. Zero between operations."""
        with self._counter_lock:
            return self._open_connections

    # --- schema ------------------------------------------------------------

    def initialize(self) -> None:
        """Create or validate the database. Safe to call repeatedly and concurrently.

        A database that belongs to another application, or that was written by
        a newer schema than this code understands, is refused without being
        modified. Guessing at either would risk corrupting data this version
        cannot interpret.
        """
        self._validate_existing_database()

        try:
            os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        except OSError as exc:
            raise SessionBackendError(
                f"Could not create the state directory for {self._db_path}: {exc}"
            ) from exc

        conn = self._connect(timeout=DEFAULT_SESSION_TIMEOUT)
        try:
            # Both of these are recorded in the file header rather than in a
            # transaction, and neither may run inside one. auto_vacuum only
            # takes effect while the database is still empty, so it has to
            # precede the first table.
            conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
            conn.execute("PRAGMA journal_mode = WAL")

            conn.execute("BEGIN IMMEDIATE")
            try:
                # Another process may have finished the work while this one
                # waited for the write lock.
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                if version == 0:
                    for statement in _SCHEMA_STATEMENTS:
                        conn.execute(statement)
                    conn.execute(f"PRAGMA application_id = {SCHEMA_APPLICATION_ID}")
                    conn.execute(f"PRAGMA user_version = {SCHEMA_USER_VERSION}")
                conn.execute("COMMIT")
            except BaseException:
                _rollback_without_masking_original(conn)
                raise
        except sqlite3.Error as exc:
            _close_connection(conn, exc)
            raise SessionBackendError(
                f"Could not initialize state database {self._db_path}: {exc}"
            ) from exc
        else:
            _close_connection(conn, None)

        self._initialized = True

    def _validate_existing_database(self) -> None:
        """Refuse a database this code should not write to, without touching it.

        The probe is read-only on purpose: opening an unrecognized database
        read-write can rewrite its header or checkpoint its write-ahead log,
        which would modify a file that turns out not to be ours.
        """
        if not os.path.exists(self._db_path):
            return
        try:
            conn = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise SessionBackendError(
                f"Could not open state database {self._db_path}: {exc}"
            ) from exc
        try:
            application_id = conn.execute("PRAGMA application_id").fetchone()[0]
            user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        except sqlite3.Error as exc:
            raise SessionBackendError(
                f"Could not read the header of {self._db_path}: {exc}"
            ) from exc
        finally:
            _close_connection(conn, None)

        if application_id not in (0, SCHEMA_APPLICATION_ID):
            raise SessionBackendError(
                f"{self._db_path} carries application id {application_id}, not "
                f"{SCHEMA_APPLICATION_ID}. It belongs to another program; move "
                "it aside rather than letting autorun write to it."
            )
        if user_version > SCHEMA_USER_VERSION:
            raise SessionBackendError(
                f"{self._db_path} uses schema version {user_version} but this "
                f"build understands version {SCHEMA_USER_VERSION}. Upgrade "
                "autorun, or point it at a different state directory."
            )

    # --- connections -------------------------------------------------------

    def _connect(self, timeout: float):
        try:
            conn = sqlite3.connect(
                self._db_path,
                timeout=max(0.0, timeout),
                # Transactions are started explicitly so that every write
                # begins with BEGIN IMMEDIATE. Implicit transaction handling
                # would start read transactions that cannot be upgraded once
                # another writer arrives.
                isolation_level=None,
                check_same_thread=True,
            )
        except sqlite3.Error as exc:
            raise SessionBackendError(
                f"Could not open state database {self._db_path}: {exc}"
            ) from exc

        try:
            conn.execute(f"PRAGMA busy_timeout = {int(max(0.0, timeout) * 1000)}")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA synchronous = FULL")
            conn.execute(f"PRAGMA journal_size_limit = {JOURNAL_SIZE_LIMIT_BYTES}")
            conn.execute(f"PRAGMA wal_autocheckpoint = {WAL_AUTOCHECKPOINT_PAGES}")
        except sqlite3.Error as exc:
            _close_connection(conn, exc)
            raise SessionBackendError(
                f"Could not configure state connection for {self._db_path}: {exc}"
            ) from exc
        return conn

    def _owner(self) -> StateUnitOfWork:
        owner = getattr(self._owners, "unit", None)
        if owner is None:
            owner = StateUnitOfWork()
            self._owners.unit = owner
        if owner.depth and owner.owner_pid != os.getpid():
            # Inherited across fork(). The connection belongs to the parent and
            # sharing it would corrupt both sides, so the child starts over.
            owner.clear()
        return owner

    @contextlib.contextmanager
    def operation_scope(self, timeout: float = DEFAULT_SESSION_TIMEOUT):
        """Own one connection and one deadline for the duration of an operation.

        Nested calls reuse the outer scope so that a helper can open one
        without knowing whether its caller already did.
        """
        owner = self._owner()
        if owner.depth and owner.connection is not None:
            owner.depth += 1
            try:
                yield owner
            finally:
                owner.depth -= 1
            return

        owner.clear()
        owner.deadline = time.monotonic() + max(0.0, timeout)
        owner.owner_thread = threading.get_ident()
        owner.owner_pid = os.getpid()
        owner.connection = self._connect(timeout)
        owner.depth = 1
        with self._counter_lock:
            self._open_connections += 1

        leaked = False
        try:
            yield owner
            leaked = owner.transaction_depth != 0
        finally:
            active_error = sys.exc_info()[1]
            conn = owner.connection
            if owner.transaction_depth:
                _rollback_without_masking_original(conn)
            owner.clear()
            with self._counter_lock:
                self._open_connections = max(0, self._open_connections - 1)
            _close_connection(conn, active_error)

        if leaked:
            raise SessionBackendError(
                "A state transaction was left open when its operation scope "
                "ended. It has been rolled back. Use the write_transaction "
                "context manager rather than entering it manually."
            )

    @contextlib.contextmanager
    def write_transaction(self, owner: StateUnitOfWork):
        """Run a body inside one BEGIN IMMEDIATE transaction.

        IMMEDIATE rather than the default deferred mode because a deferred
        transaction takes a read lock first and cannot be upgraded once
        another writer has arrived — the wait would fail instead of queueing.
        """
        if not owner.belongs_to_caller():
            raise SessionBackendError(
                "A state operation scope was used from a thread or process "
                "other than the one that opened it. SQLite connections cannot "
                "be shared; open a scope on this thread instead."
            )
        if owner.connection is None:
            raise SessionBackendError(
                "No state connection is open. Enter an operation scope first."
            )

        if owner.transaction_depth:
            owner.transaction_depth += 1
            try:
                yield owner.connection
            finally:
                owner.transaction_depth -= 1
            return

        conn = owner.connection
        remaining = owner.remaining_seconds()
        try:
            conn.execute(f"PRAGMA busy_timeout = {int(remaining * 1000)}")
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            if _is_contention_error(exc):
                raise SessionTimeoutError(
                    f"Could not acquire the state write transaction within "
                    f"{remaining:.3f}s; another writer holds it"
                ) from exc
            raise SessionBackendError(
                f"Could not begin a state transaction: {exc}"
            ) from exc

        owner.transaction_depth = 1
        try:
            yield conn
        except BaseException:
            owner.transaction_depth = 0
            _rollback_without_masking_original(conn)
            raise

        try:
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            owner.transaction_depth = 0
            _rollback_without_masking_original(conn)
            raise SessionBackendError(
                f"Could not commit a state transaction: {exc}"
            ) from exc
        finally:
            owner.transaction_depth = 0

    # --- reads -------------------------------------------------------------

    def read_field(self, session_id: str, field: str, default=MISSING,
                   timeout: float = DEFAULT_SESSION_TIMEOUT):
        """One field, without taking the writer slot.

        Returns ``default`` — ``MISSING`` unless the caller supplies another —
        when the field has never been stored. A field stored as ``None``
        returns ``None``.
        """
        _validate_session_id(session_id)
        found = self.read_fields(session_id, (field,), timeout=timeout)
        return found.get(field, default)

    def read_fields(self, session_id: str, fields=None,
                    timeout: float = DEFAULT_SESSION_TIMEOUT) -> dict:
        """Selected fields, or every field when ``fields`` is None.

        Passing None is the explicit bulk path: its cost grows with the size
        of the session, so hot paths name what they need instead.

        The result is decoded fresh, so a caller may keep or mutate it without
        reaching back into stored state.
        """
        _validate_session_id(session_id)
        with self.operation_scope(timeout) as owner:
            # A context open on this same operation already holds this
            # session's fields, including changes no row carries yet. It is
            # the complete and current picture, so it answers the read
            # directly — a caller invoked from inside an update must not act
            # on the pre-update rows.
            buffer = owner.buffers.get(session_id)
            if buffer is not None:
                if fields is None:
                    return copy.deepcopy(dict(buffer.values))
                wanted = set(fields)
                return copy.deepcopy(
                    {f: v for f, v in buffer.values.items() if f in wanted}
                )

            conn = owner.connection
            if fields is None:
                rows = conn.execute(
                    "SELECT field, value_json FROM state WHERE session = ?",
                    (session_id,),
                ).fetchall()
            else:
                requested = list(dict.fromkeys(fields))
                if not requested:
                    return {}
                rows = []
                # Bound the parameter list so a large request cannot exceed
                # SQLite's variable limit.
                for start in range(0, len(requested), QUERY_PARAMETER_CHUNK):
                    chunk = requested[start:start + QUERY_PARAMETER_CHUNK]
                    placeholders = ",".join("?" * len(chunk))
                    rows.extend(
                        conn.execute(
                            "SELECT field, value_json FROM state "
                            f"WHERE session = ? AND field IN ({placeholders})",
                            (session_id, *chunk),
                        ).fetchall()
                    )

            return {
                field: _decode_state_value(session_id, field, raw)
                for field, raw in rows
            }

    # --- writes ------------------------------------------------------------

    @contextlib.contextmanager
    def session(self, session_id: str, timeout: float = DEFAULT_SESSION_TIMEOUT):
        """Read a session, let the caller change it, and stage what changed.

        The whole body runs inside one transaction, because callers read a
        value, decide from it, and write back — splitting that would let two
        of them interleave and lose an update.

        Cost grows with the number of fields in this session, which is the
        price of the dict-like surface. Paths that know which field they want
        should use ``read_field`` and ``mutate_fields`` instead.
        """
        _validate_session_id(session_id)
        with self.operation_scope(timeout) as owner:
            with self.write_transaction(owner) as conn:
                existing = owner.buffers.get(session_id)
                if existing is not None:
                    # A nested context for a session already open shares its
                    # buffer; the outermost one stages the result.
                    yield existing.proxy
                    return

                rows = conn.execute(
                    "SELECT field, value_json FROM state WHERE session = ?",
                    (session_id,),
                ).fetchall()
                buffer = SessionBuffer(session_id, rows)
                owner.buffers[session_id] = buffer
                try:
                    yield buffer.proxy
                    changed, removed = buffer.diff_encoded()
                    if changed or removed:
                        self._stage(conn, session_id, changed, removed)
                        buffer.mark_persisted(changed, removed)
                finally:
                    owner.buffers.pop(session_id, None)

    def mutate_fields(self, session_id: str, updater,
                      timeout: float = DEFAULT_SESSION_TIMEOUT):
        """Apply ``updater`` to a session's fields inside one transaction.

        The updater receives the same dict-like view a session context yields
        and may return a value, which is passed back to the caller. This is
        the targeted form of ``session``: identical atomicity, no context
        manager for callers that only need one round trip.
        """
        with self.session(session_id, timeout) as state:
            return updater(state)

    def _stage(self, conn, session_id: str, changed: dict, removed) -> None:
        """Write the changed rows and advance the session's modification time.

        The session row is upserted first because the state, task, and event
        rows reference it. Doing it here rather than at each call site also
        keeps "last_modified advances only on a durable change" in one place.
        """
        now = time.time()
        try:
            conn.execute(
                "INSERT INTO sessions (session, namespace, last_modified) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(session) DO UPDATE SET "
                # A backward step of the system clock must not make active
                # state look older than it is; retention reads this column.
                "  last_modified = max(sessions.last_modified, excluded.last_modified)",
                (session_id, _namespace_for(session_id), now),
            )
            if changed:
                conn.executemany(
                    "INSERT INTO state (session, field, value_json, updated_at) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(session, field) DO UPDATE SET "
                    "  value_json = excluded.value_json, updated_at = excluded.updated_at",
                    [(session_id, field, encoded, now)
                     for field, encoded in changed.items()],
                )
            if removed:
                conn.executemany(
                    "DELETE FROM state WHERE session = ? AND field = ?",
                    [(session_id, field) for field in removed],
                )
        except (sqlite3.Error, UnicodeError, ValueError) as exc:
            # Most often an unpaired surrogate, which has no UTF-8 form and so
            # cannot be bound. Named here because the driver's own message
            # says only that encoding failed, not which field carried it.
            raise SessionBackendError(
                f"Could not store session {session_id!r} "
                f"field(s) {sorted(set(changed) | set(removed))}: {exc}"
            ) from exc


class TaskRepository:
    """Tasks and their events as rows, so one task changes on its own.

    Task state is the bulk of what autorun stores, and the largest session
    holds hundreds of tasks in one value. Keeping them in a single field means
    finishing one task costs as much as the whole history of that session, so
    each task gets a row and each output or audit record gets an append-only
    event row.

    ``task_id``, ``status``, and ``updated_at`` are columns because retention
    and the Stop check query on them; everything else in the record travels in
    the payload. The split is invisible to callers — a record goes in and the
    same record comes back — but it is what lets "is anything unfinished?" be
    answered without decoding finished work.
    """

    # Kept out of the payload because they have a column of their own.
    # Storing them twice would let the two copies disagree.
    _COLUMN_FIELDS = ("status", "updated_at")

    def __init__(self, store: SQLiteStore):
        self._store = store

    # --- tasks -------------------------------------------------------------

    def get_task(self, session_id: str, task_id: str,
                 timeout: float = DEFAULT_SESSION_TIMEOUT):
        """One task, or MISSING. Reads and decodes nothing else."""
        _validate_session_id(session_id)
        with self._store.operation_scope(timeout) as owner:
            row = owner.connection.execute(
                "SELECT status, updated_at, payload_json FROM tasks "
                "WHERE session = ? AND task_id = ?",
                (session_id, task_id),
            ).fetchone()
        if row is None:
            return MISSING
        return self._record_from_row(session_id, task_id, row)

    def put_task(self, session_id: str, task_id: str, record: dict,
                 timeout: float = DEFAULT_SESSION_TIMEOUT) -> None:
        """Store one task, creating its session row if this is the first."""
        _validate_session_id(session_id)
        with self._store.operation_scope(timeout) as owner:
            with self._store.write_transaction(owner) as conn:
                self._write_task(conn, session_id, task_id, record)

    def mutate_task(self, session_id: str, task_id: str, updater,
                    timeout: float = DEFAULT_SESSION_TIMEOUT) -> dict:
        """Read one task, apply ``updater``, write the result — as one step.

        Several hooks from a single assistant turn update tasks at the same
        instant. Reading and writing separately lets one of them overwrite
        another's change, which is how a completed task ends up recorded as
        still pending.
        """
        _validate_session_id(session_id)
        with self._store.operation_scope(timeout) as owner:
            with self._store.write_transaction(owner) as conn:
                row = conn.execute(
                    "SELECT status, updated_at, payload_json FROM tasks "
                    "WHERE session = ? AND task_id = ?",
                    (session_id, task_id),
                ).fetchone()
                if row is None:
                    raise KeyError(
                        f"No task {task_id!r} in session {session_id!r}"
                    )
                current = self._record_from_row(session_id, task_id, row)
                updated = updater(current)
                self._write_task(conn, session_id, task_id, updated)
                return copy.deepcopy(updated)

    def delete_task(self, session_id: str, task_id: str,
                    timeout: float = DEFAULT_SESSION_TIMEOUT) -> None:
        """Remove one task. Its events go with it, by foreign key."""
        _validate_session_id(session_id)
        with self._store.operation_scope(timeout) as owner:
            with self._store.write_transaction(owner) as conn:
                conn.execute(
                    "DELETE FROM tasks WHERE session = ? AND task_id = ?",
                    (session_id, task_id),
                )

    def list_tasks(self, session_id: str,
                   timeout: float = DEFAULT_SESSION_TIMEOUT) -> dict:
        """Every task in a session, keyed by ID.

        The explicit bulk path: its cost grows with the number of tasks and
        the size of their records. Callers that need one task, or only the
        unfinished ones, have narrower methods above.
        """
        _validate_session_id(session_id)
        with self._store.operation_scope(timeout) as owner:
            rows = owner.connection.execute(
                "SELECT task_id, status, updated_at, payload_json FROM tasks "
                "WHERE session = ? ORDER BY task_id",
                (session_id,),
            ).fetchall()
        return {
            row[0]: self._record_from_row(session_id, row[0], row[1:])
            for row in rows
        }

    def list_incomplete(self, session_id: str, terminal_statuses,
                        timeout: float = DEFAULT_SESSION_TIMEOUT) -> list:
        """Tasks that are not in a terminal status.

        This is the question the Stop check asks. Answering it from the status
        column means finished work is never decoded to establish that it is
        finished.
        """
        _validate_session_id(session_id)
        statuses = list(terminal_statuses)

        if tuple(statuses) == TERMINAL_TASK_STATUSES:
            # Spelled exactly like the partial index predicate so SQLite can
            # use it. Without the match the query degrades to walking every
            # task in the session, finished ones included, which is the cost
            # this index exists to remove.
            sql = (
                "SELECT task_id, status, updated_at, payload_json FROM tasks "
                f"WHERE session = ? AND status NOT IN {_TERMINAL_TASK_STATUS_SQL} "
                "ORDER BY task_id"
            )
            params = (session_id,)
        else:
            placeholders = ",".join("?" * len(statuses)) or "NULL"
            sql = (
                "SELECT task_id, status, updated_at, payload_json FROM tasks "
                f"WHERE session = ? AND status NOT IN ({placeholders}) "
                "ORDER BY task_id"
            )
            params = (session_id, *statuses)

        with self._store.operation_scope(timeout) as owner:
            rows = owner.connection.execute(sql, params).fetchall()
        return [
            self._record_from_row(session_id, row[0], row[1:]) for row in rows
        ]

    def list_terminal_before(self, session_id: str, statuses, cutoff: float,
                             timeout: float = DEFAULT_SESSION_TIMEOUT) -> list:
        """Finished tasks last touched before ``cutoff``.

        A task stamped in the future is left alone. That means a clock
        anomaly, and treating it as ancient would delete live work.
        """
        _validate_session_id(session_id)
        selected = list(statuses)
        if not selected:
            return []
        placeholders = ",".join("?" * len(selected))
        now = time.time()
        with self._store.operation_scope(timeout) as owner:
            rows = owner.connection.execute(
                "SELECT task_id, status, updated_at, payload_json FROM tasks "
                f"WHERE session = ? AND status IN ({placeholders}) "
                "AND updated_at < ? AND updated_at <= ? "
                "ORDER BY updated_at, task_id",
                (session_id, *selected, cutoff, now),
            ).fetchall()
        return [
            self._record_from_row(session_id, row[0], row[1:]) for row in rows
        ]

    # --- events ------------------------------------------------------------

    def append_event(self, session_id: str, task_id, *, event_id: str,
                     idempotency_key: str, event_type: str, payload: dict,
                     created_at: "float | None" = None,
                     timeout: float = DEFAULT_SESSION_TIMEOUT) -> None:
        """Record one event once.

        Repeating an event with the same key and the same content does
        nothing, so a retried hook cannot append the same output twice. The
        same key with different content is refused: one of the two is wrong,
        and picking either silently would drop a real event or invent one.
        """
        _validate_session_id(session_id)
        stamp = time.time() if created_at is None else created_at
        encoded = _encode_state_value(f"event {event_id}", payload)
        with self._store.operation_scope(timeout) as owner:
            with self._store.write_transaction(owner) as conn:
                self._ensure_session_row(conn, session_id, stamp)
                existing = conn.execute(
                    "SELECT event_id, payload_json FROM task_events "
                    "WHERE session = ? AND idempotency_key = ?",
                    (session_id, idempotency_key),
                ).fetchone()
                if existing is not None:
                    if existing[0] == event_id and existing[1] == encoded:
                        return
                    raise SessionBackendError(
                        f"Event key {idempotency_key!r} in session "
                        f"{session_id!r} already identifies a different event "
                        f"({existing[0]!r}). Two events sharing a key means "
                        "the key does not identify what it claims to."
                    )
                try:
                    conn.execute(
                        "INSERT INTO task_events (session, task_id, event_id, "
                        "idempotency_key, event_type, created_at, payload_json) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (session_id, task_id, event_id, idempotency_key,
                         event_type, stamp, encoded),
                    )
                except sqlite3.IntegrityError as exc:
                    raise SessionBackendError(
                        f"Could not record event {event_id!r} for session "
                        f"{session_id!r}: {exc}"
                    ) from exc

    def events(self, session_id: str, task_id=MISSING, *, limit=None,
               after=None, timeout: float = DEFAULT_SESSION_TIMEOUT) -> list:
        """Events in the order they happened, a page at a time.

        Ordered by time and then by ID, so events written within the same
        clock tick still have one definite order. ``after`` takes the last
        event of the previous page.
        """
        _validate_session_id(session_id)
        conditions = ["session = ?"]
        params = [session_id]
        if task_id is not MISSING:
            if task_id is None:
                conditions.append("task_id IS NULL")
            else:
                conditions.append("task_id = ?")
                params.append(task_id)
        if after is not None:
            conditions.append("(created_at, event_id) > (?, ?)")
            params.extend([after["created_at"], after["event_id"]])

        sql = (
            "SELECT task_id, event_id, idempotency_key, event_type, "
            "created_at, payload_json, projected_at FROM task_events "
            f"WHERE {' AND '.join(conditions)} "
            "ORDER BY created_at, event_id"
        )
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))

        with self._store.operation_scope(timeout) as owner:
            rows = owner.connection.execute(sql, params).fetchall()
        return [self._event_from_row(session_id, row) for row in rows]

    def pending_projection(self, *, limit: int = 100,
                           timeout: float = DEFAULT_SESSION_TIMEOUT) -> list:
        """Events not yet written to the human-readable audit file.

        The row is authoritative and stays pending until the append is known
        to have happened. A crash in between may repeat a line — repeats
        carry an event ID and are identifiable — but cannot lose the event.
        """
        with self._store.operation_scope(timeout) as owner:
            rows = owner.connection.execute(
                "SELECT session, task_id, event_id, idempotency_key, event_type, "
                "created_at, payload_json, projected_at FROM task_events "
                "WHERE projected_at IS NULL "
                "ORDER BY created_at, event_id LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [self._event_from_row(row[0], row[1:]) for row in rows]

    def mark_projected(self, session_id: str, event_id: str,
                       timeout: float = DEFAULT_SESSION_TIMEOUT) -> None:
        """Record that an event reached the audit file."""
        _validate_session_id(session_id)
        with self._store.operation_scope(timeout) as owner:
            with self._store.write_transaction(owner) as conn:
                conn.execute(
                    "UPDATE task_events SET projected_at = ? "
                    "WHERE session = ? AND event_id = ?",
                    (time.time(), session_id, event_id),
                )

    # --- internals ---------------------------------------------------------

    def _record_from_row(self, session_id: str, task_id: str, row) -> dict:
        status, updated_at, payload_json = row
        record = _decode_state_value(session_id, f"task {task_id}", payload_json)
        if not isinstance(record, dict):
            raise SessionBackendError(
                f"Stored task {task_id!r} in session {session_id!r} is a "
                f"{type(record).__name__}, not a task record."
            )
        record["status"] = status
        record["updated_at"] = updated_at
        return record

    def _event_from_row(self, session_id: str, row) -> dict:
        task_id, event_id, key, event_type, created_at, payload_json, projected = row
        return {
            "session": session_id,
            "task_id": task_id,
            "event_id": event_id,
            "idempotency_key": key,
            "event_type": event_type,
            "created_at": created_at,
            "payload": _decode_state_value(session_id, f"event {event_id}",
                                           payload_json),
            "projected_at": projected,
        }

    def _write_task(self, conn, session_id: str, task_id: str, record: dict) -> None:
        if not isinstance(record, dict):
            raise SessionBackendError(
                f"A task record must be a mapping; got {type(record).__name__} "
                f"for task {task_id!r}."
            )
        status = record.get("status", "pending")
        updated_at = record.get("updated_at", time.time())
        payload = {k: v for k, v in record.items() if k not in self._COLUMN_FIELDS}
        encoded = _encode_state_value(f"task {task_id}", payload)

        self._ensure_session_row(conn, session_id, updated_at)
        try:
            conn.execute(
                "INSERT INTO tasks (session, task_id, status, updated_at, payload_json) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(session, task_id) DO UPDATE SET "
                "  status = excluded.status, "
                "  updated_at = excluded.updated_at, "
                "  payload_json = excluded.payload_json",
                (session_id, task_id, status, updated_at, encoded),
            )
        except (sqlite3.Error, UnicodeError, ValueError) as exc:
            raise SessionBackendError(
                f"Could not store task {task_id!r} in session {session_id!r}: {exc}"
            ) from exc

    @staticmethod
    def _ensure_session_row(conn, session_id: str, stamp: float) -> None:
        """Create or touch the session that owns this row.

        Every task and event references it, so the first write for a session
        has to create it in the same transaction. Doing it here also keeps
        "last_modified moves forward only" in one place.
        """
        conn.execute(
            "INSERT INTO sessions (session, namespace, last_modified) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(session) DO UPDATE SET "
            "  last_modified = max(sessions.last_modified, excluded.last_modified)",
            (session_id, _namespace_for(session_id), max(stamp, time.time())),
        )


class RetentionPolicy:
    """What may be removed, and whether removal is allowed at all.

    ``delete`` is False so that a policy which has not been thought about
    reports instead of acting. Every field here widens what is eligible; none
    of them narrows what is protected, because protection is decided by the
    rules in ``StateRetention`` rather than by configuration.
    """

    # Sessions whose deletion consequences are known and bounded. Anything
    # else is protected: a namespace nobody has classified is exactly the one
    # whose removal has not been reasoned about.
    SWEEPABLE_NAMESPACES = frozenset({"session", "task_lifecycle"})

    # Statuses that mean the work is over. Paused is deliberately absent: a
    # paused task is work someone means to come back to.
    TERMINAL_TASK_STATUSES = ("completed", "deleted", "ignored")

    def __init__(self, session_max_age_seconds: "float | None" = None,
                 task_max_age_seconds: "float | None" = None,
                 archive_dir=None, delete: bool = False,
                 protected_sessions=()):
        self.session_max_age_seconds = session_max_age_seconds
        self.task_max_age_seconds = task_max_age_seconds
        self.archive_dir = Path(archive_dir) if archive_dir else None
        self.delete = delete
        self.protected_sessions = frozenset(protected_sessions)


class StateRetention:
    """Bounded growth, on purpose rather than by accident.

    Collection today is manual and confirmation-gated, so in practice it does
    not happen; the measured store holds 8,106 sessions. This makes it
    automatic while keeping deletion off until someone turns it on, and
    keeping the archive ahead of the delete so a mistake is recoverable.
    """

    def __init__(self, store: "SQLiteStore", policy: RetentionPolicy):
        self.store = store
        self.policy = policy

    # --- sessions ----------------------------------------------------------

    def sweep_sessions(self) -> dict:
        """Find, archive, and optionally remove whole expired sessions.

        A session goes entirely or not at all. Removing only its old fields
        would leave a session that half exists, which no caller is written to
        expect.
        """
        eligible, protected, anomalies = self._classify_sessions()
        report = {
            "eligible": eligible,
            "protected": protected,
            "anomalies": anomalies,
            "deleted": [],
            "archive": "",
            "report_only": not self.policy.delete,
        }
        if not eligible or not self.policy.delete:
            return report

        report["archive"] = str(self._archive_sessions(eligible))
        with self.store.operation_scope(DEFAULT_SESSION_TIMEOUT) as owner:
            with self.store.write_transaction(owner) as conn:
                conn.executemany(
                    "DELETE FROM sessions WHERE session = ?",
                    [(session_id,) for session_id in eligible],
                )
        report["deleted"] = eligible
        return report

    def _classify_sessions(self):
        """Sort every session into eligible, protected, and suspicious."""
        now = time.time()
        max_age = self.policy.session_max_age_seconds
        with self.store.operation_scope(DEFAULT_SESSION_TIMEOUT) as owner:
            rows = owner.connection.execute(
                "SELECT session, namespace, last_modified FROM sessions "
                "ORDER BY session"
            ).fetchall()

        eligible, protected, anomalies = [], [], []
        for session_id, namespace, last_modified in rows:
            if last_modified > now:
                # The clock moved, not the session. Treating it as ancient
                # would delete something in use.
                anomalies.append(session_id)
                protected.append(session_id)
                continue
            if (namespace not in self.policy.SWEEPABLE_NAMESPACES
                    or session_id in self.policy.protected_sessions):
                protected.append(session_id)
                continue
            if max_age is None or (now - last_modified) < max_age:
                protected.append(session_id)
                continue
            if self._has_unfinished_tasks(session_id):
                protected.append(session_id)
                continue
            eligible.append(session_id)
        return eligible, protected, anomalies

    def _has_unfinished_tasks(self, session_id: str) -> bool:
        statuses = list(self.policy.TERMINAL_TASK_STATUSES)
        placeholders = ",".join("?" * len(statuses))
        with self.store.operation_scope(DEFAULT_SESSION_TIMEOUT) as owner:
            row = owner.connection.execute(
                "SELECT 1 FROM tasks WHERE session = ? "
                f"AND status NOT IN ({placeholders}) LIMIT 1",
                (session_id, *statuses),
            ).fetchone()
        return row is not None

    # --- tasks -------------------------------------------------------------

    def prune_tasks(self, session_id: str) -> dict:
        """Remove finished tasks past their age, leaving the session behind.

        A session outlives its tasks: its metadata and counters are still the
        record of what happened there.
        """
        _validate_session_id(session_id)
        max_age = self.policy.task_max_age_seconds
        report = {"eligible": [], "deleted": [], "archive": "",
                  "report_only": not self.policy.delete}
        if max_age is None:
            return report

        repo = TaskRepository(self.store)
        eligible = repo.list_terminal_before(
            session_id,
            statuses=self.policy.TERMINAL_TASK_STATUSES,
            cutoff=time.time() - max_age,
        )
        report["eligible"] = [task["id"] for task in eligible]
        if not eligible or not self.policy.delete:
            return report

        report["archive"] = str(self._archive_tasks(session_id, eligible))
        with self.store.operation_scope(DEFAULT_SESSION_TIMEOUT) as owner:
            with self.store.write_transaction(owner) as conn:
                conn.executemany(
                    "DELETE FROM tasks WHERE session = ? AND task_id = ?",
                    [(session_id, task["id"]) for task in eligible],
                )
        report["deleted"] = report["eligible"]
        return report

    # --- archive -----------------------------------------------------------

    def _archive_sessions(self, session_ids) -> Path:
        repo = TaskRepository(self.store)
        payload = {"kind": "sessions", "captured_at": _artifact_timestamp(),
                   "sessions": {}}
        for session_id in session_ids:
            payload["sessions"][session_id] = {
                "state": self.store.read_fields(session_id),
                "tasks": repo.list_tasks(session_id),
                "events": repo.events(session_id),
            }
        return self._publish_archive(payload, prefix="sessions")

    def _archive_tasks(self, session_id: str, tasks) -> Path:
        repo = TaskRepository(self.store)
        payload = {
            "kind": "tasks",
            "captured_at": _artifact_timestamp(),
            "session": session_id,
            "tasks": {task["id"]: task for task in tasks},
            "events": {
                task["id"]: repo.events(session_id, task_id=task["id"])
                for task in tasks
            },
        }
        return self._publish_archive(payload, prefix=f"tasks-{session_id}")

    def _publish_archive(self, payload: dict, prefix: str) -> Path:
        """Write the archive and make it durable before anything is deleted.

        Raises rather than returning on failure, so a caller cannot proceed to
        the delete with nothing written.
        """
        if self.policy.archive_dir is None:
            raise SessionBackendError(
                "Deletion is enabled but no archive directory is configured. "
                "Nothing is removed without a copy of it on disk first."
            )
        self.policy.archive_dir.mkdir(parents=True, exist_ok=True)
        safe_prefix = re.sub(r"[^A-Za-z0-9_.-]", "_", prefix)[:80]
        destination = (self.policy.archive_dir
                       / f"{_artifact_timestamp()}-{safe_prefix}.json")
        counter = 1
        while destination.exists():
            destination = (self.policy.archive_dir
                           / f"{_artifact_timestamp()}-{safe_prefix}.{counter}.json")
            counter += 1

        StateMigrator._write_json_durably(destination, payload)
        return destination

    # --- maintenance -------------------------------------------------------

    def backup(self, destination) -> dict:
        """Copy the database using SQLite's own backup, never by file copy.

        A plain copy of the main file omits whatever is still in the
        write-ahead log, which is where the most recent commits live.
        """
        destination = Path(destination)
        if destination.exists():
            raise SessionBackendError(
                f"{destination} already exists. Choose another name rather "
                "than replacing a backup that may be the only good copy."
            )
        destination.parent.mkdir(parents=True, exist_ok=True)

        staged = destination.with_name(f"{destination.name}.stage-{os.getpid()}")
        staged.unlink(missing_ok=True)
        target = sqlite3.connect(str(staged))
        try:
            with self.store.operation_scope(DEFAULT_SESSION_TIMEOUT) as owner:
                owner.connection.backup(target)
            integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise SessionBackendError(
                    f"The backup written to {staged} failed its integrity "
                    f"check ({integrity}) and has not been published."
                )
        finally:
            _close_connection(target, sys.exc_info()[1])

        try:
            os.link(staged, destination)
        except FileExistsError as exc:
            raise SessionBackendError(
                f"{destination} appeared while the backup was being written; "
                "nothing was overwritten."
            ) from exc
        finally:
            staged.unlink(missing_ok=True)
        StateMigrator._sync_directory(destination.parent)
        return {"destination": str(destination), "integrity": "ok"}

    def maintenance(self, reclaim: bool = False) -> dict:
        """Report space, and optionally return freed pages to the file.

        Reclaiming is incremental and belongs to maintenance, never to a hook:
        a full vacuum would hold the database for as long as it takes.
        """
        with self.store.operation_scope(DEFAULT_SESSION_TIMEOUT) as owner:
            conn = owner.connection
            if reclaim:
                conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                conn.execute("PRAGMA incremental_vacuum")
            page_size = conn.execute("PRAGMA page_size").fetchone()[0]
            page_count = conn.execute("PRAGMA page_count").fetchone()[0]
            freelist = conn.execute("PRAGMA freelist_count").fetchone()[0]

        wal = Path(str(self.store.db_path) + "-wal")
        return {
            "page_size": page_size,
            "page_count": page_count,
            "freelist_count": freelist,
            "reclaimable_bytes": freelist * page_size,
            "database_bytes": page_count * page_size,
            "wal_bytes": wal.stat().st_size if wal.exists() else 0,
            "reclaimed": reclaim,
        }


def _artifact_timestamp() -> str:
    """A sortable stamp for migration and backup artifacts.

    Minute resolution, ordered lexically, so successive artifacts in one
    directory read in the order they were produced.
    """
    return time.strftime("%Y-%m-%d-%H%M", time.localtime())


class StateMigrator:
    """Move legacy JSON state into the row store, resumably.

    The migration spans a file and a database, and nothing makes those two
    one transaction. A crash can therefore land between them, so each step is
    recorded in a receipt published beside them:

        PREPARED           a verified database exists, built off to one side
        SOURCE_RETIRED     the original file has been renamed, not deleted
        DATABASE_PUBLISHED the database is in place under its real name
        COMPLETE           both sides agree and the store is authoritative

    Resuming reads the receipt and continues from the recorded phase. The
    receipt also records the identity of the source it consumed, so a JSON
    file that appears afterwards is recognizable as a second writer rather
    than mistaken for input.
    """

    # The forward sequence, stated once, as (phase, what it means, successor).
    # Written as data rather than an if-chain so that adding or reordering a
    # step is one edit here instead of a change scattered across the walk, the
    # docstring, and the recovery path — the kind of divergence that makes a
    # half-finished migration resume into the wrong state.
    #
    # Read it as: on entering PHASE, `advance` runs, and the result is
    # SUCCESSOR. Recovery re-enters at whatever phase the receipt records, so
    # every step is written to be safe to repeat.
    #
    #        phase                 successor            meaning
    TRANSITIONS = (
        ("NOT_STARTED",        "PREPARED"),   # nothing done yet
        ("PREPARED",           "SOURCE_RETIRED"),
        # a verified copy exists off to one side
        ("SOURCE_RETIRED",     "DATABASE_PUBLISHED"),
        # the original is renamed, never deleted
        ("DATABASE_PUBLISHED", "COMPLETE"),
        # the database is in place under its real name
    )

    # Terminal states, reachable from any phase. FAILED keeps the original
    # authoritative; ROLLED_BACK means state was exported back out.
    TERMINAL_PHASES = ("COMPLETE", "FAILED", "ROLLED_BACK")

    # Phases a fresh attempt may start from. FAILED and ROLLED_BACK are here
    # because both leave the original in place, so restarting is well defined.
    RESTARTABLE_PHASES = ("NOT_STARTED", "FAILED", "ROLLED_BACK")

    PHASES = tuple(
        dict.fromkeys(
            [phase for phase, _successor in TRANSITIONS]
            + [successor for _phase, successor in TRANSITIONS]
            + list(TERMINAL_PHASES)
        )
    )

    def __init__(self, json_path, db_path, receipt_path):
        self._json_path = Path(json_path)
        self._db_path = Path(db_path)
        self._receipt_path = Path(receipt_path)

    # --- inspection --------------------------------------------------------

    def status(self) -> dict:
        """What phase this migration is in. Reads only; starts nothing."""
        receipt = self._read_receipt()
        return {
            "phase": receipt.get("phase", "NOT_STARTED"),
            "source": str(self._json_path),
            "database": str(self._db_path),
            "source_present": self._json_path.exists(),
            "database_present": self._db_path.exists(),
            "backup": receipt.get("backup", ""),
            "fields": receipt.get("fields", 0),
            "sessions": receipt.get("sessions", 0),
        }

    # --- migration ---------------------------------------------------------

    def migrate(self) -> dict:
        """Import legacy state, or resume an interrupted import.

        Safe to call repeatedly: once complete it reports so and touches
        nothing, including a source file that has reappeared.
        """
        receipt = self._read_receipt()
        phase = receipt.get("phase", "NOT_STARTED")

        if phase == "COMPLETE":
            result = dict(receipt)
            result["already_complete"] = True
            if self._json_path.exists():
                # Not input. Something wrote legacy state after cutover, and
                # merging it would resurrect values the store has moved past.
                logger.error(
                    "%s exists after migration completed. Another process is "
                    "writing legacy state; it has been left untouched.",
                    self._json_path,
                )
                result["unexpected_source"] = True
            return result

        stage_path = self._stage_path()

        if phase in self.RESTARTABLE_PHASES:
            legacy = self._read_legacy()
            grouped, field_count = self._group_by_session(legacy)
            self._build_stage(stage_path, grouped)
            self._verify_stage(stage_path, legacy)
            self._record_phase(
                "PREPARED",
                stage=str(stage_path),
                fields=field_count,
                sessions=len(grouped),
                source_bytes=(self._json_path.stat().st_size
                              if self._json_path.exists() else 0),
                backup="",
            )
            receipt = self._read_receipt()
            phase = "PREPARED"

        if phase == "PREPARED":
            backup = self._retire_source()
            self._record_phase("SOURCE_RETIRED", backup=str(backup))
            receipt = self._read_receipt()
            phase = "SOURCE_RETIRED"

        if phase == "SOURCE_RETIRED":
            self._publish(Path(receipt.get("stage", stage_path)))
            self._record_phase("DATABASE_PUBLISHED")
            receipt = self._read_receipt()
            phase = "DATABASE_PUBLISHED"

        if phase == "DATABASE_PUBLISHED":
            self._record_phase("COMPLETE")
            receipt = self._read_receipt()

        result = dict(receipt)
        result["already_complete"] = False
        return result

    def rollback(self) -> dict:
        """Write current state back out in the legacy format.

        Exports what the store holds now, not the retired file, so anything
        recorded since the migration comes back too. Refuses to overwrite an
        existing source file: that file was written by something, and this is
        not the moment to decide it did not matter.
        """
        receipt = self._read_receipt()
        if receipt.get("phase") != "COMPLETE":
            raise SessionBackendError(
                f"Cannot roll back from phase {receipt.get('phase', 'NOT_STARTED')!r}; "
                "only a COMPLETE migration has something to roll back."
            )
        if self._json_path.exists():
            raise SessionBackendError(
                f"{self._json_path} already exists. Move it aside before "
                "rolling back, so nothing that wrote it is discarded."
            )

        store = SQLiteStore(self._db_path)
        store.initialize()
        legacy = self._reconstruct_legacy_view(store)

        staged = self._json_path.with_name(
            f"{self._json_path.name}.rollback-{os.getpid()}"
        )
        self._write_json_durably(staged, legacy)
        try:
            # Fails rather than replaces if the name was taken meanwhile.
            os.link(staged, self._json_path)
        except FileExistsError as exc:
            staged.unlink(missing_ok=True)
            raise SessionBackendError(
                f"{self._json_path} appeared while rolling back; nothing was "
                "overwritten."
            ) from exc
        finally:
            staged.unlink(missing_ok=True)
        self._sync_directory(self._json_path.parent)

        self._record_phase("ROLLED_BACK", fields=len(legacy))
        return {"phase": "ROLLED_BACK", "fields": len(legacy),
                "source": str(self._json_path)}

    # --- phases ------------------------------------------------------------

    def _read_legacy(self) -> dict:
        if not self._json_path.exists():
            return {}
        try:
            data = json.loads(self._json_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise SessionBackendError(
                f"Could not read legacy state from {self._json_path}: {exc}. "
                "Migration has not started; the file is unchanged."
            ) from exc
        if not isinstance(data, dict):
            raise SessionBackendError(
                f"{self._json_path} holds a {type(data).__name__}, not a "
                "mapping of session state."
            )
        return data

    @staticmethod
    def _group_by_session(legacy: dict):
        """Split flat ``session/field`` keys, refusing anything ambiguous.

        Only the first separator divides the two, because field names may
        themselves contain one. A key with no separator names no session, and
        assigning it to one would file it somewhere it never belonged.
        """
        grouped: dict = {}
        for key, value in legacy.items():
            if not isinstance(key, str) or "/" not in key:
                raise SessionBackendError(
                    f"Legacy key {key!r} has no session separator, so there is "
                    "no way to tell which session it belongs to. Remove or "
                    "correct it and run the migration again."
                )
            session_id, field = key.split("/", 1)
            if not session_id.strip():
                raise SessionBackendError(
                    f"Legacy key {key!r} names an empty session."
                )
            grouped.setdefault(session_id, {})[field] = value
        return grouped, len(legacy)

    def _stage_path(self) -> Path:
        return self._db_path.with_name(f"{self._db_path.name}.stage")

    def _build_stage(self, stage_path: Path, grouped: dict) -> None:
        """Create a complete database off to one side, then leave it closed."""
        stage_path.unlink(missing_ok=True)
        Path(str(stage_path) + "-wal").unlink(missing_ok=True)
        Path(str(stage_path) + "-shm").unlink(missing_ok=True)

        store = SQLiteStore(stage_path)
        store.initialize()
        with store.operation_scope(DEFAULT_SESSION_TIMEOUT) as owner:
            with store.write_transaction(owner):
                for session_id, fields in grouped.items():
                    with store.session(session_id) as state:
                        for field, value in fields.items():
                            state[field] = value

    def _verify_stage(self, stage_path: Path, legacy: dict) -> None:
        """Rebuild the legacy view from the copy and require it to match.

        Comparing what came out against what went in is the only check that
        covers encoding, key splitting, and the write path at once.
        """
        store = SQLiteStore(stage_path)
        store.initialize()
        reconstructed = self._reconstruct_legacy_view(store)
        if reconstructed != legacy:
            missing = sorted(set(legacy) - set(reconstructed))
            extra = sorted(set(reconstructed) - set(legacy))
            changed = sorted(
                key for key in set(legacy) & set(reconstructed)
                if legacy[key] != reconstructed[key]
            )
            self._record_phase("FAILED", stage=str(stage_path))
            raise SessionBackendError(
                "Migration verification failed: the copy does not reproduce "
                f"its source. Missing {missing[:5]}, unexpected {extra[:5]}, "
                f"changed {changed[:5]}. The original file is untouched and "
                "remains authoritative."
            )

    def _reconstruct_legacy_view(self, store: "SQLiteStore") -> dict:
        """The flat ``session/field`` mapping the legacy format used."""
        with store.operation_scope(DEFAULT_SESSION_TIMEOUT) as owner:
            rows = owner.connection.execute(
                "SELECT session, field, value_json FROM state"
            ).fetchall()
        return {
            f"{session}/{field}": _decode_state_value(session, field, raw)
            for session, field, raw in rows
        }

    def _retire_source(self) -> Path:
        """Rename the original out of the way. It is never deleted."""
        if not self._json_path.exists():
            return Path("")
        backup = self._json_path.with_name(
            f"{self._json_path.name}.migrated.{_artifact_timestamp()}"
        )
        counter = 1
        while backup.exists():
            backup = self._json_path.with_name(
                f"{self._json_path.name}.migrated.{_artifact_timestamp()}.{counter}"
            )
            counter += 1
        os.replace(self._json_path, backup)
        self._sync_directory(self._json_path.parent)
        return backup

    def _publish(self, stage_path: Path) -> None:
        """Put the verified database in place under its real name."""
        if self._db_path.exists():
            # A resumed run may have published already.
            return
        if not stage_path.exists():
            raise SessionBackendError(
                f"The staged database {stage_path} is gone, so the migration "
                "cannot be completed. Restore the retired source file and "
                "start again."
            )
        os.replace(stage_path, self._db_path)
        self._sync_directory(self._db_path.parent)

    # --- receipt -----------------------------------------------------------

    def _read_receipt(self) -> dict:
        if not self._receipt_path.exists():
            return {}
        try:
            data = json.loads(self._receipt_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise SessionBackendError(
                f"Could not read the migration receipt {self._receipt_path}: "
                f"{exc}. Without it there is no way to tell how far a previous "
                "run got; inspect both artifacts by hand."
            ) from exc
        return data if isinstance(data, dict) else {}

    def _record_phase(self, phase: str, **details) -> None:
        """Publish the receipt for a phase before moving past it."""
        if phase not in self.PHASES:
            raise SessionBackendError(f"Unknown migration phase {phase!r}")
        receipt = self._read_receipt()
        receipt.update(details)
        receipt["phase"] = phase
        receipt["recorded_at"] = _artifact_timestamp()
        self._write_json_durably(self._receipt_path, receipt)

    # --- durability helpers ------------------------------------------------

    @staticmethod
    def _write_json_durably(path: Path, data: dict) -> None:
        """Write and replace atomically, so a crash leaves one whole version."""
        path.parent.mkdir(parents=True, exist_ok=True)
        staged = path.with_name(f"{path.name}.tmp-{os.getpid()}")
        try:
            with open(staged, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(staged, path)
        except OSError as exc:
            staged.unlink(missing_ok=True)
            raise SessionBackendError(
                f"Could not write {path}: {exc}"
            ) from exc
        StateMigrator._sync_directory(path.parent)

    @staticmethod
    def _sync_directory(directory: Path) -> None:
        """Make a rename durable, not just the bytes it points at."""
        try:
            fd = os.open(str(directory), os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError as exc:
            logger.warning("Could not sync directory %s: %s", directory, exc)
        finally:
            os.close(fd)


def _reset_for_testing():
    """Reset module-level singletons. For use in test fixtures ONLY."""
    global _store, _manager
    _stores.clear()
    _managers.clear()
    _store = None
    _manager = None
