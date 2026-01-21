---
name: tmux
description: Tmux session management - create, list, cleanup isolated sessions
model: sonnet
---

# Tmux Session Management

**Simple, safe tmux session management** that creates isolated environments without affecting your current Claude Code session.

## Quick Start

```bash
# Create session (most common use)
/cr:tmux create my-project

# List sessions
/cr:tmux list

# Clean up when done
/cr:tmux cleanup
```

## Available Actions

### `create <name>` - Create Session
Creates isolated tmux session for development or testing.

**Examples**:
```bash
/cr:tmux create my-project
/cr:tmux create test-env --template testing
```

### `list` - Show Sessions
Shows all active clautorun sessions with status and health information.

### `cleanup` - Remove Old Sessions
Removes sessions older than 1 hour automatically.

## Safety Features

✅ **Always Safe**: Commands never affect your current Claude Code session
✅ **Isolated Sessions**: Each session runs in complete isolation
✅ **Automatic Cleanup**: Old sessions are cleaned up automatically
✅ **Health Monitoring**: Sessions are monitored for responsiveness

## Safety Guarantee

Commands will **NEVER** interfere with your current Claude Code session. All commands target isolated "clautorun" sessions by default.

This provides **safe, reliable** session management that's **easy to use correctly and hard to use incorrectly**.

**Advanced Automation**: See `tmux-session-automation.md` agent for:
- Health monitoring and automatic recovery
- Extended session lifecycle management
- Integration with ai-monitor for autonomous workflows

