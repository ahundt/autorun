---
description: Remove this session's /ar:ok and /ar:no rules, or the global ones with --global
---

# Clear pattern rules

Removes every block and allow added with `/ar:no` and `/ar:ok` in this
session. With `--global`, removes the global ones instead. The default safety
guards stay active either way.

**Usage**: `/ar:clear` or `/ar:clear --global`

See also: `/ar:status` to view the current rules before clearing.

UserPromptSubmit hook processes this command and clears the selected rules.
