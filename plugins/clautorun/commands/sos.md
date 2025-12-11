---
description: Emergency stop - immediately halt all actions (short for /cr:estop)
allowed-tools: Bash(*)
---

# Emergency Stop (SOS)

Run this command and display the result to the user:

```bash
echo '{"prompt": "/cr:sos", "session_id": "default"}' | "${CLAUDE_PLUGIN_ROOT}/commands/clautorun" | python3 -c "import sys,json; r=json.load(sys.stdin); print(r.get('response','No response'))"
```

**WARNING**: This will immediately halt all autonomous operations.

Display ONLY the output of the above command. Do not add any additional commentary.
