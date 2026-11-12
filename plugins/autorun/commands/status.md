---
description: Show this session's file policy, its /ar:ok and /ar:no rules, and the global rules
---

# autorun status

Shows, for the current session:

- the new-file policy set by `/ar:file` (allow-all, justify-create or strict-search);
- session blocks and allows added with `/ar:no` and `/ar:ok`;
- global blocks and allows added with `/ar:no --global` and `/ar:ok --global`;
- whether task enforcement is paused.

**Usage**: `/ar:status`

The default safety guards are not listed.

UserPromptSubmit hook processes this command and displays the state.
