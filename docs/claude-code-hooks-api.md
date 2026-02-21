# Claude Code Hooks API Reference

**Official Documentation**: https://code.claude.com/docs/en/hooks

**Last Updated**: 2026-02-15

## Event Names

| Event Name | When It Fires | Equivalent Gemini CLI Event |
|------------|---------------|----------------------------|
| PreToolUse | Before tool execution | BeforeTool |
| PostToolUse | After tool execution | AfterTool |
| UserPromptSubmit | User message submission | BeforeAgent |
| Stop | Session/agent termination | AfterAgent |
| SubagentStart | Subagent spawn | (not in Gemini) |
| SubagentStop | Subagent completion | (not in Gemini) |
| SessionStart | Session initialization | SessionStart |
| SessionEnd | Session termination | SessionEnd |
| PermissionRequest | Permission prompt to user | (not in Gemini) |
| Notification | System notifications | Notification |
| TaskCompleted | Task completion event | (not in Gemini) |
| TeammateIdle | Teammate idle state | (not in Gemini) |
| PreCompact | Before context compression | PreCompress |
| PostToolUseFailure | After tool failure | (not in Gemini) |

## Tool Names (PascalCase convention)

### File Operations
- `Write` - Write new file or overwrite existing
- `Edit` - Edit file with find/replace
- `Read` - Read file contents
- `Glob` - File pattern matching
- `Grep` - Content search

### Execution
- `Bash` - Execute bash command

### Subagents
- `Task` - Launch subagent

### Web Access
- `WebFetch` - Fetch web content
- `WebSearch` - Web search

### Plan Mode
- `ExitPlanMode` - Exit plan mode (accept/reject plan)
- `EnterPlanMode` - Enter plan mode

### Other Tools
- `AskUserQuestion` - Ask user for input
- `TaskCreate`, `TaskUpdate`, `TaskList`, `TaskGet` - Task management

### MCP Tools
- Pattern not explicitly documented
- Likely similar to Gemini: `mcp__<server>__<tool>`

## Input Schema (Hook Receives)

### Common Fields (All Events)

```json
{
  "session_id": "string",          // Session identifier
  "transcript_path": "string",     // Path to session transcript
  "permission_mode": "auto|manual",// Permission mode
  "hook_event_name": "PreToolUse"  // Event type
}
```

### PreToolUse/PostToolUse Specific

```json
{
  "tool_name": "Write",            // Tool being executed (PascalCase)
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
  "permission_mode": "auto|manual"
}
```

## Output Schema (Hook Returns)

### Decision Control (PreToolUse)

```json
{
  "decision": "approve|block",     // Top-level decision (legacy)
  "continue": true,                // Keep session running
  "stopReason": "",                // Reason to stop (if continue:false)
  "suppressOutput": false,         // Hide hook output
  "systemMessage": "string",       // Message to user
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",           // ✅ MUST match Claude Code event name
    "permissionDecision": "allow|deny|ask",  // Actual decision
    "permissionDecisionReason": "string",    // Reason shown to user
    "updatedInput": {}                       // Modified tool input (optional)
  }
}
```

**CRITICAL**: The `hookEventName` field MUST use Claude Code event names:
- ✅ Correct: `"hookEventName": "PreToolUse"`
- ❌ Wrong: `"hookEventName": "BeforeTool"` (Gemini event name)

### Context Injection (UserPromptSubmit, PostToolUse)

```json
{
  "decision": "approve",           // Must be "approve"
  "continue": true,
  "stopReason": "",
  "suppressOutput": false,
  "systemMessage": "",
  "hookSpecificOutput": {
    "additionalContext": "string"  // Injected context
  }
}
```

## Decision Values

### Top-Level `decision`

| Value | Behavior |
|-------|----------|
| `approve` | Tool executes (legacy field) |
| `block` | Tool blocked (legacy field) |

### hookSpecificOutput `permissionDecision`

| Value | Behavior |
|-------|----------|
| `allow` | Tool executes immediately |
| `deny` | Tool blocked, session continues |
| `ask` | Show confirmation prompt to user |

**Note**: Claude Code uses `hookSpecificOutput.permissionDecision` as the actual decision field. The top-level `decision` is legacy.

