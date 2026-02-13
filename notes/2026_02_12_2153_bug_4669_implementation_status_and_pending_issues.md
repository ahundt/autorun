# Bug #4669 Workaround Implementation Status

**Date**: 2026-02-12 21:53
**Branch**: feature/gemini-cli-integration

## Current Status

### ✅ Completed Work

**Bug #4669 Workaround - Core Implementation:**
1. ✅ hook_entry.py: Accepts exit codes 0 AND 2, passes through stderr and exit code
2. ✅ config.py: Auto-detection functions (detect_cli_type, should_use_exit2_workaround)
3. ✅ client.py: Unified output_hook_response() handler (DRY)
4. ✅ core.py: hookSpecificOutput added to ALL hook responses
5. ✅ __main__.py: CLI argument support (--exit2-mode auto/always/never)
6. ✅ CLAUDE.md: Documentation and primary install command

**Fixes Applied:**
1. ✅ Removed duplicate hooks field from plugin.json
2. ✅ hook_entry.py returns True for exit 0 (prevents hook errors)
3. ✅ Added hookSpecificOutput to Stop and default responses
4. ✅ Added DRY hook lifecycle logging to daemon.log
5. ✅ Removed legacy plan-export plugin (was causing incomplete JSON errors)

**7 Commits Created:**
```
df15a91 fix(plan_export): temporarily disable SessionStart plan recovery to debug hang
008e08f fix(core): add hookSpecificOutput to all hook responses
1ea8904 feat(client): add DRY hook lifecycle logging to daemon.log
81c168e docs(CLAUDE.md): add primary install command with timestamped logging
8735689 fix(hooks): return True for exit 0 to prevent hook errors
d3fd341 fix(plugin): remove duplicate hooks field from plugin.json
aad23fe fix(hooks): pass through exit code 2 and stderr for Bug #4669 workaround
```

### ❌ Known Issues

**1. SessionStart:resume Hook Error** - PARTIALLY IMPROVED
   - **Previous**: Hook hung with no output (hook timeout)
   - **Current**: "Daemon error (fail-open): 'NoneType' object has no attribute 'get'"
   - **Progress**: Hook now completes but returns error instead of hanging
   - **Root cause**: plan_export.py recover_unexported_plans() was hanging on SessionTimeoutError
   - **Temporary fix**: Plan recovery disabled (see TODO below)
   - **Remaining issue**: NoneType error suggests missing response handling

**2. Stop Hook Error** - LIKELY FIXED (needs testing)
   - **Previous**: "JSON validation failed: Invalid input"
   - **Fix applied**: hookSpecificOutput now included in all responses
   - **Status**: Needs Claude Code restart to verify
   - **Test**: Manually tested Stop hook returns valid JSON with hookSpecificOutput

## Key Commands

### Primary Install Command (from CLAUDE.md)
```bash
(uv run --project plugins/clautorun python -m clautorun --install --force && \
  cd plugins/clautorun && \
  uv tool install --force --editable . && \
  cd ../.. && \
  clautorun --restart-daemon) 2>&1 | tee "install-$(date +%Y%m%d-%H%M%S).log"
```

**Important**: Use 3-minute timeout when running via Bash tool

### Test Bug #4669 Workaround
```bash
# Create test file
echo "test" > /tmp/test-bug4669.txt

# Try to remove (should be blocked)
rm /tmp/test-bug4669.txt

# Expected behavior:
# - Tool BLOCKED (file still exists)
# - Trash suggestion shown to AI via stderr
# - Exit code 2 passed through all layers
# - NO "hook error" message

# Verify blocking worked
ls -la /tmp/test-bug4669.txt  # Should still exist
```

### Daemon Management
```bash
# Restart daemon
clautorun --restart-daemon

# Check daemon status
ps aux | grep "clautorun.*daemon" | grep -v grep
ls -la ~/.clautorun/daemon.sock

# View daemon logs
tail -100 ~/.clautorun/daemon.log | grep -B 3 -A 5 "CLIENT→DAEMON\|DAEMON→CLIENT"
```

