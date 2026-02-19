---
description: Show active session-level pattern blocks and allows
---

# Show Session Blocks

Display the current session-level command blocks and allows.

**Usage**: `/cr:blocks`

Shows:
- Session blocks added via `/cr:no <pattern>`
- Session allows added via `/cr:ok <pattern>`

Does not show global blocks (use `/cr:globalstatus` for those) or default safety guards.

UserPromptSubmit hook processes this command and displays session state.
