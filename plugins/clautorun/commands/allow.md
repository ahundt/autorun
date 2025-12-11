---
description: Allow all file creation - full permission to create and modify files
allowed-tools: Bash(*)
---

# AutoFile Allow Mode

Run this command and display the result to the user:

```bash
echo '{"prompt": "/cr:allow", "session_id": "default"}' | "${CLAUDE_PLUGIN_ROOT}/commands/clautorun" | python3 -c "import sys,json; r=json.load(sys.stdin); print(r.get('response','No response'))"
```

Display ONLY the output of the above command. Do not add any additional commentary.
