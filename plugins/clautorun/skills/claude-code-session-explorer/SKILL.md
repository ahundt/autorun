---
name: claude-code-session-explorer
description: This skill should be used when the user asks to "find Claude sessions", "explore session history", "search Claude sessions", "analyze session", "list sessions", "extract session content", "find tool usage in sessions", "session timeline", mentions "Claude Code session.jsonl files", or needs to explore, search, or analyze Claude Code session histories stored in ~/.claude/projects/.
version: "1.0.0"

# VISIBILITY & TRIGGERING
user-invocable: true              # User can type /cr:claude-code-session-explorer
disable-model-invocation: false   # AI can call this autonomously when needed

# EXECUTION CONTEXT - Tools this skill can use
allowed-tools:
  - Bash                          # For running session_explorer.py
  - Read                          # For reading session files
  - Grep                          # For searching session content
  - Glob                          # For finding session files
---

# Session Explorer - Find and Analyze Claude Code Sessions

General-purpose tool for exploring, searching, and analyzing Claude Code session histories stored in `~/.claude/projects/`. Supports custom instructions and operations.

**Usage:**
- `/cr:claude-code-session-explorer` - Show full guide
- `/cr:claude-code-session-explorer list <PROJECT>` - List all sessions for a project
- `/cr:claude-code-session-explorer search <PATTERN>` - Search sessions for pattern
- `/cr:claude-code-session-explorer extract <PROJECT> <SESSION-ID> <TYPE>` - Extract specific content
- `/cr:claude-code-session-explorer analyze <PROJECT> <SESSION-ID>` - Analyze session structure
- `/cr:claude-code-session-explorer cross-ref <PROJECT> <SESSION-ID> <FILE>` - Cross-reference with file
- `/cr:claude-code-session-explorer find-tool <TOOL> <PATTERN>` - Find specific tool usage
- `/cr:claude-code-session-explorer help <OPERATION>` - Get help on specific operation

## Arguments

This command accepts arguments for automated operations:

```
$ARGUMENTS
```

Supported operations:

- **list <PROJECT>**: Show all sessions for a project with metadata
- **search <PATTERN>**: Find pattern across all sessions and projects
- **extract <PROJECT> <SESSION-ID> <TYPE>**: Extract specific content type
  - Types: `pbcopy`, `bash-output`, `user-prompts`, `assistant-responses`, `tool-usage`, `all`
- **analyze <PROJECT> <SESSION-ID>**: Show session structure and statistics
- **cross-ref <PROJECT> <SESSION-ID> <FILE>**: Cross-reference session changes with file
- **find-tool <TOOL> [PATTERN]**: Find usage of specific tools (Read, Edit, Bash, Grep, etc.)
- **timeline <PROJECT> <SESSION-ID>**: Show chronological timeline of events

### Examples

```
/cr:claude-code-session-explorer list repomix
/cr:claude-code-session-explorer search "your-pattern"
/cr:claude-code-session-explorer extract repomix 6ab3b336 pbcopy
/cr:claude-code-session-explorer extract repomix 6ab3b336 user-prompts
/cr:claude-code-session-explorer analyze repomix 6ab3b336
/cr:claude-code-session-explorer cross-ref repomix 6ab3b336 PR-DESCRIPTION.md
/cr:claude-code-session-explorer find-tool Bash "grep"
/cr:claude-code-session-explorer find-tool Edit
/cr:claude-code-session-explorer timeline repomix 6ab3b336
```

## General-Purpose Usage

### Finding Session Content

Extract different types of content from sessions:

```python
# All pbcopy commands (what was copied)
/cr:claude-code-session-explorer extract repomix SESSION_ID pbcopy

# All bash commands executed
/cr:claude-code-session-explorer extract repomix SESSION_ID bash-output

# All user prompts (what you asked)
/cr:claude-code-session-explorer extract repomix SESSION_ID user-prompts

# Assistant responses (what Claude said)
/cr:claude-code-session-explorer extract repomix SESSION_ID assistant-responses

# Specific tool usage
/cr:claude-code-session-explorer find-tool Read "gitLogHandle"
/cr:claude-code-session-explorer find-tool Edit "PR-DESCRIPTION"
/cr:claude-code-session-explorer find-tool Bash "git commit"
```

### Custom Search Patterns

Search for anything across all sessions:

```bash
# Find all discussions about a feature
/cr:claude-code-session-explorer search "feature-name"

# Find all parameter discussions
/cr:claude-code-session-explorer search "--param-name"

# Find all mentions of a function
/cr:claude-code-session-explorer search "functionName"

# Find specific file edits
/cr:claude-code-session-explorer search "path/to/file.ts"
```

### Timeline Analysis

Understand the chronological flow of a session:

```bash
/cr:claude-code-session-explorer timeline myproject SESSION_ID
# Shows: user prompt → assistant response → tool execution → tool result → ...
```

### Session Statistics

Get overview of what happened in a session:

```bash
/cr:claude-code-session-explorer analyze repomix 6ab3b336
# Shows: total lines, tool count, user prompts, file changes, etc.
```

## Multi-Layer Verification Strategy

When verifying that changes were applied correctly:

### Layer 1: Content Classification
Identify all content types being modified:
- Tool outputs (Bash, Edit, Read results)
- User prompts and discussions
- Assistant responses and reasoning
- Code snippets and examples
- Structured data (tables, lists, configurations)

### Layer 2: Temporal Ordering
Track chronological sequence of events:
1. Extract all events with timestamps
2. Group by content type
3. Identify iterations and refinements
4. Note which version was finalized

