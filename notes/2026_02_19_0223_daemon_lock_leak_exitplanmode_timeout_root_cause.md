# Daemon Lock Leak Investigation: ExitPlanMode Timeout Root Cause

**Date**: 2026-02-19 02:23
**Branch**: `feature/gemini-cli-integration`
**Status**: Root cause confirmed; fix pending

---

## Summary

4 of 19 tests in `test_claude_e2e_real_money.py::TestClaudeHookEntryPoint` fail with `subprocess.TimeoutExpired` after 15-20 seconds. All 4 involve ExitPlanMode events (PostToolUse and PreToolUse). Root cause is a **leaked exclusive flock** held by daemon PID 48642 on `~/.claude/sessions/.__plan_export__.lock` (FD 11), combined with a leaked shelve DB (FD 12). Every `session_state(GLOBAL_SESSION_ID)` call within the daemon blocks for ~1s (100 retries × 0.01s) → test hooks queue behind real Claude session hooks → cumulative delay exceeds 15-20s subprocess timeout.

---

## Test Failures (4 remaining)

**Terminal log**: `/tmp/clautorun-pytest-terminal3.log`

```
FAILED test_plan_export_posttooluse_exitplanmode_writes_to_notes  — TimeoutExpired (20s)
FAILED test_pretooluse_exitplanmode_returns_continue_not_deny     — TimeoutExpired (15s)
FAILED test_all_hook_responses_are_valid_json                     — TimeoutExpired (15s)
FAILED test_deny_exits_with_code_2_allow_exits_with_code_0       — TimeoutExpired (15s)
```

**Common error pattern**:
```
subprocess.TimeoutExpired: Command '['uv', 'run', '--quiet', '--project',
  '/Users/athundt/.claude/clautorun/plugins/clautorun', 'python',
  '/Users/athundt/.claude/clautorun/plugins/clautorun/hooks/hook_entry.py', '--cli', 'claude']'
  timed out after N seconds
```

**Prior state** (from `/tmp/clautorun-pytest-terminal2.log`): Before the fix in the previous context, there were 12 failures. The 12 → 4 fix was killing daemon PID 3666 (stuck at 98.5% CPU, refusing socket connections → falling back to allow response for all hooks). After that restart, 15 tests pass.

---

## Investigation Chronology

### Step 1: Checked lock file content

```bash
cat ~/.claude/sessions/.__plan_export__.lock
```
Output: `{"pid": 48642, "start_time": 1771484695.367688, "session_id": "__plan_export__"}`

Lock file is owned by daemon PID **48642**.

### Step 2: Checked daemon process state

```bash
cat ~/.clautorun/daemon.lock
```
Output: `48642`

```bash
pgrep -fl clautorun
```
Found **TWO** daemons: PIDs **48575** and **48642**, both launched from plugin cache:
`/Users/athundt/.claude/plugins/cache/clautorun/clautorun/0.8.0/`

PID 48642 is the active daemon (owns `~/.clautorun/daemon.sock`). Running for 12+ minutes as of investigation.

### Step 3: Checked open file descriptors

```bash
lsof -p 48642 | grep -E "plan_export|sessions"
```

Key leaked FDs:
| FD | Mode | File |
|----|------|------|
| 11 | write | `~/.claude/sessions/.__plan_export__.lock` |
| 12 | read+write | `~/.claude/sessions/plugin___plan_export__.db.db` (50 MB) |

Both FDs have been open since daemon start at 02:04 (12+ minutes). This is a **leak** — they should have been released after the `with session_state()` block exited.

### Step 4: Verified flock is actually held

```python
import fcntl, errno
lock_path = "/Users/athundt/.claude/sessions/.__plan_export__.lock"
fd = open(lock_path, 'r+')
try:
    fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    print("ACQUIRED (lock not held!)")
except OSError as e:
    if e.errno in (errno.EACCES, errno.EAGAIN):
        print(f"BLOCKED — lock IS held (errno={e.errno})")
```
Result: **BLOCKED — lock IS held (errno=35)**
errno=35 = EAGAIN = EWOULDBLOCK on macOS. Lock definitively held by PID 48642.

