# Plan: tmux_utils.py Enhanced Claude Status + Batch Actions

## Summary

Two enhancements to `tmux_utils.py`:
1. Add Claude status fields to `tmux_list_windows()` output JSON
2. Create combined action function for batch window operations

## File: `plugins/clautorun/src/clautorun/tmux_utils.py`

---

## Part 1: Enhanced tmux_list_windows Output

### Current Output (when content_lines > 0)
```python
{
    'session': str,        # tmux session name
    'w': int,              # window index
    'title': str,          # enhanced title
    'cmd': str,            # foreground command
    'path': str,           # current working directory
    'pid': int,            # process ID
    'active': bool,        # currently selected window
    'activity': int,       # last activity timestamp
    'flags': str,          # tmux flags (* - Z !)
    'content': str,        # terminal output (N lines)
    'prompt_type': str,    # 'input', 'plan_approval', etc.
    'is_active': bool      # Claude actively generating
}
```

### New Fields to Add
```python
{
    # ... existing fields ...
    'claude_mode': str,       # 'default', 'plan', 'bypass', 'accept_edits'
    'is_thinking': bool,      # thinking mode enabled (status bar shows "thinking")
    'is_claude_session': bool # process tree contains claude/happy
}
```

### Implementation Location
Lines 975-983 in `tmux_list_windows()`:
```python
# Current:
if content_lines > 0:
    win['prompt_type'] = detect_prompt_type(win['content'])
    win['is_active'] = detect_claude_active(win['content'])

# Add:
    win['claude_mode'] = detect_claude_mode(win['content'])
    win['is_thinking'] = detect_thinking_mode(win['content'])  # NEW FUNCTION
    win['is_claude_session'] = tmux.is_claude_session(session_name, str(win['w']))
```

### New Function: detect_thinking_mode
```python
def detect_thinking_mode(content: str) -> bool:
    """Detect if Claude Code is in thinking mode.

    Looks for "thinking" in the status bar line.
    Status bar format: ✳ Schlepping… (esc to interrupt · 7s · ↓ 44 tokens · thinking)
    """
    lines = content.strip().split('\n')
    for line in reversed(lines[-5:]):
        if 'thinking' in line.lower() and ('tokens' in line or 'esc to interrupt' in line):
            return True
    return False
```

---

## Part 2: Combined Action Function

### Design: `execute_window_action()`

A unified function to execute actions on one or more windows:

```python
def execute_window_action(
    tmux: 'TmuxUtilities',
    action: str,
    targets: Union[WindowList, List[Dict], Dict, str],
    message: Optional[str] = None,
    force: bool = False,
    delay_ms: int = 100
) -> Dict[str, Any]:
    """Execute an action on one or more Claude Code windows.

    Args:
        tmux: TmuxUtilities instance
        action: Action to perform:
            - 'send' or 'message': Send message (requires message param)
            - 'continue': Send "continue"
            - 'escape' or 'stop': Stop generation
            - 'exit': Exit CLI (/exit)
            - 'kill': Force exit (Ctrl+C twice)
            - 'toggle_thinking': Toggle thinking mode (Tab)
            - 'cycle_mode': Cycle to next mode (Shift+Tab)
            - 'set_mode': Set specific mode (requires message='plan'|'default'|'accept_edits')
        targets: Windows to target:
            - WindowList from tmux_list_windows()
            - List of window dicts with 'session' and 'w' keys
            - Single window dict
            - Target string like "main:5"
        message: Message text for 'send' action, or mode for 'set_mode'
        force: Skip safety checks for 'send' action
        delay_ms: Delay between text and Enter

    Returns:
        Dict with results:
        {
            'success_count': int,
            'failure_count': int,
            'results': [
                {'target': 'main:5', 'success': True, 'reason': 'sent'},
                {'target': 'main:7', 'success': False, 'reason': 'active'}
            ]
        }
    """
```

### Action Mapping
| Action | Function Called | Description |
|--------|-----------------|-------------|
| `'send'`, `'message'` | `send_message_to_claude()` | Send custom message |
| `'continue'` | `send_message_to_claude(msg='continue')` | Continue working |
| `'escape'`, `'stop'` | `send_escape()` | Stop generation |
| `'exit'` | `send_exit_command()` | Exit CLI cleanly |
| `'kill'` | `send_ctrl_c_twice()` | Force exit |
| `'toggle_thinking'` | `send_tab()` | Toggle thinking mode |
| `'cycle_mode'` | `send_shift_tab()` | Cycle to next mode |
| `'set_mode'` | `cycle_to_mode()` | Set specific mode |

### Usage Examples
```python
# Get windows awaiting input
windows = tmux_list_windows(content_lines=100).prompting_user_for_input()

# Send "continue" to all awaiting windows
result = execute_window_action(tmux, 'continue', windows)
print(f"Sent to {result['success_count']} windows")

# Stop all actively generating windows
active = tmux_list_windows(content_lines=100).filter(is_active=True)
execute_window_action(tmux, 'stop', active)

# Send custom message to specific window
execute_window_action(tmux, 'send', 'main:5', message='git status')

# Set all windows to plan mode
execute_window_action(tmux, 'set_mode', windows, message='plan')
```

---

## Implementation Steps

### Step 1: Add detect_thinking_mode function (~line 1210)
- Simple function scanning last 5 lines for "thinking" in status bar

### Step 2: Update tmux_list_windows (~line 975-983)
- Add `claude_mode`, `is_thinking`, `is_claude_session` fields
- Only when `content_lines > 0`

### Step 3: Add execute_window_action function (~line 1650)
- Parse targets into list of (session, window) tuples
- Dispatch to appropriate action function
- Collect and return results

### Step 4: Update WindowList with helper methods
- Add `actively_generating()` filter for `is_active=True`
- Add `in_mode(mode)` filter for specific claude_mode

---

## Test Plan

```bash
# Test enhanced output
PYTHONPATH=plugins/clautorun/src python3 -c "
from clautorun.tmux_utils import get_tmux_utilities, tmux_list_windows
tmux = get_tmux_utilities()
windows = tmux_list_windows(content_lines=100)
for w in windows:
    print(f\"{w['session']}:{w['w']} mode={w.get('claude_mode')} thinking={w.get('is_thinking')}\")
"

# Test batch action
PYTHONPATH=plugins/clautorun/src python3 -c "
from clautorun.tmux_utils import get_tmux_utilities, tmux_list_windows, execute_window_action
tmux = get_tmux_utilities()
windows = tmux_list_windows(content_lines=100).prompting_user_for_input()
result = execute_window_action(tmux, 'continue', windows)
print(result)
"
```
