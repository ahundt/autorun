---
description: CLI testing in isolated tmux sessions - run tests without affecting your work
allowed-tools: Bash(tmux *), Bash(uv *)
argument-hint: [basic|help|"<shell command>"]
---

# CLI Testing Workflow

Active test sessions:

! tmux list-sessions -F "  #{session_name}" 2>/dev/null | grep -E "^  clautorun-test" || echo "  (no active test sessions)"

## Your Task

$ARGUMENTS

- If argument is `basic`: run the basic test sequence below.
- If argument is `help`: run the help test sequence below.
- If argument is a quoted shell command (e.g. `"npm test"`): run that command in an isolated session.
- If no argument: show usage and ask the user what to test.

## Run a Custom Command in Isolation

Replace `COMMAND_HERE` with the actual command from arguments, then execute:

```bash
uv run --project "${CLAUDE_PLUGIN_ROOT}" python -c "
from clautorun.tmux_utils import get_tmux_utilities
import time, sys

tmux = get_tmux_utilities()
session = 'clautorun-test'
cmd = 'COMMAND_HERE'  # replace with actual command from arguments

try:
    # Create isolated session (safe: never targets current session)
    tmux.ensure_session_exists(session)

    # Send command then Enter as SEPARATE send-keys calls (required by tmux protocol)
    tmux.send_keys(cmd, session)
    tmux.send_keys('C-m', session)

    # Wait for output (adjust sleep if command is slow)
    time.sleep(3)

    # Capture full pane content (not capture_current_input which returns only last line)
    # Correct: session= positional arg; execute_tmux_command auto-appends -t for capture-pane
    r = tmux.execute_tmux_command(['capture-pane', '-p'], session)
    output = r['stdout'].strip() if r and r['returncode'] == 0 else '(no output captured)'

    print('Command: ' + cmd)
    print('Output:')
    print(output)
finally:
    # Always cleanup, even if an exception occurs above
    # CORRECT: session= param, not inline -t (avoids duplicate -t in execute_tmux_command)
    tmux.execute_tmux_command(['kill-session'], session=session)
"
```

## Basic Test Sequence

```bash
uv run --project "${CLAUDE_PLUGIN_ROOT}" python -c "
from clautorun.tmux_utils import get_tmux_utilities
import time

tmux = get_tmux_utilities()
session = 'clautorun-test'
results = []

for cmd in ['echo hello', 'pwd', 'which python3']:
    try:
        tmux.ensure_session_exists(session)
        tmux.send_keys(cmd, session)
        tmux.send_keys('C-m', session)
        time.sleep(1)
        r = tmux.execute_tmux_command(['capture-pane', '-p'], session)
        output = r['stdout'].strip() if r and r['returncode'] == 0 else '(capture failed)'
        results.append((cmd, output, True))
    except Exception as e:
        results.append((cmd, str(e), False))
    finally:
        # CORRECT: session= param, not inline -t (avoids duplicate -t in execute_tmux_command)
        tmux.execute_tmux_command(['kill-session'], session=session)
        time.sleep(0.5)  # brief pause between kill and re-create to avoid race condition

for cmd, out, ok in results:
    status = 'PASS' if ok and out and 'capture failed' not in out else 'FAIL'
    print(status + ': ' + cmd)
    print('  ' + out[:120])
"
```

## Help Test Sequence

```bash
uv run --project "${CLAUDE_PLUGIN_ROOT}" python -c "
from clautorun.tmux_utils import get_tmux_utilities
import time

tmux = get_tmux_utilities()
session = 'clautorun-test'
try:
    tmux.ensure_session_exists(session)
    tmux.send_keys('clautorun --help', session)
    tmux.send_keys('C-m', session)
    time.sleep(2)
    r = tmux.execute_tmux_command(['capture-pane', '-p'], session)
    output = r['stdout'].strip() if r and r['returncode'] == 0 else '(no output)'
    print(output)
finally:
    # CORRECT: session= param, not inline -t (avoids duplicate -t in execute_tmux_command)
    tmux.execute_tmux_command(['kill-session'], session=session)
"
```

## Safety

- All tests run in a dedicated `clautorun-test` session, **never** your current session.
- Session is always killed after each test (try/finally ensures cleanup even on errors).
- No file changes are made unless the command itself writes files.
- Note: long-running commands are not automatically killed — avoid `sleep` or hanging processes.

**Advanced patterns**: See agent `cr:cli-test-automation`.
**Full tmux/byobu syntax**: See skill `cr:tmux-automation`.
