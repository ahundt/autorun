---
name: export-claude-sessions
description: This skill should be used when the user asks to "export Claude session history", "extract my Claude messages", "get Claude session transcript", "save my messages from this Claude session", "preserve Claude session context", "extract user messages from Claude sessions", mentions "Claude Code session.jsonl files", or needs to retrieve user messages and session summaries from Claude Code session transcript files for documentation or debugging purposes.
---

# Export Claude Session History from Transcript Files

Extract user messages and session summaries from Claude Code `.jsonl` session transcript files. Essential for preserving session history, debugging issues, and understanding code evolution across Claude Code sessions.

## When to Use This Skill

Use this skill when needing to:
- Export Claude Code session history for documentation
- Retrieve detailed bug descriptions from past Claude sessions
- Preserve session context before Claude session cleanup
- Debug issues by reviewing historical Claude sessions
- Build comprehensive project notes with user requirements from Claude sessions
- Find related Claude Code sessions for complete context

## Claude Code Session File Location

Claude Code stores session transcripts in project-specific directories:
```
~/.claude/projects/<project-path-encoded>/<session-id>.jsonl
```

**Path encoding:** `/` becomes `-` in directory names
- Example: `/Users/name/project` → `~/.claude/projects/-Users-name-project/`

## Quick Start Commands

### Extract Single Claude Code Session

Use the provided export script:

```bash
# Navigate to skill directory
cd ~/.claude/clautorun/plugins/clautorun/skills/export-claude-sessions

# Export Claude session with metadata and summaries
./scripts/export_claude_session.sh <session-id> output.md [project-dir]

# Example:
./scripts/export_claude_session.sh 73722f5a-92c5-4c44-8a6a-3665ad8b1cce claude_session_export.md
```

**Output includes:**
- Claude session metadata (date, git branch, working directory)
- Compact session summary (if available from context compaction)
- All user messages from the Claude session (filtered, cleaned)

### Export Recent Claude Code Sessions

Extract all Claude Code sessions from last N days:

```bash
# Export last 2 days of Claude sessions
./scripts/export_recent_claude_sessions.sh 2 recent_claude_sessions.md

# Export last 7 days
./scripts/export_recent_claude_sessions.sh 7 last_week_claude_sessions.md

# Custom project directory
./scripts/export_recent_claude_sessions.sh 2 output.md ~/.claude/projects/-Users-name-custom-project/
```

**Output includes:**
- All Claude Code sessions from specified time range
- Metadata and session summaries for each Claude session
- User messages organized by Claude session

## Core Extraction Commands

### User Messages from Claude Code Session

Extract clean user text messages from a Claude Code session:

```bash
cat session.jsonl | grep '"type":"user"' | jq -r '
  if (.message.content | type) == "string" then
    .message.content
  else
    .message.content[] | select(.type == "text") | .text
  end
' 2>/dev/null | grep -v '^null$' | grep -v '^\[Request interrupted' | grep -v '^<task-notification>' | grep -v '^<system-reminder>'
```

**What this does:**
- Filters for user messages only
- Handles both string and array content formats
- Removes system notifications and interruptions
- Outputs clean message text

### Claude Code Session Summaries

Extract session summaries created during Claude Code context compaction:

```bash
cat session.jsonl | jq -r 'select(.isCompactSummary == true) | .message.content' 2>/dev/null | head -1
```

**Summaries include:**
- Chronological session analysis
- Technical concepts discussed
- Files modified and code changes
- Errors encountered and fixes applied
- Problem-solving process
- User message history
- Pending tasks

### Claude Code Session Metadata

Extract Claude Code session information:

```bash
cat session.jsonl | head -1 | jq '{
  sessionId: .sessionId,
  timestamp: .timestamp,
  gitBranch: .gitBranch,
  cwd: .cwd,
  version: .version
}'
```

### Find Recent Claude Code Sessions

