# Gemini CLI Hooks API Reference

**Official Documentation**: https://geminicli.com/docs/hooks/reference/

**Last Updated**: 2026-02-15

## Event Names

| Event Name | When It Fires | Equivalent Claude Code Event |
|------------|---------------|------------------------------|
| BeforeTool | Before tool execution | PreToolUse |
| AfterTool | After tool execution | PostToolUse |
| BeforeAgent | Before agent spawn/user prompt | UserPromptSubmit |
| AfterAgent | After agent completion | Stop |
| BeforeModel | Before model API call | (not in Claude Code) |
| AfterModel | After model response | (not in Claude Code) |
| BeforeToolSelection | Before tool selection phase | (not in Claude Code) |
| SessionStart | Session initialization | SessionStart |
| SessionEnd | Session termination | SessionEnd |
| Notification | System notifications | Notification |
| PreCompress | Before context compression | PreCompact |

## Tool Names (snake_case convention)

### File Operations
- `read_file` - Read file contents
- `write_file` - Write new file or overwrite existing
- `edit_file` - Edit file with find/replace
- `replace` - Replace text in file

### Search Operations
- `glob` - File pattern matching
- `grep` - Content search

### Execution
- `execute_bash` - Execute bash command
- `run_shell_command` - Run shell command

### Web Access
- `web_search` - Web search
- `web_fetch` - Fetch web content

### Plan Mode
- `exit_plan_mode` - Exit plan mode (accept/reject plan)
- `enter_plan_mode` - Enter plan mode

### MCP Tools
- Pattern: `mcp__<server>__<tool>`
- Example: `mcp__github__create_issue`

## Input Schema (Hook Receives)

### Common Fields (All Events)

```json
{
  "session_id": "string",          // Session identifier
  "transcript_path": "string",     // Path to session transcript
  "cwd": "string",                 // Current working directory
  "hook_event_name": "BeforeTool", // Event type (NOT "type")
  "type": "BeforeTool"             // Legacy field, use hook_event_name
}
```

### BeforeTool/AfterTool Specific

```json
{
  "tool_name": "write_file",       // Tool being executed (snake_case)
  "tool_input": {                  // Tool-specific input
    "file_path": "/path/to/file",
    "content": "file content"
  }
}
```

### SessionStart Specific

```json
{
  "session_id": "string",
  "transcript_path": "string",
  "cwd": "string"
}
```

## Output Schema (Hook Returns)

### Decision Control

```json
{
  "decision": "allow|deny|ask",    // Top-level decision
  "reason": "string",              // Reason shown to user
  "updated_input": {},             // Modified tool input (optional)
  "continue": true,                // Keep session running
  "stopReason": "",                // Reason to stop (if continue:false)
  "suppressOutput": false,         // Hide hook output
  "systemMessage": "string"        // Message to user
}
```

### hookSpecificOutput

For BeforeTool events, include hookSpecificOutput:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "BeforeTool",           // ✅ MUST match Gemini event name
    "permissionDecision": "allow|deny|ask",  // Same as top-level decision
    "permissionDecisionReason": "string"     // Same as reason
  }
}
```

**CRITICAL**: The `hookEventName` field MUST use Gemini event names:
- ✅ Correct: `"hookEventName": "BeforeTool"`
- ❌ Wrong: `"hookEventName": "PreToolUse"` (causes "Invalid hook event name" warning)

## Decision Values

| Value | Behavior |
|-------|----------|
| `allow` | Tool executes immediately |
| `deny` | Tool blocked, session continues |
| `ask` | Show confirmation prompt to user |

## Field Naming Convention

**All fields use snake_case:**
- `session_id` (not `sessionId`)
- `transcript_path` (not `transcriptPath`)
- `tool_name` (not `toolName`)
- `tool_input` (not `toolInput`)
- `hook_event_name` (not `hookEventName`)

**Exception**: `hookSpecificOutput` is camelCase (inherited from Claude Code API)

## Examples

### BeforeTool Hook (File Creation Block)

```json
{
  "decision": "deny",
  "reason": "File creation blocked by policy",
  "continue": true,
  "stopReason": "",
  "suppressOutput": false,
  "systemMessage": "File creation blocked by policy",
  "hookSpecificOutput": {
    "hookEventName": "BeforeTool",
    "permissionDecision": "deny",
    "permissionDecisionReason": "File creation blocked by policy"
  }
}
```

### AfterTool Hook (Plan Export)

```json
{
  "decision": "allow",
  "continue": true,
  "stopReason": "",
  "suppressOutput": false,
  "systemMessage": "Plan exported successfully"
}
```

## Daemon Integration

When implementing a hook daemon that serves both Gemini CLI and Claude Code:

**Incoming Normalization** (Request):
- Gemini sends: `"type": "BeforeTool"` → normalize to internal `"PreToolUse"`
- Claude sends: `"hook_event_name": "PreToolUse"` → use as-is

**Outgoing Denormalization** (Response):
- For Gemini: Internal `"PreToolUse"` → convert to `"BeforeTool"`
- For Claude: Internal `"PreToolUse"` → keep as `"PreToolUse"`

See `plugins/clautorun/src/clautorun/core.py` for reference implementation:
- `GEMINI_EVENT_MAP` - Request normalization (line 88-95)
- `get_cli_event_name()` - Response denormalization (line 119-131)

## Common Pitfalls

1. ❌ Using Claude Code event names in responses (`"PreToolUse"` instead of `"BeforeTool"`)
2. ❌ Using camelCase field names (`sessionId` instead of `session_id`)
3. ❌ Hardcoding event names without CLI detection
4. ❌ Forgetting to include both `decision` and `hookSpecificOutput.permissionDecision`
5. ❌ Missing tool names in hook matchers (e.g., forgetting `exit_plan_mode`)

## See Also

- Claude Code Hooks API: `notes/claude-code-hooks-api.md`
- Daemon implementation: `plugins/clautorun/src/clautorun/core.py`
- Hook configuration: `plugins/clautorun/hooks/hooks.json`
- Official Gemini docs: https://geminicli.com/docs/hooks/reference/
