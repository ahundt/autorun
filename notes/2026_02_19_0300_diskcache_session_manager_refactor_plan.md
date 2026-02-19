# diskcache Session Manager Refactor Plan

**Date**: 2026-02-19
**Branch**: `feature/gemini-cli-integration`
**Status**: PLANNING — not yet implemented

---

## Problem Statement

The daemon (`clautorun`) gets permanently stuck at 98.5% CPU after handling
`PostToolUse(ExitPlanMode)`, blocking the asyncio event loop and causing all
subsequent hook connections to time out (4 failing E2E tests).

### Root Causes (3)

1. **`shelve` is not thread/process-safe** — `session_manager.py`'s 420-line
   custom `fcntl.flock()` implementation is the source of CPU spin, FD leaks,
   and re-entrance bugs. On macOS, `flock()` via a different fd from the same
   process returns `errno=35 (EAGAIN)` even with `_lock_registry` protection.

2. **Synchronous dispatch blocks asyncio** — `core.py:handle_client():1184`
   calls `self.app.dispatch(ctx)` synchronously inside `async def`. Any hung
   handler permanently freezes the entire daemon event loop.

3. **Triple `session_state()` opens in `plan_export.py:export()`** — 3 separate
   acquire/release cycles (one for `self.tracking` check, one for
   `atomic_update_tracking()`, one for `atomic_update_active_plans()`)
   maximizes lock contention and creates 3 separate race windows.

---

## Solution

### 1. Replace `shelve` + `fcntl.flock()` with `diskcache`

`diskcache` is SQLite-backed and genuinely multiprocess+thread-safe without any
custom locking code. All `fcntl.flock()`, `_lock_registry`, `threading.Lock`,
and retry loops are deleted. ~400 lines → ~80 lines.

**Preserve full public API** so all 8+ callers need zero changes:
- `session_state(session_id)` contextmanager
- `shared_session_state(session_id)` contextmanager
- `SessionTimeoutError`, `SessionStateError` exceptions
- `SessionLock` class (still importable from plan_export.py)
- `get_session_manager()` function
- `DEFAULT_SESSION_TIMEOUT`, `SHARED_ACCESS_TIMEOUT` constants
- `clear_test_session_state()` function

### 2. Non-blocking dispatch in `core.py:handle_client()`

Replace synchronous `self.app.dispatch(ctx)` with
`asyncio.wait_for(loop.run_in_executor(None, self.app.dispatch, ctx), timeout=15.0)`.
A hung handler now blocks only one thread-pool thread for ≤15s instead of
permanently freezing the entire daemon.

### 3. Atomic export in `plan_export.py:export()`

Collapse the triple `session_state()` open into a single `with cache.transact():`
block so the read-modify-write is atomic and contention is minimized.

---

## Files to Change

| File | Change | Lines affected |
|------|--------|----------------|
| `plugins/clautorun/pyproject.toml` | Add `diskcache>=5.6.1` to `[project] dependencies` | `[project] dependencies` table |
| `plugins/clautorun/src/clautorun/session_manager.py` | Replace entire file with diskcache-backed impl (~80 lines) preserving full API | All ~420 lines |
| `plugins/clautorun/src/clautorun/core.py` | Replace synchronous dispatch with `run_in_executor` + `wait_for` | `handle_client():~1184` |
| `plugins/clautorun/src/clautorun/plan_export.py` | Collapse triple `session_state()` opens into single `cache.transact()` | `active_plans`:406-412, `atomic_update_active_plans`:414-419, `tracking`:421-427, `atomic_update_tracking`:429-434, `export`:671-745 |

---

## BEFORE / AFTER Code Sketches

### 1. `pyproject.toml`

**BEFORE:**
```toml
[project]
dependencies = [
    "claude-agent-sdk>=0.1.4",
    "ruff>=0.14.1",
    "bashlex>=0.18",
    "psutil",
]
```

**AFTER:**
```toml
[project]
dependencies = [
    "claude-agent-sdk>=0.1.4",
    "ruff>=0.14.1",
    "bashlex>=0.18",
    "psutil",
    "diskcache>=5.6.1",
]
```

---

### 2. `session_manager.py` — full replacement

**BEFORE (problem):**
```python
# ~420 lines:
# - SessionLock class with fcntl.flock(LOCK_EX | LOCK_NB)
# - _lock_registry: dict keyed by (pid, thread_id) for reentrant detection
# - MAX_LOCK_RETRIES = 100, LOCK_RETRY_DELAY = 0.01 (spin-wait loop)
# - session_state() contextmanager: acquires flock → opens shelve → yields → closes

# The spin-wait is the CPU culprit; macOS flock non-reentrance causes EAGAIN
# even with _lock_registry, and the shelve FD is never released on hang.
```