### Debug Hook Issues
```bash
# Check hook execution log
tail -100 ~/.clautorun/hook_entry_debug.log

# Check daemon log for lifecycle
tail -100 ~/.clautorun/daemon.log

# Manual hook test
echo '{"session_id":"test","transcript_path":"test.jsonl","cwd":"/tmp","hook_event_name":"SessionStart","source":"startup"}' | \
  CLAUDE_PLUGIN_ROOT=~/.claude/plugins/cache/clautorun/clautorun/0.8.0 \
  uv run --quiet --project ~/.claude/plugins/cache/clautorun/clautorun/0.8.0 \
  python ~/.claude/plugins/cache/clautorun/clautorun/0.8.0/hooks/hook_entry.py 2>&1 | python3 -m json.tool
```

## Pending Issues

### CRITICAL TODO: Re-enable SessionStart Plan Recovery

**Location**: `plugins/clautorun/src/clautorun/plan_export.py` lines 913-945

**Status**: Temporarily disabled to debug hang

**Current Code State:**
```python
@app.on("SessionStart")
def recover_unexported_plans(ctx: EventContext) -> Optional[Dict]:
    """
    TODO: RE-ENABLE AFTER FIXING SessionStart:resume HANG

    Evidence: SessionStart:resume hooks hang/timeout (no output in hook_entry_debug.log)
    while SessionStart:startup works fine. Likely causes:
    1. SessionTimeoutError on line 930 when trying to acquire lock
    2. Returning None doesn't trigger default response from daemon
    3. Client waits forever for response that never comes

    Re-enable when:
    1. Verify disabling fixes the hang (restart Claude Code, check for error)
    2. Fix root cause (ensure daemon sends default response when all handlers return None)
    3. Test plan recovery works without hanging
    """
    logger.info(f"SessionStart handler called (source: {ctx.payload.get('source', 'unknown')})")
    return None  # Disabled temporarily - see TODO above

    # Original code preserved in comments (DO NOT DELETE)
```

**What needs to be fixed:**
1. **NoneType error**: "Daemon error (fail-open): 'NoneType' object has no attribute 'get'"
   - Likely in daemon event processing when handler returns None
   - Need to ensure daemon always sends valid response even when handlers return None

2. **SessionTimeoutError**: Plan recovery tries to acquire SessionLock but times out
   - May need shorter timeout
   - May need better lock cleanup
   - May need to skip recovery on timeout without hanging

3. **Default response**: When all event handlers return None, daemon must send default response
   - Current behavior: No response sent, client hangs waiting
   - Needed behavior: Send default allow response

**Steps to fix:**
1. Find where daemon processes event handler return values
2. Add default response when all handlers return None
3. Fix NoneType.get() error in daemon code
4. Test SessionStart:resume with plan recovery enabled
5. Verify no hanging or timeout errors
6. Re-enable the code and remove TODO

**Reference Files:**
- `hook_entry_debug.log` line 5476: SessionStart:resume hang evidence
- `daemon.log`: Shows lifecycle logging (CLIENT→DAEMON, DAEMON→CLIENT)
- `notes/hooks_api_reference.md` lines 326-440: Bug #4669 workaround details

## Outstanding TODOs

### TODO 1: Fix NoneType.get() Error in Daemon Event Processing

**Error Message:**
```
SessionStart:resume says: Daemon error (fail-open): 'NoneType' object has no attribute 'get'
```

**Leading Theories (Evidence-Based):**

**Theory A: Missing Default Response When Handlers Return None**
- **Evidence**: daemon.log shows no "DAEMON→CLIENT RESPONSE" for SessionStart:resume
- **Hypothesis**: When `recover_unexported_plans()` returns None, daemon may not send any response
- **Possible location**: Event processing code that iterates through handler chains
- **Expected behavior**: Should send default `{"continue": true, ...}` when no handler provides response
- **Risk**: If this is the issue, affects ALL event types when handlers return None

**Theory B: EventContext Attribute Access on None**
- **Evidence**: Error mentions `.get()` method, which is dict method
- **Hypothesis**: Code expects `ctx.payload` or `ctx` to be dict, but receives None in some path
- **Possible locations**:
  - `plugins/clautorun/src/clautorun/main.py`: Hook dispatching code
  - `plugins/clautorun/src/clautorun/core.py`: EventContext initialization
  - `plugins/clautorun/src/clautorun/daemon.py`: Request processing
- **Expected behavior**: EventContext should always have valid payload dict

