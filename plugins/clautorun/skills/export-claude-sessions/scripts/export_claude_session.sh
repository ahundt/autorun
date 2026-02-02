#!/bin/bash
# Export complete session history with metadata and summaries
# Usage: ./export_session.sh <session-id> <output-file> [project-dir]

set -e

SESSION_ID="$1"
OUTPUT="$2"
# Default: encode current working directory as Claude project path
# CLAUDE_PROJECT_DIR > explicit arg > cwd-based encoding
_CWD_ENCODED=$(echo "${CLAUDE_PROJECT_DIR:-$(pwd)}" | sed 's|/|-|g')
PROJECT_DIR="${3:-$HOME/.claude/projects/$_CWD_ENCODED}"
SESSION_FILE="$PROJECT_DIR/$SESSION_ID.jsonl"

if [[ -z "$SESSION_ID" ]] || [[ -z "$OUTPUT" ]]; then
  echo "Usage: $0 <session-id> <output-file> [project-dir]"
  echo "Example: $0 73722f5a-92c5-4c44-8a6a-3665ad8b1cce session.md"
  exit 1
fi

# Check if session file exists
if [[ ! -f "$SESSION_FILE" ]]; then
  echo "Error: Session file not found: $SESSION_FILE"
  exit 1
fi

# Start output file
echo "# Session Export: $SESSION_ID" > "$OUTPUT"
echo "" >> "$OUTPUT"
echo "**Export Date:** $(date)" >> "$OUTPUT"
echo "" >> "$OUTPUT"

# Extract session metadata
echo "## Session Metadata" >> "$OUTPUT"
echo "" >> "$OUTPUT"
cat "$SESSION_FILE" | head -1 | jq -r '"**Session ID:** " + .sessionId + "
**Date:** " + .timestamp + "
**Git Branch:** " + .gitBranch + "
**Working Directory:** " + .cwd + "
**Claude Code Version:** " + .version' >> "$OUTPUT"
echo "" >> "$OUTPUT"

# Extract compact summary if available
echo "## Session Summary" >> "$OUTPUT"
echo "" >> "$OUTPUT"
SUMMARY=$(cat "$SESSION_FILE" | jq -r 'select(.isCompactSummary == true) | .message.content' 2>/dev/null | head -1)
if [[ -n "$SUMMARY" && "$SUMMARY" != "null" ]]; then
  echo "$SUMMARY" >> "$OUTPUT"
else
  echo "*No compact summary available for this session.*" >> "$OUTPUT"
fi
echo "" >> "$OUTPUT"

# Extract user messages
echo "## User Messages" >> "$OUTPUT"
echo "" >> "$OUTPUT"
echo "The following are all user messages from this conversation session:" >> "$OUTPUT"
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
echo "**End of session export**" >> "$OUTPUT"

echo "✅ Session exported to: $OUTPUT"
wc -l "$OUTPUT"
