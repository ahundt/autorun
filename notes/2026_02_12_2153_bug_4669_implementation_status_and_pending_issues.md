# Bug #4669 Workaround Implementation Status

**Date**: 2026-02-12 21:53
**Branch**: feature/gemini-cli-integration

## Quick Start: Install and Test

**Run from repository root** (`/Users/athundt/.claude/clautorun/`):

```bash
# Full install with daemon restart (requires 3-minute timeout)
(uv run --project plugins/clautorun python -m clautorun --install --force && \
  cd plugins/clautorun && \
  uv tool install --force --editable . && \
  cd ../.. && \
  clautorun --restart-daemon) 2>&1 | tee "install-$(date +%Y%m%d-%H%M%S).log"
```

**What this does:**
1. Syncs plugin to cache (both Claude Code and Gemini CLI)
2. Installs UV tool globally (`clautorun`, `claude-session-tools` commands)
3. Restarts daemon to pick up code changes (PID will change)
4. Logs output to timestamped file: `install-YYYYMMDD-HHMMSS.log`

**After installation:**
- Daemon running with latest code
- Check PID: `ps aux | grep "clautorun.*daemon"`
- Check socket: `ls -la ~/.clautorun/daemon.sock`
- **IMPORTANT**: Restart Claude Code session completely (quit and reopen) for hook changes to take effect

**Current daemon:** PID 99240

## Immediate Action Items

### 1. Fix NoneType Error in Daemon (BLOCKING)
**Error**: `'NoneType' object has no attribute 'get'`

**Action Steps:**
```bash
# Find event processing code
grep -rn "for.*handler\|process.*event\|\.chains\[" plugins/clautorun/src/clautorun/*.py

# Look for .get() calls on handler return values
grep -rn "handler(.*).get\|result.get\|response.get" plugins/clautorun/src/clautorun/daemon.py

# Add defensive None check before .get() calls
```

**What to fix:**
- Add `if result is None: result = default_response()` before accessing result.get()
- Or skip handlers that return None
- Or ensure all handlers return dict (never None)

**Test after fix:**
```bash
# Run install + restart
(install command from above)

# Restart Claude Code session
# Check for SessionStart:resume error - should be gone
```

### 2. Test Current State
**What works:**
- All 7 commits applied
- Daemon running with latest code
- Manual tests pass

**What to verify:**
```bash
# Restart Claude Code completely (Cmd+Q then reopen)

# Check for errors on startup:
# - SessionStart:resume should show NoneType error (known)
# - Stop hook error should be GONE (hookSpecificOutput added)

# Test rm blocking (Bug #4669 workaround):
echo "test" > /tmp/test-workaround.txt
rm /tmp/test-workaround.txt
# Expected: Tool blocked, file exists, trash suggestion shown

# Check logs:
tail -50 ~/.clautorun/daemon.log | grep "SessionStart\|Stop"
```

### 3. Re-enable Plan Recovery (After #1 Fixed)
**Location**: `plugins/clautorun/src/clautorun/plan_export.py:913-945`

**Action:**
- Uncomment lines 923-943 (original code)
- Remove temporary disable comment and logger.info
- Test SessionStart:resume completes without hang
- Verify plan export actually works

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

## Pre-Mortem Analysis: What Could Still Go Wrong

### Pre-Mortem 1: Bug #4669 Workaround Doesn't Actually Block Tools

**Scenario**: After restarting Claude Code, rm commands still execute files despite workaround

**Possible Failure Modes:**

1. **Claude Code Version Incompatibility**
   - Exit code 2 behavior may have changed in newer Claude Code versions
   - Stderr may not be fed back to AI in current version
   - JSON permissionDecision may have been fixed (making workaround unnecessary but harmless)
   - **Test**: Check Claude Code version, compare with known working versions

2. **Hook Execution Order Issue**
   - Other plugins' PreToolUse hooks may run after clautorun
   - Later hook returns exit 0, overriding our exit 2
   - Claude Code may only use the last hook's exit code
   - **Evidence to check**: Hook execution order in Claude Code logs

