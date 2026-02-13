# Fix SessionStart NoneType Error and Verify Bug #4669 Workaround

**Date**: 2026-02-12
**Branch**: feature/gemini-cli-integration
**Previous Work**: notes/2026_02_12_2153_bug_4669_implementation_status_and_pending_issues.md

## Context

The Bug #4669 workaround implementation is complete (7 commits) but blocked by a critical NoneType error preventing verification. Through code investigation, I found the root cause: `ctx.payload` doesn't exist in EventContext, causing `'NoneType' object has no attribute 'get'` when accessed.

**Current State**:
- ✅ Bug #4669 workaround code complete (exit 2 pathway, auto-detection, DRY handlers)
- ✅ hookSpecificOutput added to all responses
- ❌ SessionStart:resume fails with NoneType error (BLOCKING)
- ❓ Bug #4669 workaround untested end-to-end
- ❓ Stop hook fix untested

**Root Cause Found**:

File: `plugins/clautorun/src/clautorun/plan_export.py:938`

```python
logger.info(f"SessionStart handler called (source: {ctx.payload.get('source', 'unknown')})")
```

**Problem**: `ctx.payload` doesn't exist in EventContext. The `__getattr__` magic method (core.py:422-445) returns `None` for unknown attributes, then `.get()` is called on `None`.

