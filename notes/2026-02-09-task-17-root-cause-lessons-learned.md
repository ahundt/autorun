# Task #17: Pipe Blocking Bug - Root Cause Analysis & Lessons Learned

**Date**: 2026-02-09
**Issue**: Commands with pipes (e.g., `git log | grep fix`, `cargo build 2>&1 | head -50`) were being incorrectly blocked
**Severity**: CRITICAL - Broke core workflow functionality
**Resolution Time**: ~6 hours of debugging

---

## Executive Summary

**Root Cause**: Missing predicate registration in `main.py:_PREDICATES` lookup table
**Primary File**: `/Users/athundt/.claude/clautorun/plugins/clautorun/src/clautorun/main.py:816-834`
**Impact**: ALL piped commands with grep/head/tail/cat were blocked despite correct `_not_in_pipe()` predicate logic
**Fix Complexity**: 3 lines of code
**Why It Was Hard To Find**: Two separate code paths for command blocking weren't properly integrated

---

## Root Cause Analysis

### The Bug

The `_PREDICATES` lookup table in `main.py:816-834` was missing the `_not_in_pipe` entry:

```python
# BEFORE (BROKEN)
_PREDICATES = {
    "_has_unstaged_changes": _has_unstaged_changes,
    "_file_has_unstaged_changes": _file_has_unstaged_changes,
    "_stash_exists": _stash_exists,
    # MISSING: "_not_in_pipe"
}
```

### How The Bug Manifested

1. CONFIG (`config.py`) correctly defined pipe-aware integrations:
   ```python
   'grep': {
       'pattern': 'grep',
       'when': '_not_in_pipe',  # <-- Predicate name
       'suggestion': '...'
   }
   ```

2. The `_not_in_pipe()` function in `integrations.py` worked correctly

3. BUT `should_block_command()` in `main.py:856` checked:
   ```python
   if when_name and when_name in _PREDICATES:
       if not _PREDICATES[when_name](command):
           continue  # Allow if predicate says so
   ```

4. Since `"_not_in_pipe"` was NOT in `_PREDICATES`, the check failed
5. Predicate was never evaluated → commands were always blocked

### Why This Happened

**Architectural Issue**: Two separate blocking mechanisms:
- `integrations.py`: Predicate-based blocking with `_not_in_pipe()`
- `main.py`: Command blocking with `should_block_command()`

These weren't properly integrated - the predicate existed but wasn't registered in the lookup table.

---

## The Fix

### Primary Fix (main.py:302, 816-834)

```python
# 1. Import the predicate
from clautorun.integrations import _not_in_pipe

# 2. Create wrapper to match signature
def _not_in_pipe_predicate(command: str) -> bool:
    """Wrapper for _not_in_pipe that takes command string instead of context."""
    from unittest.mock import MagicMock
    ctx = MagicMock()
    ctx.tool_input = {'command': command}
    return _not_in_pipe(ctx)

# 3. Register in lookup table
_PREDICATES = {
    "_has_unstaged_changes": _has_unstaged_changes,
    "_file_has_unstaged_changes": _file_has_unstaged_changes,
    "_stash_exists": _stash_exists,
    "_not_in_pipe": _not_in_pipe_predicate,  # <-- CRITICAL FIX
}
```

### Secondary Fix (restart_daemon.py:171-230)

Enhanced daemon restart with:
- Absolute path resolution (`.resolve()`)
- `__pycache__` clearing to prevent stale bytecode
- Comprehensive logging to `daemon_startup.log`
- Path verification with clear error messages

---

## Debugging Journey (Why It Took 6 Hours)

### 1. Initial Hypothesis: bashlex parsing bug (WRONG)
- **Time**: 2 hours
- **What I Did**: Extended tests for logical operators, created heredoc tests
- **Why Wrong**: Tests passed but daemon still blocked commands
- **Lesson**: Unit tests passing ≠ integration working

### 2. Second Hypothesis: Daemon loading old code (PARTIALLY RIGHT)
- **Time**: 2 hours
- **What I Did**: Force reinstall plugin, restart Claude, check sys.path
- **Why Partially Right**: Daemon WAS loading from git repo (correct path) but still blocked
- **Lesson**: Module loading != configuration loading

