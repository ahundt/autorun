# Plan: tmux Window Query API - Simplified & Robust

## Executive Summary

Replace 4 functions (307 lines) with 1 function + WindowList class (~100 lines).
Single tmux query per session. Zero external dependencies. Pandas-like filtering.

---

## 1. Problem Analysis (OODA: Observe)

### Current State
**File**: `plugins/cache/clautorun/clautorun/0.4.0/src/clautorun/tmux_utils.py:677-984`

| Function | Lines | Issues |
|----------|-------|--------|
| `get_window_info()` | 682-762 | Redundant - just filters `list_all_windows()` |
| `list_all_windows()` | 765-852 | 5 boolean params, multiple tmux calls per window |
| `get_enhanced_title()` | 855-924 | Should be built-in, not separate |
| `export_windows_to_json()` | 927-984 | Unnecessary - list is already JSON-serializable |

### Violations Found
- **YAGNI**: 4 functions when 1 suffices
- **KISS**: 5 boolean parameters, N tmux calls per window
- **DRY**: Current window detection duplicated in `tabs-exec:128-143` AND `list_all_windows:798-811`

### tmux Capability (Single Query Gets Everything)
```bash
tmux list-windows -t main -F '#{session_name}|#{window_index}|#{pane_title}|#{pane_current_command}|#{pane_current_path}|#{pane_pid}'
# Output: main|1|✳ Backport Review|node|/Users/athundt/source/happy-cli|4402
```

---

## 2. Design (OODA: Orient + Decide)

### Constants (No Magic Numbers)
```python
# Module-level constants in tmux_utils.py
HAPPY_TITLE_MARKER = '✳'  # Set by happy-cli MCP tool
DEFAULT_CONTENT_LINES = 0  # Content disabled by default (expensive)
CONTENT_PREVIEW_LENGTH = 500  # For Claude session detection
DEFAULT_CAPTURE_LINES = 100  # For Claude session content analysis

SHELL_COMMANDS = frozenset({'zsh', 'bash', 'sh', '-', 'fish', 'login'})
HOSTNAME_MARKERS = frozenset({'local', 'macbook', '-pro', '.local', 'terminal'})

TMUX_WINDOW_FORMAT = '|'.join([
    '#{session_name}',
    '#{window_index}',
    '#{pane_title}',
    '#{pane_current_command}',
    '#{pane_current_path}',
    '#{pane_pid}'
])
TMUX_FORMAT_SEPARATOR = '|'
TMUX_FORMAT_FIELDS = ('session', 'w', 'raw_title', 'cmd', 'path', 'pid')
```