### Step 5: Confirmed macOS flock() is NOT reentrant within same process

Critical finding: on macOS/BSD, `flock()` is NOT per-process reentrant when called via a **different fd** for the same file.

```python
fd1 = open(lock_path, 'w')
fcntl.flock(fd1.fileno(), fcntl.LOCK_EX)   # Succeeds (acquires lock)
fd2 = open(lock_path, 'r+')
fcntl.flock(fd2.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # BLOCKED: errno=35
```
Result: **CONFIRMED** — same process, different fd, same file → `errno.EAGAIN` (35).

This means: when daemon PID 48642 calls `session_state(GLOBAL_SESSION_ID)`, it opens a NEW fd to `.__plan_export__.lock` and tries to acquire the flock. But the daemon's OWN leaked FD 11 blocks it → 100 retries × 0.01s = ~1s → `SessionTimeoutError`.

The `_lock_registry` reentrant tracking in `session_manager.py` should prevent this via `(pid, thread_id)` key — but the leaked FD means the lock was NEVER released, so the registry no longer has an entry for it. Incoming requests see no active lock entry → try fresh flock → blocked by leaked FD.

### Step 6: Traced the timeout cascade

1. Daemon PID 48642 holds leaked exclusive flock on `.__plan_export__.lock` (FD 11)
2. This Claude Code session is actively running → fires continuous hook events (PreToolUse, PostToolUse, etc.)
3. Each real-session hook that touches plan_export state → `session_state(GLOBAL_SESSION_ID)` → tries flock → blocks 100× 0.01s → `SessionTimeoutError` (caught and logged, returns gracefully)
4. `core.py:handle_client()` dispatches hooks **synchronously** (not `await`) — blocks asyncio event loop for ~1s per real-session hook
5. Test hooks (from `hook_entry.py` subprocesses) arrive at daemon socket → queue in asyncio event loop behind real-session hooks
6. Total queued wait per test: (N real hooks × ~1s each) → when N ≥ 15, test times out at 15s

### Step 7: Checked session_manager.py lock cleanup code

`session_manager.py:_cleanup()` (lines 194-228):
```python
try:
    fcntl.flock(self.lock_fd.fileno(), fcntl.LOCK_UN)
except Exception:
    pass   # <--- silently swallows exception
try:
    self.lock_fd.close()
except Exception:
    pass
```

**Bug**: If `fcntl.flock(LOCK_UN)` raises an exception (e.g., fd is in a bad state), `close()` is NOT called — fd remains open. However, this requires flock(LOCK_UN) to raise, which is unusual.

**More likely cause**: The fd was never passed to `_cleanup()` at all — meaning the `with session_state()` context was entered but the context manager's `__exit__` was never called. This would happen if:
- The daemon process crashed mid-context (but daemon is still running)
- A `SessionLock` object was created but its `__exit__` was never invoked (e.g., exception in outer `with` block that bypassed the `finally:`)
- The lock was acquired during daemon initialization and the init code didn't properly use `with` statement

### Step 8: Checked plan_export.py lock usage at startup

`plan_export.py`: Properties `active_plans` and `tracking` both call `session_state(GLOBAL_SESSION_ID)` on every access. During daemon startup, `PlanExport` class is instantiated. If any startup code accesses `active_plans` or `tracking` inside a bare `session_state()` call (not properly guarded), and that call leaves the context open, FD is leaked.

The 50MB shelve DB (FD 12) being open suggests `shelve.open()` was called (part of `session_state(GLOBAL_SESSION_ID)`) but `shelve.close()` was never called.

---

## Confirmed Root Cause

**Daemon PID 48642 has a LEAKED `session_state(GLOBAL_SESSION_ID)` context** that was entered (acquiring exclusive flock on `.__plan_export__.lock`, opening 50MB shelve DB) but never exited (flock never released, shelve never closed).