3. **Cache Not Actually Updated**
   - Claude Code may cache hooks.json separately from plugin code
   - May need to clear Claude Code's internal cache
   - Installation command may not trigger cache refresh
   - **Test**: Check if ~/.claude has other cache directories we're not updating

4. **Exit Code Lost in Subprocess Chain**
   - hook_entry.py calls sys.exit(2) but shell may not pass it through
   - UV or Python subprocess handling may normalize exit codes
   - Claude Code may sanitize hook exit codes
   - **Test**: Add logging to verify exit code reaches Claude Code process

5. **Auto-Detection Fails**
   - GEMINI_SESSION_ID detection logic may be wrong
   - Claude Code may set variables that look like Gemini
   - Always defaulting to "claude" may be incorrect
   - **Test**: Log actual environment variables during hook execution

**Risk Mitigation:**
- Manual testing required before claiming fix works
- Document exact Claude Code version where tested
- Provide rollback plan if workaround doesn't work

### Pre-Mortem 2: NoneType Error Fix Doesn't Resolve SessionStart:resume Issue

**Scenario**: After fixing NoneType.get() error, SessionStart:resume still fails or hangs

**Possible Failure Modes:**

1. **Multiple Root Causes**
   - NoneType error is symptom, not root cause
   - Plan recovery has additional issues beyond None handling
   - SessionLock may have deadlock conditions
   - **Next**: May need to debug plan recovery logic itself

2. **Wrong Error Location**
   - NoneType error may be in different code path than event processing
   - Could be in plan_export.py itself when re-enabled
   - Could be in session_manager.py SessionLock code
   - **Test**: Add try/except with full stack trace logging

3. **Race Condition**
   - Resume and startup hooks run 0.6ms apart
   - Concurrent database/file access may cause issues
   - Lock contention between parallel sessions
   - **Evidence**: Two SessionStart hooks logged almost simultaneously

4. **Missing Response Fields**
   - Even with hookSpecificOutput, Claude Code may need other fields
   - Response schema may have changed between versions
   - SessionStart may have different requirements than PreToolUse
   - **Test**: Compare working SessionStart:startup response format with resume attempt

5. **Transcript File Access Issue**
   - Resume may try to read previous session's transcript
   - Transcript file may be locked or unavailable
   - File I/O errors may cause silent failures
   - **Evidence to check**: Look for file access errors in daemon.log

**Risk Mitigation:**
- Don't re-enable plan recovery until NoneType fix proven to work
- Add comprehensive error logging before re-enabling
- Have disable-on-error fallback ready

### Pre-Mortem 3: Re-enabling Plan Recovery Causes New Problems

**Scenario**: After fixing NoneType error and re-enabling, new issues appear

**Possible Failure Modes:**

1. **Different Timeout Behavior**
   - SessionTimeoutError may manifest differently after fix
   - 5-second timeout may be too short under load
   - May timeout on valid operations, not just errors
   - **Consider**: Make timeout configurable, increase to 10 seconds

2. **Lock Cleanup Issues**
   - Previous sessions may leave stale locks
   - Lock files not properly cleaned up on crash
   - No mechanism to detect and remove stale locks
   - **Check**: session_manager.py lock cleanup logic

3. **Database Corruption**
   - ThreadSafeDB cache may have corrupted state
   - Active_plans tracking may have inconsistencies
   - Concurrent writes may cause data races
   - **Test**: Clear ~/.clautorun cache and test fresh

4. **Plan Detection Logic Bugs**
   - get_unexported() may return invalid data
   - Transcript parsing may fail on certain formats
   - Content hashing may have collisions
   - **Evidence needed**: Log what get_unexported() actually returns

5. **Notification Causes Hang**
   - Returning response with systemMessage may block
   - Long messages may exceed buffer limits
   - Response formatting may cause JSON issues
   - **Test**: Disable notify_claude and see if that helps

**Risk Mitigation:**
- Add timeout protection around entire recovery operation
- Wrap in try/except with specific error handling for each failure mode
- Add circuit breaker pattern (disable after N failures)
- Make plan recovery optional via config flag

### Pre-Mortem 4: Stop Hook Still Fails Despite hookSpecificOutput Fix

