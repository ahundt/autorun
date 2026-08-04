---
description: Clear all global pattern blocks and allows
# Manual-only: reachable by typing the command, not by model choice.
# Discards every global block and allow the user has accumulated.
disable-model-invocation: true
---

# Clear Global Overrides

Remove all global command blocks and allows. Default safety guards remain active.

**Usage**: `/ar:globalclear`

After clearing, only default safety guards and session-level blocks apply.

See also: `/ar:globalstatus` to view global blocks before clearing.

UserPromptSubmit hook processes this command and clears global state (daemon_state.json).
