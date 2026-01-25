#!/bin/bash
# Export all sessions from last N days into a single documentation file
# Usage: ./export_recent_sessions.sh <days> <output-file> [project-dir]

set -e

DAYS="${1:-2}"  # Default to 2 days
OUTPUT="${2:-recent_sessions.md}"
PROJECT_DIR="${3:-$HOME/.claude/projects/-Users-athundt-source-general-processtree}"

echo "# Recent Sessions Export (Last $DAYS Days)" > "$OUTPUT"
echo "" >> "$OUTPUT"
echo "**Export Date:** $(date)" >> "$OUTPUT"
echo "" >> "$OUTPUT"

# Find all sessions from last N days (exclude subagents)
SESSIONS=$(find "$PROJECT_DIR" -name "*.jsonl" -mtime -"$DAYS" -type f | grep -v "/subagents/" | sort -r)

SESSION_COUNT=$(echo "$SESSIONS" | wc -l | xargs)
echo "**Total Sessions:** $SESSION_COUNT" >> "$OUTPUT"
echo "" >> "$OUTPUT"
echo "---" >> "$OUTPUT"
echo "" >> "$OUTPUT"

for SESSION_FILE in $SESSIONS; do
  SESSION_ID=$(basename "$SESSION_FILE" .jsonl)

  echo "## Session: $SESSION_ID" >> "$OUTPUT"
  echo "" >> "$OUTPUT"

  # Metadata
  cat "$SESSION_FILE" | head -1 | jq -r '"**Date:** " + .timestamp + "
**Git Branch:** " + (.gitBranch // "unknown") + "
**Working Directory:** " + .cwd' 2>/dev/null >> "$OUTPUT"
  echo "" >> "$OUTPUT"

  # Summary if available
  SUMMARY=$(cat "$SESSION_FILE" | jq -r 'select(.isCompactSummary == true) | .message.content' 2>/dev/null | head -1)
  if [[ -n "$SUMMARY" && "$SUMMARY" != "null" ]]; then
    echo "### Summary" >> "$OUTPUT"
    echo "" >> "$OUTPUT"
    echo "$SUMMARY" >> "$OUTPUT"
    echo "" >> "$OUTPUT"
  fi

  # User messages
  echo "### User Messages" >> "$OUTPUT"
  echo "" >> "$OUTPUT"
  MSG_COUNT=$(cat "$SESSION_FILE" | grep -c '"type":"user"')
  echo "**Total user messages:** $MSG_COUNT" >> "$OUTPUT"
  echo "" >> "$OUTPUT"

  cat "$SESSION_FILE" | grep '"type":"user"' | jq -r '
    if (.message.content | type) == "string" then
      .message.content
    else
      .message.content[] | select(.type == "text") | .text
    end
  ' 2>/dev/null | grep -v '^null$' | grep -v '^\[Request interrupted' | grep -v '^<task-notification>' | grep -v '^<system-reminder>' >> "$OUTPUT"

  echo "" >> "$OUTPUT"
  echo "---" >> "$OUTPUT"
  echo "" >> "$OUTPUT"
done

echo "✅ Recent sessions exported to: $OUTPUT"
wc -l "$OUTPUT"
