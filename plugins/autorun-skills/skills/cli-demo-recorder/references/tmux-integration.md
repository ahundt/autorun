# tmux Integration for Demo Recording

Patterns from autorun (`~/.claude/autorun/plugins/autorun/tests/test_demo.py`) and canal (`~/source/canal/canal/src/bin/can-demo.rs`) for recording TUI demos via tmux.

## Single-Pane Recording (autorun pattern)

The most common TUI recording setup: one tmux session, one pane, asciinema attached.

```python
import subprocess, shlex

SESSION = "mytool-demo"
COLS, ROWS = 160, 48

# 1. Create tmux session with controlled size
subprocess.run([
    "tmux", "new-session", "-d", "-s", SESSION,
    "-x", str(COLS), "-y", str(ROWS),
])

# 2. Start asciinema recording attached to tmux
# Run in background thread — acts run in main thread
def record_thread():
    subprocess.run([
        "asciinema", "rec", CAST_FILE,
        "--overwrite",
        "--idle-time-limit", "5",
        "--command", f"tmux attach -t {SESSION}",
    ])

import threading
t = threading.Thread(target=record_thread, daemon=True)
t.start()

# 3. Drive acts via send-keys
subprocess.run(["tmux", "send-keys", "-t", SESSION, "-l", "claude"], check=True)
subprocess.run(["tmux", "send-keys", "-t", SESSION, "Enter"], check=True)
```

## Multi-Pane Recording (split-window)

Side-by-side demos showing two tools or comparing behaviors:

```bash
# Create session with first pane
tmux new-session -d -s demo -x 160 -y 48

# Split horizontally (left/right)
tmux split-window -h -t demo

# Or split vertically (top/bottom)
tmux split-window -v -t demo

# Send to specific panes (0-indexed)
tmux send-keys -t demo:0.0 -l "tool-a --version"
tmux send-keys -t demo:0.0 Enter
tmux send-keys -t demo:0.1 -l "tool-b --version"
tmux send-keys -t demo:0.1 Enter
```

## Multi-Shell Recording (canal pattern)

Canal records separate demos for each shell by creating isolated sessions:

```rust
for (shell_name, shim_path) in &shells {
    let session = format!("canal-demo-{shell_name}");
    let cast = format!("demos/canal-interactive-{shell_name}.cast");

    // Create session running the target shell
    Command::new("tmux")
        .args(["new-session", "-d", "-s", &session,
               "-x", "160", "-y", "48", shell_name])
        .status()?;

    // Record with asciinema
    Command::new("asciinema")
        .args(["rec", &cast, "--overwrite",
               "--idle-time-limit", "5",
               "--command", &format!("tmux attach -t {session}")])
        .status()?;
}
```

## send-keys: Literal Flag (-l)

Always use `-l` for text containing special characters:

```python
# WRONG: tmux interprets / { } as special
subprocess.run(["tmux", "send-keys", "-t", SESSION, "/ar:plannew Add auth"])

# CORRECT: -l sends literal text
subprocess.run(["tmux", "send-keys", "-t", SESSION, "-l", "/ar:plannew Add auth"])
subprocess.run(["tmux", "send-keys", "-t", SESSION, "Enter"])
```

Characters that require `-l`: `/`, `{`, `}`, `~`, `#`, `%`

## Pane Index Querying

Never hardcode pane indices — query them dynamically:

```python
def get_active_pane(session: str) -> str:
    result = subprocess.run(
        ["tmux", "display-message", "-t", session, "-p", "#{pane_index}"],
        capture_output=True, text=True,
    )
    return result.stdout.strip()
```

## Idle Detection: 3-Consecutive-Check Pattern

Detect when an AI response is complete by checking pane content stability:

```python
import time

def wait_for_response(session: str, timeout: float = 60.0) -> bool:
    """Wait until pane content is stable for 3 consecutive checks."""
    consecutive = 0
    last_content = ""
    deadline = time.time() + timeout

    while time.time() < deadline:
        content = subprocess.run(
            ["tmux", "capture-pane", "-t", session, "-p"],
            capture_output=True, text=True,
        ).stdout

        if content == last_content:
            consecutive += 1
            if consecutive >= 3:
                return True
        else:
            consecutive = 0
            last_content = content

        time.sleep(0.5)

    return False
```

## Trust/Safety Dialog Handling

Detect and dismiss trust dialogs using keyword matching:

```python
TRUST_KEYWORDS = ["trust", "safe", "quick safety check", "allow", "approve"]

def handle_trust_dialog(session: str) -> bool:
    content = subprocess.run(
        ["tmux", "capture-pane", "-t", session, "-p"],
        capture_output=True, text=True,
    ).stdout.lower()

    if any(kw in content for kw in TRUST_KEYWORDS):
        subprocess.run(["tmux", "send-keys", "-t", session, "Enter"])
        return True
    return False
```

## Unicode Prompt Detection

Detect when the shell is ready for input by looking for prompt characters:

```python
PROMPT_CHARS = [
    "$",    # bash/zsh default
    "%",    # zsh alternate
    ">",    # fish/PowerShell/nushell
    ">>>",  # Python REPL / xonsh
]

def wait_for_shell_prompt(session: str, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        content = subprocess.run(
            ["tmux", "capture-pane", "-t", session, "-p"],
            capture_output=True, text=True,
        ).stdout.rstrip()
        last_line = content.split("\n")[-1].strip()
        if any(last_line.endswith(p) for p in PROMPT_CHARS):
            return True
        time.sleep(0.3)
    return False
```

## Session Cleanup

Always clean up tmux sessions, even on failure:

```python
import atexit

def cleanup():
    subprocess.run(["tmux", "kill-session", "-t", SESSION],
                   capture_output=True)

# Register cleanup unless --no-cleanup flag is set
if not args.no_cleanup:
    atexit.register(cleanup)
```

## Intro Banner in tmux Pane

Run the banner as a script inside the pane — not from the Python harness:

```python
BANNER_SCRIPT = '''#!/usr/bin/env python3
import sys
CYAN, RESET = "\\033[96m", "\\033[0m"
W = 70
def pad(t): return t + " " * (W - len(t) - 2)
lines = [
    f"  +{'='*W}+",
    f"  | {pad('mytool - demo')} |",
    f"  +{'='*W}+",
]
for line in lines:
    sys.stdout.write(CYAN + line + RESET + "\\n")
sys.stdout.flush()
'''

# Write to temp file, run inside tmux, then delete
banner_path = "/tmp/demo_banner.py"
with open(banner_path, "w") as f:
    f.write(BANNER_SCRIPT)
subprocess.run(["tmux", "send-keys", "-t", SESSION, "-l",
                f"python3 {banner_path}; rm {banner_path}"])
subprocess.run(["tmux", "send-keys", "-t", SESSION, "Enter"])
```