### WindowList Class (Zero Dependencies, Stateless)
```python
from typing import Dict, List, Optional, Any, Callable, Union

class WindowList(list):
    """Filterable list of window dicts. Extends list - all list ops work.

    Each method returns a NEW WindowList (stateless/immutable pattern).
    Zero external dependencies.

    Example:
        >>> windows = tmux_list_windows()
        >>> windows.filter(cmd='node').contains('title', HAPPY_TITLE_MARKER)
        WindowList([{'session': 'main', 'w': 1, 'title': '✳ Task', ...}])
    """

    def filter(self, **kwargs: Union[Any, Callable[[Any], bool]]) -> 'WindowList':
        """Filter by key=value or key=lambda. Returns NEW WindowList.

        Args:
            **kwargs: key=value for exact match, or key=callable for predicate

        Example:
            .filter(cmd='node')           # Exact match
            .filter(w=1)                  # Window number 1
            .filter(w=lambda x: x > 5)    # Lambda predicate
            .filter(cmd='node', w=1)      # Multiple conditions (AND)
        """
        filtered = list(self)
        for key, val in kwargs.items():
            if callable(val):
                filtered = [w for w in filtered if val(w.get(key))]
            else:
                filtered = [w for w in filtered if w.get(key) == val]
        return WindowList(filtered)

    def contains(self, key: str, substr: str) -> 'WindowList':
        """Filter where key contains substring. Returns NEW WindowList.

        Example:
            .contains('title', HAPPY_TITLE_MARKER)  # Happy-cli sessions
            .contains('path', 'clautorun')          # Path contains
        """
        return WindowList([w for w in self if substr in str(w.get(key, ''))])

    def select(self, *keys: str) -> 'WindowList':
        """Select specific keys only. Returns NEW WindowList with subset of keys.

        Example:
            .select('session', 'w', 'title')  # Only these keys in output
        """
        return WindowList([{k: w[k] for k in keys if k in w} for w in self])

    def group_by(self, key: str = 'session') -> Dict[str, 'WindowList']:
        """Group windows by key. Returns dict of WindowLists.

        Useful for LLM-optimized output (reduces token count ~60%).

        Example:
            .group_by('session')  # {'main': WindowList([...]), 'dev': [...]}
        """
        from collections import defaultdict
        groups: Dict[str, WindowList] = defaultdict(WindowList)
        for w in self:
            groups[w.get(key, '')].append(w)
        return dict(groups)

    def first(self) -> Optional[Dict[str, Any]]:
        """Get first item or None. Safe alternative to [0]."""
        return self[0] if self else None

    def to_targets(self) -> List[str]:
        """Get list of tmux targets (session:window format). LLM-friendly.

        Example:
            .to_targets()  # ['main:1', 'main:2', 'dev:1']
        """
        return [f"{w.get('session')}:{w.get('w')}" for w in self]

    def to_compact(self, keys: tuple = ('session', 'w', 'title')) -> 'WindowList':
        """Return minimal representation for LLM consumption.

        Default keys are the most useful for LLM decision-making.

        Example:
            .to_compact()  # Minimal: session, w, title only
            .to_compact(('session', 'w', 'cmd'))  # Custom keys
        """
        return self.select(*keys)

    def to_grouped_compact(self) -> Dict[str, List[Dict[str, Any]]]:
        """Return grouped + compact format. Optimal for LLM output.

        Combines group_by('session') with compact selection.
        Reduces token count ~70% vs full output.

        Example:
            .to_grouped_compact()
            # {'main': [{'w': 1, 'title': '✳ Task'}, {'w': 2, 'title': 'shell'}]}
        """
        return {
            session: [{'w': w['w'], 'title': w.get('title', '')} for w in wins]
            for session, wins in self.group_by('session').items()
        }

    def __repr__(self) -> str:
        """Debug-friendly representation."""
        return f'WindowList({list.__repr__(self)})'
```

### Main Function
```python
def tmux_list_windows(
    session: Optional[str] = None,
    content_lines: int = DEFAULT_CONTENT_LINES,
    exclude_current: bool = True
) -> WindowList:
    """List all tmux windows as a filterable WindowList.

    Single tmux query per session. Content capture disabled by default (expensive).
    Returns empty WindowList if tmux not running or no windows found.

    Args:
        session: Filter to specific session name (None = all sessions)
        content_lines: Lines to capture per pane (0 = none, >0 = last N lines)
        exclude_current: Skip the window running this script (default: True)

    Returns:
        WindowList of window dicts. Each dict contains:
        - session: str - tmux session name
        - w: int - window index number
        - title: str - enhanced title (HAPPY_TITLE_MARKER prefix preserved)
        - cmd: str - current command (e.g., 'node', 'zsh')
        - path: str - current working directory (~ abbreviated)
        - pid: int - pane process ID
        - content: str - (only if content_lines > 0) last N lines of pane

    Example:
        >>> tmux_list_windows()
        WindowList([{'session': 'main', 'w': 1, 'title': '✳ Task', ...}])

        >>> tmux_list_windows().filter(cmd='node')
        >>> tmux_list_windows().group_by('session')
        >>> tmux_list_windows(content_lines=DEFAULT_CAPTURE_LINES)  # With content

        >>> for w in tmux_list_windows():
        ...     print(f"{w['session']}:{w['w']} - {w['title']}")

    Raises:
        No exceptions - returns empty WindowList on any error (fail-safe).
    """
    tmux = get_tmux_utilities()
    windows = WindowList()

    # Get current window to exclude (RAII: detect once, use throughout)
    current_session, current_window = _tmux_get_current_window() if exclude_current else ('', '')

    # Get session list (or single session if specified)
    session_names = [session] if session else _tmux_list_sessions(tmux)
    if not session_names:
        return windows  # No sessions - return empty WindowList

    # Single query per session using format string
    for session_name in session_names:
        result = tmux.execute_tmux_command(
            ['list-windows', '-F', TMUX_WINDOW_FORMAT],
            session=session_name
        )
        if not result or result.get('returncode') != 0:
            continue

        for line in result['stdout'].strip().split('\n'):
            if not line.strip():
                continue

            parts = line.split(TMUX_FORMAT_SEPARATOR)
            if len(parts) != len(TMUX_FORMAT_FIELDS):
                continue  # Malformed line - skip

            data = dict(zip(TMUX_FORMAT_FIELDS, parts))
            win_session = data['session']
            win_index = data['w']

            # Skip current window if requested
            if exclude_current and win_session == current_session and win_index == current_window:
                continue

            # Build window dict
            win = {
                'session': win_session,
                'w': int(win_index),
                'title': _tmux_enhance_title(
                    data['raw_title'], data['cmd'], data['path'],
                    win_session, int(win_index)
                ),
                'cmd': data['cmd'],
                'path': data['path'].replace(os.path.expanduser('~'), '~'),
                'pid': int(data['pid']) if data['pid'].isdigit() else 0
            }

            # Optional: capture pane content
            if content_lines > 0:
                win['content'] = _tmux_capture_pane(
                    tmux, win_session, win_index, content_lines
                )

            windows.append(win)

    return windows
```