This causes every subsequent `session_state(GLOBAL_SESSION_ID)` call within the same daemon process to:
1. Try `flock(LOCK_EX | LOCK_NB)` on a new fd
2. Get `errno=35` (EAGAIN) because daemon's own leaked FD 11 holds the lock
3. Retry 100× at 0.01s = ~1s delay
4. Raise `SessionTimeoutError`
5. Plan export handlers catch this and return gracefully — but ~1s is wasted per real-session hook

With multiple real-session hooks from THIS Claude session firing continuously, the asyncio queue fills up with ~1s-each tasks, pushing test subprocesses past the 15-20s timeout.

---

## What Was Tried

### Prior session (from compacted context)
- **Killed daemon PID 3666** (stuck at 98.5% CPU) → 12 failures fixed → 15 tests pass
- **Restarted daemon** → 4 tests still fail (all timeouts)

### This session
- Read `plan_export.py`, `session_manager.py`, `plugins.py`, `core.py` to understand lock architecture
- Checked `~/.clautorun/daemon.lock` → PID 48642
- Read lock file `.__plan_export__.lock` → confirmed PID 48642 owns it
- Used `lsof -p 48642` → confirmed FDs 11 + 12 leaked
- Tested flock non-reentrance → confirmed macOS blocks same-process different-fd flock
- Traced timeout cascade through asyncio event loop → confirmed mechanism

---

## Suspects for Lock Leak Root Cause

1. **`_cleanup()` silent exception** (`session_manager.py:194-228`): If `flock(LOCK_UN)` raises, `close()` is skipped → fd leaked. Uncommon but possible if fd entered bad state.

2. **Daemon startup code**: Some initialization path calls `session_state(GLOBAL_SESSION_ID)` without proper `with` statement, leaving context open. Need to grep for bare `session_state()` calls outside `with` blocks.

3. **`shelve.close()` hang** (possible on 50MB file): If shelve `close()` blocks for a long time during a flush/sync, the `with session_state()` block stalls mid-exit, another call from asyncio creates a second lock entry that interferes... but daemon PID is one process so asyncio is single-threaded. Low probability.

4. **Exception bypassing `finally:`**: A bare `raise SystemExit` or `os._exit()` during context could skip `__exit__`, but daemon is running so this didn't happen.

5. **Startup script explicitly holds lock open**: `plan_export.py:PlanExport.__init__()` or `recover_unexported_plans()` at SessionStart might hold a lock across an expensive operation (scanning plan files), and if SessionStart fires quickly at daemon startup before the lock is released, the daemon's own lock blocks the next access.

---

## Fix Plan (To Execute Immediately)

### Step 1 (IMMEDIATE): Kill daemon PID 48642 — releases leaked FDs 11 + 12
```bash
kill 48642
sleep 2
pgrep -fl clautorun
```

### Step 2: Remove stale lock file (if still present after kill)
```bash
rm -f ~/.claude/sessions/.__plan_export__.lock
```

### Step 3: Restart daemon cleanly
```bash
cd /Users/athundt/.claude/clautorun && clautorun --restart-daemon
# OR
uv run --project plugins/clautorun python -m clautorun --restart-daemon
```

### Step 4: Verify daemon running fresh
```bash
pgrep -fl clautorun
cat ~/.clautorun/daemon.lock
lsof -p $(cat ~/.clautorun/daemon.lock) | grep -E "plan_export|sessions"
```
Expected: NO `.__plan_export__.lock` or shelve DB held open.

### Step 5: Re-run 4 failing tests
```bash
cd /Users/athundt/.claude/clautorun
uv run pytest plugins/clautorun/tests/test_claude_e2e_real_money.py::TestClaudeHookEntryPoint::test_plan_export_posttooluse_exitplanmode_writes_to_notes plugins/clautorun/tests/test_claude_e2e_real_money.py::TestClaudeHookEntryPoint::test_pretooluse_exitplanmode_returns_continue_not_deny plugins/clautorun/tests/test_claude_e2e_real_money.py::TestClaudeHookEntryPoint::test_all_hook_responses_are_valid_json plugins/clautorun/tests/test_claude_e2e_real_money.py::TestClaudeHookEntryPoint::test_deny_exits_with_code_2_allow_exits_with_code_0 -v 2>&1 | tee /tmp/clautorun-pytest-4tests.log
```
Expected: All 4 PASS (if lock was the only cause).

