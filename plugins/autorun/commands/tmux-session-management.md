---
name: tmux-session-management
description: Simple and safe tmux session management. Creates isolated sessions that won't interfere with your current Claude Code session.
model: sonnet
---

# Tmux Session Management

**Canonical command**: `/ar:tmux` (short: `/ar:tm`)

**Simple, safe tmux session management** that creates isolated environments without affecting your current Claude Code session.

## Quick Start

```bash
# Create session (most common use)
/ar:tmux create my-project

# List sessions
/ar:tmux list

# Clean up when done
/ar:tmux cleanup
```

## Available Actions

### `create <name>` - Create Session
Creates isolated tmux session for development or testing.

**Examples**:
```bash
/ar:tmux create my-project
/ar:tmux create test-env
```

### `list` - Show Sessions
Shows all active autorun sessions with status and health information.

### `cleanup` - Remove Old Sessions
Removes sessions older than 1 hour automatically.

## Safety Features

✅ **Always Safe**: Commands never affect your current Claude Code session
✅ **Isolated Sessions**: Each session runs in complete isolation
✅ **Automatic Cleanup**: Old sessions are cleaned up automatically
✅ **Health Monitoring**: Sessions are monitored for responsiveness

## Safety Guarantee

Commands will **NEVER** interfere with your current Claude Code session. All commands target isolated "autorun" sessions by default.

This provides **safe, reliable** session management that's **easy to use correctly and hard to use incorrectly**.