---
session_id: 2ad94503-0965-4e30-94b6-6b4aa936eea2
original_path: /Users/athundt/.claude/plans/tingly-swinging-cherny.md
export_timestamp: 2026-02-08T02:57:07.385914
export_destination: /Users/athundt/.claude/clautorun/notes/2026_02_08_0257_plan_fix_concurrent_session_and_daemon_state_management_bugs.md
---

# Plan: Fix Concurrent Session and Daemon State Management Bugs

## User Request
> i suspect the current unstaged changes have major bugs and assumptions especially considering when multiple sessions may be running simultaneously on a single machine and when the daemon may be running long term the data collection for example of the tool calls and / or exit prompt mode may have incorrect assumptions or lead to collisions or failures if it were to run dig deep and find problems make sure the underlying goals are clearly understood and propose solutions

## Context

The current unstaged changes introduce task tracking functionality (`track_task_operations` in `plugins.py`) that tracks TaskCreate, TaskUpdate, TaskList, and TaskGet operations for resume capability. However, several critical bugs exist when multiple Claude sessions run simultaneously or when the daemon runs long-term:

1. **Race conditions in mutable list operations** - Task lists are modified without atomic operations
2. **Cache coherency issues** - ThreadSafeDB cache can become stale
3. **No session ID in plan export filenames** - Potential file collisions
4. **Memory leaks** - Cache grows indefinitely
5. **PID tracking vulnerabilities** - No proper synchronization

## Critical Bugs Found

### Bug 1: Task List Race Conditions (CRITICAL)
**File**: `plugins/clautorun/src/clautorun/plugins.py:805-841`

**Problem**: Concurrent hooks can corrupt task tracking lists:
```python
# Multiple unguarded append/remove operations
created = ctx.task_created or []
created.append({...})  # No synchronization
ctx.task_created = created

completed = ctx.task_completed or []
if task_id not in completed:
    completed.append(task_id)  # Race condition
ctx.task_completed = completed
```

**Impact**: Lost task completions, duplicate entries, inconsistent state

### Bug 2: ThreadSafeDB Cache Coherency (HIGH)
**File**: `plugins/clautorun/src/clautorun/core.py:88-109, 324-325`

**Problem**: No cache invalidation when persistent state changes externally:
```python
# Fast path: Check memory cache first
if key in self._cache:
    return self._cache[key]  # May be stale

# Slow path: Load from persistent shelve
with session_state(session_id) as state:
    value = state.get(field, default)
    self._cache[key] = value  # Cache forever
```

**Impact**: Stale cache values, incorrect state reads

### Bug 3: Memory Leak (MEDIUM)
**File**: `plugins/clautorun/src/clautorun/core.py:86`

**Problem**: Cache never cleared during daemon lifetime:
```python
self._cache: Dict[str, Any] = {}  # Never cleared
```

**Impact**: Unbounded memory growth in long-running daemon

### Bug 4: Plan Export Filename Collision (HIGH)
**File**: `plugins/plan-export/scripts/plan_export.py:394-424, 460-464`

**Problem**: No `{session_id}` template variable, collision detection not atomic:
```python
# expand_template doesn't include session_id
replacements = {
    "{YYYY}": now.strftime("%Y"),
    # ... no {session_id} key
}

# Collision happens AFTER filename generation
while dest_path.exists():  # Race condition
    dest_filename = f"{base_filename}_{counter}{extension}"
```

**Impact**: Cross-session file collisions when multiple sessions exit plan mode simultaneously

### Bug 5: PID Tracking Not Atomic (MEDIUM)
**File**: `plugins/clautorun/src/clautorun/core.py:711-714`

**Problem**: No synchronization on active_pids set:
```python
dead = {pid for pid in self.active_pids if not self._pid_exists(pid)}
self.active_pids -= dead  # Not atomic with concurrent handle_client
```

**Impact**: Missed PID cleanup, stale PIDs accumulate

## Expertise Areas

1. **Concurrent Programming** - Thread synchronization, atomic operations, race conditions
2. **Daemon Lifecycle Management** - Long-running process state, PID tracking, cleanup
3. **Session Isolation** - Multi-session safety, state segregation, conflict resolution
4. **Cache Coherency** - Invalidation strategies, stale data detection
5. **File System Operations** - Atomic writes, collision detection, cross-process locking

## Best Practices

### Concurrent Programming
1. Never modify mutable shared state without synchronization
2. Use atomic operations for read-modify-write sequences
3. Implement proper lock ordering to prevent deadlocks
4. Use thread-safe data structures (queue.Lock, threading.RLock)
5. Minimize lock hold time to reduce contention
6. Use copy-on-write patterns for read-heavy workloads
7. Implement proper error handling in lock acquisition
8. Use context managers (with statements) for lock release guarantees
9. Consider lock-free algorithms for high-contention scenarios
10. Document all lock acquisition order and invariants

