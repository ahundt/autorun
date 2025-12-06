---
description: Set AutoFile policy to strict-search mode (only modify existing files)
allowed-tools: Bash(*)
---

# AutoFile Strict Search Mode

!`echo '{"prompt": "/afs", "session_id": "default"}' | "${CLAUDE_PLUGIN_ROOT}/commands/clautorun"`

Set file policy to strict search - only modify existing files, no new file creation.

This activates strict search mode which blocks all new file creation and forces use of Glob/Grep to modify existing files only.