**AFTER (~80 lines):**
```python
"""session_manager.py — diskcache-backed session state (replaces shelve + fcntl)."""
import contextlib
import os
import diskcache

DEFAULT_SESSION_TIMEOUT = 30.0
SHARED_ACCESS_TIMEOUT = 5.0

class SessionTimeoutError(Exception):
    pass

class SessionStateError(Exception):
    pass

def _get_state_dir() -> str:
    return os.path.expanduser("~/.claude/sessions")

def _get_cache(state_dir: str) -> diskcache.Cache:
    """One shared Cache per process; diskcache handles all locking internally."""
    os.makedirs(state_dir, exist_ok=True)
    return diskcache.Cache(state_dir)

class _DictProxy:
    """dict-like wrapper around a diskcache.Cache namespace for one session_id."""
    def __init__(self, cache: diskcache.Cache, session_id: str):
        self._cache = cache
        self._prefix = f"{session_id}/"

    def _key(self, k: str) -> str:
        return f"{self._prefix}{k}"

    def get(self, k: str, default=None):
        return self._cache.get(self._key(k), default=default)

    def __getitem__(self, k: str):
        v = self._cache.get(self._key(k))
        if v is None:
            raise KeyError(k)
        return v

    def __setitem__(self, k: str, v):
        self._cache.set(self._key(k), v)

    def __contains__(self, k: str) -> bool:
        return self._key(k) in self._cache

    def sync(self):
        pass  # diskcache writes immediately; no-op for API compat

    def close(self):
        pass  # shared cache; closing managed elsewhere

class SessionLock:
    """No-op replacement; diskcache handles concurrency internally."""
    def __init__(self, session_id: str, timeout: float = DEFAULT_SESSION_TIMEOUT,
                 state_dir: str | None = None):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass

class SessionStateManager:
    def __init__(self, state_dir: str | None = None):
        self._state_dir = state_dir or _get_state_dir()
        self._cache = _get_cache(self._state_dir)

    @contextlib.contextmanager
    def session_state(self, session_id: str,
                      timeout: float = DEFAULT_SESSION_TIMEOUT):
        proxy = _DictProxy(self._cache, session_id)
        try:
            yield proxy
        finally:
            pass  # no lock to release

    @contextlib.contextmanager
    def shared_session_state(self, session_id: str,
                              timeout: float = SHARED_ACCESS_TIMEOUT):
        yield from self.session_state(session_id, timeout)

    def clear_test_session(self, session_id: str):
        prefix = f"{session_id}/"
        for key in list(self._cache):
            if isinstance(key, str) and key.startswith(prefix):
                del self._cache[key]

_manager: SessionStateManager | None = None

def get_session_manager(state_dir: str | None = None) -> SessionStateManager:
    global _manager
    if _manager is None:
        _manager = SessionStateManager(state_dir)
    return _manager

@contextlib.contextmanager
def session_state(session_id: str, timeout: float = DEFAULT_SESSION_TIMEOUT,
                  state_dir: str | None = None):
    mgr = get_session_manager(state_dir)
    with mgr.session_state(session_id, timeout) as s:
        yield s

@contextlib.contextmanager
def shared_session_state(session_id: str, timeout: float = SHARED_ACCESS_TIMEOUT,
                          state_dir: str | None = None):
    mgr = get_session_manager(state_dir)
    with mgr.shared_session_state(session_id, timeout) as s:
        yield s

def clear_test_session_state(session_id: str, state_dir: str | None = None):
    mgr = get_session_manager(state_dir)
    mgr.clear_test_session(session_id)
```

---

### 3. `core.py:handle_client()` — non-blocking dispatch

**BEFORE (`core.py:handle_client():~1184`):**
```python
async def handle_client(self, reader, writer):
    # ... reads payload, builds ctx ...
    response = self.app.dispatch(ctx)  # SYNCHRONOUS — blocks event loop forever if handler hangs
    # ... finally: await writer.drain(), writer.close() ...
```

**AFTER:**
```python
import asyncio

async def handle_client(self, reader, writer):
    # ... reads payload, builds ctx ...
    loop = asyncio.get_running_loop()
    try:
        response = await asyncio.wait_for(
            loop.run_in_executor(None, self.app.dispatch, ctx),
            timeout=15.0,
        )
    except asyncio.TimeoutError:
        self.app.logger.error(
            f"Handler for '{ctx.event_type}' timed out after 15s"
        )
        response = {"error": "Hook handler timed out", "status": "timeout"}
    except Exception as e:
        self.app.logger.exception(
            f"Handler for '{ctx.event_type}' raised an exception"
        )
        response = {"error": str(e), "status": "handler_exception"}
    # ... finally: await writer.drain(), writer.close() ...
```

**Caveat**: `asyncio.wait_for` cancels the `Future` but does NOT kill the
background thread. The thread continues to run to completion. This is acceptable:
the daemon is unblocked, the timeout is logged, the hung thread eventually exits
or is reaped when the process ends.

---

### 4. `plan_export.py:export()` — atomic triple-open → single transaction

