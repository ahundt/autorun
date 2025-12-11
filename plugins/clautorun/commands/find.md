---
description: Find existing files only - prevents new file creation (strictest mode)
allowed-tools: Bash(*)
---

# AutoFile Find Mode

Run this command and display the result to the user:

```bash
echo '{"prompt": "/cr:find", "session_id": "default"}' | "${CLAUDE_PLUGIN_ROOT}/commands/clautorun" | python3 -c "import sys,json; r=json.load(sys.stdin); print(r.get('response','No response'))"
```

**What this does**: Activates the strictest file policy. AI must FIND and modify existing files instead of creating new ones. Use Glob/Grep to search for files before making changes.

Display ONLY the output of the above command. Do not add any additional commentary.
