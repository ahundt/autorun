---
description: Discover and manage Claude sessions across tmux windows
allowed-tools: Bash(tmux *), Bash(python3 *)
---

# Claude Session Manager

Discover, analyze, and manage Claude sessions across tmux windows.

## Quick Start

```bash
"${CLAUDE_PLUGIN_ROOT}/commands/tabs-exec"
```

## Workflow

1. **Discover** - Run tabs-exec to get session data
2. **Analyze** - Answer user's query using session content
3. **Prepare** - Show planned actions and get user approval
4. **Execute** - Run approved commands via tabs-exec --execute or tmux send-keys

## Selection Syntax

| Syntax | Effect |
|--------|--------|
| `A,C` or `AC` | Default action for selected sessions |
| `A:git status, B:pwd` | Custom commands per session |
| `all:continue` | Execute on all sessions |
| `awaiting:continue` | Execute on sessions awaiting input |

## Execution

```bash
# Via tabs-exec
echo '{"selections": "B,C", "command": "continue", "sessions": [...]}' | "${CLAUDE_PLUGIN_ROOT}/commands/tabs-exec" --execute

# Direct tmux
tmux send-keys -t "session:window" "command" C-m
```

---

## Window Search API

**Location:** `plugins/clautorun/src/clautorun/tmux_utils.py`
- `tmux_list_windows()` - line 816
- `WindowList` class - line 710

### Basic Usage

```python
import sys
sys.path.insert(0, 'plugins/clautorun/src')
from clautorun.tmux_utils import tmux_list_windows, HAPPY_TITLE_MARKER

windows = tmux_list_windows(content_lines=200)
# Returns WindowList of dicts: {session, w, title, path, cmd, pid, content}
```

### WindowList Methods

```python
# Filter and chain (all return new WindowList)
windows.filter(cmd='node')                    # Exact match
windows.filter(w=lambda x: x > 10)            # Lambda predicate
windows.contains('title', HAPPY_TITLE_MARKER) # Substring match
windows.filter(cmd='node').contains('title', '✳')  # Chain

# Grouping and output
windows.group_by('session')      # {'main': WindowList([...]), ...}
windows.to_targets()             # ['main:1', 'main:2', ...]
windows.to_grouped_compact()     # LLM-optimized minimal output
```

### Printing Results

```python
# Slicing returns regular list - just print directly
for w in windows[:5]:
    print(f"{w['session']}:{w['w']} - {w.get('title', '')[:40]}")

# JSON output (exclude content for readability)
import json
print(json.dumps([{k: v for k, v in w.items() if k != 'content'} for w in windows[:3]], indent=2))
```

### Common Search Patterns

| Goal | Method |
|------|--------|
| Find project tabs | `windows.contains('path', 'project-name')` |
| Find Claude sessions | `windows.filter(cmd='node')` |
| Find stuck sessions | Search content for `error`, `failed`, `blocked` |
| Find awaiting input | Content ends with `>` |
| Find happy-cli sessions | `windows.contains('title', HAPPY_TITLE_MARKER)` |

### Tips

- Use `content_lines=200` for search context
- Use multiple keyword variations: `['xonsh', 'xllm', 'xontrib']`
- Check `~/.claude/plans/` for related plan files