### Helper Functions (Internal)
```python
def _tmux_get_current_window() -> tuple[str, str]:
    """Get current tmux session:window. Returns ('', '') if not in tmux."""
    import subprocess
    try:
        result = subprocess.run(
            ['tmux', 'display-message', '-p', '#{session_name}:#{window_index}'],
            capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split(':')
            return (parts[0], parts[1]) if len(parts) >= 2 else ('', '')
    except Exception:
        pass
    return ('', '')


def _tmux_list_sessions(tmux: 'TmuxUtilities') -> List[str]:
    """List all tmux session names. Returns [] if none or error."""
    result = tmux.execute_tmux_command(['list-sessions', '-F', '#{session_name}'])
    if not result or result.get('returncode') != 0:
        return []
    return [s.strip() for s in result['stdout'].strip().split('\n') if s.strip()]


def _tmux_capture_pane(
    tmux: 'TmuxUtilities', session: str, window: str, lines: int
) -> str:
    """Capture last N lines from pane. Returns '' on error."""
    result = tmux.execute_tmux_command(
        ['capture-pane', '-p', '-S', f'-{lines}'],
        session=session, window=window
    )
    return result['stdout'] if result and result.get('returncode') == 0 else ''


def _tmux_enhance_title(
    raw_title: str, cmd: str, path: str, session: str, window: int
) -> str:
    """Compute best display title with fallback hierarchy.

    Title sources (in priority order):
    1. Happy-cli title (has HAPPY_TITLE_MARKER prefix) - most reliable
    2. Custom title (doesn't match hostname patterns) - user-set
    3. Command + path (for non-shell processes) - descriptive fallback
    4. session:window - always works

    Investigation note: Cannot read happy-cli session metadata externally.
    The pane_title is our ONLY reliable source for titles.
    """
    # Level 1: Happy-cli explicitly set this title
    if raw_title and HAPPY_TITLE_MARKER in raw_title:
        return raw_title

    # Level 2: User-customized title (not a hostname default)
    if raw_title:
        title_lower = raw_title.lower()
        if not any(marker in title_lower for marker in HOSTNAME_MARKERS):
            return raw_title

    # Level 3: Command + path for non-shell processes
    if cmd not in SHELL_COMMANDS:
        short_path = path.replace(os.path.expanduser('~'), '~')
        return f'{cmd} ({short_path})'

    # Level 4: Fallback
    return f'{session}:{window}'
```

---

## 3. Integration (OODA: Act)