**Theory C: Response Building After None Return**
- **Evidence**: Error occurs after handler returns None
- **Hypothesis**: Daemon tries to build response from None value: `response = None.get('field')`
- **Possible location**: Code that processes handler return values
- **Expected behavior**: Should check if return value is None before calling .get()

**Key Locations to Investigate:**
1. `plugins/clautorun/src/clautorun/main.py` - Hook handler dispatch
2. `plugins/clautorun/src/clautorun/daemon.py` - Event processing loop
3. `plugins/clautorun/src/clautorun/core.py:EventContext` - Response building

**Concrete Evidence:**
- Line 913 `@app.on("SessionStart")` - Handler registered
- Line 934 `return None` - Handler returns None for most cases
- No corresponding "DAEMON→CLIENT RESPONSE" in daemon.log for resume
- Error only appears after disabling plan recovery (handler now runs)

### TODO 2: Re-enable SessionStart Plan Recovery

**Location**: `plugins/clautorun/src/clautorun/plan_export.py:913-945`

**Blocked by**: TODO 1 (NoneType error must be fixed first)

**Steps Required:**
1. Fix daemon to handle None returns from event handlers
2. Test SessionStart:resume completes without errors
3. Uncomment original plan recovery code (lines 923-943)
4. Remove temporary disable comment and logger.info line
5. Test plan recovery actually exports plans
6. Verify no SessionTimeoutError hangs

**Uncertainty**: May need additional changes beyond fixing None handling:
- Timeout duration (currently 5 seconds in line 794)
- Lock cleanup on session end
- Concurrent session handling

### TODO 3: Verify Bug #4669 Workaround Works End-to-End

**Current Status**: Code changes complete and installed, but not tested in actual Claude Code session

**Test Required:**
```bash
# After restarting Claude Code session:
echo "test" > /tmp/test-workaround.txt
rm /tmp/test-workaround.txt

# Expected (if workaround works):
# - Tool BLOCKED
# - File still exists: ls /tmp/test-workaround.txt
# - Stderr suggestion appears in AI context
# - No "hook error" message

# Expected (if workaround fails):
# - Tool executes (file deleted)
# - OR "hook error" with exit code 2
```

**Uncertainty Factors:**
- Claude Code version compatibility (tested on v1.0.62+)
- Cache invalidation timing
- Hook load order if multiple plugins installed

### TODO 4: Test Stop Hook Fix in Real Usage

**Status**: Manual test passed, hookSpecificOutput now included

**Test Required:**
- Trigger Stop hook during actual session
- Verify no "JSON validation failed" error
- Check response includes hookSpecificOutput with hookEventName="Stop"

**Uncertainty**: Unknown what triggers Stop hooks naturally (may need to manually invoke)

### TODO 5: Commit Hook Lifecycle Logging Changes

**File**: `plugins/clautorun/src/clautorun/client.py`

**Decision Needed:**
- Keep logging for production debugging?
- Remove after SessionStart:resume issue resolved?
- Make logging conditional via env var?

**Considerations:**
- **Pro (keep)**: Helpful for diagnosing future hook issues
- **Pro (keep)**: Minimal performance impact (file append)
- **Con (remove)**: daemon.log grows over time
- **Con (remove)**: Adds slight overhead to every hook call

### TODO 6: Handle SessionTimeoutError Gracefully

**Current Behavior** (when re-enabled):
```python
except SessionTimeoutError as e:
    logger.warning(f"SessionStart plan recovery timeout: {e}")
    return None  # May cause NoneType error
```

**Possible Improvements:**
1. Return explicit allow response instead of None
2. Reduce timeout from 5 seconds to 2 seconds
3. Add retry logic with backoff
4. Skip recovery and log warning (current approach, if None handling fixed)

**Uncertainty**: Don't know which approach is best without understanding:
- Why lock timeout occurs (stale locks? concurrent access? slow I/O?)
- How often this happens (rare edge case vs common issue?)
- Whether plan recovery is critical or optional

## Leading Theories for Root Cause

### SessionStart:resume NoneType Error

**Most Likely Cause** (confidence: medium):
Daemon event processing expects all handlers to return dict, but doesn't handle None:
```python
# Hypothetical broken code (location unknown):
for handler in handlers:
    result = handler(ctx)
    decision = result.get('decision')  # ← NoneType.get() fails if result is None
```