**EventContext attributes** (core.py:381-394):
- `ctx.session_id` ✅
- `ctx.event` ✅
- `ctx.prompt` ✅
- `ctx.tool_name` ✅
- `ctx.tool_input` ✅
- `ctx.payload` ❌ (doesn't exist, returns None)

## Implementation Plan

### Phase 1: Fix NoneType Error (CRITICAL)

**File**: `plugins/clautorun/src/clautorun/plan_export.py:938`

**Change**:
```python
# BEFORE (line 938):
logger.info(f"SessionStart handler called (source: {ctx.payload.get('source', 'unknown')})")

# AFTER:
logger.info(f"SessionStart handler called (event: {ctx.event})")
```

**Why**: `ctx.payload` doesn't exist. `ctx.event` contains the event name (always valid).

**Test After Fix**:
```bash
# Restart Claude Code completely (Cmd+Q, then reopen)
# Expected: No "SessionStart:resume hook error" on startup
# Check logs:
tail -50 ~/.clautorun/daemon.log | grep "SessionStart"
```

**Success Criteria**: SessionStart:resume completes without error.

---

### Phase 2: Verify Bug #4669 Workaround End-to-End

**Prerequisite**: Phase 1 complete, Claude Code restarted

**Test Command**:
```bash
# Create test file
echo "test content" > /tmp/test-bug4669-verification.txt

# Try to remove (should be BLOCKED)
rm /tmp/test-bug4669-verification.txt

# Verify file still exists
ls -la /tmp/test-bug4669-verification.txt
```

**Expected Behavior**:
1. ✅ Tool BLOCKED (rm command doesn't execute)
2. ✅ File still exists after rm attempt
3. ✅ Trash suggestion appears in AI context via stderr
4. ✅ Exit code 2 passed through all layers
5. ✅ NO "hook error" message

**Failure Modes to Check**:
- ❌ File deleted → Workaround not working
- ❌ "hook error" shown → Exit code 2 not handled correctly
- ❌ No trash suggestion → Stderr not passed through

**Debug if Fails**:
```bash
# Check hook execution
tail -100 ~/.clautorun/hook_entry_debug.log | grep -A 10 "rm.*test-bug4669"

# Check daemon lifecycle
tail -100 ~/.clautorun/daemon.log | grep -B 3 -A 5 "CLIENT→DAEMON\|DAEMON→CLIENT"

# Verify exit code pathway
grep "Exit.*2\|exit.*2" ~/.clautorun/daemon.log
```

---

### Phase 3: Verify Stop Hook Fix

**Prerequisite**: Claude Code restarted with latest code

**Test**: Trigger a Stop event naturally or via manual invocation

**Expected**:
- ✅ No "JSON validation failed: Invalid input" error
- ✅ Response includes `hookSpecificOutput` with `hookEventName="Stop"`

**Manual Verification**:
```bash
# Simulate Stop hook
echo '{"session_id":"test","transcript_path":"test.jsonl","cwd":"/tmp","hook_event_name":"Stop","source":"user"}' | \
  CLAUDE_PLUGIN_ROOT=~/.claude/plugins/cache/clautorun/clautorun/0.8.0 \
  uv run --quiet --project ~/.claude/plugins/cache/clautorun/clautorun/0.8.0 \
  python ~/.claude/plugins/cache/clautorun/clautorun/0.8.0/hooks/hook_entry.py 2>&1 | python3 -m json.tool

# Expected: Valid JSON with hookSpecificOutput field
```

---

### Phase 4: Re-enable SessionStart Plan Recovery

**Prerequisite**: Phases 1-3 complete and verified

**File**: `plugins/clautorun/src/clautorun/plan_export.py:913-945`

**Action**:
1. Remove temporary disable comment (lines 921-936)
2. Remove `logger.info` and `return None` (lines 938-939)
3. Uncomment original code (lines 942-onwards)

**Before** (lines 913-945):
```python
@app.on("SessionStart")
def recover_unexported_plans(ctx: EventContext) -> Optional[Dict]:
    """
    TODO: RE-ENABLE AFTER FIXING SessionStart:resume HANG
    ...
    """
    logger.info(f"SessionStart handler called (source: {ctx.payload.get('source', 'unknown')})")
    return None  # Disabled temporarily

    # Original code (disabled - DO NOT DELETE):
    # try:
    #     config = PlanExportConfig.load()
    #     ...
```

**After**:
```python
@app.on("SessionStart")
def recover_unexported_plans(ctx: EventContext) -> Optional[Dict]:
    """Recover plans from Option 1 (fresh context) on session start.

    CRITICAL: Runs in NEW session after Option 1 clears context.
    Uses GLOBAL_SESSION_ID to read active_plans from OLD session.
    Daemon integration: Shares ThreadSafeDB cache across sessions.
    """
    try:
        config = PlanExportConfig.load()
        if not config.enabled:
            return None
        exporter = PlanExport(ctx, config)
        for plan in exporter.get_unexported():
            # ... rest of original code ...
```

**Test After Re-enable**:
```bash
# Restart daemon
clautorun --restart-daemon

# Restart Claude Code completely
# Start new session, exit plan mode with Option 1
# Start another new session
# Expected: Plan exported to notes/ with timestamp
```

**Rollback Plan**: If SessionTimeoutError returns, add better error handling:
```python
except SessionTimeoutError as e:
    logger.warning(f"SessionStart plan recovery timeout: {e}")
    # Return explicit allow instead of None
    return {"continue": True, "stopReason": "", "suppressOutput": False, "systemMessage": ""}
```

---

### Phase 5: Cleanup and Documentation

**Hook Lifecycle Logging Decision**:

Current logging in `client.py:_log_hook_lifecycle()` writes to daemon.log on every hook call.

**Options**:
1. **Keep** - Helpful for future debugging, minimal overhead
2. **Make conditional** - Only log when `CLAUTORUN_DEBUG=1`
3. **Remove** - Clean up after SessionStart issue resolved

**Recommended**: Make conditional via env var for production use.

**File**: `plugins/clautorun/src/clautorun/client.py:52-70`

**Change**:
```python
def _log_hook_lifecycle(message: str, **kwargs) -> None:
    """DRY helper for hook lifecycle logging."""
    # Only log when debug enabled
    if not os.environ.get('CLAUTORUN_DEBUG'):
        return

    try:
        DEBUG_LOG.parent.mkdir(exist_ok=True)
        with open(DEBUG_LOG, 'a') as f:
            f.write(f"[{datetime.datetime.now()}] {message}\n")
            for key, value in kwargs.items():
                f.write(f"{key}: {value}\n")
    except Exception:
        pass  # Never fail on logging
```

**Documentation Updates**:

1. Update README.md "Bug #4669 Workaround" section with verification results
2. Add troubleshooting section for NoneType errors
3. Document CLAUTORUN_DEBUG environment variable
4. Update CHANGELOG.md with fixes

---

## Critical Files Modified

| File | Lines | Change |
|------|-------|--------|
| `plan_export.py` | 938 | Fix: Use `ctx.event` instead of `ctx.payload.get()` |
| `plan_export.py` | 913-945 | Re-enable: Uncomment original plan recovery code |
| `client.py` | 52-70 | Optional: Make logging conditional on CLAUTORUN_DEBUG |

---

## Verification Checklist

**Phase 1 - NoneType Fix**:
- [ ] Line 938 changed to use `ctx.event`
- [ ] Daemon restarted with latest code
- [ ] Claude Code restarted completely
- [ ] No "SessionStart:resume hook error" on startup
- [ ] daemon.log shows successful SessionStart lifecycle

**Phase 2 - Bug #4669 Workaround**:
- [ ] rm test file created in /tmp
- [ ] rm command blocked (tool doesn't execute)
- [ ] File still exists after rm attempt
- [ ] Trash suggestion appears in AI context
- [ ] No "hook error" message
- [ ] Exit code 2 logged in daemon.log

**Phase 3 - Stop Hook**:
- [ ] Stop hook manual test returns valid JSON
- [ ] hookSpecificOutput field present
- [ ] No "JSON validation failed" error

**Phase 4 - Plan Recovery**:
- [ ] Original code uncommented
- [ ] SessionStart:resume completes without hang
- [ ] Plan export works on new session
- [ ] No SessionTimeoutError

**Phase 5 - Cleanup**:
- [ ] Logging conditional on CLAUTORUN_DEBUG (optional)
- [ ] Documentation updated
- [ ] CHANGELOG.md updated
- [ ] All changes committed

---

## Success Criteria

### Overall Success
1. ✅ SessionStart:resume completes without errors
2. ✅ Bug #4669 workaround blocks rm commands end-to-end
3. ✅ Stop hooks return valid JSON
4. ✅ Plan recovery works without hanging
5. ✅ All hook errors resolved

### Concrete Tests Pass

**Test 1: SessionStart:resume**
```bash
# Restart Claude Code → No hook error on startup
```

**Test 2: rm Blocking**
```bash
echo "test" > /tmp/test.txt && rm /tmp/test.txt
ls /tmp/test.txt  # File exists
```

**Test 3: Plan Recovery**
```bash
# Exit plan mode → Option 1 → New session → Plan in notes/
```

---

## Risk Mitigation

**If NoneType fix doesn't work**:
- Check if there are other `ctx.payload` references in codebase
- Verify EventContext __getattr__ returns None for unknown attributes
- Add defensive None checks before all `.get()` calls

**If Bug #4669 workaround fails**:
- Log environment variables to verify auto-detection
- Check hook execution order with other plugins
- Verify cache timestamps match source code
- Test with `CLAUTORUN_EXIT2_WORKAROUND=always` to force workaround

**If plan recovery still hangs**:
- Reduce SessionLock timeout from 5s to 2s
- Add timeout wrapper around entire recovery operation
- Make plan recovery optional via config flag

---

## References

- **Status Document**: notes/2026_02_12_2153_bug_4669_implementation_status_and_pending_issues.md
- **Bug #4669**: https://github.com/anthropics/claude-code/issues/4669
- **Hooks API**: notes/hooks_api_reference.md lines 326-440
- **Original Plan**: /Users/athundt/.claude/plans/staged-snacking-dewdrop.md
- **EventContext**: plugins/clautorun/src/clautorun/core.py:340-469
- **Daemon**: plugins/clautorun/src/clautorun/core.py:754-873
