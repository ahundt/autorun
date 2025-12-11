---
description: Set AutoFile policy to allow-all mode (full file creation permissions)
allowed-tools: Bash(*)
---

# AutoFile Allow All Mode

Run this command and display the result to the user:

```bash
echo '{"prompt": "/afa", "session_id": "default"}' | "${CLAUDE_PLUGIN_ROOT}/commands/clautorun" | python3 -c "import sys,json; r=json.load(sys.stdin); print(r.get('response','No response'))"
```

Display ONLY the output of the above command. Do not add any additional commentary.
