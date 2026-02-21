---
description: Show active global pattern blocks and allows
---

# Show Global Block Status

Display the current global command blocks and allows (persists across sessions).

**Usage**: `/cr:globalstatus`

Shows:
- Global blocks added via `/cr:globalno <pattern>`
- Global allows added via `/cr:globalok <pattern>`

Does not show session blocks (use `/cr:blocks` for those) or default safety guards.

UserPromptSubmit hook processes this command and displays global state.
