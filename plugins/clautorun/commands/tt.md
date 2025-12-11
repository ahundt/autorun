---
name: tt
description: CLI testing in isolated tmux sessions (short for /cr:test)
model: sonnet
---

# CLI Testing Workflow (short: /cr:tt)

**Simple, safe CLI testing** that runs tests in isolated tmux sessions without affecting your current Claude Code session.

## Quick Start

```bash
# Test basic commands
/cr:tt basic

# Test specific command
/cr:tt "npm test"

# Test help system
/cr:tt help
```

## Available Test Types

### `basic` - Basic Functionality
Tests core functionality: command discovery, execution, help system, error handling.

### `help` - Help System
Tests help commands and documentation accessibility.

### Custom Commands
Test any CLI command safely:
- `/cr:tt "git status"`
- `/cr:tt "python --version"`
- `/cr:tt "mytool --help"`

## Safety Features

✅ **Isolated Testing**: Tests run in separate tmux sessions
✅ **No Interference**: Never affects your current session or files
✅ **Timeout Protection**: Tests automatically stop after 30 seconds