### Layer 3: Change Detection
Look for specific patterns of change:
- **Content reordering** (items moved, sections rearranged)
- **Introductions** (headers, context lines added)
- **Format changes** (table structure, layout updates)
- **Terminology shifts** (new terms introduced, old ones removed)

### Layer 4: File Cross-Reference
Compare current files against session changes:
1. Extract all content from session
2. Compare against current file version
3. Identify which changes were applied
4. Flag any missing or incomplete updates

### Layer 5: Pattern Matching
Use distinctive markers to find specific changes:
- Search for unique phrases
- Look for section headers that changed
- Find example commands
- Identify table column changes

## Common Search Patterns

### Find all code discussions
```bash
/cr:claude-code-session-explorer search "function"
/cr:claude-code-session-explorer search "class"
/cr:claude-code-session-explorer search "interface"
```

### Find specific operations
```bash
/cr:claude-code-session-explorer search "git commit"
/cr:claude-code-session-explorer search "npm run"
/cr:claude-code-session-explorer search "pytest"
```

### Find all tool usage
```bash
/cr:claude-code-session-explorer find-tool Bash
/cr:claude-code-session-explorer find-tool Edit
/cr:claude-code-session-explorer find-tool Read
/cr:claude-code-session-explorer find-tool Grep
```

### Find discussion topics
```bash
/cr:claude-code-session-explorer search "error"
/cr:claude-code-session-explorer search "refactor"
/cr:claude-code-session-explorer search "optimization"
```

## Session File Structure

Sessions are stored as `.jsonl` files (JSON Lines format) at:
```
~/.claude/projects/-Users-athundt-source-<PROJECT>/
  ├── <SESSION-ID>.jsonl
  ├── agent-<ID>.jsonl
  └── ...
```

Each line is a complete JSON object:

```json
{
  "type": "user|assistant|system",
  "timestamp": "2025-11-25T20:30:15.744Z",
  "message": {
    "content": [
      {
        "type": "text|tool_use|tool_result",
        "text": "...",
        "name": "ToolName",
        "input": { /* tool input */ },
        "content": "..."
      }
    ]
  }
}
```

### Common Tool Types

- **Bash**: Shell command execution
- **Read**: File content reading
- **Edit**: File modification
- **Write**: New file creation
- **Grep**: Pattern searching in files
- **Glob**: File pattern matching
- **TodoWrite**: Task list management
- **Task**: Launch specialized agents

## Exploration Workflow

### 1. List and understand available sessions
```bash
/cr:claude-code-session-explorer list myproject
# Identify which session contains the work you want to verify
```

### 2. Extract relevant content
```bash
/cr:claude-code-session-explorer extract myproject SESSION_ID user-prompts
# See what was asked/discussed
/cr:claude-code-session-explorer extract myproject SESSION_ID pbcopy
# See what was copied to clipboard
```

### 3. Analyze changes
```bash
/cr:claude-code-session-explorer analyze myproject SESSION_ID
# Understand scale and scope of changes
```

### 4. Cross-reference with files
```bash
/cr:claude-code-session-explorer cross-ref myproject SESSION_ID path/to/file.md
# Verify all changes were applied
```

### 5. Deep dive into specifics
```bash
/cr:claude-code-session-explorer find-tool Edit "filename"
# See all edits to specific file
/cr:claude-code-session-explorer search "specific-phrase"
# Find when something was discussed
```

## Tips and Best Practices

1. **Always extract complete content** - Don't rely on truncated previews
2. **Use timestamps to verify order** - Chronological sequence matters
3. **Cross-reference multiple sources** - Compare tool outputs with file results
4. **Search broadly first** - Find all mentions before deep dive
5. **Classify by content type** - Group similar changes together
6. **Document your findings** - Create a change log as you verify
7. **Look for approval markers** - Find where changes were confirmed as final
8. **Check tool outputs** - Verify tools returned what was expected
9. **Track iterations** - Note how ideas evolved and improved
10. **Note dependencies** - Understand what depended on what

## Verification Checklist

When verifying changes were applied:

- [ ] List all sessions with relevant work
- [ ] Extract all content from each session (user prompts, responses, tool outputs)
- [ ] Classify each piece of content by type
- [ ] Identify the final/authoritative version of each change
- [ ] Compare against current file state
- [ ] Note any iterations or alternatives discussed
- [ ] Mark which changes were applied
- [ ] Flag any missing or incomplete updates
- [ ] Document the verification process
- [ ] Create summary of what changed and why

## Advanced Usage

### Finding Related Sessions
```bash
/cr:claude-code-session-explorer search "PR-DESCRIPTION"
# Finds all sessions that worked on this file
```

### Tracking Evolution of Ideas
```bash
/cr:claude-code-session-explorer timeline myproject SESSION_ID
# Shows how discussion evolved chronologically
```

### Comparing Multiple Sessions
```bash
/cr:claude-code-session-explorer list myproject
# List all, then compare specific ones
```

### Understanding Decision Points
```bash
/cr:claude-code-session-explorer search "should we"
/cr:claude-code-session-explorer search "what about"
# Find where decisions were made
```

## Customization

You can use custom instructions with `/cr:claude-code-session-explorer` to:
- Create verification reports for specific projects
- Build change logs
- Track evolution of features
- Document decision-making processes
- Generate session summaries
- Compare different approaches
- Analyze patterns in your workflow

Simply provide your custom instruction after invoking the command!
