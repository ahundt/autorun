# Claude Code Session File Structure Reference

This document provides detailed technical information about Claude Code `.jsonl` Claude session transcript files.

## File Format

Claude session files are **newline-delimited JSON** (JSONL format):
- One JSON object per line
- Each line is a complete, independent JSON document
- Files can be processed line-by-line with `grep` and `jq`

## Location Patterns

### Standard Project Sessions
```
~/.claude/projects/<encoded-project-path>/<session-id>.jsonl
```

**Path encoding:** Every character that is NOT alphanumeric or a hyphen is replaced with `-`.
This includes `/`, `.`, `_`, spaces, and all other special characters.

Source: Claude Code TypeScript source, `getProjectPath()` in `utils/path.ts`
Rule: `path.replace(/[^a-zA-Z0-9-]/g, '-')`

- Example: `/Users/alice/source/myproject` → `-Users-alice-source-myproject`
- Example: `/Users/alice/.claude` → `-Users-alice--claude` (`.` → `-`, creating double dash)
- Example: `/Users/me/my_project` → `-Users-me-my-project` (`_` → `-`)

### Subagent Sessions
```
~/.claude/projects/<encoded-project-path>/<parent-session-id>/subagents/agent-<id>.jsonl
```

## Message Types

### User Messages
```json
{
  "type": "user",
  "sessionId": "73722f5a-92c5-4c44-8a6a-3665ad8b1cce",
  "timestamp": "2026-01-24T03:08:02.764Z",
  "gitBranch": "main",
  "cwd": "/home/<user>/myproject",
  "message": {
    "role": "user",
    "content": "message text"  // or array format (see below)
  }
}
```

### Assistant Messages
```json
{
  "type": "assistant",
  "message": {
    "role": "assistant",
    "content": [
      {"type": "text", "text": "response"},
      {"type": "tool_use", "name": "Read", "input": {...}}
    ]
  }
}
```

### Compact Summaries
```json
{
  "type": "user",
  "isCompactSummary": true,
  "message": {
    "role": "user",
    "content": "Analysis:\nLet me chronologically analyze...\n\n1. Primary Request...\n2. Key Technical Concepts...\n..."
  }
}
```

**Created when:** Context window runs out, Claude compacts conversation history
**Contains:** Chronological analysis, technical concepts, files modified, errors/fixes, all user messages

### File History Snapshots
```json
{
  "type": "file-history-snapshot",
  "messageId": "...",
  "snapshot": {
    "trackedFileBackups": {...},
    "timestamp": "2026-01-24T03:08:02.631Z"
  }
}
```

## Content Formats

### Legacy Format (String)
```json
"content": "User message text here"
```

**Used in:** Older sessions, compact summaries
**Extraction:** `jq -r '.message.content'`

### Modern Format (Array)
```json
"content": [
  {"type": "text", "text": "User message"},
  {"type": "image", "source": {...}},
  {"type": "tool_result", "tool_use_id": "...", "content": "..."}
]
```

**Used in:** Current sessions (2024+)
**Extraction:** `jq -r '.message.content[] | select(.type == "text") | .text'`

### Handling Both Formats

Use conditional jq to handle both:
```bash
jq -r '
  if (.message.content | type) == "string" then
    .message.content
  else
    .message.content[] | select(.type == "text") | .text
  end
'
```

## Key Fields Reference

### Session-Level Fields
- `sessionId` (string) - Unique identifier (UUID format)
- `timestamp` (ISO 8601) - Message timestamp
- `gitBranch` (string) - Active git branch when message sent
- `cwd` (string) - Working directory
- `version` (string) - Claude Code version (e.g., "2.1.17")
- `type` (string) - Message type: "user", "assistant", "file-history-snapshot"

### Message-Specific Fields
- `message.role` (string) - "user" or "assistant"
- `message.content` (string | array) - Message content
- `message.model` (string) - Model used (e.g., "claude-sonnet-4-5-20250929")
- `isCompactSummary` (boolean) - True for context compaction summaries

### Optional Fields
- `parentUuid` (string) - Parent message UUID (for threading)
- `isSidechain` (boolean) - True for side conversations
- `slug` (string) - Human-readable session identifier
- `requestId` (string) - API request ID

## Filtering Patterns

### Filter System Messages

System messages to exclude from user message extraction:
```
[Request interrupted by user]
[Request interrupted by user for tool use]
<task-notification>
<system-reminder>
null
```

**grep filter:**
```bash
grep -v '^null$' |
grep -v '^\[Request interrupted' |
grep -v '^<task-notification>' |
grep -v '^<system-reminder>'
```

