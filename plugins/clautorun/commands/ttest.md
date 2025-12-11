---
name: ttest
description: CLI testing in isolated tmux sessions - run tests without affecting your work
model: sonnet
---

# CLI Testing Workflow (/cr:test)

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
✅ **Timeout Protection**: Tests automatically stop after 30 seconds
✅ **Clean Results**: Clear success/failure feedback

## Example Results

### ✅ Success
```
✅ Basic functionality test PASSED
   Commands tested: help, version, status
   All commands executed successfully
```

### ❌ Failure
```
❌ Test FAILED
   Command: mytool --broken
   Error: Command not found
```