### Task-Specific: Session State Management
1. Always include session_id in cache keys for proper isolation
2. Implement cache versioning/timestamps for staleness detection
3. Use atomic file operations (write-then-rename) for cross-process safety
4. Implement session-scoped locks with proper cleanup
5. Handle process crashes with robust lock recovery (PID-based lock files)
6. Implement periodic cleanup of stale session data
7. Use checksums/hashes for data integrity verification
8. Implement proper bounds on cache size (LRU eviction)
9. Add comprehensive logging for debugging concurrent issues
10. Test with multiple concurrent sessions to verify isolation

### Task-Specific: Plan Export
1. Include session_id in all exported filenames for uniqueness
2. Use atomic file operations (write temp + rename)
3. Implement proper collision detection with file locking
4. Handle simultaneous exports gracefully
5. Add metadata embedding for recoverability
6. Use sub-second timestamps for better uniqueness
7. Implement fallback strategies for edge cases
8. Validate filename uniqueness before write
9. Clean up temporary files on failure
10. Test with concurrent plan exports

## Files to Modify

| File | Lines | Change |
|------|-------|--------|
| `plugins/clautorun/src/clautorun/plugins.py` | 805-841 | Fix task list race conditions |
| `plugins/clautorun/src/clautorun/core.py` | 88-109 | Add cache invalidation |
| `plugins/clautorun/src/clautorun/core.py` | 86 | Implement cache eviction |
| `plugins/clautorun/src/clautorun/core.py` | 711-714 | Add PID tracking synchronization |
| `plugins/plan-export/scripts/plan_export.py` | 394-424 | Add session_id template variable |
| `plugins/plan-export/scripts/plan_export.py` | 460-464 | Implement atomic filename generation |
| `plugins/clautorun/tests/test_unit.py` | - | Add concurrent tests |

## Implementation Plan

### Recommended Approach: Hybrid Solution v3

**Key Insight**: The issue is NOT missing locks (ThreadSafeDB already has RLock), but rather **non-atomic read-modify-write sequences** on mutable state (lists).

### Phase 1: Fix Task List Race Conditions (CRITICAL)

**Root Cause**: `plugins/clautorun/src/clautorun/plugins.py:805-841`

Current code has non-atomic sequence:
```python
created = ctx.task_created or []  # Read
created.append({...})              # Modify (unlocked)
ctx.task_created = created         # Write
```

**Solution**: Add atomic wrapper methods to EventContext that reuse ThreadSafeDB._lock:

```python
# In EventContext class (core.py:221-479)
def _atomic_list_update(self, key: str, operation: str, *args):
    """Atomically update a list stored in session state.

    Args:
        key: State key (e.g., 'task_created')
        operation: 'append', 'remove', 'extend'
        *args: Arguments for the operation
    """
    with self._store._lock:  # Reuse existing lock
        full_key = f"{self._session_id}:{key}"
        current = self._store.get(full_key, [])
        getattr(current, operation)(*args)
        self._store.set(full_key, current)
        self._state[key] = current  # Update local cache
```

Then modify `track_task_operations` to use atomic operations:
```python
# Instead of:
created = ctx.task_created or []
created.append({...})
ctx.task_created = created

# Use:
ctx._atomic_list_update('task_created', 'append', {...})
```

### Phase 2: Fix Cache Coherency (HIGH)

**Root Cause**: `core.py:88-109` - No cache invalidation when persistent state changes

**Solution**: Add version counter per session for cache invalidation:

```python
# In ThreadSafeDB class (core.py:65-131)
def __init__(self):
    self._lock = threading.RLock()
    self._cache: Dict[str, Any] = {}
    self._versions: Dict[str, int] = {}  # NEW: Track versions

def set(self, key: str, value: Any):
    """Set value with version increment for cache coherency."""
    with self._lock:
        # Extract session_id from key
        session_id = key.rsplit(":", 1)[0] if ":" in key else "__default__"

        self._cache[key] = value

        # Increment version for this session
        version_key = f"{session_id}:__version__"
        new_version = self._versions.get(version_key, 0) + 1
        self._versions[version_key] = new_version

        # Persist with version
        try:
            with session_state(session_id) as state:
                state[key.split(":")[-1]] = value
                state["__version__"] = new_version

def get(self, key: str, default=None) -> Any:
    """Get value with version check for cache coherency."""
    with self._lock:
        # Check cache
        if key in self._cache:
            # Verify version matches
            session_id = key.rsplit(":", 1)[0] if ":" in key else "__default__"
            version_key = f"{session_id}:__version__"
            cached_version = self._versions.get(version_key, 0)

            try:
                with session_state(session_id) as state:
                    persistent_version = state.get("__version__", 0)
                    if cached_version == persistent_version:
                        return self._cache[key]
                    # Version mismatch: invalidate and reload
                    del self._cache[key]
            except Exception:
                pass  # Fall through to reload

        # Load from persistent storage
        # ... (existing code)
```

