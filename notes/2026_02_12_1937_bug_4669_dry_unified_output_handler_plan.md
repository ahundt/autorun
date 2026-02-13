# Bug #4669 Workaround: DRY Unified Output Handler

## Context

**The Problem**: Claude Code ignores `permissionDecision: "deny"` at exit 0 (bug #4669). Tool executes anyway.

**Root Cause Analysis** (Round-Trip Trace):
```
Claude Code → hook_entry.py → clautorun binary → client.py → daemon → client.py → hook_entry.py → Claude Code
                                                      ↑
                                          CRITICAL OUTPUT POINT
```

**Current Issues in client.py**:
1. ❌ **4 different output paths** (lines 90, 93, 105-110, 152-157)
2. ❌ **No exit code logic** - all paths exit 0
3. ❌ **Not DRY** - `print(json.dumps({...}))` duplicated 3 times
4. ❌ **No workaround** - never exits with code 2

## Solution: Single Unified Output Handler (DRY + WOLOG)

### Design Principles

1. **DRY**: ALL output paths go through ONE function
2. **WOLOG**: Works for both normal and error cases without loss of generality
3. **Pythonic**: Clean, typed, testable
4. **First-class Gemini**: Auto-detects, no exit-2 for Gemini
5. **Complete**: Handles all 4 current paths + new workaround path

### Architecture

```
┌─────────────────────────────────────────────────────────┐
│ ALL PATHS CONVERGE TO:                                  │
│                                                         │
│   output_hook_response(response, source="normal")      │
│                                                         │
│   1. Print JSON to stdout                              │
│   2. Detect CLI type (auto)                            │
│   3. Check if workaround needed                        │
│   4. Print stderr + exit(2) OR exit(0)                 │
│                                                         │
│ WOLOG: Works for success, errors, fallbacks           │
└─────────────────────────────────────────────────────────┘
```

## Implementation

### 1. Add Detection to config.py

**File**: `plugins/clautorun/src/clautorun/config.py`

**Add at end of file**:

```python
import os
from typing import Literal

def detect_cli_type() -> Literal["claude", "gemini"]:
    """Detect which CLI is calling (Claude Code vs Gemini CLI).

    Detection order (most reliable first):
    1. GEMINI_SESSION_ID - Gemini-specific variable
    2. GEMINI_PROJECT_DIR without CLAUDE_PROJECT_DIR - Gemini only
    3. Default to "claude" (safer to apply workaround when uncertain)

    Returns:
        "claude": Claude Code (needs exit-2 workaround)
        "gemini": Gemini CLI (respects JSON decision)

    Reference: hooks_api_reference.md lines 249-272
    """
    if os.environ.get("GEMINI_SESSION_ID"):
        return "gemini"

    if os.environ.get("GEMINI_PROJECT_DIR") and not os.environ.get("CLAUDE_PROJECT_DIR"):
        return "gemini"

    # Default to Claude (safer to apply workaround if uncertain)
    return "claude"


def should_use_exit2_workaround() -> bool:
    """Check if exit-2 workaround should be applied for bug #4669.

    Modes (CLAUTORUN_EXIT2_WORKAROUND env var):
    - "auto" (default): Use workaround ONLY for Claude Code
    - "always": Force workaround for all CLIs (testing)
    - "never": Disable workaround for all CLIs (testing/future)

    Returns:
        bool: True if should apply exit-2 workaround

    Reference: hooks_api_reference.md lines 326-440
    """
    mode = os.environ.get('CLAUTORUN_EXIT2_WORKAROUND', 'auto').lower()

    if mode == "always":
        return True
    elif mode == "never":
        return False
    else:  # "auto" or any other value
        cli_type = detect_cli_type()
        return cli_type == "claude"
```

### 2. Create Unified Output Handler in client.py

**File**: `plugins/clautorun/src/clautorun/client.py`

**Add BEFORE run_client() function** (around line 50):

```python
def output_hook_response(response: dict | str, source: str = "daemon") -> None:
    """Unified hook response output handler (DRY, WOLOG).

    Single consolidation point for ALL output paths:
    - Normal daemon response
    - JSON decode error fallback
    - Buffer error response
    - Exception fail-open response

    Handles:
    1. Print JSON to stdout (always)
    2. Auto-detect CLI type (Claude vs Gemini)
    3. Apply exit-2 workaround if needed (bug #4669)
    4. Exit with correct code

    Args:
        response: Response dict OR raw string (for fallback cases)
        source: Source of response ("daemon", "error", "fallback") - for logging

    Exits:
        0: Normal (allow decision OR Gemini with deny)
        2: Claude Code workaround (deny decision with exit-2 + stderr)

    Reference: hooks_api_reference.md lines 395-427 (unified blocking pattern)
    """
    from .config import should_use_exit2_workaround

    # Handle raw string fallback (JSON decode error)
    if isinstance(response, str):
        logger.debug(f"Outputting raw response from {source}")
        print(response)
        sys.exit(0)
        return

    # Extract decision from response (works for both Claude and Gemini formats)
    decision = response.get('hookSpecificOutput', {}).get('permissionDecision',
                                                          response.get('decision', 'allow'))

    # Log decision for diagnostics (file-only)
    logger.info(f"Hook response: source={source}, decision={decision}")

    # Always print JSON to stdout first
    print(json.dumps(response))

    # Apply exit-2 workaround if needed (Claude Code bug #4669)
    if decision == "deny" and should_use_exit2_workaround():
        # Extract reason (try Claude format first, then Gemini format)
        reason = response.get('hookSpecificOutput', {}).get('permissionDecisionReason',
                                                            response.get('reason', 'Tool blocked'))

        logger.info("Applying exit-2 workaround (Claude Code bug #4669)")

        # Print reason to stderr (Claude Code feeds this back to AI)
        print(reason, file=sys.stderr)

        # Exit with code 2 (actual blocking)
        sys.exit(2)

    # Normal exit (allow decision OR Gemini CLI with deny)
    sys.exit(0)
```

### 3. Replace ALL Output Paths in client.py

**File**: `plugins/clautorun/src/clautorun/client.py`

**Path 1: Normal response** (lines 81-93):
```python
# BEFORE:
try:
    resp_json = json.loads(resp_text)
    resp_json.pop("_exit_code_2", None)  # Remove old marker
    decision = resp_json.get('hookSpecificOutput', {}).get('permissionDecision', ...)
    logger.info(f"Hook response: decision={decision}")
    print(json.dumps(resp_json))
except json.JSONDecodeError:
    print(resp_text)

# AFTER:
try:
    resp_json = json.loads(resp_text)
    output_hook_response(resp_json, source="daemon")
except json.JSONDecodeError:
    output_hook_response(resp_text, source="daemon-raw")
```

**Path 2: Buffer error** (lines 102-110):
```python
# BEFORE:
except asyncio.LimitOverrunError as e:
    logger.error(f"Client buffer error: {e}")
    print(json.dumps({
        "continue": True,
        "stopReason": "",
        "suppressOutput": False,
        "systemMessage": f"Client buffer error: ..."
    }))

# AFTER:
except asyncio.LimitOverrunError as e:
    logger.error(f"Client buffer error: {e}")
    output_hook_response({
        "continue": True,
        "stopReason": "",
        "suppressOutput": False,
        "systemMessage": f"Client buffer error: Daemon response too large. {e}",
        "decision": "allow",
        "hookSpecificOutput": {"permissionDecision": "allow"}
    }, source="buffer-error")
```

**Path 3: Exception fail-open** (lines 149-157):
```python
# BEFORE:
except Exception as e:
    logger.error(f"Client exception (fail-open): {e}", exc_info=True)
    print(json.dumps({
        "continue": True,
        "stopReason": "",
        "suppressOutput": False,
        "systemMessage": ""
    }))

# AFTER:
except Exception as e:
    logger.error(f"Client exception (fail-open): {e}", exc_info=True)
    output_hook_response({
        "continue": True,
        "stopReason": "",
        "suppressOutput": False,
        "systemMessage": "",
        "decision": "allow",
        "hookSpecificOutput": {"permissionDecision": "allow"}
    }, source="exception")
```

### 4. Unified Response in core.py (No Markers)

**File**: `plugins/clautorun/src/clautorun/core.py`

**Replace** `_format_response()` method (lines 544-574):

```python
def _format_response(self, decision: str, reason: str) -> dict:
    """Build unified hook response for both Claude Code and Gemini CLI.

    Returns response with BOTH formats (no markers, no CLI-specific logic):
    - Claude Code: hookSpecificOutput.permissionDecision
    - Gemini CLI: decision (top-level)

    Exit code logic handled by client.py output_hook_response().

    Args:
        decision: "allow", "deny", "ask", "block"
        reason: Human-readable reason

    Returns:
        dict: Unified response (clean, no internal markers)
    """
    reason_escaped = reason.replace('"', '\\"').replace('\n', '\\n')

    # PreToolUse/BeforeTool: Tool blocking while AI continues
    if self._event == "PreToolUse":
        return {
            # === Universal fields ===
            "continue": True,  # Let AI continue (don't stop entirely)
            "stopReason": "",
            "suppressOutput": False,
            "systemMessage": reason_escaped,

            # === Gemini CLI format (top-level) ===
            "decision": decision,
            "reason": reason_escaped,

            # === Claude Code format (nested) ===
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision,
                "permissionDecisionReason": reason_escaped
            }
        }

    # Stop/SubagentStop with "block" decision
    if decision == "block":
        return {
            "continue": False,
            "stopReason": reason,
            "suppressOutput": False,
            "systemMessage": reason,
            "decision": "block",
            "reason": reason
        }

    # Default response (allow)
    return {
        "continue": True,
        "stopReason": "",
        "suppressOutput": False,
        "systemMessage": "",
        "decision": decision,
        "reason": reason
    }
```

### 5. CLI Override Support

**File**: `plugins/clautorun/src/clautorun/__main__.py`

**Add argument** (around line 640):

```python
parser.add_argument(
    '--exit2-mode',
    choices=['auto', 'always', 'never'],
    default=None,
    help='Bug #4669 workaround: auto (detect CLI), always (force), never (disable)'
)
```

**Set env var** (in main function before calling other code):

```python
if args.exit2_mode:
    os.environ['CLAUTORUN_EXIT2_WORKAROUND'] = args.exit2_mode
```

### 6. Documentation

**File**: `plugins/clautorun/CLAUDE.md`

**Add section after "Hook Error Prevention"**:

```markdown
### Bug #4669 Workaround (Claude Code v1.0.62+)

**Problem**: Claude Code ignores `permissionDecision:"deny"` at exit 0.

**Solution**: Auto-detect CLI and apply exit-2 workaround for Claude only.

**Behavior**:
- **Claude Code**: Uses exit 2 + stderr (only way blocking works)
- **Gemini CLI**: Uses JSON `decision` field (works correctly)
- **Auto-detect**: Based on `GEMINI_SESSION_ID` and `GEMINI_PROJECT_DIR`

**Configuration**:
```bash
# Auto-detect (default - recommended)
export CLAUTORUN_EXIT2_WORKAROUND=auto

# Force enable for testing
export CLAUTORUN_EXIT2_WORKAROUND=always

# Disable for testing
export CLAUTORUN_EXIT2_WORKAROUND=never
```

**CLI Override**:
```bash
clautorun --exit2-mode auto    # Default
clautorun --exit2-mode always  # Force
clautorun --exit2-mode never   # Disable
```

**Reference**: `notes/hooks_api_reference.md` lines 326-440
```

## Critical Files to Modify

1. **plugins/clautorun/src/clautorun/config.py**
   - Add `detect_cli_type()` - 15 lines
   - Add `should_use_exit2_workaround()` - 12 lines

2. **plugins/clautorun/src/clautorun/client.py**
   - Add `output_hook_response()` - 40 lines (DRY consolidation)
   - Replace 4 output paths with calls to `output_hook_response()`
   - **Net change**: +40 lines, simplify 30 lines

3. **plugins/clautorun/src/clautorun/core.py**
   - Replace `_format_response()` - simpler (no markers)
   - **Net change**: -5 lines (remove marker logic)

4. **plugins/clautorun/src/clautorun/__main__.py**
   - Add `--exit2-mode` argument - 5 lines
   - Set env var from args - 2 lines

5. **plugins/clautorun/CLAUDE.md**
   - Add documentation section - 30 lines

## Complete Code Pathways (ALL CONSOLIDATED)

### Normal Path (Daemon Success)
```
daemon → client.py:forward() → resp_json
                              ↓
               output_hook_response(resp_json, "daemon")
                              ↓
               detect CLI → check decision → exit code
```

### Error Path 1 (JSON Decode Error)
```
daemon → client.py:forward() → JSONDecodeError
                              ↓
               output_hook_response(resp_text, "daemon-raw")
                              ↓
               print raw → exit(0)
```

### Error Path 2 (Buffer Overflow)
```
daemon → client.py:forward() → LimitOverrunError
                              ↓
               output_hook_response({...fail-open...}, "buffer-error")
                              ↓
               print JSON → exit(0)
```

### Error Path 3 (Exception)
```
client.py:forward() → Exception
                    ↓
     output_hook_response({...fail-open...}, "exception")
                    ↓
     print JSON → exit(0)
```

**ALL 4 PATHS** converge to `output_hook_response()` → **DRY + WOLOG**

## Testing Strategy

### Test 1: Verify Consolidation (All Paths Use Same Function)

```python
# Unit test
def test_all_paths_use_output_hook_response():
    """Verify all 4 output paths call output_hook_response."""
    import ast
    import inspect
    from plugins.clautorun.src.clautorun import client

    source = inspect.getsource(client.run_client)
    tree = ast.parse(source)

    # Find all print() calls
    prints = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Name) and node.func.id == 'print']

    # Should be ZERO direct print() calls (all go through output_hook_response)
    assert len(prints) == 0, f"Found {len(prints)} direct print() calls - all should use output_hook_response()"
```

### Test 2: Auto-Detection Works

```bash
# Test Claude detection
unset GEMINI_SESSION_ID
unset GEMINI_PROJECT_DIR
python3 -c "from plugins.clautorun.src.clautorun.config import detect_cli_type, should_use_exit2_workaround; print(f'CLI: {detect_cli_type()}, Workaround: {should_use_exit2_workaround()}')"
# Expected: CLI: claude, Workaround: True

# Test Gemini detection
export GEMINI_SESSION_ID="test-123"
python3 -c "from plugins.clautorun.src.clautorun.config import detect_cli_type, should_use_exit2_workaround; print(f'CLI: {detect_cli_type()}, Workaround: {should_use_exit2_workaround()}')"
# Expected: CLI: gemini, Workaround: False
```

### Test 3: Exit Code Logic (Claude)

```bash
# Enable auto mode (default)
export CLAUTORUN_EXIT2_WORKAROUND=auto
clautorun --install clautorun --force

# Test rm blocking
touch /tmp/test-exit2.txt && rm /tmp/test-exit2.txt
# Expected: Tool BLOCKED, trash suggestion shown, NO hook error

# Verify debug log
grep "exit code: 2" ~/.clautorun/hook_entry_debug.log
grep "CLI stderr.*trash" ~/.clautorun/hook_entry_debug.log
# Expected: Both found
```

### Test 4: Exit Code Logic (Gemini Mock)

```bash
# Mock Gemini environment
export GEMINI_SESSION_ID="test-session"
export CLAUTORUN_EXIT2_WORKAROUND=auto

# Test response
echo '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"rm test.txt"}}' | \
  python3 ~/.claude/clautorun/plugins/clautorun/src/clautorun/client.py 2>/tmp/gemini_stderr.txt
# Expected: exit 0, stderr 0 bytes, JSON has decision="deny"

wc -c /tmp/gemini_stderr.txt
# Expected: 0 bytes (no stderr for Gemini)
```

### Test 5: Response Format (Unified)

```bash
# Verify response has BOTH formats
export CLAUTORUN_DEBUG=1
rm /tmp/test.txt

# Check for both Claude and Gemini fields
grep '"decision":' ~/.clautorun/hook_entry_debug.log
grep '"hookSpecificOutput":' ~/.clautorun/hook_entry_debug.log
grep '"permissionDecision":' ~/.clautorun/hook_entry_debug.log
# Expected: All three found
```

## Success Criteria

1. ✅ **DRY**: Single `output_hook_response()` handles ALL 4 paths
2. ✅ **WOLOG**: Works for normal, error, and fallback cases without loss of generality
3. ✅ **Auto-detection**: Correctly identifies Claude vs Gemini
4. ✅ **Claude Code**: exit 2 + stderr for deny (workaround)
5. ✅ **Gemini CLI**: exit 0 + JSON for deny (standard)
6. ✅ **Unified response**: Both `decision` and `hookSpecificOutput` fields
7. ✅ **No markers**: Clean JSON, no internal fields to pop
8. ✅ **Testable**: Can unit test `output_hook_response()` in isolation
9. ✅ **Configurable**: "auto"/"always"/"never" modes work
10. ✅ **Complete**: All pathways traced and consolidated

## Architecture Benefits

### Before (NOT DRY)
```
Path 1: daemon success    → print(json.dumps(resp_json))      → exit(0)
Path 2: JSON decode error → print(resp_text)                  → exit(0)
Path 3: buffer error      → print(json.dumps({...}))          → exit(0)
Path 4: exception         → print(json.dumps({...}))          → exit(0)
```
**Issues**: 4 separate print statements, no exit-2 logic, not DRY

### After (DRY + WOLOG)
```
Path 1: daemon success    ↘
Path 2: JSON decode error  ↘
Path 3: buffer error        → output_hook_response(...) → auto-detect → exit(0 or 2)
Path 4: exception          ↗
```
**Benefits**: Single function, DRY, auto-detect, workaround, testable

## References

- **Bug #4669**: https://github.com/anthropics/claude-code/issues/4669
- **CLI Detection**: `notes/hooks_api_reference.md` lines 249-272
- **Unified Blocking**: `notes/hooks_api_reference.md` lines 395-427
- **Claude Outcome Matrix**: `notes/hooks_api_reference.md` lines 1187-1209
- **Gemini Outcome Matrix**: `notes/hooks_api_reference.md` lines 1209-1221
