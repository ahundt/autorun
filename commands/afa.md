---
description: Set AutoFile policy to allow-all mode (full file creation permissions)
allowed-tools: Bash(*)
---

# AutoFile Allow All Mode

!`echo '{"prompt": "/afa", "session_id": "default"}' | "${CLAUDE_PLUGIN_ROOT}/commands/clautorun" | python3 -c "import sys,json; r=json.load(sys.stdin); print(r.get('response','No response'))"`

Enable full permission to create and modify files without restrictions.