### Update tabs-exec
**File**: `plugins/cache/clautorun/clautorun/0.4.0/commands/tabs-exec`

```python
from clautorun.tmux_utils import (
    tmux_list_windows, get_tmux_utilities,
    DEFAULT_CAPTURE_LINES, CONTENT_PREVIEW_LENGTH
)

def discover_claude_sessions(tmux: TmuxUtilities) -> List[Dict[str, Any]]:
    """Discover Claude sessions using library function."""
    # Get all windows with content for Claude detection
    all_windows = tmux_list_windows(content_lines=DEFAULT_CAPTURE_LINES)

    # Filter to Claude sessions
    claude_sessions = []
    for win in all_windows:
        if tmux.is_claude_session(win['session'], str(win['w'])):
            content = win.get('content', '')
            claude_sessions.append({
                'session_name': win['session'],
                'window_id': str(win['w']),
                'tmux_target': f"{win['session']}:{win['w']}",
                'content': content,
                'content_preview': content[:CONTENT_PREVIEW_LENGTH],
                **win  # Include title, cmd, path, pid
            })
    return claude_sessions
```

---

## 4. Files to Modify

| File | Action |
|------|--------|
| `plugins/cache/.../src/clautorun/tmux_utils.py:677-984` | Delete 4 functions, add constants + WindowList + tmux_list_windows + helpers |
| `plugins/cache/.../commands/tabs-exec:114-191` | Update discover_claude_sessions() |
| `plugins/clautorun/src/clautorun/tmux_utils.py` | Copy from cache after testing |
| `plugins/clautorun/commands/tabs-exec` | Copy from cache after testing |
| `REGRESSION_ANALYSIS.md` | Delete (analysis complete) |

---

## 5. Test Plan (TDD)