### Find Specific Message Types

**User messages only:**
```bash
grep '"type":"user"' session.jsonl
```

**Compact summaries only:**
```bash
jq -r 'select(.isCompactSummary == true)' session.jsonl
```

**Messages from specific date:**
```bash
jq -r 'select(.timestamp | startswith("2026-01-24"))' session.jsonl
```

**Messages on specific branch:**
```bash
jq -r 'select(.gitBranch == "main")' session.jsonl
```

## Common Extraction Patterns

### Extract All Text (User + Assistant)
```bash
cat session.jsonl | jq -r '
  select(.type == "user" or .type == "assistant") |
  if (.message.content | type) == "string" then
    .message.role + ": " + .message.content
  else
    .message.role + ": " + (.message.content[] | select(.type == "text") | .text)
  end
' | grep -v '^null$'
```

### Find Sessions by Keyword
```bash
# Find sessions mentioning "indentation"
for session in ~/.claude/projects/*/[0-9a-f]*.jsonl; do
  if grep -q "indentation" "$session"; then
    echo "$session"
  fi
done
```

### Extract Tool Usage
```bash
# Find all tool calls in session
cat session.jsonl | jq -r '
  select(.type == "assistant") |
  .message.content[] |
  select(.type == "tool_use") |
  .name
' | sort | uniq -c
```

### Get Session Timeline
```bash
# Extract chronological timeline
cat session.jsonl | jq -r '
  select(.type == "user" or .type == "assistant") |
  .timestamp + " [" + .message.role + "]"
'
```

## Performance Considerations

### Large Session Files

Claude session files can be 10-50MB+ for long conversations:

**Efficient processing:**
```bash
# Stream processing with grep before jq
grep '"type":"user"' large_session.jsonl | jq -r '.message.content'

# Not: cat large_session.jsonl | jq 'select(.type == "user")'
```

**Count before extracting:**
```bash
# Check message count first
grep -c '"type":"user"' session.jsonl
```

### Multiple Session Processing

When processing many sessions, use parallel processing:

```bash
# Serial (slow)
for session in *.jsonl; do
  process_session "$session"
done

# Parallel (faster)
find . -name "*.jsonl" | xargs -P 4 -I {} process_session {}
```

## Troubleshooting

### Issue: jq Parse Errors

**Symptom:** `jq: error (at <stdin>:110): Cannot index string with number`

**Cause:** Message has string content, trying to access as array

**Solution:** Use conditional content handling (see "Handling Both Formats" above)

### Issue: Empty Output

**Symptom:** User message extraction returns empty file

**Debug steps:**
```bash
# 1. Check file exists
ls -lh session.jsonl

# 2. Count user messages
grep -c '"type":"user"' session.jsonl

# 3. Check first user message structure
grep '"type":"user"' session.jsonl | head -1 | jq .

# 4. Verify content field exists
grep '"type":"user"' session.jsonl | head -1 | jq '.message.content | type'
```

### Issue: Malformed JSON

**Symptom:** jq fails with parse error

**Solution:** Use `-r` flag and redirect errors:
```bash
jq -r 'query' session.jsonl 2>/dev/null
```

### Issue: Wrong Session Directory

**Symptom:** Session file not found

**Debug:**
```bash
# List all project directories
ls -1 ~/.claude/projects/

# Find session by partial ID
find ~/.claude/projects -name "73722f5a*"
```

## Advanced Queries

### Find Long Sessions
```bash
# Sessions with >100 user messages
for session in *.jsonl; do
  count=$(grep -c '"type":"user"' "$session")
  if [[ $count -gt 100 ]]; then
    echo "$session: $count messages"
  fi
done
```

### Extract Error Messages
```bash
# Find user messages mentioning errors
grep '"type":"user"' session.jsonl | jq -r '.message.content' | grep -i "error\|bug\|broken\|fix"
```

### Build Session Graph
```bash
# Map parent-child session relationships
jq -r '
  select(.type == "user") |
  .sessionId + " -> " + (.parentUuid // "root")
' session.jsonl | sort -u
```

### Timeline with Content Snippets
```bash
# Show timeline with first 50 chars of each message
cat session.jsonl | jq -r '
  select(.type == "user") |
  .timestamp + " " + (
    if (.message.content | type) == "string" then
      .message.content[:50]
    else
      (.message.content[] | select(.type == "text") | .text)[:50]
    end
  )
'
```

---

**See also:**
- SKILL.md for usage workflows
- `aise export session SESSION_ID` — export one session to markdown
- `aise export recent 7` — bulk export last N days of sessions
