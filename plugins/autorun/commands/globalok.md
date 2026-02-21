---
description: Allow a blocked pattern globally (persists across sessions)
---

# Global Allow Pattern

Remove a pattern from the global block list, or add a global allow to override default safety guards.

**Usage**: `/cr:globalok <pattern>`

**Examples**:
- `/cr:globalok rm` — globally allow rm (disables rm safety guard for all sessions)
- `/cr:globalok grep` — globally allow grep bash command

See also: `/cr:globalstatus` to view current global state.

UserPromptSubmit hook processes this command and updates global state (shelve DB).
