# Hook Stderr Duplication Investigation - Feb 12, 2026 17:40

## Problem Statement

Claude Code hooks failing with "PreToolUse:Bash hook error" - rm blocking not working.

**Symptoms**:
- `rm /tmp/test.txt` reaches interactive prompt instead of being blocked
- UI shows "⏺ Bash(rm /tmp/test-rm-blocking.txt) ⎿ PreToolUse:Bash hook error"
- Safety features (rm blocking, git safety) silently disabled

## Root Cause

Hook responses being written to **BOTH stdout AND stderr**. Claude Code treats ANY stderr output as "hook error" and ignores the JSON response.

**Test confirmation**:
```bash
$ echo '{"hook_event_name":"PreToolUse"...}' | hook_entry.py > stdout 2> stderr
STDOUT: 1476 bytes (correct JSON)
STDERR: 1476 bytes (DUPLICATE JSON - causes hook error)
```

## Investigation Timeline

### Test 1: Direct Hook Test (17:08)
```bash
cd ~/.claude/plugins/cache/clautorun/clautorun/0.8.0
echo '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"rm /tmp/test.txt"}}' \
  | uv run --quiet python hooks/hook_entry.py 2>&1
```

**Result**: JSON to stdout ✅, but ALSO duplicated to stderr ❌

### Test 2: Socket Test (17:15)
```bash
python3 -c "
import socket, json
from pathlib import Path
payload = {'hook_event_name': 'PreToolUse', 'tool_name': 'Bash', ...}
sock.sendall((json.dumps(payload) + '\n').encode())
resp = sock.recv(16384)
"
```

**Result**: Daemon returns clean JSON (no stderr) ✅

**Conclusion**: Duplication happens in hook_entry.py → client.py pathway, NOT in daemon.

### Test 3: Direct Python vs UV Run (17:30)
```bash
# Test 1: Direct python3
echo '{}' | python3 /tmp/debug_client.py > stdout 2> stderr
STDERR: Clean ✅

# Test 2: Via uv run
echo '{}' | uv run python3 /tmp/debug_client.py > stdout 2> stderr
STDERR: JSON duplicated ❌
```

**Hypothesis**: UV or logging configuration triggers stderr output.

### Test 4: Logging Module Defaults (17:35)
```python
import logging
logger = logging.getLogger('test')
logger.warning('Message')  # Goes to stderr by DEFAULT
```

**Discovery**: Python's logging writes to stderr by default when no handlers configured!

### Test 5: Search for Logging Configurations (17:36)
```bash
grep -r "logging.basicConfig\|StreamHandler" --include="*.py"
```

**Found**:
- `ai_monitor.py:45` - `logging.StreamHandler(sys.stderr)` ❌ CRITICAL
- `core.py` - File-only logging ✅
- `tmux_injector.py` - basicConfig(level=WARNING) - uses default stderr ❌
- `install.py` - basicConfig(level=INFO) - uses default stderr ❌

## Files with Print/Stderr/Stdout (Full Search Results)

**Total**: 1722 lines found in /tmp/print_search.txt

### Critical Files:

**client.py** (4 print statements):
- Line 77: `print(json.dumps(resp_json))` - Hook response (KEEP - correct)
- Line 80: `print(resp_text)` - Fallback (KEEP - correct)
- Line 91: `print(json.dumps({...}))` - Buffer error (KEEP - correct)
- Line 134: `print(json.dumps({...}))` - Exception (KEEP - correct)

**ai_monitor.py** (FIXED):
- Line 45: `logging.StreamHandler(sys.stderr)` - REMOVED ✅

**plan_export.py** (10+ print statements):
- Lines 781-843: All `print(json.dumps({...}))` for hook responses
- KEEP these - they're correct hook responses

**Fallback logging** (needs replacement):
- `testing_framework.py:48` - `print(f"INFO: {message}")`
- `verification_engine.py:40` - `print(f"INFO: {message}")`
- `transcript_analyzer.py:41` - `print(f"INFO: {message}")`
- `diagnostics.py:52,184` - `print(f"INFO/CRITICAL: {message}")`

