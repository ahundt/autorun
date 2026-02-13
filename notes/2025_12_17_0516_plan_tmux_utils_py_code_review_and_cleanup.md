# Plan: tmux_utils.py Code Review and Cleanup

## Summary

Review and consolidate the Claude Code CLI control functions added to `tmux_utils.py` for DRY, KISS, SOLID compliance.

## File: `plugins/clautorun/src/clautorun/tmux_utils.py`

## Code Critique

### DRY Violations (Lines 1274-1520)

1. **Repeated `import time`** - Lines 1305, 1491, ~1460 (cycle_to_mode)
   - **Fix**: Move `import time` to top of file with other imports

2. **Repeated parameter signatures** - `session, window, pane` appears in 8+ functions
   - **Fix**: Consider a `Target` dataclass, but YAGNI - current approach is clear

3. **Unused variable** - Line 1425: `target` computed but never used
   - **Fix**: Remove unused variable

### KISS Issues

1. **check_safe_to_send (lines 1316-1384)** - Complex flow with multiple returns
   - Logic is actually clear and necessary - each check serves a purpose
   - **Keep as-is**: Complexity justified by safety requirements

2. **send_message_to_claude (lines 1387-1443)** - Duplicates capture logic
   - Could use existing `tmux_list_windows()` but that's heavier
   - **Keep as-is**: Lightweight capture for single window is appropriate

### Edge Cases

1. **User scrolled up** - Captured content may not show actual prompt
   - tmux capture-pane with `-S -50` only shows last 50 lines
   - **Risk**: Low - scroll doesn't affect captured area positioning
   - **Mitigation**: Could add `-J` flag to join wrapped lines

2. **check_safe_to_send returns "unknown_state"** - Line 1384
   - Happens if prompt_type is INPUT but no '>' line found in last 10 lines
   - **Fix**: This is actually correct - if we can't find the prompt, don't send

3. **Happy-cli remote mode blocked** - Line 1361-1362
   - Currently blocks all messages to remote mode windows
   - **Consider**: Allow specific commands like "continue"?
   - **Keep as-is**: Better to be safe - user can use force=True

### Proposed Cleanup

```python
# Move to file top with other imports
import time  # Used by send_text_and_enter, send_ctrl_c_twice, cycle_to_mode

# Remove unused variable at line 1425
- target = f"{session or tmux.session_name}:{window or ''}"
- if pane:
-     target += f".{pane}"
```

### Functions Summary (Lines 1268-1520)

| Function | Purpose | Status |
|----------|---------|--------|
| `send_text_and_enter` | Text + C-m with delay | Good |
| `check_safe_to_send` | Safety verification | Good |
| `send_message_to_claude` | Safe send wrapper | Fix: remove unused var |
| `send_escape` | Stop generation | Good |
| `send_ctrl_c_twice` | Exit CLI | Good |
| `send_tab` | Toggle thinking | Good |
| `send_shift_tab` | Cycle modes | Good |
| `send_exit_command` | /exit wrapper | Good |
| `cycle_to_mode` | Cycle to target mode | Good |

### Mode Detection Summary (Lines 1210-1265)

| Constant | Value | Detection |
|----------|-------|-----------|
| `CLAUDE_MODE_DEFAULT` | 'default' | No mode text |
| `CLAUDE_MODE_PLAN` | 'plan' | "plan mode on" |
| `CLAUDE_MODE_BYPASS` | 'bypass' | "bypass permissions on" |
| `CLAUDE_MODE_ACCEPT_EDITS` | 'accept_edits' | "accept edits on" |

## Action Items

1. **Commit current work** - Capture all progress before cleanup
2. **Remove unused `target` variable** in `send_message_to_claude` line 1425-1427 - KISS
3. **Move `import time` to top of file** - DRY (optional - local imports valid)
4. **Add `-J` flag to capture-pane** line 1430 for joined lines (optional robustness)

## Test Plan

```bash
# Test safe send detection
PYTHONPATH=plugins/clautorun/src python3 -c "
from clautorun.tmux_utils import check_safe_to_send
# Test each case: active, no_prompt, user_typing, ready
"
```

## Decision

The code is largely clean and follows good practices. Minor cleanup:
- Remove unused variable
- Import time at top (optional - local imports are valid Python)

The safety checks in `check_safe_to_send` and `send_message_to_claude` are well-designed and necessary.
