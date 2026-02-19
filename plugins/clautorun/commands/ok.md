---
description: Allow a blocked pattern in this session (short for /cr:unblock)
---

# Session Allow Pattern

Remove a pattern from the session block list, or add it to the session allow list to override global blocks.

**Usage**: `/cr:ok <pattern>`

**Examples**:
- `/cr:ok rm` — allow rm in this session
- `/cr:ok grep` — allow grep in this session (overrides safety guard)

UserPromptSubmit hook processes this command and updates session state.
