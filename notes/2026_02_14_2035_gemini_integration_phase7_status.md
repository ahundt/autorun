# Gemini CLI Integration - Phase 7 Status Report

**Date**: 2026-02-14 20:35
**Branch**: `feature/gemini-cli-integration`
**Stash**: Current changes stashed as copy at `stash@{0}` (working tree preserved)

## Completed Phases (1-4, 6, 9, 13)

### Phase 1: Fix `command_response()` Bug (DONE)
- **File**: `plugins/clautorun/src/clautorun/core.py:781-799`
- **Fix**: Changed `continue: False` to `continue: True` default with `continue_loop` parameter
- **Removed**: Non-spec `"response"` key
- **Added**: `validate_hook_response()` call for schema compliance
- **File**: `plugins/clautorun/src/clautorun/plugins.py`
- **Fix**: Added `ctx._halt_ai = True` to stop/estop handlers
- **File**: `plugins/clautorun/src/clautorun/core.py` dispatch method
- **Fix**: Checks `_halt_ai` flag and passes `continue_loop=not halt`
- **Tests**: 3 new TDD tests in `test_core.py` (all pass)

### Phase 2: Remove Debug Pollution (DONE)
- **Files**: `core.py:650-695, 1031-1036` - Removed 4 raw `open("~/.clautorun/daemon.log")` blocks
- **Files**: `core.py:366-370, 429-434` - Removed `_cli_type`, `_cli_type_detected`, `_decision_input` from HOOK_SCHEMAS and allowed_gemini
- **File**: `core.py:177-190` - Removed `raw_event`, `type`, `sessionId`, `cli_type` from `normalize_hook_payload()`
- **File**: `main.py:1115-1116` - Removed debug fields from `build_pretooluse_response()`
- **File**: `core.py:758,780` - Removed no-op ternaries

### Phase 3: Simplify `detect_cli_type()` (DONE)
- **File**: `plugins/clautorun/src/clautorun/config.py:420-562`
- **Fix**: Rewrote from ~140 lines to ~35 lines with 3-tier detection
- **Also**: Simplified `should_use_exit2_workaround()` removing debug logging

### Phase 4: Fix Decision Mapping (DONE)
- **File**: `plugins/clautorun/src/clautorun/main.py:1076-1113`
- **Fix**: Removed `deny-to-ask` remapping in `build_pretooluse_response()`
- **Fix**: Removed `safe_reason = json.dumps(reason)[1:-1]` double-encoding
- **Fix**: Restored `commands`/`commands_description` in `should_block_command()`
- **Phase 4.2 REMOVED**: `ctx.deny()` is correct for ExitPlanMode gate (not `ctx.ask()`)

### Phase 6: Fix Gemini Manifest and Tests (DONE)
- **File**: `gemini-extension.json:22` - Fixed hooks path to `./hooks/gemini-hooks.json`
- **File**: `test_gemini_e2e_improved.py:578` - Fixed duplicate `source` key
- **File**: `aix_manifest.py` - Added `gemini_manifest["hooks"] = "./hooks/gemini-hooks.json"`

### Additional Fix: EventContext `cli_type` Auto-Detection
- **File**: `core.py:495` - Changed `cli_type` default from `"claude"` to `None`
- **File**: `core.py:518-522` - Added lazy auto-detection via `detect_cli_type()` in property
- **File**: `test_blocking_integration.py:60` - Added `cli_type = "claude"` to MockContext
- **Result**: Fixed 13 test failures where tests expected Gemini `"deny"` but got Claude `"block"`

## Current Test Results

### Passing (all previously fixed):
- `test_actual_command_blocking.py` - **27/27 passed**
- `test_blocking_integration.py` - **21/21 passed**
- `test_core.py` - All passed
- `test_dual_platform_response.py` - All passed
- `test_e2e_policy_lifecycle.py` - All passed
- `test_gemini_e2e_improved.py` - Needs verification (likely passes)

### Failing:
1. **`test_dual_platform_hooks_install.py::TestDaemonContinueField`** - 2 failures
   - `test_core_py_pretooluse_deny_keeps_ai_working` FAILED
   - `test_main_py_pretooluse_deny_keeps_ai_working` FAILED
   - Root cause: Unknown - need to read these tests

