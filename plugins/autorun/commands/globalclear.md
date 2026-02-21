---
description: Clear all global pattern blocks and allows
---

# Clear Global Overrides

Remove all global command blocks and allows. Default safety guards remain active.

**Usage**: `/cr:globalclear`

After clearing, only default safety guards and session-level blocks apply.

See also: `/cr:globalstatus` to view global blocks before clearing.

UserPromptSubmit hook processes this command and clears global state (shelve DB).