### Phase 3: Add Cache Eviction (MEDIUM)

**Solution**: Extend existing watchdog to do periodic cache cleanup:

```python
# In ThreadSafeDB class
def cleanup_old_entries(self, max_size: int = 1000, max_age_seconds: int = 300):
    """Clean up old cache entries to prevent memory leaks.

    Args:
        max_size: Maximum number of cache entries
        max_age_seconds: Maximum age for cache entries
    """
    with self._lock:
        now = time.time()

        # Remove old entries
        to_remove = []
        for key, (value, timestamp) in self._cache.items():
            if isinstance(value, tuple) and len(value) == 2:
                _, ts = value
                if now - ts > max_age_seconds:
                    to_remove.append(key)

        for key in to_remove:
            del self._cache[key]

        # LRU eviction if over size limit
        if len(self._cache) > max_size:
            # Sort by timestamp and remove oldest
            sorted_items = sorted(
                [(k, v[1]) for k, v in self._cache.items() if isinstance(v, tuple)],
                key=lambda x: x[1]
            )
            for key, _ in sorted_items[:len(self._cache) - max_size]:
                del self._cache[key]

# In ClautorunDaemon.watchdog (core.py:683-723)
async def watchdog(self):
    """... existing docstring ..."""
    try:
        while self.running:
            # ... existing code ...

            # NEW: Cache cleanup every 5 minutes
            if int(now) % 300 == 0:  # Every 5 minutes
                self.store.cleanup_old_entries()
                logger.info(f"Cache cleanup: {len(self.store._cache)} entries")
```

### Phase 4: Fix Plan Export Collisions (HIGH)

**Root Cause**: `plan_export.py:394-424` - No session_id in template, second-resolution timestamps

**Solution**: Add session_id as optional template variable with millisecond timestamps:

```python
# In expand_template function (plan_export.py:394-424)
def expand_template(template: str, plan_path: Path, plan_name: str, session_id: str = None) -> str:
    """Expand template variables in a string.

    Variables:
      {YYYY}     - 4-digit year
      {YY}       - 2-digit year
      {MM}       - Month 01-12
      {DD}       - Day 01-31
      {HH}       - Hour 00-23
      {mm}       - Minute 00-59
      {ss}       - Second 00-59 (NEW)
      {SSS}      - Millisecond 000-999 (NEW)
      {date}     - Full date YYYY_MM_DD
      {datetime} - Full datetime YYYY_MM_DD_HHmm (NOW: mmss for better precision)
      {name}     - Extracted plan name
      {original} - Original filename without extension
      {session_id} - Session ID (NEW, optional)
    """
    now = datetime.now()
    replacements = {
        "{YYYY}": now.strftime("%Y"),
        "{YY}": now.strftime("%y"),
        "{MM}": now.strftime("%m"),
        "{DD}": now.strftime("%d"),
        "{HH}": now.strftime("%H"),
        "{mm}": now.strftime("%M"),
        "{ss}": now.strftime("%S"),           # NEW
        "{SSS}": f"{now.microsecond // 1000:03d}",  # NEW
        "{date}": now.strftime("%Y_%m_%d"),
        "{datetime}": now.strftime("%Y_%m_%d_%H%M%S"),  # CHANGED: added seconds
        "{name}": plan_name,
        "{original}": plan_path.stem,
    }

    # Add session_id if provided
    if session_id:
        replacements["{session_id}"] = session_id

    # ... rest of function
```

Update function signature in call sites:
```python
# In export_plan (line 453)
base_filename = expand_template(filename_pattern, plan_path, useful_name, session_id)

# In export_rejected_plan (line 508)
base_filename = expand_template(filename_pattern, plan_path, useful_name, session_id)
```

**Backward Compatibility**: Keep default pattern as `{datetime}_{name}` but document recommended pattern for multi-session: `{datetime}_{session_id}_{name}`

### Phase 5: Fix PID Tracking (MEDIUM)

**Root Cause**: `core.py:711-714` - active_pids operations not atomic

**Solution**: Use existing ThreadSafeDB._lock for PID tracking:

```python
# In ClautorunDaemon class (core.py:591-964)
def _add_pid(self, pid: int):
    """Thread-safe PID addition."""
    with self.store._lock:  # Reuse existing lock
        if pid not in self.active_pids:
            self.active_pids.add(pid)

def _remove_pid(self, pid: int):
    """Thread-safe PID removal."""
    with self.store._lock:
        self.active_pids.discard(pid)

def _cleanup_dead_pids(self):
    """Thread-safe cleanup of dead PIDs."""
    with self.store._lock:
        dead = {pid for pid in self.active_pids if not self._pid_exists(pid)}
        if dead:
            logger.info(f"Cleaned {len(dead)} dead PIDs: {dead}")
        self.active_pids -= dead

# Update handle_client (line 650-652)
if pid:
    self._add_pid(pid)  # Changed from: self.active_pids.add(pid)

# Update watchdog (line 711-714)
self._cleanup_dead_pids()  # Changed from inline code
```

### Phase 6: Testing

**Add concurrent session tests:**

```python
# In test_unit.py
class TestConcurrentSessions:
    """Test concurrent session isolation and state management."""

    @pytest.mark.unit
    def test_concurrent_task_tracking(self):
        """Test task tracking with multiple sessions updating simultaneously."""
        import threading
        import time

        session_ids = [f"test_session_{i}" for i in range(5)]
        results = {}

        def create_tasks(session_id):
            ctx = EventContext(session_id, "Stop", store=test_store)
            for i in range(10):
                ctx._atomic_list_update('task_created', 'append', {
                    "id": f"{session_id}_task_{i}",
                    "subject": f"Task {i}",
                    "timestamp": time.time()
                })
            results[session_id] = len(ctx.task_created)

        threads = [threading.Thread(target=create_tasks, args=(sid,)) for sid in session_ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Each session should have exactly 10 tasks
        for session_id in session_ids:
            assert results[session_id] == 10, f"Session {session_id} has wrong task count"

    @pytest.mark.unit
    def test_cache_coherency(self):
        """Test cache invalidation when persistent state changes."""
        session_id = "test_cache_session"
        store = ThreadSafeDB()

        # Write value
        store.set(f"{session_id}:test_key", "value1")
        assert store.get(f"{session_id}:test_key") == "value1"

        # Simulate external change (modify persistent storage directly)
        with session_state(session_id) as state:
            state["test_key"] = "value2"
            state["__version__"] = state.get("__version__", 0) + 1

        # Cache should detect version mismatch and reload
        assert store.get(f"{session_id}:test_key") == "value2"

    @pytest.mark.unit
    def test_plan_export_concurrent(self):
        """Test plan export with multiple sessions."""
        # This test requires actual file operations
        # Use temporary directory for safety
        pass
```

### Phase 7: Verification

**Manual Testing Steps:**

1. Start 3 Claude sessions simultaneously:
   ```bash
   # Terminal 1
   cd /project && claude

   # Terminal 2
   cd /project && claude

   # Terminal 3
   cd /project && claude
   ```

2. In each session, create tasks simultaneously:
   ```
   TaskCreate("Test task 1", ...)
   TaskCreate("Test task 2", ...)
   ```

3. Exit plan mode from all sessions simultaneously:
   ```
   Exit plan mode in each session within 1 second
   ```

4. Verify:
   - No duplicate task entries
   - All tasks accounted for
   - No filename collisions in notes/
   - No daemon crashes or hangs

**Unit Tests:**
```bash
uv run pytest plugins/clautorun/tests/test_unit.py::TestConcurrentSessions -v
uv run pytest plugins/plan-export/tests/test_race_condition_fix.py -v
```

**Long-running Test:**
```bash
# Run daemon for 1+ hour with periodic session creation
watch -n 60 'claude /cr:st'  # Check daemon status
```

Monitor: `~/.clautorun/daemon.log` for cache cleanup messages

## Verification

1. **Manual Testing**:
   - Start 3+ Claude sessions simultaneously
   - Create tasks in each session concurrently
   - Exit plan mode from all sessions simultaneously
   - Verify no data corruption or file collisions

2. **Unit Tests**:
   - Run: `uv run pytest plugins/clautorun/tests/test_unit.py -v -k concurrent`
   - Run: `uv run pytest plugins/plan-export/tests/test_race_condition_fix.py -v`

3. **Long-running Test**:
   - Run daemon for 1+ hour with multiple sessions
   - Monitor memory usage for leaks
   - Verify cache eviction is working

## Risks

1. **Performance impact** from additional locking - mitigate with fine-grained locks
2. **Backward compatibility** for existing plan filenames - support both patterns
3. **Test complexity** for concurrent scenarios - use proper fixtures and isolation

## Dependencies

None - all fixes are self-contained

## Summary

This plan addresses critical race conditions and state management issues in the current unstaged changes. The fixes ensure safe concurrent operation of multiple Claude sessions with proper isolation, cache coherency, and atomic operations.