**Scenario**: After restart, Stop hooks show "Invalid input" error again

**Possible Failure Modes:**

1. **Missing Other Required Fields**
   - hookSpecificOutput alone may not be sufficient
   - Claude Code may validate other fields we haven't included
   - Field types may be wrong (string vs bool, etc.)
   - **Evidence needed**: Compare Stop response with working PreToolUse response

2. **Event Name Mismatch**
   - self._event may not match Claude Code's expected event name
   - May need "Stop" vs "SubagentStop" distinction
   - hookEventName validation may be case-sensitive
   - **Check**: core.py EventContext initialization for self._event value

3. **Response Schema Changed**
   - Claude Code version may expect different schema
   - Stop hooks may have different requirements than documented
   - Undocumented required fields
   - **Test**: Capture actual working hook response from another plugin

4. **Cache Staleness**
   - Plugin cache may not have been updated
   - Claude Code may load old version despite installation
   - Need to manually clear cache directories
   - **Verify**: Check md5 hash of cached core.py vs repo

5. **Multiple Hooks Conflict**
   - Other plugins may have Stop hooks that return invalid JSON
   - Claude Code may aggregate responses incorrectly
   - Last hook's response may override our correct one
   - **Check**: Find all plugins with Stop hooks

**Risk Mitigation:**
- Have manual test procedure ready to validate hook responses
- Document exact response format that works
- Add JSON schema validation before returning response

### Pre-Mortem 5: Exit Code 2 Path Causes Unintended Side Effects

**Scenario**: Exit code 2 works for blocking but breaks other functionality

**Possible Failure Modes:**

1. **Claude Code Treats Exit 2 as Fatal Error**
   - May disable hooks entirely after seeing exit 2
   - May show error UI that disrupts workflow
   - May mark plugin as "failed" and stop loading it
   - **Monitor**: Plugin status after several deny operations

2. **Stderr Pollution**
   - Every blocked command adds stderr output
   - Large stderr may overwhelm AI context
   - Repeated suggestions may confuse AI
   - **Consider**: Limit stderr output length or frequency

3. **Performance Impact**
   - Exit code 2 path may be slower than exit 0
   - May cause noticeable lag on every tool call
   - Daemon may handle exit 2 differently (slower path)
   - **Test**: Measure hook execution time before/after

4. **Shell Script Failures**
   - Scripts may check `$?` and treat exit 2 as error
   - Automation tools may abort on non-zero exit
   - CI/CD pipelines may fail
   - **Scope**: This affects Claude Code process, not user scripts

5. **Gemini CLI Compatibility Broken**
   - Auto-detection may be wrong, applying workaround to Gemini
   - Gemini may not handle exit 2 gracefully
   - May need manual testing with both CLIs
   - **Test**: Set GEMINI_SESSION_ID and verify exit 0 path used

**Risk Mitigation:**
- Document how to disable workaround (CLAUTORUN_EXIT2_WORKAROUND=never)
- Add escape hatch via environment variable
- Monitor for unexpected behavior after deployment
- Have rollback plan ready

### Pre-Mortem 6: Hook Lifecycle Logging Causes New Issues

**Scenario**: The DRY logging we added causes performance or stability problems

**Possible Failure Modes:**

1. **Log File Growth**
   - daemon.log grows unbounded (already 1.4MB)
   - May fill disk on long-running sessions
   - No log rotation mechanism
   - **Solution needed**: Add log rotation or size limits

2. **I/O Bottleneck**
   - Every hook call writes to disk
   - High-frequency tools (Read, Grep) may cause I/O bottleneck
   - File locking on log writes may cause contention
   - **Test**: Monitor hook latency under heavy load

3. **Exception in Logging Breaks Hooks**
   - Despite try/except, logging errors may propagate
   - Disk full may cause unhandled exceptions
   - Permission errors on log file may fail hooks
   - **Verify**: Ensure all logging has proper exception handling

4. **Logging to Wrong File**
   - Changed from hook_entry_debug.log to daemon.log
   - May interfere with daemon's own logging
   - File handle conflicts if daemon also writes to same file
   - **Check**: Verify daemon.log has proper file locking