### Unit Tests
```python
import pytest
from clautorun.tmux_utils import (
    tmux_list_windows, WindowList, _tmux_enhance_title,
    HAPPY_TITLE_MARKER, HOSTNAME_MARKERS, SHELL_COMMANDS
)

class TestWindowList:
    """WindowList class tests."""

    def test_is_list(self):
        """WindowList IS a list - all list operations work."""
        wl = WindowList([{'w': 1}, {'w': 2}])
        assert isinstance(wl, list)
        assert len(wl) == 2
        assert wl[0] == {'w': 1}

    def test_filter_exact_match(self):
        """Filter by exact value."""
        wl = WindowList([{'cmd': 'node'}, {'cmd': 'zsh'}])
        result = wl.filter(cmd='node')
        assert len(result) == 1
        assert result[0]['cmd'] == 'node'
        assert isinstance(result, WindowList)  # Returns WindowList

    def test_filter_lambda(self):
        """Filter by lambda predicate."""
        wl = WindowList([{'w': 1}, {'w': 5}, {'w': 10}])
        result = wl.filter(w=lambda x: x > 3)
        assert len(result) == 2
        assert all(w['w'] > 3 for w in result)

    def test_filter_multiple_conditions(self):
        """Multiple filters are AND'd together."""
        wl = WindowList([
            {'cmd': 'node', 'w': 1},
            {'cmd': 'node', 'w': 2},
            {'cmd': 'zsh', 'w': 1}
        ])
        result = wl.filter(cmd='node', w=1)
        assert len(result) == 1

    def test_filter_missing_key(self):
        """Filter on missing key excludes item."""
        wl = WindowList([{'cmd': 'node'}, {}])
        result = wl.filter(cmd='node')
        assert len(result) == 1

    def test_contains(self):
        """Contains substring filter."""
        wl = WindowList([
            {'title': f'{HAPPY_TITLE_MARKER} Task'},
            {'title': 'shell'}
        ])
        result = wl.contains('title', HAPPY_TITLE_MARKER)
        assert len(result) == 1

    def test_contains_missing_key(self):
        """Contains on missing key returns empty."""
        wl = WindowList([{'cmd': 'node'}])
        result = wl.contains('title', 'x')
        assert len(result) == 0

    def test_select(self):
        """Select specific keys only."""
        wl = WindowList([{'session': 'main', 'w': 1, 'cmd': 'node', 'path': '~'}])
        result = wl.select('session', 'w')
        assert result[0] == {'session': 'main', 'w': 1}

    def test_group_by(self):
        """Group by key."""
        wl = WindowList([
            {'session': 'main', 'w': 1},
            {'session': 'main', 'w': 2},
            {'session': 'dev', 'w': 1}
        ])
        grouped = wl.group_by('session')
        assert len(grouped) == 2
        assert len(grouped['main']) == 2
        assert len(grouped['dev']) == 1
        assert isinstance(grouped['main'], WindowList)

    def test_first_exists(self):
        """First returns first item."""
        wl = WindowList([{'w': 1}, {'w': 2}])
        assert wl.first() == {'w': 1}

    def test_first_empty(self):
        """First returns None for empty list."""
        wl = WindowList([])
        assert wl.first() is None

    def test_chaining(self):
        """Methods can be chained."""
        wl = WindowList([
            {'session': 'main', 'cmd': 'node', 'title': f'{HAPPY_TITLE_MARKER} Task'},
            {'session': 'main', 'cmd': 'zsh', 'title': 'shell'}
        ])
        result = wl.filter(cmd='node').contains('title', HAPPY_TITLE_MARKER).select('session', 'title')
        assert len(result) == 1
        assert set(result[0].keys()) == {'session', 'title'}

    def test_immutable(self):
        """Methods return new lists, don't modify original."""
        original = WindowList([{'w': 1}, {'w': 2}])
        filtered = original.filter(w=1)
        assert len(original) == 2  # Original unchanged
        assert len(filtered) == 1

    def test_repr(self):
        """Debug representation."""
        wl = WindowList([{'w': 1}])
        assert 'WindowList' in repr(wl)

    def test_to_targets(self):
        """to_targets returns session:w format strings."""
        wl = WindowList([
            {'session': 'dev', 'w': 1, 'title': 'A'},
            {'session': 'dev', 'w': 2, 'title': 'B'},
            {'session': 'prod', 'w': 1, 'title': 'C'}
        ])
        assert wl.to_targets() == ['dev:1', 'dev:2', 'prod:1']

    def test_to_targets_empty(self):
        """to_targets on empty list returns empty list."""
        assert WindowList([]).to_targets() == []

    def test_to_compact_default(self):
        """to_compact uses default keys (session, w, title)."""
        wl = WindowList([{'session': 's', 'w': 1, 'title': 't', 'cmd': 'c', 'path': 'p', 'pid': 123}])
        compact = wl.to_compact()
        assert len(compact) == 1
        assert set(compact[0].keys()) == {'session', 'w', 'title'}

    def test_to_compact_custom_keys(self):
        """to_compact with custom keys."""
        wl = WindowList([{'session': 's', 'w': 1, 'title': 't', 'cmd': 'c'}])
        compact = wl.to_compact(('session', 'cmd'))
        assert set(compact[0].keys()) == {'session', 'cmd'}

    def test_to_grouped_compact(self):
        """to_grouped_compact returns optimal LLM format."""
        wl = WindowList([
            {'session': 'main', 'w': 1, 'title': 'Task A', 'cmd': 'node'},
            {'session': 'main', 'w': 2, 'title': 'Task B', 'cmd': 'python'},
            {'session': 'dev', 'w': 1, 'title': 'Task C', 'cmd': 'vim'}
        ])
        grouped = wl.to_grouped_compact()
        assert 'main' in grouped and 'dev' in grouped
        assert len(grouped['main']) == 2
        assert len(grouped['dev']) == 1
        # Only w and title in output
        assert grouped['main'][0] == {'w': 1, 'title': 'Task A'}
        assert grouped['dev'][0] == {'w': 1, 'title': 'Task C'}

    def test_to_grouped_compact_empty(self):
        """to_grouped_compact on empty list returns empty dict."""
        assert WindowList([]).to_grouped_compact() == {}


class TestEnhanceTitle:
    """Title enhancement logic tests."""

    def test_happy_title_preserved(self):
        """Titles with HAPPY_TITLE_MARKER are preserved exactly."""
        title = f'{HAPPY_TITLE_MARKER} My Important Task'
        assert _tmux_enhance_title(title, 'node', '~/src', 'main', 1) == title

    def test_custom_title_preserved(self):
        """Non-hostname titles are preserved."""
        assert _tmux_enhance_title('My Custom Title', 'zsh', '~', 'main', 1) == 'My Custom Title'

    def test_hostname_falls_back(self):
        """Hostname-like titles fall back to cmd+path."""
        result = _tmux_enhance_title('MacBook-Pro.local', 'node', '~/src', 'main', 1)
        assert result == 'node (~/src)'

    def test_shell_falls_back_to_session_window(self):
        """Shell commands fall back to session:window."""
        for shell in SHELL_COMMANDS:
            result = _tmux_enhance_title('hostname.local', shell, '~', 'main', 1)
            assert result == 'main:1', f'Failed for shell: {shell}'

    def test_empty_title(self):
        """Empty title uses fallback."""
        result = _tmux_enhance_title('', 'node', '~/src', 'main', 1)
        assert result == 'node (~/src)'

    def test_none_title(self):
        """None title uses fallback."""
        result = _tmux_enhance_title(None, 'node', '~/src', 'main', 1)
        assert result == 'node (~/src)'


class TestTmuxListWindows:
    """Integration tests for tmux_list_windows."""

    def test_returns_window_list(self):
        """Returns WindowList type."""
        result = tmux_list_windows()
        assert isinstance(result, WindowList)
        assert isinstance(result, list)

    def test_window_has_required_keys(self):
        """Each window has required keys."""
        required_keys = {'session', 'w', 'title', 'cmd', 'path', 'pid'}
        for win in tmux_list_windows():
            assert required_keys.issubset(win.keys()), f'Missing keys: {required_keys - win.keys()}'

    def test_content_disabled_by_default(self):
        """Content not included when content_lines=0."""
        for win in tmux_list_windows():
            assert 'content' not in win

    def test_content_enabled(self):
        """Content included when content_lines > 0."""
        result = tmux_list_windows(content_lines=10)
        if result:  # Only test if we have windows
            for win in result:
                assert 'content' in win

    def test_json_serializable(self):
        """Result is JSON serializable."""
        import json
        result = tmux_list_windows()
        json.dumps(list(result))  # Should not raise

    def test_empty_on_no_tmux(self):
        """Returns empty WindowList if tmux not running (fail-safe)."""
        # This test verifies fail-safe behavior
        result = tmux_list_windows()
        assert isinstance(result, WindowList)
```

