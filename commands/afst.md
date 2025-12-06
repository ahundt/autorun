---
description: Show current AutoFile policy and settings
allowed-tools: Bash(*)
---

# AutoFile Status

!`echo '{"prompt": "/afst", "session_id": "default"}' | "${CLAUDE_PLUGIN_ROOT}/commands/clautorun" | python3 -c "import sys,json; r=json.load(sys.stdin); print(r.get('response','No response'))"`

Display the current file creation policy and enforcement settings.
