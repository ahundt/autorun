---
description: Find existing files only - no new file creation (short for /cr:find)
---

# AutoFile Strict Search Mode

Policy set to: **strict-search**

**ONLY modify existing files** - all new file creation is blocked.

You must use Glob/Grep to find existing files first, then modify them. This prevents file clutter during refactoring.

UserPromptSubmit hook has updated the session policy.