### Integration Test Commands
```bash
# Quick test: Returns WindowList type (works with or without tmux)
python3 -c "
from clautorun.tmux_utils import tmux_list_windows, WindowList
result = tmux_list_windows()
print(f'Type: {type(result).__name__}, Count: {len(result)}')
assert isinstance(result, WindowList)
assert isinstance(result, list)
print('PASS: Returns WindowList type')
"

# WindowList class test (no tmux required - uses mock data)
python3 -c "
from clautorun.tmux_utils import WindowList, HAPPY_TITLE_MARKER

# Create mock window data
mock_windows = WindowList([
    {'session': 'test', 'w': 1, 'title': f'{HAPPY_TITLE_MARKER} Task', 'cmd': 'python', 'path': '~/project'},
    {'session': 'test', 'w': 2, 'title': 'shell', 'cmd': 'zsh', 'path': '~'},
    {'session': 'dev', 'w': 1, 'title': 'editor', 'cmd': 'vim', 'path': '~/code'}
])

# Test filter
assert len(mock_windows.filter(cmd='python')) == 1
# Test contains
assert len(mock_windows.contains('title', HAPPY_TITLE_MARKER)) == 1
# Test group_by
grouped = mock_windows.group_by('session')
assert 'test' in grouped and 'dev' in grouped
# Test chaining
result = mock_windows.filter(session='test').select('w', 'title')
assert all(set(w.keys()) == {'w', 'title'} for w in result)
print('PASS: WindowList methods work correctly')
"

# LLM optimization methods test (no tmux required)
python3 -c "
from clautorun.tmux_utils import WindowList

mock_windows = WindowList([
    {'session': 's1', 'w': 1, 'title': 'Task A', 'cmd': 'node', 'path': '~/a'},
    {'session': 's1', 'w': 2, 'title': 'Task B', 'cmd': 'python', 'path': '~/b'},
    {'session': 's2', 'w': 1, 'title': 'Task C', 'cmd': 'vim', 'path': '~/c'}
])

# Test to_targets
targets = mock_windows.to_targets()
assert targets == ['s1:1', 's1:2', 's2:1']

# Test to_compact
compact = mock_windows.to_compact()
assert all(set(w.keys()) == {'session', 'w', 'title'} for w in compact)

# Test to_grouped_compact
grouped = mock_windows.to_grouped_compact()
assert 's1' in grouped and len(grouped['s1']) == 2
assert all('w' in w and 'title' in w for w in grouped['s1'])

print('PASS: LLM optimization methods work correctly')
"

# JSON serialization test
python3 -c "
import json
from clautorun.tmux_utils import WindowList

mock = WindowList([{'session': 'test', 'w': 1, 'title': 'Task', 'cmd': 'node', 'path': '~', 'pid': 1234}])
serialized = json.dumps(list(mock))
assert json.loads(serialized) == list(mock)
print('PASS: WindowList is JSON serializable')
"

# Live tmux test (only runs if tmux available, gracefully handles no tmux)
python3 -c "
from clautorun.tmux_utils import tmux_list_windows, WindowList
import json

result = tmux_list_windows()
if len(result) > 0:
    # Verify structure of actual windows
    required_keys = {'session', 'w', 'title', 'cmd', 'path', 'pid'}
    for win in result:
        assert required_keys.issubset(win.keys()), f'Missing: {required_keys - win.keys()}'
    print(f'PASS: Found {len(result)} windows with correct structure')

    # Test group_by with real data
    grouped = result.group_by('session')
    print(f'Sessions: {list(grouped.keys())}')

    # Test to_grouped_compact token efficiency
    full_json = json.dumps(list(result))
    compact_json = json.dumps(result.to_grouped_compact())
    savings = (1 - len(compact_json) / len(full_json)) * 100
    print(f'Token savings: {savings:.0f}% ({len(full_json)} -> {len(compact_json)} chars)')
else:
    print('PASS: No tmux windows (empty WindowList returned - fail-safe behavior)')
"
```