**Supporting Evidence:**
- Error message: `'NoneType' object has no attribute 'get'`
- Timing: Error appeared after disabling plan recovery (handler now returns None immediately)
- Pattern: Only affects SessionStart:resume, not other events (may be specific to event chain handling)

**Alternative Theories:**

**Theory 2: EventContext Not Fully Initialized** (confidence: low)
- Some code path creates EventContext with None payload
- Code tries `ctx.payload.get('field')` but payload is None
- **Against this**: Would affect all events, not just SessionStart:resume

**Theory 3: Response Merging Logic** (confidence: low-medium)
- Multiple handlers return values, code merges them
- Merge code doesn't handle None values: `merged_response.update(None.get('key'))`
- **Needs**: Check if SessionStart has multiple handlers registered

### Stop Hook JSON Validation Error

**Status**: Likely fixed (hookSpecificOutput added), but needs verification

**Original Cause** (confidence: high):
Stop events used default response without hookSpecificOutput field:
```python
# Before fix (core.py:587-595):
return {
    "continue": True,
    "stopReason": "",
    "suppressOutput": False,
    "systemMessage": "",
    "decision": decision,
    "reason": reason_escaped
    # ← Missing hookSpecificOutput
}
```

**Fix Applied** (commit 008e08f):
Added hookSpecificOutput to all responses including Stop events.

**Uncertainty**: Needs actual Claude Code session restart to verify fix works in production.

### SessionStart:resume Hang (Before Disabling Plan Recovery)

**Original Symptom**: Hook produced no output, causing timeout

**Likely Cause** (confidence: high):
SessionTimeoutError in `recover_unexported_plans()`:
```python
# plan_export.py:794
lock_context = SessionLock(session_id, timeout=5.0)
```

**Possible Reasons for Lock Timeout:**
1. Previous session didn't release lock properly
2. Concurrent SessionStart hooks (resume + startup 0.6ms apart)
3. Stale lock file from crashed session
4. Lock held by different session trying same operation

**Evidence:**
- hook_entry_debug.log line 5476: SessionStart:resume with no CLI output
- SessionStart:startup works immediately after (line 5480)
- Sessions have different session_ids (not competing for same lock)

**Uncertainty**: Without seeing actual SessionTimeoutError in logs, can't confirm lock was the issue

## Key File Locations for Investigation

**Event Processing (Unknown - Need to Find):**
- Likely in: `plugins/clautorun/src/clautorun/daemon.py` or `main.py`
- Function name: Unknown (search for: "for handler in", "process_event", "handle_hook")
- Purpose: Iterates through event handler chains, collects responses
- **This is where the NoneType.get() error likely occurs**

**EventContext Creation:**
- File: `plugins/clautorun/src/clautorun/core.py`
- Class: `EventContext` (definition location: unknown line number)
- Methods: `respond()` at line 509, `command_response()` at line 597
- **Check**: How EventContext.payload is initialized

**SessionLock Implementation:**
- File: `plugins/clautorun/src/clautorun/session_manager.py`
- Class: `SessionLock` at line 54
- Timeout handling: Lines unknown
- **Check**: Lock acquisition and release logic

**Plan Recovery Code** (Temporarily Disabled):
- File: `plugins/clautorun/src/clautorun/plan_export.py`
- Function: `recover_unexported_plans()` at lines 913-945
- Lock timeout: Line 794 `SessionLock(session_id, timeout=5.0)`
- Exception handling: Lines 930-931 (SessionTimeoutError)

**Hook Registration:**
- File: `plugins/clautorun/src/clautorun/core.py`
- Decorator: `@app.on("SessionStart")` pattern
- Chain storage: Line 655 `"SessionStart": []`
- **Check**: How multiple handlers for same event are processed

## Investigation Strategy

**To find NoneType.get() error location:**
1. Search for `.get(` in daemon.py and main.py
2. Look for code that processes handler return values
3. Check for response merging or aggregation logic
4. Add defensive None checks before any `.get()` calls

**To verify theories:**
1. Add more detailed logging around event handler processing
2. Log when handlers return None vs dict
3. Log response building steps
4. Check if default response is sent when all handlers return None

