---
description: Allow a blocked pattern globally (persists across sessions)
---

# Global Allow Pattern

Remove a pattern from the global block list, or add a global allow to override default safety guards.

**Usage**: `/ar:globalok <pattern>`

**Examples**:
- `/ar:globalok rm` — globally allow rm (disables rm safety guard for all sessions)
- `/ar:globalok grep` — globally allow grep bash command

See also: `/ar:globalstatus` to view current global state.

UserPromptSubmit hook processes this command and updates global state (shelve DB).
