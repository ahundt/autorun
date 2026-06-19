---
description: Find existing files only - no new file creation (short for /ar:find)
---

# AutoFile Strict Search Mode

Policy set to: **strict-search**

**ONLY modify existing files** - all new file creation is blocked.

You must use platform-native search to find existing files first, then modify them. Use Glob/Grep where those tools exist, or `rg --files` and `rg -n` in Codex.

UserPromptSubmit hook has updated the session policy.