**To test fixes:**
1. Restart Claude Code session after each fix
2. Check for SessionStart:resume hook error
3. Verify daemon.log shows complete request/response lifecycle
4. Test with plan recovery re-enabled

### Other Pending Work

**1. Verify Bug #4669 Workaround End-to-End**
   - Needs Claude Code session restart (hooks cached at session start)
   - Test rm blocking with exit code 2
   - Verify stderr feedback appears in AI context
   - Confirm no "hook error" message

**2. Test Stop Hook Fix**
   - hookSpecificOutput now included in all responses
   - Manual test passed
   - Needs Claude Code restart to verify in actual usage

**3. Commit Remaining Changes**
   - client.py: Hook lifecycle logging changes (currently uncommitted)
   - Consider whether to keep or remove logging after debugging

## File Locations

**Git Repository (Development):**
- `/Users/athundt/.claude/clautorun/`
- Plugin code: `plugins/clautorun/src/clautorun/`
- Hooks: `plugins/clautorun/hooks/`

**Cached Plugin (Runtime):**
- `/Users/athundt/.claude/plugins/cache/clautorun/clautorun/0.8.0/`
- Claude Code loads from this location
- Updated by running install command

**Log Files:**
- `~/.clautorun/daemon.log` - Daemon lifecycle and event processing
- `~/.clautorun/hook_entry_debug.log` - Hook entry script execution details
- `~/.clautorun/daemon_startup.log` - Daemon startup diagnostics

**Daemon:**
- Socket: `~/.clautorun/daemon.sock`
- PID: 99240 (current)
- Source: `/Users/athundt/.claude/clautorun/plugins/clautorun/src/`

## Architecture Summary

### Bug #4669 Workaround Architecture

```
Claude Code → hook_entry.py → client.py → daemon → client.py → hook_entry.py → Claude Code
                                  ↓                    ↓              ↓
                          Auto-detect CLI    Process event    Exit 0 or 2
                                  ↓                    ↓              ↓
                         Check decision      Return JSON   Pass through
                                  ↓                              ↓
                        Claude: exit 2 + stderr      Hook: sys.exit(code)
                        Gemini: exit 0 only               ↓
                                                    Claude Code: Receives
```

**Two Pathways:**
- **Pathway A (Claude Code deny)**: exit 2 + stderr → AI sees suggestion
- **Pathway B (Normal/Gemini)**: exit 0 only → JSON decision field

**Single Flag Check:**
- `should_use_exit2_workaround()` in config.py
- Checks CLAUTORUN_EXIT2_WORKAROUND env var (auto/always/never)
- Auto mode detects CLI type via GEMINI_SESSION_ID

### DRY Improvements

**client.py:**
- Single `output_hook_response()` handles all 4 output paths
- Single `_log_hook_lifecycle()` for consistent logging
- Single flag check determines exit code pathway

**core.py:**
- Unified response format with both Claude and Gemini fields
- hookSpecificOutput included in ALL event types
- Dynamic hookEventName from self._event

**hook_entry.py:**
- Accepts both exit codes 0 and 2
- Passes through stderr and exit code
- Simple conditional: if exit 2 → sys.exit(2), else → return True

## Next Actions

**For debugging SessionStart:resume error:**
1. Find daemon code that processes event handler return values
2. Add handling for when all handlers return None
3. Fix NoneType.get() error
4. Test with plan recovery re-enabled
5. Verify no hanging or timeout

**For testing Bug #4669 workaround:**
1. Restart Claude Code session (quit and reopen)
2. Run rm test in /tmp folder
3. Verify tool blocked, file exists, stderr suggestion appears
4. Confirm no "hook error" messages

**For documentation:**
1. Update README.md with Bug #4669 workaround details
2. Document the two-pathway architecture
3. Add troubleshooting section for hook errors

## References

- **Bug #4669**: https://github.com/anthropics/claude-code/issues/4669
- **Plan file**: `/Users/athundt/.claude/plans/staged-snacking-dewdrop.md`
- **Plan copy**: `notes/2026_02_12_2013_bug_4669_hook_entry_exit_code_fix.md`
- **Hooks API**: `notes/hooks_api_reference.md` lines 326-440
- **Claude Code Hooks Docs**: https://code.claude.com/docs/en/hooks
