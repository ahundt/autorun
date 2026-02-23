---
name: claude-session-tools
description: This skill should be used when the user asks to "find Claude sessions", "explore session history", "search Claude sessions", "analyze session", "list sessions", "extract session content", "export Claude sessions", "find tool usage in sessions", "session timeline", "recover a file", "find a file from a previous session", "recover code from a session", mentions "Claude Code session.jsonl files", or needs to explore, search, analyze, export, or recover files and code from Claude Code session histories stored in ~/.claude/projects/.
version: "0.9.0"

# VISIBILITY & TRIGGERING
user-invocable: true
disable-model-invocation: false

# EXECUTION CONTEXT - Tools this skill can use
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
---

# Claude Session Tools

General-purpose tool for exploring, searching, analyzing, exporting, and recovering files and code from Claude Code session histories stored in `~/.claude/projects/`.

All capabilities are provided by `aise` (from [ai-session-tools](https://github.com/ahundt/ai_session_tools)).

## Quick Reference

```bash
# Show all available commands
aise --help
aise files --help
aise messages --help
aise tools --help
aise export --help
```

## File and Code Recovery

```bash
# Find all files Claude wrote or edited across all sessions
aise files search

# Find specific files by pattern
aise files search --pattern "*.py"
aise files search --pattern "cli.py"

# See version history of a file (read-only)
aise files history cli.py

# Extract latest version of a file to stdout
aise files extract cli.py

# Extract a specific version
aise files extract cli.py --version 2

# Redirect to disk
aise files extract cli.py > cli.py

# Find files modified in a specific session
aise files search --include-sessions ab841016

# Find files by extension with min edits
aise files search --include-extensions py --min-edits 3
```

## Session Discovery

```bash
# List all sessions (newest first)
aise list

# Filter by project
aise list --project myproject

# Filter by date range
aise list --after 2026-01-01 --before 2026-02-01

# JSON output for scripting
aise list --format json

# Limit results
aise list --limit 10
```

## Message Search

```bash
# Search messages across all sessions
aise messages search "authentication bug"
aise search messages --query "authentication bug"

# Filter by message type
aise messages search "error" --type user
aise messages search "fix" --type assistant

# Increase result limit
aise messages search "function" --limit 20

# Search with context (N messages before/after match)
aise messages search "error" --context 3

# JSON output
aise messages search "refactor" --format json
```

## Session Content

```bash
# Read all messages from a specific session
aise messages get ab841016
aise get ab841016

# Read only user messages
aise messages get ab841016 --type user

# Read only assistant messages
aise messages get ab841016 --type assistant --limit 5

# Extract clipboard (pbcopy) content from a session
aise messages extract ab841016 pbcopy

# JSON output of clipboard entries
aise messages extract ab841016 pbcopy --format json
```

## Tool Usage Analysis

```bash
# Find all Write tool calls
aise tools search Write

# Find Bash calls matching a pattern
aise tools search Bash "git commit"

# Find Edit calls with a specific filename
aise tools search Edit "cli.py"

# Find all Read calls (via root search with --tool)
aise search --tool Read --query "engine.py"

# JSON output
aise tools search Edit --format json
```

## Session Analysis

```bash
# Analyze session structure (message counts, tool usage, files touched)
aise messages analyze ab841016

# Chronological timeline of user/assistant events
aise messages timeline ab841016

# Shorter content preview in timeline
aise messages timeline ab841016 --preview-chars 80

# JSON output
aise messages analyze ab841016 --format json
```

## Correction Pattern Analysis

```bash
# Find user corrections across all sessions
aise messages corrections

# Filter by project
aise messages corrections --project myproject

# Limit results
aise messages corrections --limit 50

# Filter by date range
aise messages corrections --after 2026-01-01

# JSON output
aise messages corrections --format json
```

Categories detected: `regression`, `skip_step`, `misunderstanding`, `incomplete`, `other`

Patterns include: "you forgot", "you broke", "actually", "wrong", "nono", "stop", "you didn't", "wait,", "should have", "but you", and more.

## Planning Command Analysis

```bash
# Count planning command usage across all sessions
aise messages planning

# Filter by project
aise messages planning --project myproject

# JSON output
aise messages planning --format json
```

Commands tracked: `/ar:plannew`, `/ar:planrefine`, `/ar:planupdate`, `/ar:planprocess`, and their short aliases (`/ar:pn`, `/ar:pr`, `/ar:pu`, `/ar:pp`).

## Cross-Reference

```bash
# Find all Edit/Write calls to a file and check if content appears in current version
aise files cross-ref ./path/to/file.md

# Limit to a specific session
aise files cross-ref ./cli.py --session ab841016

# JSON output
aise files cross-ref ./engine.py --format json
```

Shows `✓` for edits found in current file, `✗` for edits that are missing or were overwritten.

## Export Operations

```bash
# Export single session to stdout (pipe to file)
aise export session ab841016
aise export session ab841016 > session.md

# Export to explicit output file
aise export session ab841016 --output session.md

# Preview without writing
aise export session ab841016 --dry-run

# Export all sessions from last N days
aise export recent 7 --output weekly_sessions.md
aise export recent 14 --output fortnight.md

# Bulk export filtered by project
aise export recent 7 --project myproject --output project_sessions.md

# Default: last 7 days
aise export recent --output this_week.md
```

Export includes: session metadata (date, git branch, working directory), all user+assistant messages filtered of system noise, and compact summaries when present.

## Statistics

```bash
# Session and file recovery statistics
aise stats
```

## Combined / Cross-Domain Search

```bash
# Search both files and messages at once
aise search --pattern "*.py" --query "error"

# Auto-detect domain from flags
aise search --query "authentication"       # → messages domain
aise search --pattern "cli.py"             # → files domain
aise search --tool Write --query "login"   # → tool search

# Explicit domain + tool filter
aise search tools --tool Bash --query "git commit"

# Aliases: find == search
aise find messages --query "error"
aise find files --pattern "*.py"
aise find --tool Edit --query "engine.py"
```

## Arguments

This command accepts arguments for automated operations:

```
$ARGUMENTS
```

## Exploration Workflow

### 1. List and understand available sessions
```bash
aise list --project myproject
aise list --after 2026-01-01
```
Identify which session contains the work you want to verify.

### 2. Extract relevant content
```bash
aise messages get SESSION_ID --type user     # what was asked
aise messages extract SESSION_ID pbcopy      # what was copied to clipboard
aise messages timeline SESSION_ID            # chronological view
```

### 3. Analyze changes
```bash
aise messages analyze SESSION_ID
```
Understand scale and scope of changes (tool usage, files touched).

### 4. Cross-reference with files
```bash
aise files cross-ref path/to/file.md --session SESSION_ID
```
Verify which of Claude's edits are present in the current file version.

### 5. Deep dive into specifics
```bash
aise tools search Edit "filename"
aise messages search "specific-phrase"
aise messages search "specific-phrase" --context 2
```
See all edits to a specific file, or find when something was discussed.

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
```bash
aise messages timeline SESSION_ID
aise messages analyze SESSION_ID --format json | jq '.tool_uses_by_name'
```

### Layer 3: Change Detection
Look for specific patterns of change:
```bash
aise messages search "content reordering" --context 3
aise messages search "section" --type user
```

### Layer 4: File Cross-Reference
Compare current files against session changes:
```bash
aise files cross-ref path/to/file.md
```
Shows which of Claude's Edit/Write calls are found in the current file.

### Layer 5: Pattern Matching
Use distinctive markers to find specific changes:
```bash
aise messages search "unique phrase"
aise tools search Edit "filename"
aise messages search "table column" --context 2
```

## Common Search Patterns

### Find all code discussions
```bash
aise messages search "function"
aise messages search "class"
aise messages search "interface"
```

### Find specific operations
```bash
aise messages search "git commit"
aise messages search "npm run"
aise messages search "pytest"
```

### Find all tool usage
```bash
aise tools search Bash
aise tools search Edit
aise tools search Read
aise tools search Grep
```

### Find discussion topics
```bash
aise messages search "error"
aise messages search "refactor"
aise messages search "optimization"
```

## Session File Structure

Sessions are stored as `.jsonl` files (JSON Lines format) at:
```
~/.claude/projects/<ENCODED-PATH>/
  ├── <SESSION-ID>.jsonl
  ├── agent-<ID>.jsonl
  └── ...
```

The `<ENCODED-PATH>` is the project directory path with every non-alphanumeric,
non-hyphen character replaced by `-` (rule: `[^a-zA-Z0-9-]` → `-`):
- macOS: `/Users/<user>/project` → `-Users-<user>-project`
- macOS: `/Users/<user>/.claude` → `-Users-<user>--claude` (`.` → `-`)
- Linux: `/home/<user>/project` → `-home-<user>-project`
- Underscores: `/my_project` → `-my-project` (`_` → `-`)

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
        "input": { "/* tool input */" },
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

## Configuration

### Environment Variable

Override the projects directory:

```bash
export AI_SESSION_TOOLS_PROJECTS=~/.claude/projects
```

## Tips and Best Practices

1. **Always extract complete content** - Don't rely on truncated previews; use `--max-chars 0`
2. **Use timestamps to verify order** - Chronological sequence matters; use `aise messages timeline`
3. **Cross-reference multiple sources** - Compare tool outputs with file results via `aise files cross-ref`
4. **Search broadly first** - Find all mentions before deep dive; use `aise messages search`
5. **Classify by content type** - Group similar changes together using `--type user/assistant`
6. **Use JSON output for scripting** - Add `--format json` to pipe results to `jq` or other tools
7. **Combine domains** - `aise search --pattern "*.py" --query "error"` searches both files and messages
8. **Check corrections** - `aise messages corrections` reveals patterns in how you guide Claude
9. **Track planning** - `aise messages planning` shows which planning commands you use most
10. **Export for documentation** - `aise export session ID > notes/session.md`

## Verification Checklist

When verifying changes were applied:

- [ ] `aise list --project myproject` — identify relevant sessions
- [ ] `aise messages get SESSION_ID --type user` — review what was asked
- [ ] `aise messages timeline SESSION_ID` — understand chronological sequence
- [ ] `aise messages analyze SESSION_ID` — check tool usage counts
- [ ] `aise files cross-ref path/to/file.md` — verify edits appear in current file
- [ ] `aise tools search Edit "filename"` — see all specific file edits
- [ ] `aise messages search "key phrase" --context 2` — find decision points with context
- [ ] `aise export session SESSION_ID > notes/session.md` — document the session

## Export for Documentation

### Single Session Export

```bash
aise export session SESSION_ID --output documentation/session-analysis.md
aise export session SESSION_ID > documentation/session-analysis.md
```

### Bulk Export

```bash
aise export recent 7 --output documentation/weekly-work.md
aise export recent 14 --project myproject --output documentation/sprint.md
```

### Integration with Notes

```bash
aise export session SESSION_ID >> notes/investigation.md
```