## Field Naming Convention

**All fields use snake_case:**
- `session_id` (not `sessionId`)
- `transcript_path` (not `transcriptPath`)
- `permission_mode` (not `permissionMode`)
- `hook_event_name` (not `hookEventName`)

**Exception**: `hookSpecificOutput` and its nested fields use camelCase:
- `hookSpecificOutput` (not `hook_specific_output`)
- `permissionDecision` (not `permission_decision`)
- `permissionDecisionReason` (not `permission_decision_reason`)
- `additionalContext` (not `additional_context`)

## Examples

### PreToolUse Hook (File Creation Block)

```json
{
  "decision": "block",
  "continue": true,
  "stopReason": "",
  "suppressOutput": false,
  "systemMessage": "",
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "File creation blocked by policy"
  }
}
```

### PreToolUse Hook (Ask User)

```json
{
  "decision": "block",
  "continue": true,
  "stopReason": "",
  "suppressOutput": false,
  "systemMessage": "",
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "ask",
    "permissionDecisionReason": "Dangerous command detected. Use 'trash' instead of 'rm'?"
  }
}
```

### PostToolUse Hook (Plan Export)

```json
{
  "decision": "approve",
  "continue": true,
  "stopReason": "",
  "suppressOutput": false,
  "systemMessage": "Plan exported successfully"
}
```

### UserPromptSubmit Hook (Context Injection)

```json
{
  "decision": "approve",
  "continue": true,
  "stopReason": "",
  "suppressOutput": false,
  "systemMessage": "",
  "hookSpecificOutput": {
    "additionalContext": "Current autorun stage: Stage 2 (Critical Evaluation)"
  }
}
```

## Daemon Integration

When implementing a hook daemon that serves both Claude Code and Gemini CLI:

**Incoming Normalization** (Request):
- Claude sends: `"hook_event_name": "PreToolUse"` → use as-is internally
- Gemini sends: `"type": "BeforeTool"` → normalize to `"PreToolUse"`

**Outgoing Denormalization** (Response):
- For Claude: Internal `"PreToolUse"` → keep as `"PreToolUse"`
- For Gemini: Internal `"PreToolUse"` → convert to `"BeforeTool"`

See `plugins/clautorun/src/clautorun/core.py` for reference implementation:
- `GEMINI_EVENT_MAP` - Request normalization (line 88-95)
- `get_cli_event_name()` - Response denormalization (line 119-131)

## Known Issues

### Bug #4669: permissionDecision Ignored on Exit 0

**Problem**: Claude Code ignores `permissionDecision: "deny"` when hook exits with code 0.

**Workaround**: Use `permissionDecision: "ask"` instead of `"deny"` to show user prompt with reason.

**Status**: Unfixed as of Claude Code v1.0.62+

**References**:
- GitHub Issue: #4669
- Documentation: https://code.claude.com/docs/en/hooks#known-issues

### Bug #10964: Exit Code 2 Stderr Goes to Claude

**Problem**: When hook exits with code 2, stderr message goes to Claude instead of user.

**Workaround**: Use JSON response with `permissionDecision: "ask"` instead of exit code 2.

**Status**: Unfixed

## Common Pitfalls

1. ❌ Using Gemini CLI event names in responses (`"BeforeTool"` instead of `"PreToolUse"`)
2. ❌ Using top-level `decision` field instead of `hookSpecificOutput.permissionDecision`
3. ❌ Hardcoding event names without CLI detection
4. ❌ Using snake_case for hookSpecificOutput fields (`permission_decision` instead of `permissionDecision`)
5. ❌ Missing tool names in hook matchers (e.g., forgetting `ExitPlanMode`)
6. ❌ Relying on `decision: "block"` without `hookSpecificOutput.permissionDecision: "deny"`

## See Also

- Gemini CLI Hooks API: `notes/gemini-cli-hooks-api.md`
- Daemon implementation: `plugins/clautorun/src/clautorun/core.py`
- Hook configuration: `plugins/clautorun/hooks/claude-hooks.json`
- Official Claude docs: https://code.claude.com/docs/en/hooks