5. **Timestamp Overhead**
   - datetime.datetime.now() called on every hook
   - May add measurable latency
   - String formatting may be expensive
   - **Consider**: Use simpler timestamp or disable in production

**Risk Mitigation:**
- Make logging conditional via CLAUTORUN_DEBUG env var
- Add log rotation after debugging complete
- Consider removing logging once issues resolved
- Document how to disable if causing problems

## Critical Assumptions That May Be Wrong

### Assumption 1: Claude Code Actually Respects Exit Code 2
- **Assumption**: Exit code 2 causes Claude Code to block tool execution
- **What if wrong**: Workaround doesn't work, tools still execute
- **How to validate**: Manual rm test with file existence check
- **Alternative**: May need different blocking mechanism

### Assumption 2: Daemon Processes Events Synchronously
- **Assumption**: Event handlers run in order, responses sent sequentially
- **What if wrong**: Race conditions, out-of-order responses
- **How to validate**: Check daemon threading/asyncio implementation
- **Alternative**: May need request/response correlation IDs

### Assumption 3: SessionStart:resume NoneType Error is in Daemon
- **Assumption**: Error occurs in daemon event processing code
- **What if wrong**: Error may be in client.py or hook_entry.py
- **How to validate**: Add try/except with stack traces everywhere
- **Alternative**: May be in plan_export.py itself when re-enabled

### Assumption 4: hookSpecificOutput Fix Solves Stop Hook Error
- **Assumption**: Missing hookSpecificOutput was the only issue
- **What if wrong**: Stop hooks may have other validation requirements
- **How to validate**: Test Stop hook after Claude Code restart
- **Alternative**: May need to match exact schema from working examples

### Assumption 5: Auto-Detection Correctly Identifies CLI Type
- **Assumption**: GEMINI_SESSION_ID is reliable indicator
- **What if wrong**: May apply workaround to Gemini or vice versa
- **How to validate**: Test with both CLIs, log detected type
- **Alternative**: May need additional detection heuristics

### Assumption 6: Cache Updates Take Effect Immediately
- **Assumption**: Running install command updates cache for current session
- **What if wrong**: Claude Code may require full restart to pick up changes
- **How to validate**: Check cache timestamps vs code changes
- **Alternative**: Always document "restart required" for changes

### Assumption 7: Disabling Plan Recovery Fixes Hang
- **Assumption**: recover_unexported_plans() was causing timeout
- **What if wrong**: Hang may be in other SessionStart handler (task_lifecycle.py)
- **How to validate**: Check if task_lifecycle also has SessionStart handler
- **Alternative**: May need to disable all SessionStart handlers to isolate

### Assumption 8: Returning None is Safe for Event Handlers
- **Assumption**: Handlers can return None without causing errors
- **What if wrong**: Daemon may expect all handlers to return dict
- **How to validate**: Check daemon event processing code contract
- **Alternative**: All handlers should return explicit allow response

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

## Summary: What We Know vs What We Don't Know

### ✅ What We Know (Evidence-Based)

**Bug #4669 Workaround Architecture:**
- Code changes complete: 7 commits on feature/gemini-cli-integration
- Auto-detection implemented: detect_cli_type() and should_use_exit2_workaround()
- Exit code pathway: hook_entry.py accepts 0 and 2, passes through to Claude Code
- Manual tests pass: Stop hook returns valid JSON, SessionStart works in isolation
- Daemon running: PID 99240, loaded from source directory

**SessionStart:resume Hang:**
- SessionStart:resume hooks produce no output (hook_entry_debug.log:5476)
- SessionStart:startup works immediately after with full output
- Sessions have different IDs (not lock conflict on same session)
- After disabling plan recovery: NoneType error appears instead of hang
- daemon.log shows no DAEMON→CLIENT RESPONSE for resume events

**Stop Hook Issue:**
- Previously missing hookSpecificOutput field in default responses
- Fix applied: Added to all response paths in core.py
- Manual test confirms valid JSON with hookSpecificOutput
- Production verification pending (needs Claude Code restart)

### ❌ What We Don't Know (Uncertainties)

