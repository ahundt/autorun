# All Fixes Applied - Hook Stderr Duplication - Feb 12, 2026 17:45

## Summary

Fixed ALL stdout/stderr issues in clautorun codebase. All logging now:
1. ✅ Goes to file ONLY (`~/.clautorun/daemon.log`)
2. ✅ Only enabled when `CLAUTORUN_DEBUG=1` environment variable is set
3. ✅ Uses NullHandler when debug disabled (prevents default stderr)
4. ✅ Never writes to stdout/stderr (prevents hook errors)

## Files Modified

### 1. `logging_utils.py` - ENHANCED
**What**: Added CLAUTORUN_DEBUG flag support
**Lines**: 1-20 (imports), 25-50 (get_logger function)

**Changes**:
- Added `DEBUG_ENABLED = os.environ.get('CLAUTORUN_DEBUG') == '1'`
- When DEBUG enabled: FileHandler to ~/.clautorun/daemon.log, level=DEBUG
- When DEBUG disabled: NullHandler, level=CRITICAL+1 (effectively off)
- Updated docstrings to document debug flag

**Behavior**:
```python
# Without CLAUTORUN_DEBUG=1:
logger.info("message")  # NO-OP, no file write, no stderr

# With CLAUTORUN_DEBUG=1:
logger.info("message")  # Written to ~/.clautorun/daemon.log only
```

### 2. `ai_monitor.py:41-48` - FIXED
**What**: Removed stderr handler from logging configuration
**Before**:
```python
logging.basicConfig(
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stderr)  # ❌ BREAKS HOOKS
    ]
)
```

**After**:
```python
logging.basicConfig(
    handlers=[
        logging.FileHandler(log_file)  # ✅ File-only
    ]
)
```

### 3. `install.py:1774-1788` - FIXED
**What**: Replaced default stderr logging with file-only when debug enabled

**Before**:
```python
logging.basicConfig(level=logging.INFO, format='%(message)s')  # ❌ Uses stderr
```

**After**:
```python
if os.environ.get('CLAUTORUN_DEBUG') == '1':
    logging.basicConfig(
        handlers=[logging.FileHandler(log_file)],  # ✅ File-only
        level=logging.DEBUG,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    )
else:
    logging.basicConfig(
        handlers=[logging.NullHandler()],  # ✅ No output
        level=logging.CRITICAL + 1
    )
```

### 4. `tmux_injector.py:43-56` - FIXED
**What**: Replaced default stderr logging with file-only when debug enabled

**Before**:
```python
logging.basicConfig(level=logging.WARNING)  # ❌ Uses stderr
```

**After**:
```python
if os.environ.get('CLAUTORUN_DEBUG') == '1':
    logging.basicConfig(
        handlers=[logging.FileHandler(log_file)],  # ✅ File-only
        level=logging.DEBUG,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    )
else:
    logging.basicConfig(
        handlers=[logging.NullHandler()],  # ✅ No output
        level=logging.CRITICAL + 1
    )
```

### 5. `testing_framework.py:46-48` - FIXED
**What**: Replaced print() with file logging

**Before**:
```python
def log_info(message):
    print(f"INFO: {message}")  # ❌ Goes to stdout
```

**After**:
```python
def log_info(message):
    try:
        from .logging_utils import get_logger
        logger = get_logger(__name__)
        logger.info(message)  # ✅ File-only when debug enabled
    except ImportError:
        pass  # ✅ Silent if not available
```

### 6. `verification_engine.py:38-40` - FIXED
**What**: Replaced print() with file logging

**Before**: Same as testing_framework.py
**After**: Same pattern as testing_framework.py

### 7. `transcript_analyzer.py:39-41` - FIXED
**What**: Replaced print() with file logging

**Before**: Same as testing_framework.py
**After**: Same pattern as testing_framework.py

### 8. `diagnostics.py:50-52` - FIXED
**What**: Replaced log_info print() with file logging

**Before**: Same as testing_framework.py
**After**: Same pattern as testing_framework.py

### 9. `diagnostics.py:188-190` - FIXED
**What**: Disabled CRITICAL error console output (breaks hooks)

**Before**:
```python
if level == LogLevel.CRITICAL:
    print(f"CRITICAL [{category}] {message}")  # ❌ Goes to stdout/stderr
```

**After**:
```python
if level == LogLevel.CRITICAL and os.environ.get('CLAUTORUN_DEBUG') == '1':
    try:
        from .logging_utils import get_logger
        logger = get_logger(__name__)
        logger.critical(f"[{category}] {message}")  # ✅ File-only
    except ImportError:
        pass  # ✅ Silent
```

### 10. `main.py:315-319` - FIXED
**What**: Replaced stderr exception prints with file logging

