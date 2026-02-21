---
description: Show active session-level pattern blocks and allows
---

# Show Session Blocks

Display the current session-level command blocks and allows.

**Usage**: `/ar:blocks`

Shows:
- Session blocks added via `/ar:no <pattern>`
- Session allows added via `/ar:ok <pattern>`

Does not show global blocks (use `/ar:globalstatus` for those) or default safety guards.

UserPromptSubmit hook processes this command and displays session state.
