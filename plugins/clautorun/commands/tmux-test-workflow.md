---
name: tmux-test-workflow
description: Simple CLI testing in isolated tmux sessions. Tests commands without affecting your current work.
model: sonnet
---

# CLI Testing Workflow

**Simple, safe CLI testing** that runs tests in isolated tmux sessions without affecting your current Claude Code session.

## Quick Start

```bash
# Test basic commands
/clautorun tmux-test-workflow basic

# Test specific command
/clautorun tmux-test-workflow "npm test"

# Test help system
/clautorun tmux-test-workflow help
```

## Available Test Types

### `basic` - Basic Functionality
Tests core functionality:
- Command discovery and availability
- Basic command execution
- Help system functionality
- Error handling

### `help` - Help System
Tests help commands:
- Help commands work correctly
- Documentation is accessible
- Help text is clear and useful

### Custom Commands
Test any CLI command safely:

**Examples**:
- `/clautorun tmux-test-workflow "git status"`
- `/clautorun tmux-test-workflow "python --version"`
- `/clautorun tmux-test-workflow "mytool --help"`

## Safety Features

✅ **Isolated Testing**: Tests run in separate tmux sessions
✅ **No Interference**: Never affects your current session or files
✅ **Timeout Protection**: Tests automatically stop after 30 seconds
✅ **Clean Results**: Clear success/failure feedback

## Example Results

### ✅ Success
```
✅ Basic functionality test PASSED
   Commands tested: help, version, status
   All commands executed successfully
   Response time: < 1 second average
```

### ❌ Failure
```
❌ Help system test FAILED
   Issue: 'mytool --help' returned non-zero exit code
   Error: "command not found"
   Suggestion: Check if mytool CLI is installed and in PATH
```

## Why This Is Safe

- **Session Isolation**: Tests run in dedicated "clautorun-test" sessions
- **Explicit Targeting**: Commands never go to your current session
- **Resource Limits**: Tests are monitored and stopped if they hang
- **No File Changes**: Read-only testing unless explicitly specified

This focused testing provides **simple, reliable** CLI verification that's **easy to use correctly and hard to use incorrectly**.