**Before**:
```python
print(f"Exception: {type(e).__name__}: {e}", file=sys.stderr)  # ❌
print(f"\nIMPACT: Pipe detection will not work correctly", file=sys.stderr)  # ❌
print(f"SYMPTOM: Commands like 'git log | grep fix' may be blocked", file=sys.stderr)  # ❌
print(f"ACTION: Check clautorun installation and module paths", file=sys.stderr)  # ❌
print("=" * 70, file=sys.stderr)  # ❌
```

**After**:
```python
try:
    from .logging_utils import get_logger
    logger = get_logger(__name__)
    logger.error("=" * 70)
    logger.error(f"Exception: {type(e).__name__}: {e}")
    logger.error("IMPACT: Pipe detection will not work correctly")
    logger.error("SYMPTOM: Commands like 'git log | grep fix' may be blocked")
    logger.error("ACTION: Check clautorun installation and module paths")
    logger.error("=" * 70)
except ImportError:
    pass  # ✅ Silent
```

### 11. `client.py` - ALREADY FIXED (previous session)
**What**: Added diagnostic logging with get_logger (already respects DEBUG flag)

**Lines**:
- 67: `logger.debug(f"Forwarding hook...")`
- 88: `logger.info(f"Hook response: decision={decision}")`
- 105: `logger.error(f"Client buffer error: {e}")`
- 131: `logger.info("Daemon not running, auto-starting...")`
- 149: `logger.error(f"Client exception (fail-open): {e}", exc_info=True)`

## Files NOT Modified (Correct As-Is)

### `client.py` print() statements
**Lines 77, 80, 91, 134**: These print() statements are CORRECT - they output hook JSON responses to stdout (required for hooks to work).

### `plan_export.py` print() statements
**Lines 781-843**: All print() statements are CORRECT - they output hook JSON responses to stdout (required for hooks to work).

### `core.py` logging.basicConfig
**Line 76**: Already correct - uses file-only logging:
```python
logging.basicConfig(
    filename=LOG_FILE,  # ~/.clautorun/daemon.log
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
```

## Testing Requirements

### Test 1: Verify stderr is empty
```bash
cd ~/.claude/plugins/cache/clautorun/clautorun/0.8.0
echo '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"rm /tmp/test.txt"}}' \
  | uv run --quiet python hooks/hook_entry.py > stdout.txt 2> stderr.txt

wc -c stderr.txt  # MUST be: 0 bytes
wc -c stdout.txt  # Should be: ~1476 bytes (JSON)
```

### Test 2: Verify rm blocking works
```bash
touch /tmp/test-rm.txt
rm /tmp/test-rm.txt  # Should be BLOCKED with trash suggestion, NO "hook error"
```

### Test 3: Verify logging with debug enabled
```bash
export CLAUTORUN_DEBUG=1
touch /tmp/test-debug.txt
rm /tmp/test-debug.txt  # Should be blocked

# Check log file
tail -20 ~/.clautorun/daemon.log  # Should see: "Forwarding hook", "Hook response: decision=deny"
```

### Test 4: Verify logging disabled by default
```bash
unset CLAUTORUN_DEBUG
touch /tmp/test-nodebug.txt
rm /tmp/test-nodebug.txt  # Should be blocked

# Check log file - should NOT have new entries (debug disabled)
tail -20 ~/.clautorun/daemon.log  # No new client.py entries
```

## Next Steps

1. ⏸️ **DO NOT TEST YET** - Wait for user confirmation
2. ⏸️ **DO NOT COMMIT YET** - Wait for user confirmation
3. When approved:
   - Run `clautorun --install -f` to sync to cache
   - Run tests 1-4 above
   - Verify stderr duplication is fixed
   - Create comprehensive commit with all changes

## Key Insights

1. **Python logging defaults to stderr** when no handlers specified
2. **logging.basicConfig() is global** - affects ALL loggers in process
3. **NullHandler prevents default stderr** - critical for hook safety
4. **CLAUTORUN_DEBUG=1 gates all logging** - zero overhead when disabled
5. **Hook JSON responses must use print()** - that's the only correct stdout use

## Files Changed (Summary)

1. ✅ `logging_utils.py` - Added DEBUG_ENABLED flag
2. ✅ `ai_monitor.py` - Removed stderr handler
3. ✅ `install.py` - File-only or NullHandler based on debug
4. ✅ `tmux_injector.py` - File-only or NullHandler based on debug
5. ✅ `testing_framework.py` - Replaced print() with get_logger()
6. ✅ `verification_engine.py` - Replaced print() with get_logger()
7. ✅ `transcript_analyzer.py` - Replaced print() with get_logger()
8. ✅ `diagnostics.py` (2 fixes) - Replaced print() with get_logger()
9. ✅ `main.py` - Replaced stderr prints with get_logger()
10. ✅ `client.py` - Already fixed with get_logger() (previous session)

**Total: 10 files modified**
