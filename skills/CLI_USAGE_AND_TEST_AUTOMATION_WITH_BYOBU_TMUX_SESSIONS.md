---
name: automated-cli-testing-sessions
description: Complete CLI testing automation using tmux/byobu sessions with ai-monitor integration. Create automated workflows for testing plugins, CLIs, and terminal applications. Includes session management, keystroke control, output capture, and autonomous monitoring capabilities.
---

# Automated CLI Testing with Session Management

This skill provides comprehensive capabilities for automated testing of CLI applications, plugins, and terminal workflows using tmux/byobu sessions with full ai-monitor integration. Perfect for testing Claude Code plugins, command-line tools, and automated workflows.

## Core Commands

### Session Management
- `byobu new-session -d -s <session-name>` - Create new detached session
- `byobu list-sessions` - List all active sessions
- `byobu attach-session -t <session-name>` - Attach to existing session
- `byobu detach-session` - Detach from session (F6)
- `byobu kill-session -t <session-name>` - Terminate session
- `tmux list-sessions` - List tmux sessions

### Window Management
- `byobu new-window -t <session-name>` - Create new window
- `byobu list-windows -t <session-name>` - List windows in session
- `byobu kill-window -t <session-name>:<window-index>` - Kill specific window
- `byobu select-window -t <session-name>:<window-index>` - Select window

### Input and Output Control
- `byobu send-keys -t <session-name> <keys>` - Send keystrokes to window
- `byobu send-keys -t <session-name> C-m` - Send Enter/Return key
- `byobu send-keys -t <session-name> C-c` - Send Ctrl+C interrupt
- `byobu capture-pane -t <session-name>` - Capture window output
- `byobu capture-pane -t <session-name> -p -S -<lines>` - Capture last N lines

### Tmux Commands (direct)
- `tmux new-session -d -s <session-name>` - Create new detached tmux session
- `tmux list-sessions` - List all tmux sessions
- `tmux attach-session -t <session-name>` - Attach to tmux session
- `tmux detach` - Detach from tmux session

## Specific Keystroke Commands

### Control Sequences
- `C-c` - Ctrl+C (interrupt/stop current command)
- `C-m` - Ctrl+M (Enter/Return)
- `C-d` - Ctrl+D (EOF/end of input)
- `C-l` - Ctrl+L (clear screen)
- `C-a` - Ctrl+A (tmux prefix, then command)
- `C-z` - Ctrl+Z (suspend current process)
- `Tab` - Tab completion
- `Esc` - Escape key

### Byobu Specific Keys
- `F2` - Create new window
- `Shift+F2` - Split window horizontally
- `Ctrl+F2` - Split window vertically
- `F3` - Previous window
- `F4` - Next window
- `F5` - Reload profile
- `F6` - Detach session
- `F7` - Search scrollback buffer
- `F8` - Enter scrollback mode
- `F9` - Configure byobu
- `Shift+F3`/`Shift+F4` - Move window left/right
- `Ctrl+F3`/`Ctrl+F4` - Focus pane left/right

### Tmux Prefix Commands (default Ctrl+B)
- `Ctrl+B c` - Create new window
- `Ctrl+B &` - Kill current window
- `Ctrl+B ,` - Rename window
- `Ctrl+B n` - Next window
- `Ctrl+B p` - Previous window
- `Ctrl+B %` - Split window horizontally
- `Ctrl+B "` - Split window vertically
- `Ctrl+B o` - Switch panes
- `Ctrl+B d` - Detach session
- `Ctrl+B s` - List sessions

## AI-Monitor Integration

The ai_monitor.py in clautorun provides comprehensive tmux monitoring and automation:

### Core AI-Monitor Functions
```python
# Start monitoring a session
start_monitor(session_id, prompt="Continue working", stop_marker="COMPLETE", max_cycles=5)

# Stop monitoring
stop_monitor(session_id)

# Check if monitor is running
check_monitor(session_id)
```

### AI-Monitor Capabilities
- **Session Discovery**: Automatically finds tmux sessions and windows
- **Content Monitoring**: Tracks window content changes and detects meaningful updates
- **Change Detection**: Filters out minor changes (echoes) vs. substantial content updates
- **Automatic Re-prompting**: Sends continuation prompts when idle (configurable intervals)
- **Stop Marker Detection**: Automatically stops when specific completion markers appear
- **Cycle Management**: Limits number of re-prompt cycles to prevent infinite loops
- **Window Management**: Can target specific windows for monitoring and interaction

### AI-Monitor Configuration Options
- `--prompt/-p <text>` - Custom prompt to send when idle
- `--stop/-s <string>` - Stop marker to detect completion
- `--stop-delay-seconds <n>` - Delay before stopping after detecting stop marker
- `--max-retry-cycles/-c <n>` - Maximum number of re-prompt cycles
- `--max-runtime-minutes <n>` - Maximum runtime in minutes
- `--check-interval <n>` - Seconds between checks
- `--prompt-on-start` - Send prompt immediately on start
- `--start <window_numbers>` - Target specific windows

## Usage Examples

### Create Testing Session with AI-Monitor
```bash
# Create session
byobu new-session -d -s "test-session"

# Navigate and start process
byobu send-keys -t "test-session" "cd /path/to/project" C-m
byobu send-keys -t "test-session" "npm test" C-m

# Start ai-monitor (from separate terminal)
python3 ai_monitor.py "test-session" --prompt "Continue testing" --stop "Tests completed" --max-cycles 3
```

### Monitor Claude Code Session
```bash
# Create Claude Code session
byobu new-session -d -s "claude-test"
byobu send-keys -t "claude-test" "cd /project" C-m
byobu send-keys -t "claude-test" "claude" C-m

# Start monitoring for autonomous work
python3 ai_monitor.py "claude-test" --prompt "Continue working autonomously" --stop "AUTORUN_ALL_TASKS_COMPLETED" --max-cycles 10
```

