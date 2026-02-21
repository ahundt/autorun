---
name: tmux-test-workflow
description: Simple CLI testing in isolated tmux sessions. Tests commands without affecting your current work.
model: sonnet
---

# CLI Testing Workflow

**Canonical command**: `/cr:ttest` (short: `/cr:tt`)

**Simple, safe CLI testing** that runs tests in isolated tmux sessions without affecting your current Claude Code session.

## Quick Start

```bash
# Test basic commands
/cr:ttest basic

# Test specific command
/cr:ttest "npm test"

# Test help system
/cr:ttest help
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
- `/cr:ttest "git status"`
- `/cr:ttest "python --version"`
- `/cr:ttest "mytool --help"`

## Safety Features

✅ **Isolated Testing**: Tests run in separate tmux sessions
✅ **No Interference**: Never affects your current session or files
✅ **Session Cleanup**: Sessions are always killed after each test (try/finally)
✅ **Clean Results**: Clear success/failure feedback

## Example Results

### ✅ Success
```
PASS: echo hello
  hello
PASS: pwd
  /Users/user/project
```

### ❌ Failure
```
FAIL: mytool --broken
  command not found
```

## Why This Is Safe

- **Session Isolation**: Tests run in dedicated "clautorun-test" sessions
- **Explicit Targeting**: Commands never go to your current session
- **Guaranteed Cleanup**: try/finally ensures session is always killed
- **No File Changes**: Read-only testing unless explicitly specified

This focused testing provides **simple, reliable** CLI verification that's **easy to use correctly and hard to use incorrectly**.