### 3. Third Hypothesis: Heredoc naive matching (REAL BUT SECONDARY)
- **Time**: 1 hour
- **What I Did**: Fixed bashlex heredoc parsing with `_normalize_heredoc_delimiters()`
- **Why Secondary**: This WAS a bug but not THE bug causing pipe blocking
- **Lesson**: Multiple bugs can obscure each other

### 4. Final Discovery: Missing predicate registration (THE ACTUAL BUG)
- **Time**: 1 hour
- **How Found**: Created integration test `test_should_block_command_pipes_allowed()`
- **Key Insight**: Test directly called `should_block_command()` and FAILED
- **Lesson**: Test the COMPLETE pathway, not just individual components

---

## Lessons Learned

### 1. Test Integration Pathways, Not Just Components

**Problem**: Had tests for `_not_in_pipe()` predicate (passed ✓) but not for `should_block_command()` integration

**Solution**: Created `test_daemon_pipe_blocking_integration.py` with 11 end-to-end tests:
- `test_should_block_command_pipes_allowed()` - CRITICAL test that caught the bug
- `test_real_world_blocked_command()` - Reproduces exact user command
- `test_config_has_pipe_predicate()` - Verifies CONFIG structure

**Preventative Measure**:
```python
# ALWAYS test the COMPLETE pathway
def test_end_to_end_blocking(self):
    """Test from command string -> should_block_command() -> decision"""
    result = should_block_command("session-id", "git log | grep fix")
    assert result is None  # Not blocked
```

### 2. Make Missing Dependencies Fail Loudly

**Problem**: Missing predicate in `_PREDICATES` failed silently - just skipped the check

**Solution**: Added diagnostic verification:
```python
_REQUIRED_PREDICATES = ["_not_in_pipe", "_has_unstaged_changes"]
for pred_name in _REQUIRED_PREDICATES:
    if pred_name not in _PREDICATES:
        print(f"CRITICAL ERROR: Required predicate '{pred_name}' missing!", file=sys.stderr)
```

**Preventative Measure**: Startup validation that crashes loudly on misconfiguration

### 3. Log Module Loading Paths

**Problem**: Daemon could load from site-packages (old) or git repo (new) - no visibility

**Solution**: Added explicit logging in `restart_daemon.py`:
```python
daemon_code = (
    f"import sys; sys.path.insert(0, r'{src_dir}'); "
    f"import clautorun; print(f'Loaded clautorun from: {{clautorun.__file__}}', flush=True); "
    # ...
)
```

**Preventative Measure**: Always log WHERE modules are loaded from in critical paths

### 4. Separate Unit vs Integration Test Files

**Problem**: Mixed predicate tests with pipe detection tests - hard to isolate failures

**Solution**: Created separate test files:
- `test_task_17_pipe_blocking_fix.py` - Predicate logic (11 tests)
- `test_daemon_pipe_blocking_integration.py` - End-to-end integration (11 tests)
- `test_naive_string_matching_bug.py` - Heredoc parsing (9 tests)

**Preventative Measure**: Keep test scopes focused and clearly named

### 5. Clear Cache on Code Changes

**Problem**: Python's `__pycache__` can persist old bytecode even after source changes

**Solution**: Added safe cache clearing in `restart_daemon.py`:
```python
for pycache in src_dir.rglob("__pycache__"):
    if pycache.name == "__pycache__" and pycache.is_dir():
        try:
            pycache.relative_to(src_dir)  # Safety: only within our code
            shutil.rmtree(pycache)
        except ValueError:
            continue  # Skip if outside our directory
```

**Preventative Measure**: Always clear bytecode cache when debugging "code changed but behavior didn't"

---

## Preventative Measures

### Code Quality

1. **Predicate Registration Validation**
   ```python
   # In main.py startup:
   for pattern, config in CONFIG["default_integrations"].items():
       when = config.get("when")
       if when and when not in _PREDICATES:
           raise ValueError(f"Integration '{pattern}' uses undefined predicate '{when}'")
   ```