### Automated Plugin Testing Workflow
```bash
# Create session for clautorun testing
byobu new-session -d -s "clautorun-test"

# Navigate to project
byobu send-keys -t "clautorun-test" "cd /Users/athundt/.claude/clautorun" C-m

# Start Claude Code
byobu send-keys -t "clautorun-test" "claude" C-m
sleep 5

# Set model to haiku for efficiency
byobu send-keys -t "clautorun-test" "/model haiku" C-m
sleep 2

# Install plugin locally
byobu send-keys -t "clautorun-test" "/plugin marketplace add /Users/athundt/.claude/clautorun" C-m
sleep 3

# Install plugin
byobu send-keys -t "clautorun-test" "/plugin install clautorun@clautorun-dev" C-m
sleep 5

# Test commands
byobu send-keys -t "clautorun-test" "/afs" C-m
sleep 3

byobu send-keys -t "clautorun-test" "/afst" C-m
sleep 3

# Capture output for verification
byobu capture-pane -t "clautorun-test" -p -S -20
```

### Error Handling and Recovery
```bash
# Check if session exists before operations
if byobu list-sessions | grep -q "test-session"; then
    echo "Session exists, proceeding..."
else
    echo "Session not found, creating..."
    byobu new-session -d -s "test-session"
fi

# Interrupt hanging commands
byobu send-keys -t "test-session" C-c

# Kill and recreate session if needed
byobu kill-session -t "test-session"
byobu new-session -d -s "test-session"
```

## Best Practices

1. **Session Naming**: Use descriptive names (e.g., "clautorun" for main testing, "clautorun-test" for specific tests)
2. **Default Session**: Use "clautorun" as the default session name for clautorun testing workflows
3. **Existence Checks**: Always verify session existence before operations using `byobu list-sessions`
4. **Error Recovery**: Implement Ctrl+C handling for stuck commands and session recreation when needed
5. **Output Verification**: Use capture-pane for command result verification and testing validation
6. **Resource Management**: Clean up sessions after testing with `byobu kill-session`
7. **Timing**: Add appropriate sleep intervals between commands (2-5 seconds for Claude Code startup)
8. **AI-Monitor Integration**: Use ai-monitor for long-running autonomous sessions and automated workflows
9. **Model Selection**: Use haiku for automated tasks to reduce resource usage and improve performance
10. **Byobu vs Tmux**: Prefer byobu commands for user-friendly interface, tmux commands for scripting

## Implementation Notes

- This skill uses tmux as the primary backend (byobu is a tmux wrapper providing user-friendly interface)
- Byobu provides enhanced F-key shortcuts and session management over tmux
- All tmux commands work with both byobu and tmux sessions (byobu with tmux backend)
- AI-monitor provides the automation layer for autonomous workflows and session monitoring
- Control sequences (C-m for Enter, C-c for interrupt) are critical for proper command execution
- Proper timing and error handling ensure reliable automated testing workflows
- Byobu sessions can be managed with both byobu commands and direct tmux commands

## External Resources and References

### Documentation Links
- **Byobu Official Documentation** - https://www.byobu.org/
  - Use when learning byobu-specific features and configuration
  - Essential for understanding F-key shortcuts and byobu workflow
  - Visit when setting up new byobu environments

- **Tmux Manual** - https://github.com/tmux/tmux/wiki
  - Reference for advanced tmux commands and scripting
  - Use when creating complex session automation scripts
  - Essential for understanding tmux prefix commands and scripting

- **Claude Code Skills Documentation** - https://docs.claude.com/en/docs/claude-code/skills
  - Learn how to create and structure Claude Code skills
  - Use when creating additional skills or extending this one
  - Essential for understanding skill YAML frontmatter and formatting

- **Claude Code Plugin Documentation** - https://docs.claude.com/en/docs/claude-code/plugins
  - Reference for Claude Code plugin development
  - Use when developing or debugging Claude Code plugins
  - Essential for understanding plugin architecture and hook systems

- **Claude Code Plugin Examples** - https://raw.githubusercontent.com/anthropics/claude-code/refs/heads/main/plugins/README.md
  - Official examples and patterns for Claude Code plugins
  - Use when implementing plugin features or debugging issues
  - Essential for understanding best practices from official plugins

### Testing and Automation Resources
- **AI-Monitor Integration (clautorun)** - `src/clautorun/ai_monitor.py`
  - Built-in tmux monitoring and automation capabilities
  - Use for autonomous session monitoring and automated re-prompting
  - Essential for long-running automated workflows and testing

- **Bash Scripting Guide** - https://www.gnu.org/software/bash/manual/
  - Reference for creating automated test scripts
  - Use when writing complex test automation scripts
  - Essential for proper error handling and process control

### When to Use Each Resource

**Setup Phase:**
- Visit Byobu Documentation when first setting up your testing environment
- Check Claude Code Skills Documentation when creating new skills
- Review tmux Manual when learning advanced session management

**Development Phase:**
- Use AI-Monitor integration for implementing autonomous workflows
- Reference Claude Code Plugin Examples when developing plugins
- Consult Bash Scripting Guide for complex automation scripts

**Debugging Phase:**
- Check Claude Code Plugin Documentation when troubleshooting plugin issues
- Review tmux Manual when debugging session management problems
- Use AI-Monitor logs when monitoring automated workflows

**Best Practices:**
- Always reference official Claude Code Plugin Examples for patterns
- Keep Byobu Documentation handy for session management shortcuts
- Use tmux Manual for scripting complex automation scenarios