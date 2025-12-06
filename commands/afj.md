---
description: Set AutoFile policy to justify-create mode (require justification for new files)
allowed-tools: Bash(*)
---

# AutoFile Justify Create Mode

!`echo '{"prompt": "/afj", "session_id": "default"}' | "${CLAUDE_PLUGIN_ROOT}/commands/clautorun" | python3 -c "import sys,json; r=json.load(sys.stdin); print(r.get('response','No response'))"`

Require justification before creating new files. AI must include AUTOFILE_JUSTIFICATION tag with reasoning for new file creation.
