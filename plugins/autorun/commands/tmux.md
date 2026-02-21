---
description: Tmux session management - create, list, cleanup isolated sessions
allowed-tools: Bash(tmux *), Bash(uv *)
argument-hint: [create <name>|list|cleanup [--all]]
---

# Tmux Session Management

Current tmux sessions:

! tmux list-sessions -F "  #{session_name}: #{session_windows} windows" 2>/dev/null || echo "  (no active sessions)"

## Your Task

$ARGUMENTS

If no arguments provided, show the session list above and ask the user what to do.

## Actions

### `create <name>` — Create Isolated Session

```bash
uv run --project "${CLAUDE_PLUGIN_ROOT}" python -c "
from autorun.tmux_utils import get_tmux_utilities
tmux = get_tmux_utilities()
name = 'SESSION_NAME'  # replace with actual name from arguments

# Pre-check: does it already exist? (ensure_session_exists cannot distinguish create vs. exists)
# CORRECT: use session= param, NOT inline -t (execute_tmux_command auto-appends -t for has-session)
check = tmux.execute_tmux_command(['has-session'], session=name)
already_existed = check and check['returncode'] == 0

tmux.ensure_session_exists(name)

r = tmux.execute_tmux_command(['list-sessions', '-F', '  #{session_name}: #{session_windows} windows'])
sessions_out = r['stdout'].strip() if r and r['returncode'] == 0 else '  (none)'
status = 'already existed' if already_existed else 'created'
print('Session ' + repr(name) + ' ' + status + '.')
print('Active sessions:')
print(sessions_out)
"
```

### `list` — Show Sessions with Status

```bash
uv run --project "${CLAUDE_PLUGIN_ROOT}" python -c "
from autorun.tmux_utils import get_tmux_utilities
tmux = get_tmux_utilities()
# NOTE: #{session_created_string} is NOT a standard tmux format; #{session_created} returns unix timestamp
# Use #{session_created} and format it in Python, or omit timestamp to avoid format errors
r = tmux.execute_tmux_command(['list-sessions', '-F', '  #{session_name}: #{session_windows} windows'])
print(r['stdout'].strip() if r and r['returncode'] == 0 else 'No active sessions.')
"
```

### `cleanup` — Remove Sessions Older Than 1 Hour

```bash
uv run --project "${CLAUDE_PLUGIN_ROOT}" python -c "
from autorun.tmux_utils import get_tmux_utilities
import time
tmux = get_tmux_utilities()
r = tmux.execute_tmux_command(['list-sessions', '-F', '#{session_name} #{session_created}'])
if not r or r['returncode'] != 0:
    print('No sessions to clean up.')
else:
    now = time.time()
    killed = []
    for line in r['stdout'].strip().splitlines():
        parts = line.rsplit(' ', 1)
        if len(parts) == 2:
            sname, created_str = parts[0], parts[1]
            try:
                if now - int(created_str) > 3600:
                    # CORRECT: session= param, not inline -t (avoids duplicate -t in execute_tmux_command)
                    tmux.execute_tmux_command(['kill-session'], session=sname)
                    killed.append(sname)
            except ValueError:
                pass  # skip malformed lines
    print('Cleaned up: ' + ', '.join(killed) if killed else 'No sessions older than 1 hour.')
"
```

### `cleanup --all` — Remove All autorun-test Sessions

> **Safety**: Only removes sessions matching `autorun-test*` prefix. Never removes user work sessions.

```bash
uv run --project "${CLAUDE_PLUGIN_ROOT}" python -c "
from autorun.tmux_utils import get_tmux_utilities
tmux = get_tmux_utilities()
r = tmux.execute_tmux_command(['list-sessions', '-F', '#{session_name}'])
if not r or r['returncode'] != 0:
    print('No sessions to remove.')
else:
    killed = []
    for sname in r['stdout'].strip().splitlines():
        sname = sname.strip()
        if sname.startswith('autorun-test'):  # SAFE: only test sessions, not work sessions
            # CORRECT: session= param, not inline -t (avoids duplicate -t in execute_tmux_command)
            tmux.execute_tmux_command(['kill-session'], session=sname)
            killed.append(sname)
    print('Removed: ' + ', '.join(killed) if killed else 'No autorun-test sessions found.')
"
```

## Safety

Commands always target isolated sessions and **never** affect your current Claude Code session.

**Advanced automation**: See agent `ar:tmux-session-automation` for health monitoring and lifecycle management.
**Full tmux/byobu syntax reference**: See skill `ar:tmux-automation`.