**NoneType Error Root Cause:**
- **Unknown**: Exact line where `.get()` is called on None
- **Unknown**: Whether error is in daemon.py, main.py, or core.py
- **Unknown**: If this affects other event types when handlers return None
- **Need**: Stack trace or line-by-line debugging to locate

**Bug #4669 Workaround Effectiveness:**
- **Unknown**: If exit code 2 actually blocks tools in current Claude Code version
- **Unknown**: If stderr is actually fed back to AI context
- **Unknown**: If cache updates are actually being loaded by Claude Code
- **Need**: End-to-end test with fresh Claude Code session

**Plan Recovery Hang Cause:**
- **Unknown**: If SessionTimeoutError was actually occurring (not logged)
- **Unknown**: Why lock timeout would happen for resume but not startup
- **Unknown**: If there are stale lock files we haven't found
- **Need**: Re-enable with comprehensive error logging to capture actual exception

**Daemon Event Processing:**
- **Unknown**: How daemon iterates through event handler chains
- **Unknown**: What happens when all handlers return None
- **Unknown**: If there's supposed to be a default response mechanism
- **Need**: Read daemon.py or main.py code to understand architecture

**Hook Execution Order:**
- **Unknown**: If clautorun hooks run before or after other plugins
- **Unknown**: If other plugins' hooks can override our responses
- **Unknown**: How Claude Code aggregates multiple hook responses
- **Need**: Test with multiple plugins enabled, check execution order

### ⚠️ High-Risk Areas

**1. Exit Code 2 Pathway (Unverified in Production)**
- Theory: Works based on Bug #4669 discussion
- Reality: Not tested in actual Claude Code session
- Risk: May not block tools, may cause "hook error", may disable plugin
- **Mitigation**: Manual test required before claiming success

**2. Event Handler None Returns (Architectural Assumption)**
- Theory: Handlers can safely return None
- Reality: May violate daemon's contract expectations
- Risk: May cause errors in all event types, not just SessionStart
- **Mitigation**: Check if None is documented as valid return value

**3. Cache Synchronization (Timing Assumption)**
- Theory: Install command updates cache for current session
- Reality: Claude Code may cache hooks at app startup, not session startup
- Risk: Changes may not take effect until full app restart
- **Mitigation**: Always document "requires app restart" for hook changes

**4. Auto-Detection Logic (Environment Variable Assumption)**
- Theory: GEMINI_SESSION_ID reliably distinguishes CLIs
- Reality: May have false positives/negatives
- Risk: Wrong exit code path applied, breaking one or both CLIs
- **Mitigation**: Add logging to verify detected CLI type

**5. Plan Recovery Re-enable (Multiple Unknowns)**
- Theory: Fixing NoneType error will allow safe re-enable
- Reality: May have additional issues (locks, timeouts, race conditions)
- Risk: Hang may return, or new errors may appear
- **Mitigation**: Re-enable incrementally with extensive error logging

## What Success Looks Like (Testable Outcomes)

### ✅ Bug #4669 Workaround Working
**Concrete test:**
```bash
echo "test" > /tmp/test.txt
rm /tmp/test.txt
ls /tmp/test.txt  # File still exists
```
**Expected**: File exists, AI sees trash suggestion, no hook error

### ✅ SessionStart:resume Fixed
**Observable**: Restart Claude Code session
**Expected**: No "SessionStart:resume hook error" message

### ✅ Stop Hook Fixed
**Observable**: Stop hook executes during session
**Expected**: No "JSON validation failed" error

### ✅ Plan Recovery Working
**Observable**: Exit plan mode with Option 1 (fresh context), start new session
**Expected**: Plan exported to notes/ with "(from fresh context)" notation

## References

- **Bug #4669**: https://github.com/anthropics/claude-code/issues/4669
- **Plan file**: `/Users/athundt/.claude/plans/staged-snacking-dewdrop.md`
- **Plan copy**: `notes/2026_02_12_2013_bug_4669_hook_entry_exit_code_fix.md`
- **Hooks API**: `notes/hooks_api_reference.md` lines 326-440
- **Claude Code Hooks Docs**: https://code.claude.com/docs/en/hooks