Find Claude Code sessions modified in last N days:

```bash
# Last 2 days
find ~/.claude/projects/-Users-athundt-source-general-processtree/ -name "*.jsonl" -mtime -2 -type f

# Exclude subagent sessions
find ~/.claude/projects/*/  -name "*.jsonl" -mtime -2 -type f | grep -v "/subagents/"

# Sort by modification time
find ~/.claude/projects/*/ -name "*.jsonl" -mtime -2 -type f -exec ls -lt {} +
```

## Common Workflows

### Append Claude Session History to Project Notes

Add Claude Code session history to existing documentation:

```bash
# Export and append Claude session
echo "" >> notes/investigation.md
echo "## Claude Code Session History" >> notes/investigation.md
./scripts/export_claude_session.sh <session-id> - >> notes/investigation.md
```

### Find Claude Code Sessions with Keywords

Locate Claude Code sessions mentioning specific topics:

```bash
# Find sessions about "indentation"
grep -l "indentation" ~/.claude/projects/-Users-name-project/*.jsonl

# With session IDs
find ~/.claude/projects/-Users-name-project/ -name "*.jsonl" -exec grep -l "table.*indent" {} \;
```

### Extract Across Multiple Related Claude Code Sessions

For complete context spanning multiple related Claude Code sessions:

```bash
# Get session IDs from transcript references
grep -h "read the full transcript at:" current_session.jsonl | \
  grep -o '[0-9a-f-]\{36\}\.jsonl' | \
  sed 's/\.jsonl$//' | \
  sort -u > related_sessions.txt

# Export each
while read session_id; do
  ./scripts/export_claude_session.sh "$session_id" "export_${session_id}.md"
done < related_sessions.txt
```

## Integration with Notes Files

### Pattern: Claude Code Session References Section

Add Claude Code conversation transcript references to project notes:

```markdown
## Related Claude Code Session Transcripts

### Primary Implementation Session
**Session ID:** 73722f5a-92c5-4c44-8a6a-3665ad8b1cce
**Context:** Table indentation fixes and UI improvements
**Transcript:** `~/.claude/projects/-Users-name-project/73722f5a-92c5-4c44-8a6a-3665ad8b1cce.jsonl`

If you need specific details from this Claude Code session (code snippets, error messages, debugging process), read the full transcript at the path above.
```

### Pattern: Complete Claude Session History

Append full user message history from Claude Code sessions to notes:

```markdown
## Complete Claude Code Session History

This section contains all user messages from related Claude Code sessions for complete context.

### Claude Session: 73722f5a-92c5-4c44-8a6a-3665ad8b1cce
**Lines:** 821 messages
**Date:** 2026-01-24

[User messages here]
```

## Tips and Best Practices

**Filter system messages:** Always exclude `[Request interrupted`, `<task-notification>`, `<system-reminder>` for clean output

**Check both content formats:** Claude Code sessions may use string or array content - the provided jq command handles both

**Extract summaries first:** Compact session summaries provide excellent high-level context before diving into detailed messages

**Preserve git branch info:** Cross-reference Claude session timestamps with git commits for complete picture

**Use provided scripts:** `export_claude_session.sh` and `export_recent_claude_sessions.sh` handle edge cases correctly

**Count messages as sanity check:**
```bash
grep -c '"type":"user"' claude_session.jsonl  # Should match expectations
```

**Follow conversation chains:** When Claude Code sessions mention "read the full transcript at:", extract those related sessions too for complete context

## Additional Resources

### Scripts
- **`scripts/export_claude_session.sh`** - Complete Claude session export with metadata and summaries
- **`scripts/export_recent_claude_sessions.sh`** - Bulk export from last N days

### References
- **`references/claude-session-format.md`** - Detailed Claude session file format documentation, message types, field reference, advanced queries

---

**See also:**
- Git commit workflow (reference session IDs in commits)
- Project documentation patterns
- Context preservation strategies