### Step 6: Investigate and fix lock leak root cause in code

**Grep for bare session_state() calls outside `with` blocks**:
```bash
grep -n "session_state" plugins/clautorun/src/clautorun/plan_export.py | head -40
grep -n "session_state" plugins/clautorun/src/clautorun/plugins.py | head -20
```

**Check `_cleanup()` in session_manager.py**:
- Add `self.lock_fd.close()` in `finally:` regardless of flock(LOCK_UN) result
- Or use `contextlib.suppress` but still always close fd

**Check PlanExport initialization path**:
- Does `__init__()` or any startup-time code access `self.active_plans` or `self.tracking`?
- If yes, ensure `with session_state()` is used properly
- If `recover_unexported_plans()` fires on SessionStart and holds lock during file I/O, check it's properly bounded

### Step 7: Add automated test for lock leak detection

Add a test that:
1. Calls `export_on_exit_plan_mode()` directly in subprocess
2. Verifies that after the call, the lock file is NOT held
3. Uses `flock(LOCK_EX | LOCK_NB)` to check from a separate process

### Step 8: Run full test suite after fix

```bash
uv run pytest plugins/clautorun/tests/ -v --tb=short 2>&1 | tee /tmp/clautorun-pytest-full-suite.log
```
Expected: All 19 tests in `test_claude_e2e_real_money.py` PASS, no regressions in other test files.

---

## File References (Current Line Numbers May Shift)

| File | Location | Relevance |
|------|----------|-----------|
| `plugins/clautorun/src/clautorun/session_manager.py` | `SessionLock._cleanup():194-228` | Silent exception may skip `close()` |
| `plugins/clautorun/src/clautorun/session_manager.py` | `session_state():334-382` | Context manager that acquires lock |
| `plugins/clautorun/src/clautorun/plan_export.py` | `PlanExport.active_plans:406-412` | Calls session_state() on every access |
| `plugins/clautorun/src/clautorun/plan_export.py` | `PlanExport.tracking:421-427` | Calls session_state() on every access |
| `plugins/clautorun/src/clautorun/plan_export.py` | `export_on_exit_plan_mode():1005-1030` | PostToolUse handler, catches SessionTimeoutError |
| `plugins/clautorun/src/clautorun/plan_export.py` | `recover_unexported_plans():1033-1074` | SessionStart handler, may hold lock long |
| `plugins/clautorun/src/clautorun/core.py` | `handle_client():1123-1214` | Synchronous dispatch blocks asyncio event loop |
| `plugins/clautorun/hooks/claude-hooks.json` | PostToolUse matcher line ~18 | Fires hook_entry.py for ExitPlanMode |
| `plugins/clautorun/tests/test_claude_e2e_real_money.py` | `_run():344`, `run_hook():183` | subprocess.run with timeout=15/20 |
| `~/.claude/sessions/.__plan_export__.lock` | lock file | PID 48642 holds exclusive flock (leaked) |
| `~/.claude/sessions/plugin___plan_export__.db.db` | shelve DB, 50MB | PID 48642 holds open (leaked) |

---

## Environment

- **Daemon lock file**: `~/.clautorun/daemon.lock` → PID 48642
- **Socket**: `~/.clautorun/daemon.sock`
- **Session lock**: `~/.claude/sessions/.__plan_export__.lock`
- **Shelve DB**: `~/.claude/sessions/plugin___plan_export__.db.db` (50MB)
- **Two daemons**: PIDs 48575 + 48642 (both from plugin cache 0.8.0); PID 48642 is active
- **GLOBAL_SESSION_ID**: `"__plan_export__"` (`plan_export.py:190`)
- **macOS flock non-reentrance**: CONFIRMED — same process, different fd → EAGAIN (errno=35)
- **Retry params**: `MAX_LOCK_RETRIES=100`, `LOCK_RETRY_DELAY=0.01s` → ~1s max per contested call
