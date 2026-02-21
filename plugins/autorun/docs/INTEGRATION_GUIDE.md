# Claude Code Integration Guide

This guide describes how to integrate with autorun (v0.7+). The v0.6.1-era Agent SDK
and MCP Server integration modes have been superseded by the daemon-based v0.7 architecture.

## Current Integration Architecture (v0.7+)

autorun operates as a Unix socket daemon that processes Claude Code hook events efficiently.
The integration path is:

```
Claude Code hook event
  → hooks/hook_entry.py       (bootstrap; detects Claude vs Gemini CLI)
  → src/autorun/client.py   (Unix socket client; auto-starts daemon)
  → src/autorun/core.py     (AutorunDaemon; async dispatch)
  → src/autorun/plugins.py  (hook handlers: file policy, blocking, plan export)
```

**Performance**: 1–5ms per hook event (daemon mode) vs 50–150ms (legacy direct mode).

## Integration Options

### 1. Daemon Mode (Default — Recommended)

The daemon starts automatically on first hook event. No manual setup required.

```bash
# Verify daemon is running:
autorun --status

# Restart if needed:
autorun --restart-daemon
```

The daemon handles all hook events (UserPromptSubmit, PreToolUse, PostToolUse, SessionStart,
Stop, SubagentStop) through `hooks/claude-hooks.json` for Claude Code and `hooks/hooks.json`
for Gemini CLI.

**When to use**: All production usage. Fastest, most reliable.

### 2. Direct Mode (No Daemon — Debugging)

Run the hook handler in-process without the daemon. Useful when debugging hook logic or
when Unix sockets are not available.

```bash
# Set environment variable to bypass daemon:
export AUTORUN_USE_DAEMON=0

# Then run autorun normally — it goes through main.py directly:
echo '{"hook_event_name": "UserPromptSubmit", "prompt": "/ar:st", "session_id": "test"}' \
  | autorun

# Or inline:
AUTORUN_USE_DAEMON=0 autorun
```

**When to use**: Debugging hook logic, systems without Unix socket support.
**Performance**: 50–150ms per event (acceptable for debugging; use daemon in production).

### 3. Hook Integration (Slash Commands via .md Files)

Add new slash commands by placing markdown files in `commands/`:

```bash
# commands/mycommand.md — automatically available as /ar:mycommand
# Contents describe what Claude should do when the command is invoked
```

See `commands/` for 76 existing command examples. Commands are loaded by Claude Code at
session start and do not require hook handler changes.

**When to use**: Adding new slash commands without Python code.

### 4. Python Hook Handler Extension

Add new hook handlers in `src/autorun/plugins.py` using the factory patterns:

```python
# In plugins.py — add a new PreToolUse handler:
@app.on("PreToolUse")
def my_handler(ctx: EventContext) -> None:
    if ctx.tool_name == "Bash":
        # ctx.deny("reason") to block, return None to allow
        pass
```

See `src/autorun/plugins.py` for existing handler examples using `_make_policy_handler()`
and `_make_block_op()` factory patterns.

**When to use**: Adding new enforcement logic that requires Python.

## Hook File Naming (Important)

| Filename | CLI | Notes |
|----------|-----|-------|
| `hooks/claude-hooks.json` | Claude Code | Referenced by `plugin.json` |
| `hooks/hooks.json` | Gemini CLI | **Filename required by Gemini — cannot rename** |

## Testing

```bash
# Run full test suite:
uv run pytest plugins/autorun/tests/ -v --tb=short

# Test specific hook behavior:
uv run pytest plugins/autorun/tests/test_core.py -v
uv run pytest plugins/autorun/tests/test_integrations.py -v
```

## Bug #4669 Workaround (Claude Code v1.0.62+)

Claude Code ignores `permissionDecision:"deny"` at exit 0. The workaround:

```python
# client.py handles this automatically:
# Claude Code: exit 2 + stderr reason (only way blocking works)
# Gemini CLI: exit 0 + JSON decision field (works correctly per spec)
```

See `src/autorun/client.py:output_hook_response()` for implementation.
See `src/autorun/config.py:should_use_exit2_workaround()` for CLI detection.