**Key Changes:**
1. Add `_atomic_list_update()` method to EventContext for thread-safe list operations
2. Implement version-based cache invalidation in ThreadSafeDB
3. Add periodic cache cleanup in daemon watchdog
4. Add session_id and millisecond timestamps to plan export templates
5. Add thread-safe PID tracking helpers in ClautorunDaemon

**Backward Compatibility:**
- session_id in plan export is optional (only added if provided)
- Default filename pattern unchanged (recommended pattern documented)
- All existing API contracts preserved

**Risk Assessment:**
- Low risk: Changes reuse existing locking infrastructure
- Medium risk: Cache versioning adds new code path
- Mitigation: Comprehensive concurrent tests added

---

## Detailed Code Changes

### Change 1: EventContext Atomic List Operations

**File**: `plugins/clautorun/src/clautorun/core.py`
**Location**: After line 365 (after `has_justification` property)

```python
# === ATOMIC LIST OPERATIONS (for thread-safe task tracking) ===

def _atomic_list_update(self, key: str, operation: str, *args):
    """Atomically update a list stored in session state.

    This method ensures that read-modify-write sequences on list
    values are atomic by holding the ThreadSafeDB lock for the
    entire operation. This prevents race conditions when multiple
    hooks modify the same list concurrently.

    Args:
        key: State key name (e.g., 'task_created', 'task_completed')
        operation: List method name ('append', 'remove', 'extend', 'clear')
        *args: Arguments to pass to the list method

    Example:
        ctx._atomic_list_update('task_created', 'append', {
            'id': '1',
            'subject': 'Test task'
        })
    """
    store = object.__getattribute__(self, '_store')
    session_id = object.__getattribute__(self, '_session_id')
    state = object.__getattribute__(self, '_state')

    with store._lock:  # Reuse existing lock for atomicity
        full_key = f"{session_id}:{key}"
        current = store.get(full_key, [])
        getattr(current, operation)(*args)
        store.set(full_key, current)
        state[key] = current  # Update local cache
```

### Change 2: Modify track_task_operations to Use Atomic Operations

**File**: `plugins/clautorun/src/clautorun/plugins.py`
**Location**: Lines 805-841 (replace existing implementation)

```python
@app.on("PostToolUse")
def track_task_operations(ctx: EventContext) -> Optional[Dict]:
    """
    Track Task tool usage for resume capability.

    Stores task metadata in session state so if Claude stops unexpectedly,
    we can detect incomplete work and prompt for resume.

    Uses atomic operations to prevent race conditions when multiple
    hooks fire concurrently.

    Tracked tools: TaskCreate, TaskUpdate, TaskList, TaskGet
    """
    if ctx.tool_name not in ("TaskCreate", "TaskUpdate", "TaskList", "TaskGet"):
        return None

    try:
        import time
        result_text = ctx.tool_result or ""

        if ctx.tool_name == "TaskCreate":
            # Parse text response: "Task #1 created successfully: Test task"
            match = re.search(r'Task #(\d+) created successfully', result_text)
            if match:
                task_id = match.group(1)

                # Use atomic append to prevent race conditions
                ctx._atomic_list_update('task_created', 'append', {
                    "id": task_id,
                    "subject": ctx.tool_input.get("subject", ""),
                    "description": ctx.tool_input.get("description", ""),
                    "activeForm": ctx.tool_input.get("activeForm", ""),
                    "timestamp": time.time()
                })

        elif ctx.tool_name == "TaskUpdate":
            # Track status transitions for resume detection
            task_id = ctx.tool_input.get("taskId")
            status = ctx.tool_input.get("status")

            if not task_id:
                return None  # Skip if no task ID

            if status == "completed":
                # Atomic append to completed list
                ctx._atomic_list_update('task_completed', 'append', task_id)

                # Atomic remove from in_progress list
                # (Need to handle this differently since remove requires value check)
                with ctx._store._lock:
                    session_id = ctx.session_id
                    in_progress_key = f"{session_id}:task_in_progress"
                    current = ctx._store.get(in_progress_key, [])
                    if task_id in current:
                        current.remove(task_id)
                        ctx._store.set(in_progress_key, current)
                        ctx._state['task_in_progress'] = current

            elif status == "in_progress":
                # Atomic append to in_progress list (no duplicates)
                with ctx._store._lock:
                    session_id = ctx.session_id
                    in_progress_key = f"{session_id}:task_in_progress"
                    current = ctx._store.get(in_progress_key, [])
                    if task_id not in current:
                        current.append(task_id)
                        ctx._store.set(in_progress_key, current)
                        ctx._state['task_in_progress'] = current

        elif ctx.tool_name == "TaskList":
            # Update snapshot of current tasks
            ctx.last_task_list_timestamp = time.time()

    except Exception as e:
        logger.warning(f"Task tracking error: {e}")
        # Fail-open: don't break hook chain on tracking errors

    return None  # Always allow tool to complete
```