**BEFORE (plan_export.py:export():671-745 — three separate opens):**
```python
def export(self, plan_path, rejected=False, force=False):
    content_hash = get_content_hash(plan_path)
    if content_hash in self.tracking:          # 1st session_state open+close
        return {"success": True, "skipped": True, ...}
    # ... file copy logic ...
    self.atomic_update_tracking(record_hash)   # 2nd session_state open+close
    self.atomic_update_active_plans(remove)    # 3rd session_state open+close
```

**AFTER (single open):**
```python
def export(self, plan_path, rejected=False, force=False):
    content_hash = get_content_hash(plan_path)
    with session_state(GLOBAL_SESSION_ID) as state:
        tracking = state.get("tracking", {})
        if content_hash in tracking and not force:
            return {"success": True, "skipped": True, ...}

        # ... file copy logic (outside state write, before committing) ...

        # Atomic update both dicts in one open
        new_tracking = dict(tracking)
        new_tracking[content_hash] = _make_tracking_record(...)
        state["tracking"] = new_tracking

        active_plans = state.get("active_plans", {})
        proj = active_plans.get(self.project_key, {})
        if str(plan_path) in proj:
            del proj[str(plan_path)]
            active_plans[self.project_key] = proj
            state["active_plans"] = active_plans

    return {"success": True, "skipped": False}
```

---

## Session State Callers (all must keep working after refactor)

All callers use the `session_state(session_id)` contextmanager API unchanged:

| File | session_id used | Usage count |
|------|----------------|-------------|
| `plan_export.py` | `GLOBAL_SESSION_ID` | 8 usages |
| `plugins.py` | `"__global__"` | 2 usages |
| `task_lifecycle.py` | `self.global_key` | ~8 usages |
| `core.py` | `session_id` (per-session) | 3 usages |
| `main.py` | `session_id` (per-session) | 8 usages |
| `__main__.py` | session-scoped | 5 usages |

None of these need code changes — they keep calling `session_state()` identically.

---

## Data Migration

The old `shelve` DBM files live at `~/.claude/sessions/plugin___plan_export__.db`
(and `.db.dir`, `.db.bak` on some platforms). The new `diskcache` writes to
`~/.claude/sessions/` as a SQLite file (`cache.db`).

**Migration strategy**: No automated migration. On first run with the new code,
`active_plans` and `tracking` start empty. Any plans tracked under the old shelve
are treated as untracked. On the next ExitPlanMode they will be re-exported
(content-hash dedup prevents duplicates for plans already in `notes/`).

If the old data is critical: a one-time migration script can be added later.

---

## Test Plan

### Existing tests that must continue passing

```bash
# Full suite — no regressions
uv run pytest plugins/clautorun/tests/ -v --tb=short

# Targeted: session state tests
uv run pytest plugins/clautorun/tests/ -k "session" -v

# Targeted: plan export tests
uv run pytest plugins/clautorun/tests/test_plan_export_class.py -v

# Targeted: 4 failing E2E tests (should pass after fix)
uv run pytest plugins/clautorun/tests/test_claude_e2e_real_money.py -v --timeout=30
```

### New tests to add (if not already present)

1. `test_session_manager.py::TestDiskCache` — verify `session_state()` API works
   identically with diskcache backend (get/set/contains/sync/close no-ops)
2. `test_session_manager.py::TestConcurrency` — spawn 5 processes, all write
   simultaneously, verify no data corruption
3. `test_core.py::TestHandleClientTimeout` — mock `dispatch()` to sleep 20s,
   verify `handle_client()` returns within 16s with timeout error response

---

## Execution Steps

1. Kill stuck daemon: `kill -9 10130` (PID from `~/.clautorun/daemon.lock`)
2. Edit `plugins/clautorun/pyproject.toml` — add `diskcache>=5.6.1`
3. Replace `plugins/clautorun/src/clautorun/session_manager.py` with new impl
4. Edit `plugins/clautorun/src/clautorun/core.py:handle_client()` (~line 1184)
5. Edit `plugins/clautorun/src/clautorun/plan_export.py:export()` (lines 671-745)
6. Install and restart:
   ```bash
   uv run --project plugins/clautorun python -m clautorun --install --force && \
     cd plugins/clautorun && uv tool install --force --editable . && \
     cd ../.. && clautorun --restart-daemon
   ```
7. Run failing tests: `uv run pytest plugins/clautorun/tests/test_claude_e2e_real_money.py -v`
8. Run full suite: `uv run pytest plugins/clautorun/tests/ -v --tb=short`
9. Commit

---

## References

- Gemini consultation (diskcache recommendation): `notes/2026_02_19_0254_gemini_session_manager_review.json`
- Gemini consultation (sqlite3 WAL research): `notes/2026_02_19_0253_storage_architecture_review.json`
- Root cause analysis: `notes/2026_02_19_0223_daemon_lock_leak_exitplanmode_timeout_root_cause.md`
- diskcache docs: https://grantjenks.com/docs/diskcache/
- asyncio.run_in_executor: https://docs.python.org/3/library/asyncio-eventloop.html#asyncio.loop.run_in_executor
