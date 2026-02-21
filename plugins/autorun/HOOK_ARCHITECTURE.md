# Hook Architecture Documentation

**Version**: 0.8.0
**Last Updated**: 2026-02-10
**Status**: Complete for Claude Code and Gemini CLI

---

## Table of Contents

- [Overview](#overview)
- [Dual CLI Support](#dual-cli-support)
- [Hook Files](#hook-files)
- [Hook Entry Point](#hook-entry-point)
- [Event Flow](#event-flow)
- [Tool Name Mapping](#tool-name-mapping)
- [Payload Normalization](#payload-normalization)
- [Testing](#testing)
- [Debugging](#debugging)
- [Common Pitfalls](#common-pitfalls)

---

## Overview

autorun implements a unified hook system that works across both **Claude Code** and **Gemini CLI**. The system uses separate hooks files for each CLI but shares the same Python handler code.

**Key Principles**:
- **Single Source of Truth**: One Python handler (`hook_entry.py`) for all CLIs
- **Format Separation**: Separate hooks.json for each CLI's specific format
- **Automatic Normalization**: Payload normalization handles CLI differences
- **Testing Coverage**: 133+ tests verify functionality across both CLIs

---

## Dual CLI Support

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Hook System Architecture                 │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Claude Code                         Gemini CLI             │
│  ─────────────                       ──────────             │
│                                                              │
│  claude-hooks.json                   hooks.json             │
│  ├─ PreToolUse                       ├─ BeforeTool          │
│  ├─ PostToolUse                      ├─ AfterTool           │
│  ├─ UserPromptSubmit                 ├─ BeforeAgent         │
│  ├─ SessionStart                     ├─ SessionStart        │
│  └─ Stop                             └─ SessionEnd          │
│                                                              │
│  ${CLAUDE_PLUGIN_ROOT}               ${extensionPath}       │
│  (environment variable)              (template substitution)│
│                                                              │
│  Tool names:                         Tool names:            │
│  - Write, Bash, Edit                 - write_file           │
│  - ExitPlanMode                      - run_shell_command    │
│  - TaskCreate                        - replace              │
│                                                              │
│  ──────────────────────┬────────────────────────            │
│                        │                                     │
│                        ▼                                     │
│              hooks/hook_entry.py                            │
│              ├─ get_plugin_root()                           │
│              ├─ normalize_payload()                         │
│              ├─ handle_hook()                               │
│              └─ main()                                      │
│                        │                                     │
│                        ▼                                     │
│           src/autorun/main.py                             │
│           ├─ handle_pretooluse()                            │
│           ├─ handle_posttooluse()                           │
│           ├─ handle_userpromptsubmit()                      │
│           ├─ handle_session_start()                         │
│           └─ Command blocking logic                         │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Installation Flow

**Installer Logic** (`plugins/autorun/src/autorun/install.py:883-920`):

1. `hooks/hooks.json` is always Gemini format (`${extensionPath}`, Gemini event names)
2. `hooks/claude-hooks.json` is always Claude Code format (`${CLAUDE_PLUGIN_ROOT}`)
3. Gemini CLI hardcodes reading `hooks/hooks.json` — no swap needed
4. Claude Code reads the `"hooks"` field from `plugin.json` → `./hooks/claude-hooks.json`

No swap logic required. Each CLI reads its own hooks file.

---

## Hook Files

### Claude Code Format (`hooks/hooks.json`)

```json
{
  "description": "autorun v0.8 - unified daemon-based hook handler",
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Write|Bash|ExitPlanMode",
        "hooks": [{
          "type": "command",
          "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/hook_entry.py",
          "timeout": 10
        }]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "ExitPlanMode|Write|Edit",
        "hooks": [{
          "type": "command",
          "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/hook_entry.py",
          "timeout": 10
        }]
      }
    ],
    "UserPromptSubmit": [
      {
        "matcher": "/afs|/afa|/afj|/afst|/autorun|/autostop|/estop|/ar:",
        "hooks": [{
          "type": "command",
          "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/hook_entry.py",
          "timeout": 10
        }]
      }
    ],
    "SessionStart": [
      {
        "hooks": [{
          "type": "command",
          "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/hook_entry.py",
          "timeout": 10
        }]
      }
    ]
  }
}
```

**Key Features**:
- Event names: `PreToolUse`, `PostToolUse`, `UserPromptSubmit`, `SessionStart`, `Stop`
- Environment variable: `${CLAUDE_PLUGIN_ROOT}` (set by Claude Code)
- Tool names: `Write`, `Bash`, `Edit`, `ExitPlanMode`, `TaskCreate`
- Timeout: 10 seconds (seconds, not milliseconds)

### Gemini CLI Format (`hooks/hooks.json`)

```json
{
  "description": "autorun v0.8 - Gemini CLI native compatibility hooks",
  "hooks": {
    "BeforeTool": [
      {
        "matcher": "write_file|run_shell_command|replace",
        "hooks": [{
          "name": "autorun-pretool",
          "type": "command",
          "command": "python3 ${extensionPath}/hooks/hook_entry.py",
          "timeout": 10000
        }]
      }
    ],
    "AfterTool": [
      {
        "matcher": "write_file|replace",
        "hooks": [{
          "name": "autorun-posttool-plan",
          "type": "command",
          "command": "python3 ${extensionPath}/hooks/hook_entry.py",
          "timeout": 10000
        }]
      }
    ],
    "BeforeAgent": [
      {
        "matcher": "/afs|/afa|/afj|/afst|/autorun|/autostop|/estop|/ar:",
        "hooks": [{
          "name": "autorun-command",
          "type": "command",
          "command": "python3 ${extensionPath}/hooks/hook_entry.py",
          "timeout": 10000
        }]
      }
    ],
    "SessionStart": [
      {
        "hooks": [{
          "name": "autorun-session-start",
          "type": "command",
          "command": "python3 ${extensionPath}/hooks/hook_entry.py",
          "timeout": 10000
        }]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [{
          "name": "autorun-session-end",
          "type": "command",
          "command": "python3 ${extensionPath}/hooks/hook_entry.py",
          "timeout": 10000
        }]
      }
    ]
  }
}
```

**Key Differences**:
- Event names: `BeforeTool`, `AfterTool`, `BeforeAgent`, `SessionStart`, `SessionEnd`
- Template substitution: `${extensionPath}` (replaced during install, not env var)
- Tool names: `write_file`, `run_shell_command`, `replace`
- Timeout: 10000 milliseconds (Gemini uses ms, Claude uses seconds)
- Required field: `type: "command"` (Gemini CLI requirement)
- Optional field: `name` (for debugging)

**CRITICAL**: Do NOT use environment variable assignment syntax like `VAR=${extensionPath}` in Gemini hooks. Gemini CLI doesn't support `VAR=value command` syntax.

---

## Hook Entry Point

### File: `hooks/hook_entry.py`

**Purpose**: Unified entry point for all CLIs that normalizes payloads and dispatches to main handler.

#### Key Functions

**`get_plugin_root() -> str`**

```python
def get_plugin_root() -> str:
    """Get plugin root directory (works for both installed and source).

    Priority:
    1. AUTORUN_PLUGIN_ROOT env var
    2. CLAUDE_PLUGIN_ROOT env var
    3. Infer from __file__ (for Gemini CLI)
    """
    try:
        plugin_root = os.environ.get("AUTORUN_PLUGIN_ROOT")
        if plugin_root:
            return plugin_root

        plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
        if plugin_root:
            return plugin_root

        # Gemini CLI doesn't set env vars, infer from script location
        script_path = os.path.abspath(__file__)
        hooks_dir = os.path.dirname(script_path)
        plugin_root = os.path.dirname(hooks_dir)
        return plugin_root
    except Exception:
        return os.getcwd()
```

**Why Inference Works**: Gemini CLI uses template substitution (`${extensionPath}`), so the script is always in the correct location relative to the plugin root.

**`normalize_payload(hook_input: dict) -> dict`**

```python
def normalize_payload(hook_input: dict) -> dict:
    """Normalize Gemini CLI payloads to Claude Code format.

    Mappings:
    - BeforeTool → PreToolUse
    - AfterTool → PostToolUse
    - BeforeAgent → UserPromptSubmit
    - SessionStart/SessionEnd → unchanged
    - toolName → tool_name (camelCase → snake_case)
    - hookEventName → hook_event_name
    """
    normalized = hook_input.copy()

    # Map event names
    event_mapping = {
        "BeforeTool": "PreToolUse",
        "AfterTool": "PostToolUse",
        "BeforeAgent": "UserPromptSubmit",
    }

    event = hook_input.get("hook_event_name") or hook_input.get("hookEventName")
    if event in event_mapping:
        normalized["hook_event_name"] = event_mapping[event]

    # Convert camelCase to snake_case
    if "toolName" in hook_input:
        normalized["tool_name"] = hook_input["toolName"]

    return normalized
```

---

## Event Flow

### PreToolUse / BeforeTool

**Purpose**: Intercept tool calls before execution (file creation, command execution, etc.)

**Trigger**: Before any tool matching the `matcher` pattern executes

**Input**:
```json
{
  "hook_event_name": "PreToolUse",  // or "BeforeTool" for Gemini
  "tool_name": "Write",             // or "write_file" for Gemini
  "tool_input": {
    "file_path": "/path/to/file.py",
    "content": "print('hello')"
  },
  "cwd": "/current/directory",
  "session_id": "abc123..."
}
```

**Output**:
```json
{
  "continue": true,                 // true = allow, false = block
  "permission": "allow",            // "allow" or "deny" (Gemini top-level)
  "systemMessage": "Optional message to Claude/Gemini",
  "modifiedToolInput": {}           // Optional: rewrite tool parameters
}
```

**Example Use Cases**:
- Block file creation (AutoFile policy)
- Block dangerous commands (cat, rm, git reset --hard)
- Redirect commands (rm → trash)
- Rewrite file paths
- Validate parameters

### PostToolUse / AfterTool

**Purpose**: React to tool execution after completion

**Trigger**: After tool matching the `matcher` pattern completes

**Input**:
```json
{
  "hook_event_name": "PostToolUse",  // or "AfterTool" for Gemini
  "tool_name": "ExitPlanMode",
  "tool_input": {...},
  "tool_output": {...},              // Tool execution result
  "cwd": "/current/directory",
  "session_id": "abc123..."
}
```

**Output**:
```json
{
  "continue": true,
  "systemMessage": "Plan exported to notes/plan-name.md"
}
```

**Example Use Cases**:
- Export plans after ExitPlanMode
- Update task tracking after TaskCreate/TaskUpdate
- Notify user of completions

### UserPromptSubmit / BeforeAgent

**Purpose**: Intercept user commands before agent processes them

**Trigger**: Before user prompt is processed (slash commands, autorun, etc.)

**Input**:
```json
{
  "hook_event_name": "UserPromptSubmit",  // or "BeforeAgent" for Gemini
  "prompt": "/ar:go Implement auth",
  "cwd": "/current/directory",
  "session_id": "abc123..."
}
```

**Output**:
```json
{
  "continue": true,
  "systemMessage": "Autorun started with justify-create file policy"
}
```

**Example Use Cases**:
- Set AutoFile policy before /ar:go
- Initialize session state
- Validate commands

### SessionStart

**Purpose**: Initialize session state, recover unexported plans

**Trigger**: When new session starts

**Input**:
```json
{
  "hook_event_name": "SessionStart",
  "session_id": "abc123...",
  "transcript_path": "/path/to/session.jsonl",  // Gemini only
  "cwd": "/current/directory"
}
```

**Output**:
```json
{
  "continue": true,
  "systemMessage": "Recovered unexported plan from previous session"
}
```

**Example Use Cases**:
- Recover unexported plans
- Initialize session defaults
- Load saved state

### Stop / SessionEnd

**Purpose**: Cleanup on session termination

**Trigger**: When session ends or user stops

**Input**:
```json
{
  "hook_event_name": "Stop",  // or "SessionEnd" for Gemini
  "reason": "exit",           // Gemini only
  "session_id": "abc123...",
  "cwd": "/current/directory"
}
```

**Output**:
```json
{
  "continue": true
}
```

**Example Use Cases**:
- Save session state
- Cleanup temporary files
- Export final plans

---

## Tool Name Mapping

### Claude Code → Gemini CLI

| Claude Code | Gemini CLI | Purpose |
|-------------|------------|---------|
| `Write` | `write_file` | Create new file |
| `Bash` | `run_shell_command` | Execute shell command |
| `Edit` | `replace` | Modify existing file |
| `ExitPlanMode` | `exit_plan_mode` | Exit plan mode |
| `TaskCreate` | `task_create` | Create task |
| `TaskUpdate` | `task_update` | Update task |

**Handler Code** (`src/autorun/main.py:normalize_tool_name()`):

```python
def normalize_tool_name(tool_input: dict) -> str:
    """Get normalized tool name from tool_input.

    Handles both Claude Code and Gemini CLI tool name formats.
    """
    tool_name = tool_input.get("tool_name")

    # Gemini CLI uses snake_case
    if not tool_name:
        tool_name = tool_input.get("toolName")

    return tool_name or "unknown"
```

---

## Payload Normalization

### Why Normalization?

Gemini CLI and Claude Code use different JSON formats for hook payloads. Normalization allows a single Python handler to work with both.

### Normalization Logic

**File**: `hooks/hook_entry.py:normalize_payload()`

**Transformations**:
1. **Event Name Mapping**: `BeforeTool` → `PreToolUse`
2. **Key Normalization**: `toolName` → `tool_name`
3. **Preserve Unknowns**: Pass through unrecognized fields

**Example**:

**Gemini Input**:
```json
{
  "hookEventName": "BeforeTool",
  "toolName": "write_file",
  "arguments": {"file_path": "/tmp/test.txt"},
  "sessionId": "abc123",
  "cwd": "/tmp"
}
```

**Normalized Output**:
```json
{
  "hook_event_name": "PreToolUse",
  "tool_name": "write_file",
  "tool_input": {"file_path": "/tmp/test.txt"},
  "session_id": "abc123",
  "cwd": "/tmp"
}
```

### Testing Normalization

**File**: `plugins/autorun/tests/test_actual_command_blocking.py:TestGeminiPayloadNormalization`

```python
def test_gemini_cat_blocked_through_normalization():
    """Test cat blocking with Gemini camelCase payload."""
    # Gemini format input
    hook_input = {
        "hookEventName": "BeforeTool",
        "toolName": "run_shell_command",
        "arguments": {"command": "cat file.txt"},
        "sessionId": "test-session"
    }

    result = handle_hook(hook_input)

    assert result["permission"] == "deny"  # Gemini top-level
    assert result["continue"] is False     # Internal
```

---

## Testing

### Test Coverage (133+ tests)

| Test File | Tests | Purpose |
|-----------|-------|---------|
| `test_hooks_format.py` | 11 | Validate hooks.json formats |
| `test_gemini_before_tool_hooks.py` | 6 | Gemini integration (via tmux) |
| `test_pretooluse_blocking_fix.py` | 9 | Claude PreToolUse blocking |
| `test_hook.py` | 16 | Hook handler logic |
| `test_session_start_handler.py` | 26 | SessionStart/plan recovery |
| `test_command_blocking_comprehensive.py` | 38 | Comprehensive blocking |
| `test_actual_command_blocking.py` | 27 | Real blocking + normalization |

**Total**: 133 tests

### Running Tests

```bash
# All hook tests
uv run pytest plugins/autorun/tests/test_*hook*.py -v

# Gemini integration tests (requires Gemini CLI installed)
uv run pytest plugins/autorun/tests/test_gemini_before_tool_hooks.py -v

# Format validation
uv run pytest plugins/autorun/tests/test_hooks_format.py -v

# Command blocking
uv run pytest plugins/autorun/tests/test_actual_command_blocking.py -v
```

### Integration Testing via Tmux

**File**: `plugins/autorun/tests/test_gemini_before_tool_hooks.py`

**Approach**: Use tmux sessions to test Gemini CLI interactively (avoid `--prompt` hangs)

```python
# Create isolated tmux session
session_name = f"gemini-hook-test-{int(time.time())}"
tmux = get_tmux_utilities(session_name)

# Start Gemini in tmux
tmux.execute_tmux_command(['new-session', '-d', '-s', session_name])
tmux.send_keys('gemini', session_name)
tmux.send_keys('C-m', session_name)

# Wait for startup
time.sleep(3)

# Send command to trigger hook
tmux.send_keys('Create test.txt', session_name)
tmux.send_keys('C-m', session_name)

# Verify hook fired via debug log
time.sleep(5)
assert debug_log.exists()
```

**Why Tmux?**: Gemini CLI hangs when run with `--prompt` in non-interactive mode. Tmux provides a real interactive terminal.

---

## Debugging

### Debug Hook Script

**Purpose**: Log all hook executions to verify hooks fire correctly

```python
#!/usr/bin/env python3
import sys
import json
import os
from datetime import datetime

DEBUG_LOG = "/tmp/gemini-before-tool-debug.log"

def main():
    stdin_data = sys.stdin.read()
    timestamp = datetime.now().isoformat()

    try:
        input_json = json.loads(stdin_data) if stdin_data else {}
    except:
        input_json = {"error": "failed to parse stdin"}

    # Log execution
    with open(DEBUG_LOG, "a") as f:
        f.write(f"\n{'='*60}\n")
        f.write(f"BeforeTool Hook: {timestamp}\n")
        f.write(f"Event: {input_json.get('hook_event_name', 'unknown')}\n")
        f.write(f"Tool: {input_json.get('tool_name', 'unknown')}\n")
        f.write(f"CWD: {os.getcwd()}\n")
        f.write(f"Input: {json.dumps(input_json, indent=2)}\n")
        f.write(f"{'='*60}\n")

    # Allow the tool to execute
    response = {"continue": True, "systemMessage": f"Debug hook executed"}
    print(json.dumps(response))
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

**Usage**:
1. Replace `~/.gemini/extensions/ar/hooks/hook_entry.py` with debug script
2. Run Gemini commands
3. Check `/tmp/gemini-before-tool-debug.log`
4. Restore original hook

### Troubleshooting

**Hook Not Firing**:
1. Check CLI version (Gemini v0.28.0+)
2. Verify settings.json has `enableHooks: true`
3. Check extension installed: `gemini extensions list`
4. Verify hooks.json format matches CLI
5. Check hook matcher patterns
6. Look for Python errors in CLI output

**Wrong Format**:
1. Check `hooks.json` uses `${CLAUDE_PLUGIN_ROOT}` (Claude)
2. Check `hooks.json` uses `${extensionPath}` (Gemini)
3. Verify no environment variable assignment in Gemini hooks
4. Run format validation tests: `uv run pytest test_hooks_format.py`

**Payload Issues**:
1. Add debug logging to `hook_entry.py`
2. Check stdin JSON format
3. Verify normalization logic handles all fields
4. Test with actual command blocking tests

For comprehensive troubleshooting, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

---

## Common Pitfalls

### 1. Environment Variable Assignment in Gemini

❌ **WRONG**:
```json
{
  "command": "AUTORUN_PLUGIN_ROOT=${extensionPath} python3 ${extensionPath}/hooks/hook_entry.py"
}
```

✅ **CORRECT**:
```json
{
  "command": "python3 ${extensionPath}/hooks/hook_entry.py"
}
```

**Reason**: Gemini CLI doesn't support `VAR=value command` syntax.

### 2. Editing Installed Extensions Directly

❌ **WRONG**:
```bash
# Editing installed extension
vim ~/.gemini/extensions/ar/hooks/hooks.json
```

✅ **CORRECT**:
```bash
# Edit source repository
cd ~/.claude/autorun
vim plugins/autorun/hooks/hooks.json

# Reinstall
uv run python -m plugins.autorun.src.autorun.install --install --gemini-only --force
```

**Reason**: Installed extensions are overwritten on reinstall.

### 3. Using subprocess.run() for Gemini Testing

❌ **WRONG**:
```python
result = subprocess.run(
    ["gemini", "--prompt", "create test.txt"],
    timeout=60
)
# Hangs indefinitely!
```

✅ **CORRECT**:
```python
# Use tmux session
session_name = f"test-{int(time.time())}"
tmux = get_tmux_utilities(session_name)
tmux.execute_tmux_command(['new-session', '-d', '-s', session_name])
tmux.send_keys('gemini', session_name)
# ... interactive testing
```

**Reason**: Gemini CLI requires interactive terminal.

### 4. Forgetting Timeout Units

❌ **WRONG** (Gemini with seconds):
```json
{
  "timeout": 10  // Should be 10000 for Gemini (milliseconds)
}
```

✅ **CORRECT**:
```json
{
  // Claude Code (seconds)
  "timeout": 10

  // Gemini CLI (milliseconds)
  "timeout": 10000
}
```

### 5. Missing Required Fields

❌ **WRONG** (Gemini without type):
```json
{
  "hooks": [{
    "command": "python3 ${extensionPath}/hooks/hook_entry.py"
    // Missing "type" field!
  }]
}
```

✅ **CORRECT**:
```json
{
  "hooks": [{
    "type": "command",  // Required by Gemini CLI
    "command": "python3 ${extensionPath}/hooks/hook_entry.py"
  }]
}
```

---

## References

- **Gemini CLI Documentation**: https://github.com/google-gemini/gemini-cli
- **Claude Code Plugin Docs**: https://docs.claude.com/en/docs/claude-code/plugins
- **Hook Reference**: https://docs.claude.com/en/docs/claude-code/hooks
- **Troubleshooting Guide**: [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- **Gemini Usage**: [GEMINI.md](GEMINI.md)
- **Integration Notes**: [notes/2026_02_10_1948_gemini_hooks_integration_complete_notes.md](../../notes/2026_02_10_1948_gemini_hooks_integration_complete_notes.md)

---

**Version**: 0.8.0
**Maintainer**: autorun project
**Last Verified**: 2026-02-10 with Gemini CLI v0.28.0 and Claude Code