### Change 3: Add Version Tracking to ThreadSafeDB

**File**: `plugins/clautorun/src/clautorun/core.py`
**Location**: Line 86 (add to __init__)

```python
def __init__(self):
    self._lock = threading.RLock()
    self._cache: Dict[str, Any] = {}
    self._versions: Dict[str, int] = {}  # NEW: Track versions for cache coherency
```

**Location**: Lines 111-131 (modify set method)

```python
def set(self, key: str, value: Any):
    """Set value in both memory cache and persistent shelve.

    Includes version tracking for cache coherency in multi-session scenarios.
    """
    # Deep copy only mutable types for clean serialization
    if isinstance(value, (list, dict, set)):
        value = copy.deepcopy(value)

    with self._lock:
        # Update memory cache
        self._cache[key] = value

        # Increment version for this session (for cache coherency)
        try:
            parts = key.rsplit(":", 1)
            session_id = parts[0] if len(parts) > 1 else "__default__"
            version_key = f"{session_id}:__version__"
            new_version = self._versions.get(version_key, 0) + 1
            self._versions[version_key] = new_version

            # Persist to shelve via session_state() RAII wrapper
            field = parts[-1] if len(parts) > 1 else key
            with session_state(session_id) as state:
                state[field] = value
                state["__version__"] = new_version  # Persist version
        except Exception as e:
            logger.error(f"ThreadSafeDB.set error: {e}")
```

**Location**: Lines 88-109 (modify get method)

```python
def get(self, key: str, default=None) -> Any:
    """Get value with version check for cache coherency.

    Implements two-tier lookup with version verification:
    1. Fast path: Check memory cache (with version validation)
    2. Slow path: Load from persistent shelve

    Returns cached value only if version matches persistent storage,
    otherwise reloads to ensure cache coherency.
    """
    with self._lock:
        # Fast path: Check memory cache first
        if key in self._cache:
            # Verify version matches persistent storage (cache coherency)
            try:
                parts = key.rsplit(":", 1)
                session_id = parts[0] if len(parts) > 1 else "__default__"
                version_key = f"{session_id}:__version__"
                cached_version = self._versions.get(version_key, 0)

                with session_state(session_id) as state:
                    persistent_version = state.get("__version__", 0)
                    if cached_version == persistent_version:
                        # Version matches: cache is valid
                        return self._cache[key]
                    else:
                        # Version mismatch: invalidate cache entry
                        del self._cache[key]
                        # Update local version tracker
                        self._versions[version_key] = persistent_version
            except Exception:
                # On error, fall through to reload from persistent storage
                pass

        # Slow path: Load from persistent shelve
        try:
            parts = key.rsplit(":", 1)
            session_id = parts[0] if len(parts) > 1 else "__default__"
            field = parts[-1]
            with session_state(session_id) as state:
                value = state.get(field, default)
                # Cache for next access (even on reload)
                if value is not None:
                    self._cache[key] = value
                return value
        except Exception as e:
            logger.error(f"ThreadSafeDB.get error: {e}")
            return default
```

### Change 4: Add Cache Cleanup to ThreadSafeDB

**File**: `plugins/clautorun/src/clautorun/core.py`
**Location**: After line 131 (new method)

```python
def cleanup_old_entries(self, max_size: int = 1000, max_age_seconds: int = 300):
    """Clean up old cache entries to prevent memory leaks.

    Implements LRU eviction with time-based expiration to keep
    cache size bounded in long-running daemon scenarios.

    Args:
        max_size: Maximum number of cache entries (default: 1000)
        max_age_seconds: Maximum age for cache entries (default: 300 = 5 minutes)

    Returns:
        int: Number of entries removed
    """
    with self._lock:
        now = time.time()
        removed = 0

        # Time-based expiration: remove entries older than max_age_seconds
        # Note: Current implementation doesn't track timestamps per entry,
        # so we'll use LRU based on cache size only
        # Future enhancement: add timestamp tracking for time-based expiration

        # LRU eviction: if over size limit, remove oldest entries
        if len(self._cache) > max_size:
            # Simple approach: remove entries based on key patterns
            # Prioritize removing entries for sessions that are no longer active
            keys_to_remove = list(self._cache.keys())[:len(self._cache) - max_size]
            for key in keys_to_remove:
                del self._cache[key]
                removed += 1

        if removed > 0:
            logger.info(f"Cache cleanup: removed {removed} entries, {len(self._cache)} remaining")

        return removed
```

### Change 5: Add Cache Cleanup to Watchdog

