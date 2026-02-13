# Hooks API Reference: Claude Code & Gemini CLI

**Version:** 2.2
**Generated:** 2026-02-12
**Purpose:** Comprehensive comparison of hook APIs across Claude Code and Gemini CLI, with emphasis on JSON I/O, tool blocking, directory layouts, and variable systems.

**Key Enhancement (v2.2):** Detailed bug #4669 documentation with expected vs actual behavior, affected versions (v1.0.62+), and complete I/O pathway specifications for all tables.

---

## Table of Contents

1. [Master Comparison Table](#master-comparison-table)
2. [JSON Input Parameters](#json-input-parameters)
3. [JSON Output Parameters](#json-output-parameters)
4. [Environment Variables](#environment-variables)
5. [Hook Configuration Variables](#hook-configuration-variables)
6. [Tool Blocking: How to Block Tools While AI Continues](#tool-blocking-how-to-block-tools-while-ai-continues)
7. [Execution Flow with Outcomes](#execution-flow-with-outcomes)
8. [Outcome Matrices](#outcome-matrices)
9. [Directory Layout: Marketplace vs Plugin vs Extension](#directory-layout-marketplace-vs-plugin-vs-extension)
10. [Event Names Comparison](#event-names-comparison)
11. [Tool Names Comparison](#tool-names-comparison)
12. [Stdout/Stderr Behavior](#stdoutstderr-behavior)
13. [Hook Configuration Format](#hook-configuration-format)
14. [Implementation Patterns](#implementation-patterns)
15. [Per-Event Decision Control](#per-event-decision-control)
16. [LLM Request/Response (Gemini Only)](#llm-requestresponse-gemini-only)
17. [References](#references)

---

## Master Comparison Table

### Core Concepts

| Concept | Claude Code | Gemini CLI |
|---------|-------------|------------|
| **Extension Name** | Plugin | Extension |
| **Manifest File** | `.claude-plugin/plugin.json` | `gemini-extension.json` |
| **Hook Config** | `hooks/hooks.json` | `hooks/hooks.json` |
| **Config Variable** | `${CLAUDE_PLUGIN_ROOT}` | `${extensionPath}` |
| **Key Naming** | snake_case | camelCase |

### Hook Events

| Purpose | Claude Code Event | Gemini CLI Event |
|---------|-------------------|------------------|
| Before tool execution | `PreToolUse` | `BeforeTool` |
| After tool execution | `PostToolUse` | `AfterTool` |
| User submits message | `UserPromptSubmit` | `BeforeAgent` |
| After agent response | - | `AfterAgent` |
| Before LLM call | - | `BeforeModel` |
| After LLM response | - | `AfterModel` |
| Before tool selection | - | `BeforeToolSelection` |
| Session starts | `SessionStart` | `SessionStart` |
| Session ends | `SessionEnd` / `Stop` | `SessionEnd` |
| Subagent ends | `SubagentStop` | - |
| Before context compact | `PreCompact` | - |
| System notification | `Notification` | - |

### Blocking Mechanisms

| Mechanism | Claude Code | Gemini CLI |
|-----------|-------------|------------|
| **Decision Field** | `hookSpecificOutput.permissionDecision` | `decision` (top-level) |
| **Decision Values** | `"allow"`, `"deny"`, `"ask"` | `"allow"`, `"deny"`, `"block"`, `"ask"` |
| **Exit Code 2** | Blocks tool (bug #4669 workaround) | Blocks tool |
| **`continue: false`** | Stops AI entirely | Stops AI entirely |
| **Blocking Requires** | Exit code 2 + stderr (workaround) | `decision: "deny"` + `continue: true` |

**Bug #4669 Detail (Claude Code PreToolUse):**
- **JSON Key:** `hookSpecificOutput.permissionDecision`
- **Value:** `"deny"` at exit 0
- **Should:** Tool blocked, reason fed to AI
- **Does:** Tool executes anyway (denial ignored)
- **Versions:** v1.0.62+ through v2.1.39 (current)
- **Workaround:** Exit code 2 + reason on stderr

### Stdout/Stderr

| Stream | Claude Code | Gemini CLI |
|--------|-------------|------------|
| **stdout** | JSON response only | JSON response only |
| **stderr** | ANY output = hook error | Safe for debug logging |
| **On stderr** | JSON ignored, fail-open | stderr shown to user, JSON processed |
| **Blocking I/O** | stderr (exit 2) → AI feedback | stdout JSON `reason` → AI feedback |

### Required Settings

| Setting | Claude Code | Gemini CLI |
|---------|-------------|------------|
| **Enable hooks** | Automatic | `enableHooks: true` |
| **Additional** | None | `enableMessageBusIntegration: true` |
| **Config file** | `~/.claude/settings.json` | `~/.gemini/settings.json` |

---

## JSON Input Parameters

### Claude Code Input (stdin)

```json
{
  "hook_event_name": "PreToolUse",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "tool_name": "Bash",
  "tool_input": {
    "command": "rm -rf /important",
    "description": "Delete important files"
  },
  "tool_result": null,
  "session_transcript": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ]
}
```

### Gemini CLI Input (stdin)

```json
{
  "type": "BeforeTool",
  "sessionId": "550e8400-e29b-41d4-a716-446655440000",
  "prompt": "Delete the important files",
  "toolName": "run_shell_command",
  "toolInput": {
    "command": "rm -rf /important"
  },
  "toolResult": null,
  "transcriptPath": "/path/to/transcript.jsonl",
  "cwd": "/current/working/directory"
}
```

### Input Field Comparison

| Purpose | Claude Code | Gemini CLI | Notes |
|---------|-------------|------------|-------|
| Event type | `hook_event_name` | `type` | Different naming |
| Session ID | `session_id` | `sessionId` | snake_case vs camelCase |
| Tool name | `tool_name` | `toolName` | snake_case vs camelCase |
| Tool input | `tool_input` | `toolInput` | snake_case vs camelCase |
| Tool result | `tool_result` | `toolResult` | snake_case vs camelCase |
| User prompt | In transcript | `prompt` | Gemini provides directly |
| Transcript | `session_transcript` (array) | `transcriptPath` (file path) | Different access pattern |
| Working directory | Via env var | `cwd` | Gemini provides directly |

---

## JSON Output Parameters

### Claude Code Output Schema

#### Basic Response (All Events)

```json
{
  "continue": true,
  "stopReason": "",
  "suppressOutput": false,
  "systemMessage": "Optional message shown to user"
}
```

#### PreToolUse Response (Tool Blocking)

```json
{
  "continue": true,
  "stopReason": "",
  "suppressOutput": false,
  "systemMessage": "Tool blocked: dangerous command",
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "rm command blocked for safety",
    "modifiedToolInput": {
      "command": "trash /important"
    }
  }
}
```

### Gemini CLI Output Schema

#### Standard Response

```json
{
  "decision": "deny",
  "reason": "rm command blocked for safety",
  "continue": true,
  "stopReason": "",
  "suppressOutput": false,
  "systemMessage": "Use trash command instead of rm",
  "modifiedToolInput": {
    "command": "trash /important"
  }
}
```

### Output Field Comparison

| Purpose | Claude Code | Gemini CLI | Notes |
|---------|-------------|------------|-------|
| Continue execution | `continue` | `continue` | Same |
| Stop reason | `stopReason` | `stopReason` | Same |
| Suppress output | `suppressOutput` | `suppressOutput` | Same |
| User message | `systemMessage` | `systemMessage` | Same |
| **Decision** | `hookSpecificOutput.permissionDecision` | `decision` | **KEY DIFFERENCE** |
| **Reason** | `hookSpecificOutput.permissionDecisionReason` | `reason` | **KEY DIFFERENCE** |
| Modify tool | `hookSpecificOutput.modifiedToolInput` | `modifiedToolInput` | Different nesting |
| Event echo | `hookSpecificOutput.hookEventName` | - | Claude only |

### Decision Values Comparison

| Action | Claude Code | Gemini CLI |
|--------|-------------|------------|
| Allow tool | `"allow"` | `"allow"` |
| Block tool, AI continues | `"deny"` | `"deny"` |
| Block everything | `"deny"` + `continue: false` | `"block"` |
| Ask user | `"ask"` | `"ask"` |

---

## Environment Variables

### Claude Code Environment Variables

| Variable | Purpose | Example Value |
|----------|---------|---------------|
| `CLAUDE_PROJECT_DIR` | Current project directory | `/home/user/myproject` |
| `CLAUDE_PLUGIN_ROOT` | Path to plugin directory | `/home/user/.claude/plugins/cache/org/plugin/1.0.0` |

### Gemini CLI Environment Variables

| Variable | Purpose | Example Value |
|----------|---------|---------------|
| `GEMINI_SESSION_ID` | Current session identifier | `550e8400-e29b-41d4-a716-446655440000` |
| `GEMINI_PROJECT_DIR` | Current project directory | `/home/user/myproject` |
| `GEMINI_CLI_HOME` | Root for Gemini CLI config | `/home/user/.gemini` |
| `GEMINI_API_KEY` | API key | `AIza...` |
| `GEMINI_MODEL` | Default model | `gemini-2.0-flash` |
| `GEMINI_SANDBOX` | Sandbox mode | `true`, `false` |
| `DEBUG` / `DEBUG_MODE` | Verbose logging | `1`, `true` |

### CLI Detection Pattern

```python
import os

def detect_cli_type() -> str:
    """Detect which CLI is calling the hook."""
    # GEMINI_SESSION_ID is most reliable
    if os.environ.get("GEMINI_SESSION_ID"):
        return "gemini"
    # GEMINI_PROJECT_DIR without CLAUDE_PROJECT_DIR
    if os.environ.get("GEMINI_PROJECT_DIR") and not os.environ.get("CLAUDE_PROJECT_DIR"):
        return "gemini"
    # Default to Claude
    return "claude"

def get_project_dir() -> str:
    """Get project directory regardless of CLI."""
    return (
        os.environ.get("GEMINI_PROJECT_DIR") or
        os.environ.get("CLAUDE_PROJECT_DIR") or
        os.getcwd()
    )
```

---

## Hook Configuration Variables

### Claude Code Variables (in hooks.json)

| Variable | Expands To | Example |
|----------|------------|---------|
| `${CLAUDE_PLUGIN_ROOT}` | Absolute path to plugin directory | `/home/user/.claude/plugins/cache/clautorun/clautorun/0.8.0` |
| `${CLAUDE_PROJECT_DIR}` | Current project directory | `/home/user/myproject` |

**Example hooks.json command:**
```json
{
  "command": "uv run --project ${CLAUDE_PLUGIN_ROOT} python ${CLAUDE_PLUGIN_ROOT}/hooks/hook_entry.py",
  "timeout": 10
}
```

### Gemini CLI Variables (in hooks.json)

| Variable | Expands To | Example |
|----------|------------|---------|
| `${extensionPath}` | Absolute path to extension directory | `/home/user/.gemini/extensions/clautorun-workspace/plugins/clautorun` |
| `${workspacePath}` | Current workspace/project directory | `/home/user/myproject` |
| `${/}` or `${pathSeparator}` | Platform-specific path separator | `/` (Unix) or `\` (Windows) |

**Example hooks.json command:**
```json
{
  "command": "uv run --quiet --project ${extensionPath} python ${extensionPath}/hooks/hook_entry.py",
  "timeout": 10000
}
```

### Key Variable Differences

| Aspect | Claude Code | Gemini CLI |
|--------|-------------|------------|
| **Plugin/Extension path** | `${CLAUDE_PLUGIN_ROOT}` | `${extensionPath}` |
| **Symlink handling** | Resolves to cache | Preserves symlinks |
| **Timeout units** | Seconds | Milliseconds |
| **Project dir** | `${CLAUDE_PROJECT_DIR}` | `${workspacePath}` |

---

## Tool Blocking: How to Block Tools While AI Continues

### The Problem

Both CLIs treat `continue: false` as "stop the AI entirely", NOT "stop just this tool". To block a tool while allowing the AI to suggest alternatives, you need special handling.

### Claude Code Blocking (Bug #4669 Workaround)

**BUG #4669 - Status: OPEN**

| Aspect | Description |
|--------|-------------|
| **Should Happen** | `permissionDecision: "deny"` + exit 0 → tool blocked, reason fed to AI, AI continues |
| **Actually Happens** | `permissionDecision: "deny"` + exit 0 → tool **executes anyway**, denial ignored |
| **Affected Versions** | Claude Code v1.0.62+ through v2.1.39 (current as of 2026-02-12) |
| **Workaround** | Exit code 2 + reason printed to stderr → tool actually blocked |

**Reference:** [GitHub Issue #4669](https://github.com/anthropics/claude-code/issues/4669)

**SOLUTION:** Use exit code 2 + print reason to stderr.

```python
def block_tool_claude(reason: str) -> None:
    """Block tool in Claude Code while allowing AI to continue.

    CRITICAL: Must exit with code 2 and write to stderr.
    The JSON response is ignored when permissionDecision="deny".
    """
    response = {
        "continue": True,  # Allow AI to continue
        "stopReason": "",
        "suppressOutput": False,
        "systemMessage": reason,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason
        }
    }
    # Print JSON to stdout (required format)
    print(json.dumps(response))
    # Print reason to stderr (Claude feeds this back to AI)
    print(reason, file=sys.stderr)
    # Exit code 2 triggers actual blocking
    sys.exit(2)
```

**Reference:** [GitHub Issue #4669](https://github.com/anthropics/claude-code/issues/4669)

### Gemini CLI Blocking

**BEHAVIOR:** `decision: "deny"` is respected in JSON response.

```python
def block_tool_gemini(reason: str) -> None:
    """Block tool in Gemini CLI while allowing AI to continue.

    Gemini respects decision="deny" in JSON response.
    """
    response = {
        "decision": "deny",  # Block the tool
        "reason": reason,
        "continue": True,    # Allow AI to continue
        "stopReason": "",
        "suppressOutput": False,
        "systemMessage": f"Blocked: {reason}\nSuggest an alternative approach."
    }
    print(json.dumps(response))
    # Optional: log to stderr for debugging
    print(f"[hook] Blocked tool: {reason}", file=sys.stderr)
    sys.exit(0)  # Exit 0 is fine, decision="deny" is respected
```

### Unified Blocking Implementation

```python
def block_tool(cli_type: str, reason: str, alternative: str = "") -> None:
    """Block tool while allowing AI to continue, for both CLIs."""
    message = f"BLOCKED: {reason}"
    if alternative:
        message += f"\nAlternative: {alternative}"

    response = {
        "continue": True,  # AI continues in both CLIs
        "stopReason": "",
        "suppressOutput": False,
        "systemMessage": message,
        # Gemini CLI fields
        "decision": "deny",
        "reason": reason,
        # Claude Code fields
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason
        }
    }

    print(json.dumps(response))

    if cli_type == "claude":
        # Claude Code: exit 2 for actual blocking
        print(reason, file=sys.stderr)
        sys.exit(2)
    else:
        # Gemini CLI: decision="deny" is respected
        sys.exit(0)
```

### Blocking Behavior Matrix

| CLI | JSON Field | Exit Code | `continue` | Result |
|-----|------------|-----------|------------|--------|
| Claude | `permissionDecision: "deny"` | 0 | `true` | ❌ TOOL EXECUTES (BUG #4669 - denial ignored, v1.0.62+) |
| Claude | `permissionDecision: "deny"` | 2 | `true` | ✅ **TOOL BLOCKED**, AI continues (workaround) |
| Claude | - | 2 | - | ✅ **TOOL BLOCKED**, stderr to AI |
| Gemini | `decision: "deny"` | 0 | `true` | ✅ **TOOL BLOCKED**, AI continues (works as designed) |
| Gemini | `decision: "deny"` | 2 | `true` | ✅ **TOOL BLOCKED**, AI continues |
| Gemini | `decision: "block"` | 0 | `false` | ⚠️ **AI STOPS** entirely |

**Bug Note:** In Claude Code, `permissionDecision: "deny"` at exit 0 should block the tool but doesn't. The tool executes anyway as if allowed. This affects v1.0.62+ (current: v2.1.39). Use exit code 2 for actual blocking.

### Parameter Rewriting (Alternative to Blocking)

```python
def rewrite_tool(cli_type: str, tool_input: dict, safe_alternative: dict) -> dict:
    """Modify tool parameters instead of blocking."""
    response = {
        "continue": True,
        "decision": "allow",
        "stopReason": "",
        "suppressOutput": False,
        "systemMessage": f"Modified command for safety",
    }

    if cli_type == "claude":
        response["hookSpecificOutput"] = {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "modifiedToolInput": safe_alternative
        }
    else:
        response["modifiedToolInput"] = safe_alternative

    return response

# Example: rm → trash
rewrite_tool("claude",
    {"command": "rm -rf /important"},
    {"command": "trash /important"}
)
```

---

## Directory Layout: Marketplace vs Plugin vs Extension

### Claude Code Directory Structure

```
~/.claude/
├── settings.json                    # Global settings
├── CLAUDE.md                        # Global instructions
│
├── plugins/
│   ├── cache/                       # INSTALLED PLUGINS (read-only)
│   │   └── <org>/
│   │       └── <plugin-name>/
│   │           └── <version>/
│   │               ├── .claude-plugin/
│   │               │   ├── plugin.json      # Plugin manifest
│   │               │   └── marketplace.json # Optional marketplace config
│   │               ├── commands/            # Slash commands (.md files)
│   │               ├── agents/              # Subagent definitions
│   │               ├── skills/              # Skill definitions
│   │               └── hooks/
│   │                   └── hooks.json       # Hook configuration
│   │
│   └── marketplace/                 # MARKETPLACE DEFINITIONS
│       └── my-marketplace/
│           └── marketplace.json     # Lists plugins in marketplace
│
└── projects/
    └── <project-hash>/
        └── .claude/
            └── plugins/             # PROJECT-LOCAL PLUGINS
                └── local-plugin/
                    └── ...
```

### Gemini CLI Directory Structure

```
~/.gemini/
├── settings.json                    # Global settings (requires enableHooks)
├── config.json                      # CLI configuration
│
├── extensions/                      # INSTALLED EXTENSIONS
│   └── <extension-name>/
│       ├── gemini-extension.json    # Extension manifest
│       ├── commands/                # Custom commands (.toml files)
│       │   └── mycommand.toml
│       ├── hooks/
│       │   └── hooks.json           # Hook configuration
│       ├── prompts/                 # System prompts
│       │   └── system_prompt.md
│       └── src/                     # Extension source code
│           └── handler.py
│
└── workspace/                       # WORKSPACE EXTENSIONS
    └── <workspace-name>/
        └── ...                      # Symlinked or copied extensions
```

### Directory Comparison Table

| Concept | Claude Code | Gemini CLI |
|---------|-------------|------------|
| **Install location** | `~/.claude/plugins/cache/<org>/<name>/<ver>/` | `~/.gemini/extensions/<name>/` |
| **Manifest file** | `.claude-plugin/plugin.json` | `gemini-extension.json` |
| **Commands format** | Markdown (`.md`) | TOML (`.toml`) |
| **Commands dir** | `commands/` | `commands/` |
| **Hooks config** | `hooks/hooks.json` | `hooks/hooks.json` |
| **Marketplace config** | `.claude-plugin/marketplace.json` | N/A |
| **Settings file** | `~/.claude/settings.json` | `~/.gemini/settings.json` |

### Claude Code: Marketplace vs Plugin

**Marketplace** (`marketplace.json`):
- Defines a COLLECTION of plugins
- Lists plugin sources (GitHub, git URL, relative path)
- Can restrict to specific marketplaces via `strictKnownMarketplaces`

**Plugin** (`plugin.json`):
- Single plugin definition
- Specifies commands, agents, skills, hooks locations
- Installed into cache directory

```json
// Marketplace: ~/.claude/plugins/marketplace/my-marketplace/marketplace.json
{
  "marketplace": "my-marketplace",
  "plugins": [
    {
      "name": "toolkit",
      "source": {
        "type": "github",
        "owner": "myorg",
        "repo": "claude-toolkit"
      }
    },
    {
      "name": "local-helper",
      "source": {
        "type": "relative",
        "path": "../local-plugins/helper"
      }
    }
  ]
}
```

```json
// Plugin: .claude-plugin/plugin.json
{
  "name": "toolkit",
  "version": "1.0.0",
  "description": "Development toolkit",
  "commands": ["commands/"],
  "agents": ["agents/"],
  "skills": ["skills/"],
  "hooks": "hooks/hooks.json"
}
```

### Gemini CLI: Extension Structure

No marketplace concept - extensions are installed directly:

```json
// Extension: gemini-extension.json
{
  "name": "toolkit",
  "version": "1.0.0",
  "description": "Development toolkit",
  "entryPoint": "src/index.ts",
  "contributes": {
    "commands": [
      {
        "command": "myCommand",
        "title": "My Command",
        "category": "Toolkit"
      }
    ],
    "hooks": "hooks/hooks.json"
  }
}
```

### Installation Behavior

| Action | Claude Code | Gemini CLI |
|--------|-------------|------------|
| **Install** | Copies to cache | Copies to extensions dir |
| **Link (dev)** | Not available | `gemini extensions link` (symlinks) |
| **Update** | `claude plugin update` | Re-install or re-link |
| **Remove** | `claude plugin remove` | `gemini extensions uninstall` |

---

## Event Names Comparison

### Mapping Table

| Purpose | Claude Code | Gemini CLI | Unified Name |
|---------|-------------|------------|--------------|
| Block/modify tool | `PreToolUse` | `BeforeTool` | `pre_tool` |
| Post-process tool | `PostToolUse` | `AfterTool` | `post_tool` |
| Intercept command | `UserPromptSubmit` | `BeforeAgent` | `user_prompt` |
| Session init | `SessionStart` | `SessionStart` | `session_start` |
| Session cleanup | `Stop` / `SessionEnd` | `SessionEnd` | `session_end` |
| Subagent cleanup | `SubagentStop` | - | `subagent_stop` |
| Before compact | `PreCompact` | - | `pre_compact` |
| Notification | `Notification` | - | `notification` |
| After agent | - | `AfterAgent` | `after_agent` |
| Before LLM | - | `BeforeModel` | `before_model` |
| After LLM | - | `AfterModel` | `after_model` |
| Filter tools | - | `BeforeToolSelection` | `before_tool_selection` |

### Event Normalization Code

```python
def normalize_event(event: str) -> str:
    """Normalize event names across CLIs."""
    mapping = {
        # Gemini -> Claude
        "BeforeTool": "PreToolUse",
        "AfterTool": "PostToolUse",
        "BeforeAgent": "UserPromptSubmit",
        # SessionStart and SessionEnd are same
    }
    return mapping.get(event, event)
```

---

## Tool Names Comparison

### Mapping Table

| Purpose | Claude Code | Gemini CLI |
|---------|-------------|------------|
| Create file | `Write` | `write_file` |
| Edit file | `Edit` | `replace` |
| Run command | `Bash` | `run_shell_command` |
| Read file | `Read` | `read_file` |
| Find files | `Glob` | `glob` |
| Search text | `Grep` | `search_file_content` |
| Create task | `TaskCreate` | `write_todos` |
| Update task | `TaskUpdate` | `write_todos` |
| List tasks | `TaskList` | `write_todos` |
| Web fetch | `WebFetch` | `read_url` |
| Web search | `WebSearch` | `search_web` |

### Tool Name Normalization

```python
def normalize_tool_name(tool: str) -> str:
    """Normalize tool names across CLIs."""
    mapping = {
        # Gemini -> Claude
        "write_file": "Write",
        "replace": "Edit",
        "run_shell_command": "Bash",
        "read_file": "Read",
        "glob": "Glob",
        "search_file_content": "Grep",
        "write_todos": "TaskUpdate",  # Maps all task operations
    }
    return mapping.get(tool, tool)
```

---

## Stdout/Stderr Behavior

### Claude Code

```
┌─────────────────────────────────────────────────────────────┐
│                    CLAUDE CODE HOOK FLOW                    │
├─────────────────────────────────────────────────────────────┤
│  Hook executes                                              │
│       ↓                                                     │
│  Exit code?                                                 │
│       ├── 0: Parse stdout as JSON                           │
│       │       └── stderr? → Treat as error, ignore JSON     │
│       ├── 2: Feed stderr back to Claude, block tool         │
│       └── other: Log error, process stdout                  │
│                                                             │
│  CRITICAL: ANY stderr output = hook error = fail-open       │
└─────────────────────────────────────────────────────────────┘
```

**Rules:**
1. stdout MUST be valid JSON only
2. ANY stderr output causes hook to be treated as failed
3. Failed hooks result in fail-open (operation allowed)
4. Exit code 2 + stderr = actual blocking

### Gemini CLI

```
┌─────────────────────────────────────────────────────────────┐
│                    GEMINI CLI HOOK FLOW                     │
├─────────────────────────────────────────────────────────────┤
│  Hook executes                                              │
│       ↓                                                     │
│  Exit code?                                                 │
│       ├── 0: Parse stdout as JSON                           │
│       │       └── stderr shown to user/agent for debug     │
│       ├── 2: Blocking error, show stderr                    │
│       └── other: Log warning, continue                      │
│                                                             │
│  stderr is SAFE for debug logging                           │
└─────────────────────────────────────────────────────────────┘
```

**Rules:**
1. stdout MUST be valid JSON only
2. stderr is safe for debug/logging output
3. stderr is displayed to user/agent
4. JSON `decision` field is respected

### Implementation Pattern

```python
import sys
import json

def output_response(response: dict, cli_type: str, debug_msg: str = "") -> None:
    """Output response correctly for both CLIs."""
    # Always print JSON to stdout
    print(json.dumps(response))

    # Handle debug/logging
    if debug_msg:
        if cli_type == "gemini":
            # Safe to log to stderr in Gemini
            print(f"[hook] {debug_msg}", file=sys.stderr)
        # Claude Code: NEVER write to stderr

    # Handle exit codes
    if cli_type == "claude" and response.get("hookSpecificOutput", {}).get("permissionDecision") == "deny":
        # Claude Code blocking requires exit 2
        print(response.get("hookSpecificOutput", {}).get("permissionDecisionReason", ""), file=sys.stderr)
        sys.exit(2)
    else:
        sys.exit(0)
```

---

## Hook Configuration Format

### Claude Code hooks.json

```json
{
  "description": "Plugin description",
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash|Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "uv run --project ${CLAUDE_PLUGIN_ROOT} python ${CLAUDE_PLUGIN_ROOT}/hooks/hook_entry.py",
            "timeout": 10
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "matcher": "/mycommand|/another",
        "hooks": [
          {
            "type": "command",
            "command": "...",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

**Fields:**
- `description`: Plugin description (required)
- `hooks`: Event mapping (required)
- `matcher`: Regex pattern for tool/command matching
- `type`: Always `"command"`
- `command`: Shell command to execute
- `timeout`: Timeout in **seconds**

### Gemini CLI hooks.json

```json
{
  "description": "Extension description",
  "hooks": {
    "BeforeTool": [
      {
        "matcher": "write_file|run_shell_command|replace",
        "hooks": [
          {
            "name": "hook-name",
            "type": "command",
            "command": "uv run --quiet --project ${extensionPath} python ${extensionPath}/hooks/hook_entry.py",
            "timeout": 10000
          }
        ]
      }
    ]
  }
}
```

**Fields:**
- `description`: Extension description (required)
- `hooks`: Event mapping (required)
- `matcher`: Regex pattern for tool matching
- `name`: Hook name (optional but recommended)
- `type`: Always `"command"`
- `command`: Shell command to execute
- `timeout`: Timeout in **milliseconds**

### Configuration Differences

| Aspect | Claude Code | Gemini CLI |
|--------|-------------|------------|
| **Timeout units** | Seconds | Milliseconds |
| **Hook name** | Not supported | `name` field supported |
| **Quiet flag** | Manual | `--quiet` recommended |
| **Variable** | `${CLAUDE_PLUGIN_ROOT}` | `${extensionPath}` |

---

## Implementation Patterns

### Cross-Platform Response Builder

```python
from typing import Literal
from dataclasses import dataclass

@dataclass
class HookContext:
    cli_type: Literal["claude", "gemini"]
    event: str
    tool_name: str
    tool_input: dict

def build_response(
    ctx: HookContext,
    decision: Literal["allow", "deny", "ask"],
    reason: str = "",
    modified_input: dict = None,
    system_message: str = ""
) -> dict:
    """Build response compatible with both CLIs."""

    base = {
        "continue": True,  # Always allow AI to continue
        "stopReason": "" if decision != "deny" else reason,
        "suppressOutput": False,
        "systemMessage": system_message or reason,
    }

    # Add decision fields for both CLIs
    if ctx.cli_type == "claude":
        base["hookSpecificOutput"] = {
            "hookEventName": ctx.event,
            "permissionDecision": decision,
            "permissionDecisionReason": reason
        }
        if modified_input:
            base["hookSpecificOutput"]["modifiedToolInput"] = modified_input
    else:
        base["decision"] = decision
        base["reason"] = reason
        if modified_input:
            base["modifiedToolInput"] = modified_input

    return base

def output_and_exit(response: dict, cli_type: str) -> None:
    """Output response and exit with correct code."""
    import json
    import sys

    print(json.dumps(response))

    # Claude Code: exit 2 for actual blocking
    if cli_type == "claude":
        decision = response.get("hookSpecificOutput", {}).get("permissionDecision")
        if decision == "deny":
            reason = response.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
            print(reason, file=sys.stderr)
            sys.exit(2)

    sys.exit(0)
```

### Complete Hook Entry Example

```python
#!/usr/bin/env python3
"""Hook entry point supporting both Claude Code and Gemini CLI."""

import json
import os
import sys
from pathlib import Path

def detect_cli() -> str:
    """Detect which CLI is calling."""
    if os.environ.get("GEMINI_SESSION_ID"):
        return "gemini"
    if os.environ.get("GEMINI_PROJECT_DIR") and not os.environ.get("CLAUDE_PROJECT_DIR"):
        return "gemini"
    return "claude"

def read_input() -> dict:
    """Read hook input from stdin."""
    if sys.stdin.isatty():
        return {}
    return json.load(sys.stdin)

def normalize_input(data: dict, cli_type: str) -> dict:
    """Normalize input to common format."""
    if cli_type == "gemini":
        return {
            "event": {"BeforeTool": "PreToolUse", "AfterTool": "PostToolUse"}.get(data.get("type"), data.get("type")),
            "tool_name": data.get("toolName", ""),
            "tool_input": data.get("toolInput", {}),
            "session_id": data.get("sessionId", ""),
        }
    return {
        "event": data.get("hook_event_name", ""),
        "tool_name": data.get("tool_name", ""),
        "tool_input": data.get("tool_input", {}),
        "session_id": data.get("session_id", ""),
    }

def handle_tool_use(ctx: dict, cli_type: str) -> dict:
    """Handle PreToolUse/BeforeTool events."""
    tool = ctx["tool_name"]
    tool_input = ctx["tool_input"]

    # Example: Block dangerous commands
    if tool in ("Bash", "run_shell_command"):
        cmd = tool_input.get("command", "")
        if "rm -rf" in cmd and "/important" in cmd:
            return {
                "continue": True,
                "stopReason": "Dangerous command blocked",
                "suppressOutput": False,
                "systemMessage": "Use 'trash' instead of 'rm -rf /important'",
                "decision": "deny",
                "reason": "Dangerous command blocked",
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Dangerous command blocked"
                }
            }

    # Allow by default
    return {
        "continue": True,
        "stopReason": "",
        "suppressOutput": False,
        "systemMessage": "",
        "decision": "allow",
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow"
        }
    }

def main():
    cli_type = detect_cli()
    raw_input = read_input()
    ctx = normalize_input(raw_input, cli_type)

    event = ctx["event"]

    if event in ("PreToolUse", "BeforeTool"):
        response = handle_tool_use(ctx, cli_type)
    else:
        response = {"continue": True, "stopReason": "", "suppressOutput": False, "systemMessage": ""}

    # Output and exit
    print(json.dumps(response))

    if cli_type == "claude":
        decision = response.get("hookSpecificOutput", {}).get("permissionDecision")
        if decision == "deny":
            print(response.get("hookSpecificOutput", {}).get("permissionDecisionReason", ""), file=sys.stderr)
            sys.exit(2)

    sys.exit(0)

if __name__ == "__main__":
    main()
```

---

## Execution Flow with Outcomes

### Claude Code PreToolUse Flow

**Source:** [Claude Code Hooks Documentation](https://docs.anthropic.com/en/docs/claude-code/hooks)

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                      CLAUDE CODE PreToolUse EXECUTION FLOW                      │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  1. AI REQUESTS TOOL USE                                                        │
│     └── Example: Bash with command "rm -rf /important"                          │
│                                                                                 │
│  2. HOOK TRIGGERS (if tool matches matcher regex)                               │
│     └── hooks.json: "matcher": "Bash|Write|Edit"                                │
│                                                                                 │
│  3. HOOK SCRIPT EXECUTES                                                        │
│     ├── stdin: JSON with tool_name, tool_input, session_transcript              │
│     ├── env: CLAUDE_PLUGIN_ROOT, CLAUDE_PROJECT_DIR                             │
│     └── timeout: Seconds from hooks.json (default: varies)                      │
│                                                                                 │
│  4. HOOK OUTPUTS RESPONSE                                                       │
│     ├── stdout: JSON response (REQUIRED)                                        │
│     └── stderr: ERROR if ANY output (causes fail-open)                          │
│                                                                                 │
│  5. EXIT CODE DETERMINES OUTCOME                                                │
│     │                                                                           │
│     ├── exit 0 + JSON parsed                                                    │
│     │   ├── permissionDecision: "allow" → TOOL EXECUTES (normal)                │
│     │   ├── permissionDecision: "deny"  → TOOL EXECUTES (BUG #4669)             │
│     │   │   └── SHOULD: Tool blocked, reason to AI  │ DOES: Tool runs anyway     │
│     │   │   └── Affects: v1.0.62+ through v2.1.39 (current)                     │
│     │   └── continue: false            → AI STOPS ENTIRELY                      │
│     │                                                                           │
│     ├── exit 2 (WORKAROUND for bug #4669)                                       │
│     │   ├── stderr fed back to AI as feedback                                   │
│     │   ├── TOOL BLOCKED                                                        │
│     │   └── AI continues (can suggest alternative)                              │
│     │                                                                           │
│     └── exit 1 or other                                                         │
│         ├── JSON parsed if valid                                                │
│         └── Warning logged, tool may execute (fail-open)                        │
│                                                                                 │
│  CRITICAL: ANY stderr output at exit 0 = hook error = fail-open                 │
│  WORKAROUND: Use exit 2 + stderr for actual blocking (bug #4669, v1.0.62+)      │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Gemini CLI BeforeTool Flow

**Source:** [Gemini CLI Hooks Reference](https://geminicli.com/docs/hooks/reference/), [Writing Hooks Guide](https://geminicli.com/docs/hooks/writing-hooks/)

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                      GEMINI CLI BeforeTool EXECUTION FLOW                       │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  1. AI REQUESTS TOOL USE                                                        │
│     └── Example: run_shell_command with "rm -rf /important"                     │
│                                                                                 │
│  2. HOOK TRIGGERS (if tool matches matcher regex)                               │
│     └── hooks.json: "matcher": "run_shell_command|write_file|replace"           │
│                                                                                 │
│  3. HOOK SCRIPT EXECUTES                                                        │
│     ├── stdin: JSON with type, toolName, toolInput, cwd, transcriptPath         │
│     ├── env: GEMINI_SESSION_ID, GEMINI_PROJECT_DIR                              │
│     └── timeout: Milliseconds from hooks.json (default: 5000)                   │
│                                                                                 │
│  4. HOOK OUTPUTS RESPONSE                                                       │
│     ├── stdout: JSON response (REQUIRED)                                        │
│     └── stderr: Safe for debug/logging (shown to user)                          │
│                                                                                 │
│  5. JSON DECISION FIELD DETERMINES OUTCOME                                      │
│     │                                                                           │
│     ├── decision: "allow"                                                       │
│     │   └── TOOL EXECUTES                                                       │
│     │                                                                           │
│     ├── decision: "deny"                                                        │
│     │   ├── TOOL BLOCKED                                                        │
│     │   ├── reason shown to AI                                                  │
│     │   └── AI continues (can suggest alternative)                              │
│     │                                                                           │
│     ├── decision: "block"                                                       │
│     │   └── AI STOPS ENTIRELY (use sparingly)                                   │
│     │                                                                           │
│     └── decision: "ask"                                                         │
│         └── User prompted for decision                                          │
│                                                                                 │
│  6. EXIT CODE BEHAVIOR                                                          │
│     ├── exit 0: Normal, JSON decision respected                                 │
│     ├── exit 2: Blocking error, stderr shown, equivalent to "deny"              │
│     └── exit 1/other: Warning, JSON decision still respected                    │
│                                                                                 │
│  KEY DIFFERENCE: decision field in JSON is RESPECTED (no exit 2 workaround)     │
│  STDERR SAFE: Can log debug info without affecting behavior                     │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### UserPromptSubmit / BeforeAgent Flow

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                    USER PROMPT INTERCEPT FLOW                                   │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  CLAUDE CODE: UserPromptSubmit                                                  │
│  ──────────────────────────────────────                                         │
│  Input:                                                                         │
│    {                                                                            │
│      "hook_event_name": "UserPromptSubmit",                                     │
│      "session_transcript": [...],                                               │
│      "user_prompt": "User's message text"                                       │
│    }                                                                            │
│                                                                                 │
│  Output Options:                                                                │
│    • continue: true + suppressOutput: false → Prompt processed normally         │
│    • continue: true + suppressOutput: true → Prompt hidden from transcript      │
│    • decision: "block" + continue: false → AI never sees prompt                 │
│                                                                                 │
│  GEMINI CLI: BeforeAgent                                                        │
│  ─────────────────────────────                                                  │
│  Input:                                                                         │
│    {                                                                            │
│      "type": "BeforeAgent",                                                     │
│      "sessionId": "...",                                                        │
│      "prompt": "User's message text",                                           │
│      "transcriptPath": "/path/to/transcript.jsonl"                              │
│    }                                                                            │
│                                                                                 │
│  Output Options:                                                                │
│    • decision: "allow" → Prompt processed normally                              │
│    • decision: "deny" + reason → Prompt shown to AI with rejection reason       │
│    • additionalContext → Inject extra context into AI's view                    │
│                                                                                 │
│  USE CASE: Intercept commands like /autorun, /estop, custom slash commands      │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Outcome Matrices

### PreToolUse/BeforeTool Outcome Matrix

**Claude Code PreToolUse Outcomes:**

| Exit Code | stdout JSON Key | JSON Value | `continue` | stderr | Result | AI Continues? |
|-----------|-----------------|------------|------------|--------|--------|---------------|
| 0 | `hookSpecificOutput.permissionDecision` | `"allow"` | `true` | none | ✅ Tool executes | Yes |
| 0 | `hookSpecificOutput.permissionDecision` | `"allow"` | `true` | **any** | ⚠️ Hook error, tool executes (fail-open) | Yes |
| 0 | `hookSpecificOutput.permissionDecision` | `"deny"` | `true` | none | ❌ Tool executes (BUG #4669: SHOULD block) | Yes |
| 0 | `hookSpecificOutput.permissionDecision` | `"deny"` | `true` | **any** | ⚠️ Hook error, tool executes (fail-open) | Yes |
| 0 | - | - | `false` | none | ❌ Tool skipped, AI stops | **No** |
| 0 | - | - | `false` | **any** | ⚠️ Hook error, behavior undefined | Varies |
| **2** | `hookSpecificOutput.permissionDecision` | `"deny"` | `true` | **reason** | ✅ Tool blocked, stderr→AI | **Yes** |
| **2** | - | - | - | **reason** | ✅ Tool blocked, stderr→AI | **Yes** |
| 1 | `hookSpecificOutput.permissionDecision` | `"allow"` | `true` | none | ⚠️ Warning logged, tool executes | Yes |
| 1 | `hookSpecificOutput.permissionDecision` | `"deny"` | `true` | none | ❌ Tool executes (BUG #4669: SHOULD block) | Yes |

**Bug #4669 Detail:**
- **JSON Path:** `hookSpecificOutput.permissionDecision`
- **Should:** `"deny"` at exit 0 → tool blocked, `permissionDecisionReason` fed to AI
- **Does:** Tool executes anyway as if `"allow"`
- **Versions:** v1.0.62+ through v2.1.39 (current as of 2026-02-12)
- **Workaround:** Exit code 2 + print reason to stderr → tool actually blocked

**Gemini CLI BeforeTool Outcomes:**

| Exit Code | stdout JSON Key | JSON Value | `continue` | stderr | Result | AI Continues? |
|-----------|-----------------|------------|------------|--------|--------|---------------|
| 0 | `decision` | `"allow"` | `true` | any | ✅ Tool executes | Yes |
| 0 | `decision` | `"deny"` | `true` | any | ✅ Tool blocked, `reason`→AI | **Yes** |
| 0 | `decision` | `"block"` | `false` | any | ❌ AI stops entirely | **No** |
| 0 | `decision` | `"ask"` | `true` | any | ❓ User prompted | Varies |
| 0 | `decision` | `"approve"` | `true` | any | ✅ Pre-approved, no confirmation | Yes |
| 2 | - | - | - | any | ✅ Tool blocked, stderr shown | **Yes** |
| 1 | `decision` | `"allow"` | `true` | any | ⚠️ Warning, tool executes | Yes |
| 1 | `decision` | `"deny"` | `true` | any | ✅ Tool blocked, `reason`→AI | **Yes** |

### PostToolUse/AfterTool Outcome Matrix

**Claude Code PostToolUse:**

| Exit Code | stdout JSON Key | JSON Value | `systemMessage` | Result |
|-----------|-----------------|------------|-----------------|--------|
| 0 | `hookSpecificOutput.permissionDecision` | `"allow"` | - | Tool result passed to AI |
| 0 | `hookSpecificOutput.permissionDecision` | `"block"` | "feedback" | Feedback injected, AI adjusts |
| 0 | - | - | "info text" | Message shown to user (stdin→stdout) |

**Gemini CLI AfterTool:**

| Exit Code | stdout JSON Key | JSON Value | `systemMessage` | Result |
|-----------|-----------------|------------|-----------------|--------|
| 0 | `decision` | `"allow"` | - | Tool result passed to AI |
| 0 | `decision` | `"block"` | "feedback" | Automated feedback provided |
| 0 | - | - | "info text" | Message logged/shown |

### Stop/SubagentStop Outcome Matrix

**Claude Code:**

| Event | stdout JSON Key | JSON Value | `stopReason` | Result |
|-------|-----------------|------------|--------------|--------|
| Stop | `continue` | `true` | - | AI continues running |
| Stop | `continue` | `false` | "reason text" | AI stops, reason shown |
| SubagentStop | `continue` | `true` | - | Subagent continues |
| SubagentStop | `continue` | `false` | - | Subagent terminates |

**Gemini CLI:**

| Event | stdout JSON Key | JSON Value | `stopReason` | Result |
|-------|-----------------|------------|--------------|--------|
| SessionEnd | `continue` | `true` | - | Session continues |
| SessionEnd | `continue` | `false` | "reason text" | Session ends |

---

## Per-Event Decision Control

**Source:** [Claude Code Hooks Documentation](https://docs.anthropic.com/en/docs/claude-code/hooks)

### Claude Code Event-Specific Decision Fields

| Event | stdout JSON Key | JSON Values | Exit Code | I/O Pathway | Blocking Mechanism |
|-------|-----------------|-------------|-----------|-------------|-------------------|
| **PreToolUse** | `hookSpecificOutput.permissionDecision` | "allow", "deny", "ask" | 0 | stdout (JSON) | ❌ BUG #4669: ignored |
| **PreToolUse** | `hookSpecificOutput.permissionDecision` | "deny" | **2** | stderr (reason) | ✅ Tool blocked (workaround) |
| **PostToolUse** | `hookSpecificOutput.permissionDecision` | "allow", "block" | 0 | stdout (JSON) | JSON respected |
| **UserPromptSubmit** | `decision` (top-level) | "allow", "block" | 0 | stdout (JSON) | JSON respected |
| **Stop** | `continue` | `true`, `false` | 0 | stdout (JSON) | JSON respected |
| **SubagentStop** | `continue` | `true`, `false` | 0 | stdout (JSON) | JSON respected |
| **Notification** | `decision` | "allow", "block" | 0 | stdout (JSON) | JSON respected |

**Bug Note:** For PreToolUse with `permissionDecision: "deny"` at exit 0, the denial is IGNORED (bug #4669, v1.0.62+). Use exit code 2 with reason on stderr for actual blocking.

### Gemini CLI Event-Specific Decision Fields

| Event | stdout JSON Key | JSON Values | Exit Code | I/O Pathway | Blocking Mechanism |
|-------|-----------------|-------------|-----------|-------------|-------------------|
| **BeforeTool** | `decision` (top-level) | "allow", "deny", "ask", "approve" | 0 | stdout (JSON) | JSON respected ✅ |
| **AfterTool** | `decision` (top-level) | "allow", "block" | 0 | stdout (JSON) | JSON respected |
| **BeforeAgent** | `decision` (top-level) | "allow", "deny" | 0 | stdout (JSON) | JSON respected |
| **AfterAgent** | `decision` (top-level) | "allow", "block" | 0 | stdout (JSON) | JSON respected |
| **BeforeModel** | `decision` (top-level) | "allow", "deny" | 0 | stdout (JSON) | JSON respected |
| **AfterModel** | `decision` (top-level) | "allow", "block" | 0 | stdout (JSON) | JSON respected |
| **BeforeToolSelection** | `toolConfig` | Filter tool list | 0 | stdout (JSON) | JSON respected |
| **SessionEnd** | `continue` | `true`, `false` | 0 | stdout (JSON) | JSON respected |

### hookSpecificOutput Fields (Claude Code Only)

**Source:** [Claude Code Hooks Documentation](https://docs.anthropic.com/en/docs/claude-code/hooks) - PreToolUse section

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "Dangerous command blocked",
    "modifiedToolInput": {
      "command": "trash /important"
    }
  }
}
```

| Field | Type | Purpose | Events | Design Behavior | Actual Behavior (2026-02-12) |
|-------|------|---------|--------|-----------------|------------------------------|
| `hookEventName` | string | Echo of event name | PreToolUse, PostToolUse | Echoed to response | Works as designed |
| `permissionDecision` | string | "allow", "deny", "ask", "block" | PreToolUse, PostToolUse | `"deny"` blocks tool | ❌ BUG #4669: `"deny"` ignored at PreToolUse exit 0, tool executes |
| `permissionDecisionReason` | string | Human-readable reason | PreToolUse | Fed to AI on deny | Works with exit 2 workaround |
| `modifiedToolInput` | object | Replacement tool parameters | PreToolUse | Rewrites tool params | Works at exit 0 |

### hookSpecificOutput Fields (Gemini CLI)

**Source:** [Gemini CLI Hooks Reference](https://geminicli.com/docs/hooks/reference/)

```json
{
  "hookSpecificOutput": {
    "additionalContext": "Extra information to inject into AI context",
    "llm_request": { /* LLMRequest object */ },
    "llm_response": { /* LLMResponse object */ },
    "toolConfig": {
      "allowedTools": ["read_file", "glob"],
      "blockedTools": ["run_shell_command"]
    }
  }
}
```

| Field | Type | Purpose | Events |
|-------|------|---------|--------|
| `additionalContext` | string | Inject context into AI view | BeforeAgent, BeforeModel |
| `llm_request` | LLMRequest | Modify LLM request | BeforeModel |
| `llm_response` | LLMResponse | Modify LLM response | AfterModel |
| `toolConfig` | object | Filter available tools | BeforeToolSelection |

---

## LLM Request/Response (Gemini Only)

**Source:** [Gemini CLI Hooks Reference](https://geminicli.com/docs/hooks/reference/) - Model Hooks section

Gemini CLI provides hooks that intercept at the LLM level, allowing modification of requests to and responses from the model.

### LLMRequest Object (BeforeModel)

```json
{
  "hookSpecificOutput": {
    "llm_request": {
      "model": "gemini-2.0-flash",
      "temperature": 0.7,
      "max_output_tokens": 8192,
      "system_instruction": "You are a helpful assistant.",
      "contents": [
        {
          "role": "user",
          "parts": [{"text": "Hello"}]
        }
      ],
      "tools": [...],
      "tool_config": {...}
    }
  }
}
```

### LLMResponse Object (AfterModel)

```json
{
  "hookSpecificOutput": {
    "llm_response": {
      "candidates": [
        {
          "content": {
            "role": "model",
            "parts": [{"text": "Response text"}]
          },
          "finish_reason": "STOP"
        }
      ],
      "usage_metadata": {
        "prompt_token_count": 100,
        "candidates_token_count": 50,
        "total_token_count": 150
      }
    }
  }
}
```

### Use Cases for Model-Level Hooks

| Hook | Use Case | Example |
|------|----------|---------|
| **BeforeModel** | Modify system prompt per-project | Add coding standards for work projects |
| **BeforeModel** | Enforce model parameters | Cap temperature for production |
| **AfterModel** | Log/token usage tracking | Record to analytics |
| **AfterModel** | Content filtering | Redact sensitive info |
| **AfterModel** | Response transformation | Format code blocks |

### BeforeToolSelection (Gemini Only)

Filter which tools are available to the AI before it selects one:

```json
{
  "hookSpecificOutput": {
    "toolConfig": {
      "allowedTools": ["read_file", "glob", "grep"],
      "blockedTools": ["run_shell_command", "write_file"]
    }
  }
}
```

---

## References

### Source Attribution by Section

| Section | Primary Sources |
|---------|-----------------|
| Master Comparison Table | [Claude Code Hooks](https://docs.anthropic.com/en/docs/claude-code/hooks), [Gemini CLI Hooks Reference](https://geminicli.com/docs/hooks/reference/) |
| JSON Input Parameters | [Claude Code Hooks](https://docs.anthropic.com/en/docs/claude-code/hooks), [Gemini CLI Hooks Reference](https://geminicli.com/docs/hooks/reference/) |
| JSON Output Parameters | [Claude Code Hooks](https://docs.anthropic.com/en/docs/claude-code/hooks), [Gemini CLI Writing Hooks](https://geminicli.com/docs/hooks/writing-hooks/) |
| Environment Variables | [Claude Code Plugins](https://docs.anthropic.com/en/docs/claude-code/plugins), [Gemini CLI Configuration](https://geminicli.com/docs/get-started/configuration/) |
| Hook Configuration Variables | [Claude Code Plugins](https://docs.anthropic.com/en/docs/claude-code/plugins), [Gemini CLI Extensions](https://geminicli.com/docs/extensions/reference/) |
| Tool Blocking | [Claude Code Issue #4669](https://github.com/anthropics/claude-code/issues/4669), [Gemini CLI Writing Hooks](https://geminicli.com/docs/hooks/writing-hooks/) |
| Execution Flow with Outcomes | [Claude Code Hooks](https://docs.anthropic.com/en/docs/claude-code/hooks), [Gemini CLI Writing Hooks](https://geminicli.com/docs/hooks/writing-hooks/) |
| Outcome Matrices | [Claude Code Hooks](https://docs.anthropic.com/en/docs/claude-code/hooks), [Gemini CLI Hooks Reference](https://geminicli.com/docs/hooks/reference/) |
| Per-Event Decision Control | [Claude Code Hooks](https://docs.anthropic.com/en/docs/claude-code/hooks), [Gemini CLI Hooks Reference](https://geminicli.com/docs/hooks/reference/) |
| LLM Request/Response | [Gemini CLI Hooks Reference](https://geminicli.com/docs/hooks/reference/) |
| Directory Layout | [Claude Code Plugins](https://docs.anthropic.com/en/docs/claude-code/plugins), [Gemini CLI Extensions](https://geminicli.com/docs/extensions/) |
| Event Names Comparison | [Claude Code Hooks](https://docs.anthropic.com/en/docs/claude-code/hooks), [Gemini CLI Hooks Reference](https://geminicli.com/docs/hooks/reference/) |
| Tool Names Comparison | [Gemini CLI Tools Reference](https://geminicli.com/docs/tools/), Claude Code tool schema |
| Stdout/Stderr Behavior | [Claude Code Hooks](https://docs.anthropic.com/en/docs/claude-code/hooks), [Gemini CLI Writing Hooks](https://geminicli.com/docs/hooks/writing-hooks/) |
| Hook Configuration Format | [Claude Code Plugins](https://docs.anthropic.com/en/docs/claude-code/plugins), [Gemini CLI Extensions Reference](https://geminicli.com/docs/extensions/reference/) |

### Official Documentation

1. **Claude Code Hooks**
   - URL: https://docs.anthropic.com/en/docs/claude-code/hooks
   - Content: Event types, JSON schema, exit codes, blocking strategies, per-event decision control
   - Key Info: PreToolUse requires exit code 2 for blocking, PostToolUse supports "block" decision

2. **Claude Code Plugins**
   - URL: https://docs.anthropic.com/en/docs/claude-code/plugins
   - Content: Plugin structure, manifest schema, installation, component discovery
   - Key Info: `.claude-plugin/plugin.json` format, hook discovery

3. **Claude Code Marketplace Documentation**
   - URL: https://code.claude.com/docs/en/plugin-marketplaces
   - Content: Marketplace configuration, plugin sources, strictKnownMarketplaces
   - Key Info: How plugins are grouped into marketplaces

4. **Gemini CLI Hooks Reference**
   - URL: https://geminicli.com/docs/hooks/reference/
   - Content: Complete hook structure, all events, JSON schemas, hookSpecificOutput fields
   - Key Info: LLMRequest/LLMResponse objects, BeforeToolSelection, additionalContext

5. **Gemini CLI Writing Hooks**
   - URL: https://geminicli.com/docs/hooks/writing-hooks/
   - Content: stdin/stdout handling, exit codes, blocking strategies, practical examples
   - Key Info: Exit code 2 as "emergency brake", structured response vs blocking

6. **Gemini CLI Extensions**
   - URL: https://geminicli.com/docs/extensions/
   - Content: Extension structure, installation, commands, hooks integration
   - Key Info: `gemini-extension.json` format, `gemini extensions link` for dev

7. **Gemini CLI Extensions Reference**
   - URL: https://geminicli.com/docs/extensions/reference/
   - Content: Variable substitution (${extensionPath}, ${workspacePath}), command format
   - Key Info: Hooks must be in hooks/hooks.json, NOT in gemini-extension.json

8. **Gemini CLI Tools Reference**
   - URL: https://geminicli.com/docs/tools/
   - Content: Tool names (write_file, run_shell_command, replace, write_todos, etc.)
   - Key Info: Complete tool mapping for hooks matchers

9. **Gemini CLI Configuration**
   - URL: https://geminicli.com/docs/get-started/configuration/
   - Content: Settings, environment variables, config files
   - Key Info: `enableHooks: true` and `enableMessageBusIntegration: true` required

### GitHub Issues

10. **Claude Code Bug #4669 - permissionDecision ignored**
    - URL: https://github.com/anthropics/claude-code/issues/4669
    - Content: Workaround using exit code 2 for actual blocking
    - Status: Open bug, requires exit code 2 + stderr workaround

11. **Claude Code Issue #18312 - Hook blocking behavior**
    - URL: https://github.com/anthropics/claude-code/issues/18312
    - Content: Related blocking behavior discussion
    - Key Info: Confirms fail-open behavior on hook errors

12. **Gemini CLI Issue #13155 - Hooks not firing**
    - URL: https://github.com/google-gemini/gemini-cli/issues/13155
    - Content: Requires both `enableHooks` and `enableMessageBusIntegration`
    - Key Info: Both settings must be true for hooks to work

13. **Gemini CLI Issue #14932 - Hooks not working**
    - URL: https://github.com/google-gemini/gemini-cli/issues/14932
    - Content: Confirms hook issues in v0.27.x
    - Key Info: Version-specific bugs to watch for

14. **Gemini CLI PR #14460 - Extension hooks support**
    - URL: https://github.com/google-gemini/gemini-cli/pull/14460
    - Content: Implementation of hooks/hooks.json in extensions
    - Key Info: Hooks in extensions feature implementation details

### Blog Posts & Announcements

15. **Tailor Gemini CLI with Hooks**
    - URL: https://developers.googleblog.com/tailor-gemini-cli-to-your-workflow-with-hooks/
    - Date: January 28, 2026
    - Content: Official announcement of hooks feature
    - Key Info: Feature overview, use cases, getting started

### Related Project Files

16. **clautorun hooks.json (Claude Code)**
    - Path: `plugins/clautorun/hooks/hooks.json`
    - Content: Working Claude Code hook configuration
    - Key Info: Event names (PreToolUse, PostToolUse), matcher patterns

17. **clautorun gemini-hooks.json (Gemini CLI)**
    - Path: `plugins/clautorun/hooks/gemini-hooks.json`
    - Content: Working Gemini CLI hook configuration
    - Key Info: Event names (BeforeTool, AfterTool), timeout in milliseconds

18. **bugs_and_issues.md**
    - Path: `notes/bugs_and_issues.md`
    - Content: Documented bugs and fixes in clautorun hooks system
    - Key Info: Historical issues with hooks.json format, try_cli bugs

---

**Document Version:** 2.2
**Status:** Complete
**Changes in v2.2:**
- **Bug #4669 Documentation:** Added comprehensive bug documentation with:
  - Expected behavior (what SHOULD happen: tool blocked)
  - Actual behavior (what DOES happen: tool executes)
  - Affected versions (v1.0.62+ through v2.1.39 current)
  - I/O pathway specifications (stdout JSON vs stderr)
- **Enhanced Tables:** All outcome matrices now include:
  - stdout JSON Key column (exact field path)
  - JSON Value column (quoted values)
  - I/O pathway column (stdout/stderr/exit code)
  - Design Behavior vs Actual Behavior columns
- **Per-Event Decision Control:** Added exit code and I/O pathway columns
- **hookSpecificOutput Fields:** Added design behavior and actual behavior columns

**Changes in v2.1:**
- Added Execution Flow with Outcomes section (step-by-step diagrams)
- Added Outcome Matrices section (exit code × decision × continue tables)
- Added Per-Event Decision Control section (event-specific fields)
- Added LLM Request/Response section (Gemini-only model hooks)
- Added Source Attribution table linking sections to primary sources
- Enhanced References with detailed key info for each source
- Added related project files to references

**Changes in v2.0:**
- Complete restructure with comprehensive comparison tables
- Added detailed JSON input/output parameter comparisons
- Added environment variables section with detection patterns
- Added hook configuration variables comparison
- Expanded tool blocking section with code examples
- Added directory layout comparison (marketplace vs plugin vs extension)
- Added event and tool name mapping tables
- Added stdout/stderr behavior diagrams
- Added complete implementation patterns

**Next Review:** When hooks API changes in either CLI