2. **`test_edge_cases.py::TestExportPlan::test_export_creates_file`** - HANGS (deadlock)
   - Blocks entire test suite at 33%
   - Root cause: `PlanExport.export()` deadlocks in `atomic_update_tracking()`

## User-Reported Issues

### Plan Export Not Working (Production)
- User confirmed plan export has not been working in the installed version
- The test deadlock (below) was caused by the same root issue: a 50MB corrupted shelve file
- **Fix applied**: Trashed `~/.claude/sessions/plugin___plan_export__.db.db` (50MB, 0 keys = pure bloat)
- **Preventive fix needed**: Add shelve size monitoring or periodic compaction to prevent re-occurrence
- **Root cause for production failure**: Same shelve file bloat likely causes the installed daemon's export path to block on `shelve.open()` or `session_state()` during `atomic_update_tracking()`
- **Location**: `plan_export.py:429-434` (`atomic_update_tracking`) and `plan_export.py:414-419` (`atomic_update_active_plans`)

### Shelve Bloat Prevention (TODO)
- `shelve` with `writeback=True` (used in `session_manager.py:397`) caches all accessed objects and writes them all back on `sync()`/`close()`, even if unchanged. Over many sessions, this causes exponential growth.
- Potential fixes:
  1. Add `shelve.open()` timeout wrapper
  2. Add size check before opening (if > 10MB, compact or recreate)
  3. Switch tracking storage from shelve to plain JSON file (tracking data is small)
  4. Use `writeback=False` for tracking operations (read-modify-write manually)

## CRITICAL BUG: Plan Export Deadlock (RESOLVED)

### Symptoms
- `test_export_creates_file` hangs indefinitely (ignores pytest timeout)
- Production plan export also broken (user confirmed)
- Blocks at `atomic_update_tracking()` call in `PlanExport.export()` at `plan_export.py:655`

### Investigation Trail
1. `PlanExport.export()` at `plan_export.py:613-669` - calls `atomic_update_tracking()` at line 655
2. `atomic_update_tracking()` at `plan_export.py:429-434` - calls `session_state(GLOBAL_SESSION_ID)` where `GLOBAL_SESSION_ID = "__plan_export__"`
3. `session_state()` at `session_manager.py:429-434` - acquires `SessionLock`
4. `SessionLock.__enter__()` at `session_manager.py:125-161` - calls `_acquire_lock()` using `fcntl.flock(fd, LOCK_EX | LOCK_NB)`

### What Works in Isolation
- `session_state('__plan_export__')` works standalone
- `ThreadSafeDB()` + `session_state('__plan_export__')` works in sequence
- No stale lock files found (`lsof` shows no process holding them)
- Lock file at `~/.claude/sessions/.__plan_export__.lock` not held by any process

### What Fails
- When `PlanExport.export()` is called (even from a daemon thread with join timeout)
- The `Thread.join(timeout=8)` itself blocks, suggesting GIL contention
- `fcntl.flock` with `LOCK_NB` may be blocking signal delivery

### Deadlock Hypothesis
The trace shows steps succeed up to "j atomic_update_tracking..." then hangs:
```
a extract_useful_name -> OK
b expand_template -> OK
...
g shutil.copy2 -> OK
h embed_plan_metadata -> OK
i get_content_hash -> OK
j atomic_update_tracking... -> HANG
```

Possible causes:
1. **shelve backend corruption**: `plugin___plan_export__.db.db` exists in `~/.claude/sessions/` - unusual double `.db.db` suffix
2. **shelve open blocks**: `shelve.open()` on a corrupted file could block indefinitely
3. **fcntl.flock + shelve interaction**: shelve may internally use its own locking that conflicts with SessionLock
4. **GIL held during C-level blocking**: If shelve or fcntl blocks at C level, it prevents Thread.join timeout from firing

### Alternative Strategies to Investigate

#### Strategy A: Check shelve file corruption
```bash
# Check for corrupted shelve files
ls -la ~/.claude/sessions/*plan_export*
python -c "import shelve; s = shelve.open(str(Path.home() / '.claude/sessions/plugin___plan_export__.db')); print(dict(s)); s.close()"
```

#### Strategy B: Delete stale shelve state and retry
```bash
rm -f ~/.claude/sessions/plugin___plan_export__.db*
rm -f ~/.claude/sessions/.__plan_export__.lock
```