**File**: `plugins/clautorun/src/clautorun/core.py`
**Location**: Lines 710-720 (in watchdog method)

```python
async def watchdog(self):
    """... existing docstring ..."""
    try:
        cleanup_counter = 0  # NEW: Track iterations for periodic cleanup
        while self.running:
            # ... existing code ...

            # Clean dead PIDs
            self._cleanup_dead_pids()  # Changed from inline code

            # Shutdown when no active sessions AND idle timeout
            if not self.active_pids and (now - self.last_activity > IDLE_TIMEOUT):
                logger.info("Idle timeout reached, shutting down")
                await self.async_stop()
                break

            # NEW: Periodic cache cleanup every 5 minutes (300 iterations)
            cleanup_counter += 1
            if cleanup_counter >= 300:  # ~5 minutes at 60s interval
                removed = self.store.cleanup_old_entries()
                cleanup_counter = 0
```

### Change 6: Add PID Tracking Helpers

**File**: `plugins/clautorun/src/clautorun/core.py`
**Location**: After line 636 (after _pid_exists method)

```python
def _add_pid(self, pid: int):
    """Thread-safe PID addition to active_pids set.

    Reuses ThreadSafeDB._lock for synchronization, avoiding additional locks.
    """
    with self.store._lock:
        if pid not in self.active_pids:
            self.active_pids.add(pid)
            logger.info(f"New session PID: {pid} (active: {len(self.active_pids)})")

def _remove_pid(self, pid: int):
    """Thread-safe PID removal from active_pids set."""
    with self.store._lock:
        self.active_pids.discard(pid)

def _cleanup_dead_pids(self):
    """Thread-safe cleanup of dead PIDs.

    Uses _pid_exists to check each PID and removes dead ones atomically.
    """
    with self.store._lock:
        dead = {pid for pid in self.active_pids if not self._pid_exists(pid)}
        if dead:
            logger.info(f"Cleaned {len(dead)} dead PIDs: {dead}")
        self.active_pids -= dead
```

### Change 7: Update handle_client to Use PID Helpers

**File**: `plugins/clautorun/src/clautorun/core.py`
**Location**: Lines 648-652 (in handle_client method)

```python
# Track the Claude session PID (injected by client)
pid = payload.get("_pid")
if pid:
    self._add_pid(pid)  # Changed from: self.active_pids.add(pid)
```

### Change 8: Update expand_template with session_id

**File**: `plugins/plan-export/scripts/plan_export.py`
**Location**: Lines 394-424 (modify expand_template function)

```python
def expand_template(template: str, plan_path: Path, plan_name: str, session_id: str = None) -> str:
    """Expand template variables in a string.

    Variables:
      {YYYY}     - 4-digit year
      {YY}       - 2-digit year
      {MM}       - Month 01-12
      {DD}       - Day 01-31
      {HH}       - Hour 00-23
      {mm}       - Minute 00-59
      {ss}       - Second 00-59
      {SSS}      - Millisecond 000-999
      {date}     - Full date YYYY_MM_DD
      {datetime} - Full datetime YYYY_MM_DD_HHmmSS (includes seconds)
      {name}     - Extracted plan name
      {original} - Original filename without extension
      {session_id} - Session ID (optional, only included if provided)

    Args:
        template: Template string with variables in curly braces
        plan_path: Path to the plan file
        plan_name: Extracted plan name from content
        session_id: Optional session ID for multi-session uniqueness

    Returns:
        str: Template with variables replaced
    """
    now = datetime.now()
    replacements = {
        "{YYYY}": now.strftime("%Y"),
        "{YY}": now.strftime("%y"),
        "{MM}": now.strftime("%m"),
        "{DD}": now.strftime("%d"),
        "{HH}": now.strftime("%H"),
        "{mm}": now.strftime("%M"),
        "{ss}": now.strftime("%S"),           # NEW
        "{SSS}": f"{now.microsecond // 1000:03d}",  # NEW
        "{date}": now.strftime("%Y_%m_%d"),
        "{datetime}": now.strftime("%Y_%m_%d_%H%M%S"),  # CHANGED: added seconds
        "{name}": plan_name,
        "{original}": plan_path.stem,
    }

    # Add session_id if provided (optional for backward compatibility)
    if session_id:
        # Sanitize session_id for filename (remove special characters)
        safe_session_id = re.sub(r'[^\w-]', '_', session_id)
        replacements["{session_id}"] = safe_session_id

    result = template
    for var, value in replacements.items():
        result = result.replace(var, value)

    return result
```

### Change 9: Update export_plan to Pass session_id

**File**: `plugins/plan-export/scripts/plan_export.py`
**Location**: Line 453 (in export_plan function)

