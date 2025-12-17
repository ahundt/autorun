---
description: Python API for searching and filtering tmux windows - use when programmatically searching for windows by content, path, command, or title
---

# Tmux Window Search API

Python API for searching and filtering tmux windows. Use this when you need to programmatically find windows by content, path, command, or title.

## Location

`plugins/clautorun/src/clautorun/tmux_utils.py`
- `tmux_list_windows()` - line 816
- `WindowList` class - line 710
- `detect_prompt_type()` - line 1002
- `find_windows_awaiting_input()` - line 1086

## Basic Usage

```python
import sys
sys.path.insert(0, 'plugins/clautorun/src')
from clautorun.tmux_utils import tmux_list_windows, HAPPY_TITLE_MARKER

windows = tmux_list_windows(content_lines=200)
# Returns WindowList of dicts with keys:
# session, w, title, path, cmd, pid, active, activity, flags, content
```

## WindowList Methods

All filter methods return a new WindowList, enabling method chaining:

```python
# Exact match filter
windows.filter(cmd='node')
windows.filter(session='main')

# Lambda predicate
windows.filter(w=lambda x: x > 10)
windows.filter(pid=lambda p: p > 50000)

# Substring match
windows.contains('title', HAPPY_TITLE_MARKER)
windows.contains('path', 'clautorun')
windows.contains('content', 'error')

# Chaining
windows.filter(cmd='node').contains('title', '✳')

# Grouping
windows.group_by('session')  # {'main': WindowList([...]), ...}

# Output formats
windows.to_targets()         # ['main:1', 'main:2', ...]
windows.to_grouped_compact() # LLM-optimized minimal output
```

## Prompt Detection

```python
from clautorun.tmux_utils import detect_prompt_type, find_windows_awaiting_input

# Detect prompt type for a single window
for w in windows:
    prompt = detect_prompt_type(w.get('content', ''))
    if prompt:
        print(f"{w['session']}:{w['w']} - {prompt}")

# Find all windows awaiting input
awaiting = find_windows_awaiting_input()
for w in awaiting:
    print(f"{w['session']}:{w['w']} - {w['prompt_type']}")
```

### Prompt Types

| Type | Pattern |
|------|---------|
| `plan_approval` | "Would you like to proceed?" + ❯ |
| `tool_permission_yn` | [Y/n], (yes/no) |
| `tool_permission_numbered` | [1] Allow once [2] Allow always |
| `question` | ❯ with 1-4 numbered options |
| `input` | Standalone `>` at line end |
| `happy_mode_switch` | 📱 Press space |
| `clarification` | Line ending with ? |
| `error_prompt` | Press Enter, retry? |

## Printing Results

```python
# Slicing returns regular Python list (loses methods)
for w in windows[:5]:
    print(f"{w['session']}:{w['w']} - {w.get('title', '')[:40]}")

# JSON output (exclude content for readability)
import json
print(json.dumps([{k: v for k, v in w.items() if k != 'content'} for w in windows[:3]], indent=2))
```

## Common Search Patterns

| Goal | Method |
|------|--------|
| Find project tabs | `windows.contains('path', 'project-name')` |
| Find Claude sessions | `windows.filter(cmd='node')` |
| Find stuck sessions | `windows.contains('content', 'error')` |
| Find awaiting input | `find_windows_awaiting_input()` |
| Find happy-cli sessions | `windows.contains('title', HAPPY_TITLE_MARKER)` |

## Tips

- Use `content_lines=200` for search context
- Slicing (`windows[:5]`) returns regular list, not WindowList
- Use `to_grouped_compact()` for minimal LLM token usage