## Fixes Implemented

### Phase 1: Remove Stderr Handler ✅
**File**: `plugins/clautorun/src/clautorun/ai_monitor.py:38-47`

**Before**:
```python
logging.basicConfig(
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stderr)  # BREAKS HOOKS
    ]
)
```

**After**:
```python
logging.basicConfig(
    handlers=[
        logging.FileHandler(log_file)  # File-only
    ]
)
```

### Phase 2: Create Logging Utility ✅
**New file**: `plugins/clautorun/src/clautorun/logging_utils.py`

```python
def get_logger(name: str) -> logging.Logger:
    """Get file-only logger (never stdout/stderr)."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.FileHandler(LOG_FILE)
        handler.setFormatter(...)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
```

**Purpose**: Centralized file-only logging that never writes to stderr.

### Phase 3: Add Diagnostic Logging to client.py ✅
**File**: `plugins/clautorun/src/clautorun/client.py`

Added:
```python
from .logging_utils import get_logger
logger = get_logger(__name__)

# Line 67: Log forwarding
logger.debug(f"Forwarding hook: event={event}, tool={tool}")

# Line 88: Log decision
logger.info(f"Hook response: decision={decision}")

# Line 105: Log errors
logger.error(f"Client buffer error: {e}")

# Line 131: Log daemon auto-start
logger.info("Daemon not running, auto-starting...")

# Line 149: Log exceptions
logger.error(f"Client exception (fail-open): {e}", exc_info=True)
```

## Installation Issues Found

### Issue 1: logging_utils.py Not Installed
```bash
$ ls ~/.claude/plugins/cache/clautorun/clautorun/0.8.0/src/clautorun/logging_utils.py
ls: No such file or directory
```

**Cause**: File created but not synced to cache yet.
**Fix**: Need to run `clautorun --install -f`

### Issue 2: Daemon Restart Failed
```bash
$ clautorun --install -f
...
✗ ERROR: Daemon did not start
```

**Status**: Daemon is actually running (PID 46034 confirmed), installer just reports error.

## Testing Status

### Test 1: Direct Hook Test
**Command**:
```bash
echo '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"rm /tmp/test.txt"}}' \
  | uv run --quiet python hooks/hook_entry.py > stdout.txt 2> stderr.txt
wc -c stderr.txt  # Expected: 0 bytes
```

**Current Result**: 1476 bytes in stderr ❌ (still duplicating)
**Expected**: 0 bytes

**Reason**: logging_utils.py not yet installed to cache.

### Test 2: Live Hook Test
**Command**:
```bash
touch /tmp/test-rm.txt
rm /tmp/test-rm.txt
```

**Current Result**: Interactive prompt, hook error ❌
**Expected**: Blocked with trash suggestion, no hook error

## Remaining Work

### TODO 1: Complete Installation
- [ ] Force install to sync logging_utils.py to cache
- [ ] Restart daemon to pick up ai_monitor.py fix
- [ ] Verify `wc -c stderr.txt` returns 0

### TODO 2: Add Debug Flag Support (User Request)
Make all logging depend on `CLAUTORUN_DEBUG` env var or `--debug` CLI param:

```python
# In logging_utils.py
DEBUG_ENABLED = os.environ.get('CLAUTORUN_DEBUG') == '1'

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if DEBUG_ENABLED:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)
    return logger
```

### TODO 3: Log JSON Outputs (User Request)
When debug enabled, log all JSON hook responses:

```python
# In client.py line 90
if DEBUG_ENABLED:
    logger.debug(f"Hook JSON response: {json.dumps(resp_json)}")
```

### TODO 4: Actionable Debug Info (User Request)
Include:
- Daemon PID
- JSON files written
- Socket paths
- Timestamps

Example:
```python
logger.info(f"Forwarding to daemon PID={get_daemon_pid()}, sock={SOCKET_PATH}")
```

## Success Criteria

1. ✅ `stderr.txt` is 0 bytes when running hooks
2. ✅ rm command blocked without "hook error"
3. ✅ All diagnostics in `~/.clautorun/daemon.log`
4. ✅ No print() except for hook JSON to stdout
5. ✅ Tests pass