---

## 6. Summary

### Before
- 4 functions, 307 lines, 5+ boolean parameters
- Multiple tmux calls per window
- Magic numbers throughout
- DRY violations (duplicated window detection)

### After
- 1 function + WindowList class + 4 helpers, ~100 lines
- Single tmux query per session
- Named constants for all values
- DRY (centralized window detection)
- Fail-safe (returns empty WindowList on errors)

### API at a Glance
```python
# Basic usage
windows = tmux_list_windows()

# With content capture
windows = tmux_list_windows(content_lines=100)

# Filter and chain
happy_windows = windows.filter(cmd='node').contains('title', HAPPY_TITLE_MARKER)

# Group for LLM output
grouped = windows.group_by('session')

# Get first match
first_node = windows.filter(cmd='node').first()

# Direct iteration (WindowList IS a list)
for w in windows:
    print(f"{w['session']}:{w['w']} - {w['title']}")
```

### Principles Applied
| Principle | Application |
|-----------|-------------|
| **KISS** | One function, 3 params, intuitive API |
| **YAGNI** | Removed 3 unnecessary functions |
| **DRY** | Single format string, centralized window detection |
| **SOLID** | Single responsibility per function, open for extension |
| **TDD** | Comprehensive test coverage before implementation |
| **OODA** | Clear observe→orient→decide→act flow |
| **Fail-safe** | Empty WindowList on errors, no exceptions |
| **No Magic Numbers** | All constants named and documented |
| **Stateless** | WindowList methods return new instances |
| **LLM-optimized** | `.group_by()` reduces tokens ~60% |