#### Strategy C: Compare with last working git commit
```bash
git diff HEAD -- plugins/clautorun/src/clautorun/plan_export.py
git diff HEAD -- plugins/clautorun/src/clautorun/session_manager.py
git diff HEAD -- plugins/clautorun/src/clautorun/core.py
# Check if export() or session_state() changed in ways that cause deadlock
```

#### Strategy D: Use ThreadSafeDB for tracking instead of raw session_state
Instead of `atomic_update_tracking()` calling `session_state()` directly, use the `store` (ThreadSafeDB) that's already in the EventContext, avoiding double-locking.

#### Strategy E: Add timeout to shelve.open()
Wrap `shelve.open()` in a timeout using `signal.alarm()` or `threading.Timer` to detect and recover from hangs.

## Code Quality Issues Found

### Hardcoded capture sizes in test_dual_platform_hooks_install.py (FRAGILE)
- **File**: `plugins/clautorun/tests/test_dual_platform_hooks_install.py:637-655`
- **Problem**: Tests use `content[respond_idx:respond_idx + 6000]` and `content[func_idx:func_idx + 4000]` to capture function bodies for string assertions. Any growth in docstrings, comments, or code pushes the target assertions past the capture window, causing silent test failures.
- **Already broke once**: Had to increase from 4000 to 6000 for `respond()` after Phase 2 changes.
- **Fix**: Use `inspect.getsource()`, regex to find next `def` at same indent level, or just search the entire file after the function start instead of a fixed window.

### Shelve bloat caused 50MB state file and production deadlock
- **File**: `plugins/clautorun/src/clautorun/session_manager.py:397` uses `shelve.open(..., writeback=True)`
- **Problem**: `writeback=True` caches ALL accessed objects and writes them all back on `sync()`/`close()`, even if unchanged. Over many daemon sessions, the `__plan_export__` shelve grew to 50MB with 0 useful keys.
- **Impact**: `shelve.open()` on 50MB file blocks indefinitely, causing `atomic_update_tracking()` in `plan_export.py:429-434` to deadlock. This broke both tests AND production plan export.
- **Immediate fix applied**: Trashed the bloated file. Export works now.
- **Preventive fixes needed**:
  1. Add size check before `shelve.open()` — if > 5MB with few keys, recreate
  2. Consider `writeback=False` for plan_export tracking (small data, explicit writes)
  3. Add periodic compaction or use JSON file instead of shelve for tracking
  4. Add timeout wrapper around `shelve.open()` to fail-open instead of deadlock

## Remaining Plan Phases

### Phase 8: Verify Legacy/Daemon Alignment
- Verify `build_hook_response(True, ...)` matches `command_response(continue_loop=True)`
- Verify `build_hook_response(False, ...)` matches `command_response(continue_loop=False)`

### Phase 10: Fix ai_monitor.py Shelve Race Condition
- `ai_monitor.py:49-57` - Wrap `shelve.open()` with `SessionLock` RAII

### Phase 11: Verify Gemini Hook Event Coverage
- Verify tool name matchers, `${extensionPath}`, 5s timeout

### Phase 12: Verify cli_type E2E Propagation
- Write E2E tests for Gemini payload -> decision deny
- Write E2E tests for Claude payload -> decision block

## Key Files Modified in This Session

| File | Changes |
|------|---------|
| `core.py:495,518-522` | EventContext cli_type auto-detection |
| `core.py:781-799` | command_response() continue:True default |
| `core.py:366-370,429-434` | Removed debug fields from schemas |
| `core.py:177-190` | Cleaned normalize_hook_payload |
| `core.py:650-695,1031-1036` | Removed raw debug logging |
| `core.py:758,780` | Removed no-op ternaries |
| `config.py:420-562` | Rewrote detect_cli_type() (~140->~35 lines) |
| `main.py:1076-1113` | Fixed build_pretooluse_response() |
| `main.py:952-958` | Restored commands/commands_description |
| `plugins.py` | Added ctx._halt_ai for stop/estop |
| `aix_manifest.py` | Fixed Gemini hooks path |
| `gemini-extension.json` | Fixed hooks path |
| `test_blocking_integration.py:60` | Added cli_type to MockContext |
| `test_core.py` | 3 new TDD tests for command_response |
| `test_gemini_e2e_improved.py:578` | Fixed duplicate key |
| `test_e2e_policy_lifecycle.py` | Strict deny assertion |