## References

- [UV Running Scripts](https://docs.astral.sh/uv/guides/scripts/)
- [UV Issue #12636: python -u equivalent](https://github.com/astral-sh/uv/issues/12636)
- [UV Commands Reference](https://docs.astral.sh/uv/reference/cli/)
- CLAUDE.md:84-100 - Hook Error Prevention
- GitHub Issues: #4669, #18312, #13744, #20946

## Key Insights

1. **Python logging defaults to stderr** - Must explicitly configure file-only handlers
2. **logging.basicConfig() is global** - First call wins, affects all subsequent loggers
3. **UV doesn't cause duplication** - It's logging configuration
4. **Daemon is clean** - Only returns JSON once to stdout
5. **Hook pathway is the issue** - hook_entry.py → client.py uses logging

## Relevant Files, Functions, and Line Ranges

### Modified Files

#### 1. `plugins/clautorun/src/clautorun/ai_monitor.py`
**Line 38-47**: Removed stderr handler from logging configuration
```python
# Before (BROKEN - causes stderr duplication):
logging.basicConfig(
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stderr)  # Line 45 - REMOVED
    ]
)

# After (FIXED):
logging.basicConfig(
    handlers=[
        logging.FileHandler(log_file)  # File-only logging
    ]
)
```

#### 2. `plugins/clautorun/src/clautorun/client.py`
**Imports (after line 36)**:
```python
from .logging_utils import get_logger
logger = get_logger(__name__)
```

**Line 67**: Added forwarding diagnostic
```python
logger.debug(f"Forwarding hook to daemon: event={payload.get('hook_event_name')}, tool={payload.get('tool_name')}")
```

**Line 77-90**: Hook response with logging (in `async def forward()`)
```python
# Line 82-90: Parse and log response
try:
    resp_json = json.loads(resp_text)
    resp_json.pop("_exit_code_2", None)
    decision = resp_json.get('hookSpecificOutput', {}).get('permissionDecision', resp_json.get('decision', 'allow'))
    logger.info(f"Hook response: decision={decision}")  # Line 88
    print(json.dumps(resp_json))  # Line 90 - KEEP (stdout)
```

**Line 102-109**: Buffer error logging
```python
except asyncio.LimitOverrunError as e:
    logger.error(f"Client buffer error: {e}")  # Line 105
    print(json.dumps({...}))  # Line 106 - KEEP (stdout)
```

**Line 127-142**: Daemon auto-start logging
```python
if not daemon_alive:
    logger.info("Daemon not running, auto-starting...")  # Line 131
    # ... spawn daemon ...
else:
    logger.debug(f"Daemon alive (PID in lock file), retrying connection (depth={depth})")  # Line 135
```

**Line 142-153**: Exception handling with logging
```python
try:
    asyncio.run(forward())
except SystemExit:
    raise
except Exception as e:
    logger.error(f"Client exception (fail-open): {e}", exc_info=True)  # Line 149
    print(json.dumps({...}))  # Line 150 - KEEP (stdout)
```

### New Files Created

#### 3. `plugins/clautorun/src/clautorun/logging_utils.py`
**Entire file** - New utility for file-only logging

**Function**: `get_logger(name: str) -> logging.Logger`
```python
def get_logger(name: str) -> logging.Logger:
    """Get file-only logger (never writes to stdout/stderr)."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.FileHandler(LOG_FILE)  # ~/.clautorun/daemon.log
        handler.setFormatter(
            logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
```

### Files with Print Statements (Found but NOT Modified Yet)

#### 4. `plugins/clautorun/src/clautorun/plan_export.py`
**Lines with print()**: 781, 785, 790, 796, 806, 813, 816, 823, 831, 837, 840, 843

All are correct hook responses - KEEP these:
```python
# Line 781
print(json.dumps({"continue": True, "suppressOutput": True}))

# Line 831
print(json.dumps({
    "continue": True,
    "systemMessage": f"📋 Recovered unexported plan: {result['message']}",
}))
```

#### 5. `plugins/clautorun/src/clautorun/testing_framework.py`
**Line 48**: Fallback logging
```python
def log_info(message):
    print(f"INFO: {message}")  # TODO: Replace with get_logger()
```

#### 6. `plugins/clautorun/src/clautorun/verification_engine.py`
**Line 40**: Fallback logging
```python
def log_info(message):
    print(f"INFO: {message}")  # TODO: Replace with get_logger()
```

#### 7. `plugins/clautorun/src/clautorun/transcript_analyzer.py`
**Line 41**: Fallback logging
```python
def log_info(message):
    print(f"INFO: {message}")  # TODO: Replace with get_logger()
```

#### 8. `plugins/clautorun/src/clautorun/diagnostics.py`
**Line 52**: Fallback info logging
```python
def log_info(message):
    print(f"INFO: {message}")  # TODO: Replace with get_logger()
```

**Line 184**: Critical error logging (console output intentional)
```python
if level == LogLevel.CRITICAL:
    print(f"CRITICAL [{category}] {message}")  # May be intentional for visibility
```

### Hook Entry Points

#### 9. `plugins/clautorun/hooks/hook_entry.py`
**Line 189-247**: `try_cli()` function
- Calls clautorun CLI binary
- Line 238: `print(result.stdout, end="")` - Forwards CLI stdout

**Line 389-440**: `run_fallback()` function
- Line 416-418: Imports from clautorun package
- Triggers logging configuration

**Line 447-482**: `main()` function
- Entry point for hooks
- Reads stdin, tries CLI, falls back to direct import

#### 10. `plugins/clautorun/src/clautorun/__main__.py`
**Line 600+**: `main()` function
- CLI entry point
- Not directly involved in hook pathway

#### 11. `plugins/clautorun/src/clautorun/core.py`
**Line 82-91**: Logging configuration (CORRECT - file only)
```python
logging.basicConfig(
    filename=LOG_FILE,  # ~/.clautorun/daemon.log
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger("clautorun")
```

**Line 799-847**: `handle_client()` async function
- Daemon's client handler
- Receives hook payloads via socket
- Calls dispatch()

**Line 704-732**: `dispatch()` method
- Routes events through chains
- Calls check_blocked_commands() from plugins.py

#### 12. `plugins/clautorun/src/clautorun/plugins.py`
**Line 446-544**: `check_blocked_commands()` function
- Decorated with `@app.on("PreToolUse")`
- Line 456-463: Determines event type (bash/file)
- Line 521: Pattern matching
- Line 537: `ctx.deny(msg)` - Blocks tool

#### 13. `plugins/clautorun/src/clautorun/integrations.py`
**Line 1-200+**: Integration definitions
- DEFAULT_INTEGRATIONS list
- Contains rm, git reset, git clean patterns

### Configuration Files

#### 14. `plugins/clautorun/hooks/hooks.json`
**All hook entries**: Use `uv run --quiet` to suppress UV's own output
```json
"command": "uv run --quiet --project ${CLAUDE_PLUGIN_ROOT} python ${CLAUDE_PLUGIN_ROOT}/hooks/hook_entry.py"
```

#### 15. `plugins/clautorun/hooks/gemini-hooks.json`
**All hook entries**: Use `uv run --quiet` with ${extensionPath}
```json
"command": "uv run --quiet --project ${extensionPath} python ${extensionPath}/hooks/hook_entry.py"
```

### Installation & Cache

#### 16. Cache locations
- Source: `~/.claude/clautorun/plugins/clautorun/src/clautorun/`
- Claude Code cache: `~/.claude/plugins/cache/clautorun/clautorun/0.8.0/src/clautorun/`
- Gemini cache: `~/.gemini/extensions/cr/src/clautorun/`

**Missing from cache**: `logging_utils.py` (not yet installed)

## Next Session Actions

1. Complete installation with force flag
2. Test stderr.txt shows 0 bytes
3. Test rm blocking works without hook error
4. Add CLAUTORUN_DEBUG support
5. Add JSON logging when debug enabled
6. Add actionable debug info (PID, paths, timestamps)
7. Commit all changes with comprehensive message
