# Developer Internals

> Technical implementation details for autorun contributors. Moved from README.md to reduce verbosity while preserving reference material.

## RAII Pattern Implementation

autorun uses Resource Acquisition Is Initialization (RAII) patterns for robust resource management:

```python
# RAII Session Lock - Automatic acquisition and guaranteed release
# NOTE: SessionLock is now a no-op shim. The actual locking happens inside
# _JSONStore.session() (the filelock+JSON session store). The context manager
# interface is preserved for backward compatibility.
with SessionLock(session_id, timeout, state_dir) as lock_fd:
    # SessionLock is a no-op shim — real locking is inside _JSONStore.session()
    pass  # Use session_state() context manager for actual locked access
```

**Key RAII Benefits:**
- **Automatic Resource Management**: No manual cleanup required
- **Exception Safety**: Resources released even if exceptions occur
- **Deadlock Prevention**: Timeout-based lock acquisition
- **Thread/Process Isolation**: Each session gets isolated access

## Thread & Process Safety Architecture

**Concurrency Model:**
- **Cross-process safety**: `filelock.FileLock` (from the `filelock` package) for mutual exclusion across processes
- **Same-process thread safety**: `threading.RLock` for concurrent thread serialization within one process
- **Deadlock Prevention**: Configurable timeouts with `FileLockTimeout` handling
- **Note**: `fcntl.flock` is still used specifically for the daemon process lock, not session state

**Safety Mechanisms:**
```python
# Session state with automatic timeout handling (real locking via _JSONStore.session())
with session_manager.session_state(session_id, timeout=30.0) as state:
    # Exclusive access guaranteed: filelock (cross-process) + threading.RLock (same-process)
    pass  # Lock automatically released after context exits, with atomic tempfile+rename write
```

## Centralized Error Handling (DRY)

```python
from autorun.error_handling import show_comprehensive_uv_error

# Single source of truth for all import errors
show_comprehensive_uv_error("MODULE ERROR", "Specific error details")
```

**Features:**
- **UV Environment Checking**: Automatic UV detection and setup guidance
- **Version Compatibility**: Flexible Python version support (3.10+)
- **Comprehensive Troubleshooting**: Step-by-step resolution guides
- **Consistent Messaging**: Same error format across all components

## Python Version Support

- **Minimum**: Python 3.10+ (required, matches `requires-python = ">=3.10"` in pyproject.toml)
- **Tested**: Python 3.10, 3.11, 3.12, 3.13, 3.14
- **Python 2.x**: Blocked with clear error message and solutions
- **Python 3.0-3.9**: Warning shown but functionality allowed

## Environment Requirements

**Development Environment:**
```bash
# Required: UV package manager for Python version management
uv --version  # Verify UV installation
uv sync --extra dev  # Install development dependencies
```

**Production Environment:**
- **Claude Code Plugin**: Official installation via `/plugin install`
- **Session Storage**: `~/.claude/sessions/` for state persistence
- **Lock Management**: File-based locks for cross-process coordination

## Session Storage

- Uses filelock+JSON backend for session persistence (`~/.claude/sessions/daemon_state.json`)
- Single JSON file with `filelock.FileLock` for cross-process locking and `threading.RLock` for same-process thread safety
- Atomic tempfile+rename writes for crash safety
- Located in `~/.claude/sessions/`
- State includes file policies and session status

## Agent SDK Integration

- Uses ClaudeAgentClient for communication
- Session IDs maintain conversation context
- Costs are tracked when using Claude Code APIs

## Claude Code Plugin Structure (General Pattern)

This is the general structure Claude Code expects for plugins:

```
plugin-name/
├── .claude-plugin/
│   └── plugin.json          # Plugin manifest and metadata
├── agents/
│   ├── agent-name.md          # Agent definitions (Task tool)
│   └── ...
├── commands/
│   ├── command-name           # Executable command script (JSON stdin/stdout)
│   ├── command-name.md        # Markdown slash command
│   └── ...
├── hooks/
│   ├── hooks.json             # Hook event configuration
│   └── hook_entry.py          # Hook handler script
├── skills/
│   └── skill-name.md          # Skill definitions
├── src/
│   └── package-name/          # Package code
└── ...
```

**autorun's specific structure:**
```
autorun/
├── .claude-plugin/
│   └── plugin.json          # Plugin manifest and metadata
├── agents/
│   ├── tmux-session-automation.md      # Session lifecycle automation
│   └── cli-test-automation.md         # CLI testing automation
├── commands/
│   ├── autorun            # Core plugin command script
│   ├── tmux-test-workflow.md           # Testing workflow
│   └── tmux-session-management.md      # Session management
├── src/
│   └── autorun/           # Package code
└── ... (other files)
```