2. **Integration Test Coverage**
   - ALWAYS test `should_block_command()` directly (not just predicates)
   - Test both ALLOW and BLOCK cases
   - Test real user commands from bug reports

3. **Clear Error Messages**
   - Import failures should print to stderr with CRITICAL prefix
   - Missing predicates should fail startup, not silently skip

### Development Process

1. **When Code Changes Don't Take Effect**
   - Check daemon log: `tail -50 ~/.clautorun/daemon.log`
   - Verify module path: Look for "Loaded clautorun from: ..." in `daemon_startup.log`
   - Clear cache: `find src -name __pycache__ -type d -exec rm -rf {} +`
   - Restart daemon: `python3 scripts/restart_daemon.py`

2. **When Adding New Predicates**
   - Add to `_PREDICATES` lookup table (main.py)
   - Add import at top of file
   - Add to `_REQUIRED_PREDICATES` if critical
   - Write integration test using `should_block_command()`

3. **When User Reports "Command Still Blocked"**
   - Create integration test that reproduces EXACT user command
   - Test `should_block_command()` directly (not just predicate)
   - Check daemon is loading correct code path
   - Verify predicate is in `_PREDICATES` table

### Testing Strategy

1. **Test Pyramid for Command Blocking**
   - **Unit Tests** (integrations.py): Predicate logic (fast, isolated)
   - **Integration Tests** (main.py): `should_block_command()` with predicates
   - **End-to-End Tests**: Daemon restart + live command execution

2. **Critical Test Cases**
   ```python
   # ALWAYS include these tests:
   test_pipes_allowed()              # git log | grep fix
   test_direct_blocked()             # grep pattern file.txt
   test_logical_operators()          # cmd | grep || echo
   test_real_user_commands()         # Exact commands from bug reports
   test_predicate_registered()       # _not_in_pipe in _PREDICATES
   ```

---

## Files Modified

### Primary Fix
- **src/clautorun/main.py:302** - Import `_not_in_pipe`
- **src/clautorun/main.py:816-834** - Add `_not_in_pipe_predicate` wrapper and register in `_PREDICATES`

### Secondary Fixes
- **scripts/restart_daemon.py:171-230** - Enhanced logging, path resolution, cache clearing
- **src/clautorun/command_detection.py:342-373** - Fixed heredoc delimiter parsing (secondary bug)

### Tests Added
- **tests/test_daemon_pipe_blocking_integration.py** - 11 integration tests (NEW)
- **tests/test_naive_string_matching_bug.py** - 9 heredoc tests (NEW)
- **tests/test_task_17_pipe_blocking_fix.py** - Extended with logical operator tests

---

## Success Criteria (ALL MET ✓)

- ✅ All 22 pipe blocking tests pass (11 original + 11 integration)
- ✅ User command works: `gemini extensions list | grep -A 2 -B 2 clautorun || echo 'Not found'`
- ✅ Piped commands allowed: `cargo build 2>&1 | head -50`
- ✅ Direct commands blocked: `head file.txt`
- ✅ Daemon loads from git repo (verified in `daemon_startup.log`)
- ✅ Bashlex available (verified in startup)
- ✅ No false positives on heredocs
- ✅ Logical operators work: `|| echo`, `&& cmd`

---

## Key Takeaways

1. **Integration > Components**: Component tests passed but integration failed
2. **Fail Loudly**: Silent failures waste debugging time
3. **Log Everything**: Module paths, predicate checks, blocking decisions
4. **Test Real Commands**: User bug reports = integration test cases
5. **Clear Cache**: Bytecode persistence obscures fixes

**Most Important**: When debugging "changed code but behavior didn't change" → check integration pathway, not just components.

---

## Related Issues

- **Task #17**: Pipe blocking bug (FIXED)
- **Heredoc Bug**: Bashlex failed on quoted delimiters (FIXED)
- **Daemon Loading**: Site-packages vs git repo confusion (FIXED)

**Total Time**: ~6 hours debugging, 3 lines to fix, 31 tests to prevent recurrence
