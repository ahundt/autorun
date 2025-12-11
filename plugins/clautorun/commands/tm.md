---
name: tm
description: Tmux session management - create, list, cleanup isolated sessions (short for /cr:tmux)
model: sonnet
---

# Tmux Session Management (short: /cr:tm)

**Simple, safe tmux session management** that creates isolated environments without affecting your current Claude Code session.

## Quick Start

```bash
# Create session (most common use)
/cr:tm create my-project

# List sessions
/cr:tm list

# Clean up when done
/cr:tm cleanup
```

## Available Actions

### `create <name>` - Create Session
Creates isolated tmux session for development or testing.

**Examples**:
```bash
/cr:tm create my-project
/cr:tm create test-env --template testing
```

### `list` - Show Sessions
Shows all active clautorun sessions with status and health information.

### `cleanup` - Remove Old Sessions
Removes sessions older than 1 hour automatically.

## Safety Features

All commands are **ALWAYS SAFE** - they never affect your current Claude Code session.