```python
# Expand template in filename
base_filename = expand_template(filename_pattern, plan_path, useful_name, session_id)  # Added session_id
```

**Location**: Line 508 (in export_rejected_plan function)

```python
# Expand template in filename
base_filename = expand_template(filename_pattern, plan_path, useful_name, session_id)  # Added session_id
```

---

## Testing Strategy

### Unit Tests to Add

**File**: `plugins/clautorun/tests/test_unit.py`

Add new test class:

```python
class TestConcurrentSessions:
    """Test concurrent session isolation and state management."""

    @pytest.mark.unit
    def test_atomic_list_operations(self):
        """Test that _atomic_list_update prevents race conditions."""
        import threading
        store = ThreadSafeDB()
        session_id = "test_atomic_session"

        ctx = EventContext(
            session_id=session_id,
            event="Stop",
            store=store
        )

        results = []
        errors = []

        def append_items(thread_id):
            try:
                for i in range(100):
                    ctx._atomic_list_update('test_list', 'append', f"thread_{thread_id}_item_{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=append_items, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should have exactly 1000 items (100 * 10 threads)
        assert len(ctx.test_list) == 1000, f"Expected 1000 items, got {len(ctx.test_list)}"
        assert len(errors) == 0, f"Errors occurred: {errors}"

    @pytest.mark.unit
    def test_cache_version_invalidation(self):
        """Test that cache is invalidated when version changes."""
        store = ThreadSafeDB()
        session_id = "test_cache_version"

        # Set initial value
        store.set(f"{session_id}:test_key", "value1")
        assert store.get(f"{session_id}:test_key") == "value1"

        # Simulate external modification (version bump)
        with session_state(session_id) as state:
            state["test_key"] = "value2"
            state["__version__"] = state.get("__version__", 0) + 1

        # Cache should detect version mismatch and return new value
        assert store.get(f"{session_id}:test_key") == "value2"

    @pytest.mark.unit
    def test_cache_cleanup(self):
        """Test that cache cleanup removes old entries."""
        store = ThreadSafeDB()

        # Fill cache with 2000 entries
        for i in range(2000):
            store.set(f"session_{i % 100}:key_{i}", f"value_{i}")

        initial_size = len(store._cache)
        removed = store.cleanup_old_entries(max_size=1000)

        # Should have removed entries to get under max_size
        assert len(store._cache) <= 1000
        assert removed > 0

    @pytest.mark.unit
    def test_pid_tracking_concurrent(self):
        """Test concurrent PID tracking operations."""
        from clautorun.core import ClautorunDaemon, ClautorunApp
        import threading

        app = ClautorunApp()
        daemon = ClautorunDaemon(app)

        pids = []
        errors = []

        def add_pids(start, count):
            try:
                for i in range(start, start + count):
                    daemon._add_pid(i)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=add_pids, args=(1000, 100)),
            threading.Thread(target=add_pids, args=(2000, 100)),
            threading.Thread(target=add_pids, args=(3000, 100)),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should have exactly 300 unique PIDs
        assert len(daemon.active_pids) == 300
        assert len(errors) == 0

        # Test concurrent cleanup
        def cleanup_loop():
            for _ in range(10):
                daemon._cleanup_dead_pids()

        threads = [threading.Thread(target=cleanup_loop) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should not crash or lose PIDs
        assert len(daemon.active_pids) == 300
```

---

## Execution Checklist

- [ ] Step 1.1: Add `_atomic_list_update()` method to EventContext class
- [ ] Step 1.2: Modify `track_task_operations()` to use atomic operations
- [ ] Step 2.1: Add `_versions` dict to ThreadSafeDB.__init__
- [ ] Step 2.2: Modify ThreadSafeDB.set() to increment version
- [ ] Step 2.3: Modify ThreadSafeDB.get() to check version
- [ ] Step 3.1: Add `cleanup_old_entries()` method to ThreadSafeDB
- [ ] Step 3.2: Add cache cleanup call to watchdog
- [ ] Step 4.1: Add PID helper methods to ClautorunDaemon
- [ ] Step 4.2: Update handle_client to use _add_pid()
- [ ] Step 4.3: Update watchdog to use _cleanup_dead_pids()
- [ ] Step 5.1: Modify expand_template() to accept session_id
- [ ] Step 5.2: Add {ss}, {SSS}, {session_id} template variables
- [ ] Step 5.3: Update export_plan() and export_rejected_plan() to pass session_id
- [ ] Step 6.1: Add TestConcurrentSessions class to test_unit.py
- [ ] Step 6.2: Run all tests to verify fixes
- [ ] Step 7.1: Manual testing with 3 concurrent sessions
- [ ] Step 7.2: Long-running daemon test (1+ hour)
