---
description: Show active global pattern blocks and allows
---

# Show Global Block Status

Display the current global command blocks and allows (persists across sessions).

**Usage**: `/ar:globalstatus`

Shows:
- Global blocks added via `/ar:globalno <pattern>`
- Global allows added via `/ar:globalok <pattern>`

Does not show session blocks (use `/ar:blocks` for those) or default safety guards.

UserPromptSubmit hook processes this command and displays global state.
