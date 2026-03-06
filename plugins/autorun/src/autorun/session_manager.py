"""filelock+JSON-backed session state — replaces shelve+fcntl implementation.

Design:
- Single JSON file (~/.claude/sessions/daemon_state.json) for all sessions
- filelock for cross-process mutual exclusion
- threading.RLock for same-process thread serialization
- Atomic tempfile+rename writes for crash safety
- Re-read from disk on every lock acquisition (picks up other-process writes)
"""
import contextlib
import json
import os
import threading
from pathlib import Path
from filelock import FileLock, Timeout as FileLockTimeout

DEFAULT_SESSION_TIMEOUT = 30.0
SHARED_ACCESS_TIMEOUT = 5.0


class SessionStateError(Exception):
    pass


class SessionTimeoutError(SessionStateError):
    pass


class SessionBackendError(SessionStateError):
    pass


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
        v = self._data.get(self._k(key))
        if v is None:
            raise KeyError(key)
        return v

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
            with open(self._state_file, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save(self):
        os.makedirs(os.path.dirname(self._state_file), exist_ok=True)
        tmp = self._state_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self._data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self._state_file)

    @contextlib.contextmanager
    def session(self, session_id: str, timeout: float = DEFAULT_SESSION_TIMEOUT):
        # Reentrant support: same thread already holds the lock — share _data, defer save
        if getattr(self._held_by, 'active', False):
            proxy = _StateProxy(self._data, session_id, self)
            yield proxy
            return

        try:
            file_lock = FileLock(self._lock_file, timeout=timeout)
            with file_lock:
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
_store: "_JSONStore | None" = None


def _get_store(state_dir: "str | None" = None) -> "_JSONStore":
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                d = state_dir or os.path.expanduser("~/.claude/sessions")
                _store = _JSONStore(
                    os.path.join(d, "daemon_state.json"),
                    os.path.join(d, "daemon_state.json.lock"),
                )
    return _store


class SessionStateManager:
    def __init__(self, state_dir: "str | None" = None):
        self._store = _get_store(state_dir)

    @property
    def state_dir(self) -> "Path":
        """Path to the directory containing daemon_state.json."""
        import os
        from pathlib import Path
        return Path(os.path.dirname(self._store._state_file))

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
        prefix = f"{session_id}/"
        with self._store._rlock:
            keys = [k for k in self._store._data if k.startswith(prefix)]
            for k in keys:
                del self._store._data[k]
            if keys:
                self._store._save()

    def clear_test_sessions_batch(self, session_ids):
        """Clear multiple test sessions in one save operation (O(1) disk writes)."""
        any_deleted = False
        with self._store._rlock:
            for session_id in session_ids:
                prefix = f"{session_id}/"
                keys = [k for k in self._store._data if k.startswith(prefix)]
                for k in keys:
                    del self._store._data[k]
                if keys:
                    any_deleted = True
            if any_deleted:
                self._store._save()


_manager: "SessionStateManager | None" = None
_manager_lock = threading.Lock()


def get_session_manager(state_dir: "str | None" = None) -> SessionStateManager:
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = SessionStateManager(state_dir)
    return _manager


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


def _reset_for_testing():
    """Reset module-level singletons. For use in test fixtures ONLY."""
    global _store, _manager
    _store = None
    _manager = None
